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
import audioop
import re
from dataclasses import dataclass, field
from queue import Queue
from threading import Thread
from typing import Optional

import sounddevice as sd
from ovos_plugin_manager.templates.microphone import Microphone
from ovos_utils.log import LOG
from speech_recognition import Microphone as _Mic


@dataclass
class SounDeviceMicrophone(Microphone):
    device: str = "default"
    timeout: float = 5.0
    multiplier: float = 1.0
    full_chunk = bytes()
    _thread: Optional[Thread] = None
    _queue: "Queue[Optional[bytes]]" = field(default_factory=Queue)
    _is_running: bool = False

    @staticmethod
    def find_input_device(device_name):
        """Find audio input device by name.

        Args:
            device_name: device name or regex pattern to match

        Returns: device_index (int) or None if device wasn't found
        """
        LOG.info("Searching for input device: {}".format(device_name))
        LOG.debug("Devices: ")
        pattern = re.compile(device_name)
        for device_index in range(len(sd.query_devices())):
            dev = sd.query_devices(device_index)
            LOG.debug("   {}".format(dev["name"]))
            if dev["maxInputChannels"] > 0 and pattern.match(dev["name"]):
                LOG.debug("    ^-- matched")
                return device_index
        return None

    def start(self):
        assert self._thread is None, "Already started"
        self._is_running = True
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    def read_chunk(self) -> Optional[bytes]:
        assert self._is_running, "Not running"
        return self._queue.get(timeout=self.timeout)

    def stop(self):
        assert self._thread is not None, "Not started"
        self._is_running = False
        while not self._queue.empty():
            self._queue.get()
        self._queue.put_nowait(None)
        self._thread.join()
        self._thread = None

    def _stream_callback(self, in_data, frames, time, status):
        if self.multiplier != 1.0:
            in_data = audioop.mul(in_data, self.sample_width, self.multiplier)

    def _run(self):
        try:
            assert self.sample_width in {
                2,
                4,
            }, "Only 16-bit and 32-bit sample widths are supported"

            stream = None
            try:
                LOG.debug(
                    "Opening microphone (rate=%s, width=%s, channels=%s)",
                    self.sample_rate,
                    self.sample_width,
                    self.sample_channels,
                )

                if self.device != "default":
                    index = self.find_input_device(self.device)
                    source = _Mic(
                        device_index=index,
                        sample_rate=self.sample_rate,
                        chunk_size=self.chunk_size,
                    )
                else:
                    source = _Mic(
                        sample_rate=self.sample_rate, chunk_size=self.chunk_size
                    )

                stream = sd.InputStream(
                    samplerate=source.SAMPLE_RATE,
                    device=source.device_index,
                    channels=1,
                    callback=self._stream_callback,
                )
                stream.start()

            except Exception:
                LOG.exception("Failed to open microphone")
            finally:
                if stream:
                    stream.stop()
                    stream.close()

        except Exception:
            LOG.exception("Unexpected error in pyaudio microphone thread")
