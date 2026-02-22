# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import re
import sys
from array import array
from dataclasses import dataclass, field
from queue import Empty, Full, Queue
from typing import Any, Dict, List, Optional, Tuple, Union

import sounddevice as sd
from ovos_config import Configuration
from ovos_plugin_manager.templates.microphone import Microphone
from ovos_utils.log import LOG

try:
    import audioop
except ImportError:
    audioop = None


DeviceRef = Union[str, int, None]


def _default_device() -> DeviceRef:
    listener = Configuration().get("listener", {})
    microphone = listener.get("microphone", {})
    if isinstance(microphone, dict):
        module_name = microphone.get("module")
        if module_name and isinstance(microphone.get(module_name), dict):
            device = microphone[module_name].get("device")
            if device is not None:
                return device
    device = listener.get("device")
    if device is not None:
        return device
    return "default"


@dataclass
class SoundDeviceMicrophone(Microphone):
    device: DeviceRef = field(default_factory=_default_device)
    timeout: float = 5.0
    multiplier: float = 1.0
    latency: Union[str, float] = "low"
    blocksize: Optional[int] = None
    queue_maxsize: int = 8

    # macOS / CoreAudio tuning
    use_coreaudio_settings: bool = True
    coreaudio_conversion_quality: str = "max"
    coreaudio_change_device_parameters: bool = False
    coreaudio_fail_if_conversion_required: bool = False

    # Cross-device resilience
    auto_sample_rate_fallback: bool = True
    auto_channel_fallback: bool = True
    auto_latency_fallback: bool = True

    _queue: Queue[Optional[bytes]] = field(init=False, repr=False)
    _chunk_buffer: bytearray = field(init=False, repr=False)
    stream: Optional[sd.RawInputStream] = field(default=None, init=False, repr=False)
    _capture_sample_rate: int = field(default=0, init=False, repr=False)
    _capture_channels: int = field(default=0, init=False, repr=False)
    _ratecv_state: Any = field(default=None, init=False, repr=False)
    _resample_tail: Optional[int] = field(default=None, init=False, repr=False)

    def __post_init__(self):
        queue_size = max(1, int(self.queue_maxsize))
        self._queue = Queue(maxsize=queue_size)
        self._chunk_buffer = bytearray()
        self._capture_sample_rate = int(self.sample_rate)
        self._capture_channels = int(self.sample_channels)
        if self.blocksize is None:
            # Keep callback cadence low-latency even if read chunk is larger.
            self.blocksize = min(self.frames_per_chunk, 1024)
        else:
            self.blocksize = max(0, int(self.blocksize))

    @staticmethod
    def _dtype_for_width(sample_width: int) -> str:
        mapping = {1: "int8", 2: "int16", 3: "int24", 4: "int32"}
        if sample_width not in mapping:
            raise ValueError(f"Unsupported sample width: {sample_width}")
        return mapping[sample_width]

    @staticmethod
    def _typecode_for_width(sample_width: int) -> str:
        mapping = {1: "b", 2: "h", 4: "i"}
        if sample_width not in mapping:
            raise ValueError(f"Unsupported sample width for software conversion: {sample_width}")
        return mapping[sample_width]

    @staticmethod
    def _range_for_width(sample_width: int) -> Tuple[int, int]:
        mapping = {
            1: (-128, 127),
            2: (-32768, 32767),
            4: (-2147483648, 2147483647),
        }
        if sample_width not in mapping:
            raise ValueError(f"Unsupported sample width for software conversion: {sample_width}")
        return mapping[sample_width]

    @staticmethod
    def list_input_devices() -> List[Tuple[int, Dict[str, Any]]]:
        devices = []
        for index, device in enumerate(sd.query_devices()):
            if device["max_input_channels"] > 0:
                devices.append((index, device))
        return devices

    @classmethod
    def find_input_device(cls, device_name: DeviceRef) -> Optional[int]:
        """Find audio input device by name.

        Args:
            device_name: device name or regex pattern to match

        Returns: device_index (int) or None if device wasn't found
        """
        if device_name is None:
            return None

        if isinstance(device_name, int):
            return device_name

        query = str(device_name).strip()
        if not query or query.lower() == "default":
            return None
        if query.isdigit():
            return int(query)

        inputs = cls.list_input_devices()
        if not inputs:
            return None

        lowered = query.lower()
        LOG.debug("Searching for input device: %s", query)
        LOG.debug("Available input devices:")
        for index, dev in inputs:
            LOG.debug("  %s: %s", index, dev["name"])

        for index, dev in inputs:
            if dev["name"].lower() == lowered:
                return index

        for index, dev in inputs:
            if lowered in dev["name"].lower():
                return index

        if lowered.startswith("regex:"):
            query = query.split(":", 1)[1]

        try:
            pattern = re.compile(query, re.IGNORECASE)
            for index, dev in inputs:
                if pattern.search(dev["name"]):
                    return index
        except re.error:
            LOG.warning("Invalid device regex, skipping regex lookup: %s", query)

        return None

    def _build_extra_settings(self, device_index: Optional[int], channels: int) -> Optional[Any]:
        if not self.use_coreaudio_settings or sys.platform != "darwin":
            return None
        if not hasattr(sd, "CoreAudioSettings"):
            return None

        try:
            device_info = sd.query_devices(device_index, "input")
            hostapi_info = sd.query_hostapis(device_info["hostapi"])
            hostapi_name = str(hostapi_info.get("name", "")).lower()
            if "core audio" not in hostapi_name:
                return None

            channel_map = list(range(channels))
            return sd.CoreAudioSettings(
                channel_map=channel_map,
                change_device_parameters=self.coreaudio_change_device_parameters,
                fail_if_conversion_required=self.coreaudio_fail_if_conversion_required,
                conversion_quality=self.coreaudio_conversion_quality,
            )
        except Exception:
            LOG.exception("Failed to build CoreAudio settings. Using defaults.")
            return None

    def _stream_open_attempts(
        self, device_index: Optional[int], dtype: str
    ) -> List[Dict[str, Any]]:
        if device_index is None:
            device_info = sd.query_devices(None, "input")
        else:
            device_info = sd.query_devices(device_index)

        max_input_channels = int(device_info.get("max_input_channels", 0))
        if max_input_channels < 1:
            raise RuntimeError("Selected input device does not support capture")

        channels = int(self.sample_channels)
        if channels > max_input_channels and self.auto_channel_fallback:
            LOG.warning(
                "Requested channels=%s but device only supports %s input channels. "
                "Falling back.",
                channels,
                max_input_channels,
            )
            channels = max_input_channels
        if channels < 1:
            channels = 1

        default_rate = int(float(device_info.get("default_samplerate", self.sample_rate)))
        sample_rates = [int(self.sample_rate)]
        if (
            self.auto_sample_rate_fallback
            and default_rate > 0
            and default_rate not in sample_rates
        ):
            sample_rates.append(default_rate)

        block_sizes = []
        for value in (self.blocksize, 0):
            if value not in block_sizes:
                block_sizes.append(value)

        latencies = [self.latency]
        if self.auto_latency_fallback and isinstance(self.latency, str):
            for fallback_latency in ("high", None):
                if fallback_latency not in latencies:
                    latencies.append(fallback_latency)

        attempts = []
        for rate in sample_rates:
            for blocksize in block_sizes:
                for latency in latencies:
                    extra_settings = self._build_extra_settings(device_index, channels)
                    attempt = {
                        "samplerate": rate,
                        "channels": channels,
                        "blocksize": blocksize,
                        "latency": latency,
                        "extra_settings": extra_settings,
                    }
                    attempts.append(attempt)
                    if extra_settings is not None:
                        fallback_attempt = dict(attempt)
                        fallback_attempt["extra_settings"] = None
                        attempts.append(fallback_attempt)

        deduped_attempts = []
        seen = set()
        for attempt in attempts:
            key = (
                attempt["samplerate"],
                attempt["channels"],
                attempt["blocksize"],
                attempt["latency"],
                bool(attempt["extra_settings"]),
            )
            if key not in seen:
                seen.add(key)
                deduped_attempts.append(attempt)

        for attempt in deduped_attempts:
            attempt["dtype"] = dtype
            attempt["device"] = device_index
        return deduped_attempts

    def _open_stream_with_fallbacks(self, device_index: Optional[int], dtype: str):
        attempts = self._stream_open_attempts(device_index, dtype)
        last_error = None
        for attempt in attempts:
            try:
                sd.check_input_settings(
                    device=attempt["device"],
                    channels=attempt["channels"],
                    dtype=attempt["dtype"],
                    samplerate=attempt["samplerate"],
                    extra_settings=attempt["extra_settings"],
                )
                stream = sd.RawInputStream(
                    samplerate=attempt["samplerate"],
                    device=attempt["device"],
                    channels=attempt["channels"],
                    blocksize=attempt["blocksize"],
                    dtype=attempt["dtype"],
                    latency=attempt["latency"],
                    extra_settings=attempt["extra_settings"],
                    callback=self._stream_callback,
                )
                LOG.debug(
                    "Opened stream with samplerate=%s channels=%s blocksize=%s "
                    "latency=%s coreaudio=%s",
                    attempt["samplerate"],
                    attempt["channels"],
                    attempt["blocksize"],
                    attempt["latency"],
                    bool(attempt["extra_settings"]),
                )
                return stream, attempt
            except Exception as e:
                last_error = e
                LOG.debug(
                    "Stream open attempt failed (rate=%s, channels=%s, blocksize=%s, "
                    "latency=%s, coreaudio=%s): %s",
                    attempt["samplerate"],
                    attempt["channels"],
                    attempt["blocksize"],
                    attempt["latency"],
                    bool(attempt["extra_settings"]),
                    e,
                )
        if last_error:
            raise last_error
        raise RuntimeError("No valid stream open attempts")

    def _clear_queue(self):
        while True:
            try:
                self._queue.get_nowait()
            except Empty:
                break

    def _enqueue_chunk(self, chunk: bytes):
        try:
            self._queue.put_nowait(chunk)
        except Full:
            # Keep the most recent audio data instead of introducing lag.
            try:
                self._queue.get_nowait()
            except Empty:
                pass
            self._queue.put_nowait(chunk)

    def _apply_gain(self, in_data: bytes) -> bytes:
        if self.multiplier == 1.0:
            return in_data
        if audioop is not None:
            return audioop.mul(in_data, self.sample_width, self.multiplier)

        if self.sample_width not in (1, 2, 4):
            LOG.warning(
                "Gain multiplier requires sample_width in [1, 2, 4] without audioop"
            )
            return in_data

        lower, upper = self._range_for_width(self.sample_width)
        samples = array(self._typecode_for_width(self.sample_width))
        samples.frombytes(in_data)
        for i, value in enumerate(samples):
            scaled = int(round(value * self.multiplier))
            if scaled < lower:
                scaled = lower
            elif scaled > upper:
                scaled = upper
            samples[i] = scaled
        return samples.tobytes()

    def _downmix_to_mono(self, in_data: bytes, input_channels: int) -> bytes:
        if input_channels <= 1:
            return in_data
        if audioop is not None and input_channels == 2:
            return audioop.tomono(in_data, self.sample_width, 0.5, 0.5)
        if self.sample_width not in (1, 2, 4):
            LOG.warning("Software mono downmix unsupported for sample_width=%s", self.sample_width)
            return in_data

        typecode = self._typecode_for_width(self.sample_width)
        lower, upper = self._range_for_width(self.sample_width)
        samples = array(typecode)
        samples.frombytes(in_data)
        frame_count = len(samples) // input_channels
        mono = array(typecode)
        for frame in range(frame_count):
            offset = frame * input_channels
            value = int(sum(samples[offset : offset + input_channels]) / input_channels)
            if value < lower:
                value = lower
            elif value > upper:
                value = upper
            mono.append(value)
        return mono.tobytes()

    def _upmix_channels(self, in_data: bytes, output_channels: int) -> bytes:
        if output_channels <= 1:
            return in_data
        if self.sample_width not in (1, 2, 4):
            LOG.warning("Software channel upmix unsupported for sample_width=%s", self.sample_width)
            return in_data

        typecode = self._typecode_for_width(self.sample_width)
        samples = array(typecode)
        samples.frombytes(in_data)
        upmixed = array(typecode)
        for value in samples:
            for _ in range(output_channels):
                upmixed.append(value)
        return upmixed.tobytes()

    def _convert_channels(self, in_data: bytes) -> bytes:
        if self._capture_channels == self.sample_channels:
            return in_data
        if self.sample_channels == 1 and self._capture_channels > 1:
            return self._downmix_to_mono(in_data, self._capture_channels)
        if self._capture_channels == 1 and self.sample_channels > 1:
            return self._upmix_channels(in_data, self.sample_channels)

        LOG.warning(
            "Unsupported channel conversion capture=%s target=%s. Keeping raw data.",
            self._capture_channels,
            self.sample_channels,
        )
        return in_data

    def _resample_audio(self, in_data: bytes) -> bytes:
        if self._capture_sample_rate == self.sample_rate:
            return in_data
        if audioop is not None:
            converted, self._ratecv_state = audioop.ratecv(
                in_data,
                self.sample_width,
                self.sample_channels,
                self._capture_sample_rate,
                self.sample_rate,
                self._ratecv_state,
            )
            return converted
        if self.sample_width == 2 and self.sample_channels == 1:
            return self._resample_int16_linear(
                in_data, self._capture_sample_rate, self.sample_rate
            )
        LOG.warning(
            "No resampler backend available (sample_width=%s, channels=%s).",
            self.sample_width,
            self.sample_channels,
        )
        return in_data

    def _resample_int16_linear(
        self, in_data: bytes, from_rate: int, to_rate: int
    ) -> bytes:
        if from_rate == to_rate:
            return in_data
        samples = array("h")
        samples.frombytes(in_data)
        if not samples:
            return in_data

        if self._resample_tail is not None:
            samples.insert(0, self._resample_tail)
        self._resample_tail = int(samples[-1])

        if len(samples) == 1:
            return samples.tobytes()

        ratio = to_rate / from_rate
        out_length = max(1, int((len(samples) - 1) * ratio))
        out = array("h")
        step = (len(samples) - 1) / out_length
        for i in range(out_length):
            src_pos = i * step
            src_index = int(src_pos)
            frac = src_pos - src_index
            next_index = min(src_index + 1, len(samples) - 1)
            value = int(
                round(
                    samples[src_index] * (1.0 - frac) + samples[next_index] * frac
                )
            )
            out.append(value)
        return out.tobytes()

    def start(self):
        if self.stream is not None:
            raise RuntimeError("Already started")

        frame_bytes = self.sample_width * self.sample_channels
        if frame_bytes <= 0 or self.chunk_size <= 0:
            raise ValueError("sample_width, sample_channels, and chunk_size must be > 0")
        if self.chunk_size % frame_bytes != 0:
            raise ValueError("chunk_size must be divisible by frame size")

        dtype = self._dtype_for_width(self.sample_width)
        LOG.debug(
            "Opening microphone (device=%s, rate=%s, width=%s, channels=%s)",
            self.device,
            self.sample_rate,
            self.sample_width,
            self.sample_channels,
        )

        index = self.find_input_device(self.device)
        if index is None and str(self.device).lower() not in ("", "default", "none"):
            LOG.warning("Input device '%s' not found. Using default input.", self.device)

        self._clear_queue()
        self._chunk_buffer.clear()
        self._ratecv_state = None
        self._resample_tail = None
        self.stream, opened = self._open_stream_with_fallbacks(index, dtype)
        self._capture_sample_rate = int(opened["samplerate"])
        self._capture_channels = int(opened["channels"])
        if self._capture_sample_rate != int(self.sample_rate):
            LOG.warning(
                "Input stream opened at %sHz and will be resampled to %sHz",
                self._capture_sample_rate,
                self.sample_rate,
            )
        if self._capture_channels != int(self.sample_channels):
            LOG.warning(
                "Input stream opened with %s channels and will be converted to %s",
                self._capture_channels,
                self.sample_channels,
            )
        self.stream.start()

    def read_chunk(self) -> Optional[bytes]:
        if self.stream is None:
            raise RuntimeError("Not running")
        try:
            return self._queue.get(timeout=self.timeout)
        except Empty:
            return None

    def stop(self):
        if self.stream is not None:
            stream = self.stream
            try:
                stream.stop()
            finally:
                try:
                    stream.close()
                finally:
                    self.stream = None
                    self._chunk_buffer.clear()
                    self._clear_queue()
                    self._ratecv_state = None
                    self._resample_tail = None

    def _stream_callback(self, in_data, frames, time_info, status):
        if status:
            LOG.warning("Input stream status: %s", status)

        chunk = bytes(in_data)
        if self._capture_channels != self.sample_channels:
            chunk = self._convert_channels(chunk)
        if self._capture_sample_rate != self.sample_rate:
            chunk = self._resample_audio(chunk)
        chunk = self._apply_gain(chunk)
        self._chunk_buffer.extend(chunk)

        while len(self._chunk_buffer) >= self.chunk_size:
            audio_chunk = bytes(self._chunk_buffer[: self.chunk_size])
            del self._chunk_buffer[: self.chunk_size]
            self._enqueue_chunk(audio_chunk)
