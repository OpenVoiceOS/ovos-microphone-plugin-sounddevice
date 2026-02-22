import struct
import unittest
from unittest.mock import Mock, patch

from ovos_microphone_plugin_sounddevice import SoundDeviceMicrophone


def _fake_devices():
    return [
        {
            "name": "Built-in Microphone",
            "index": 0,
            "hostapi": 0,
            "max_input_channels": 2,
            "max_output_channels": 0,
            "default_samplerate": 44100.0,
        },
        {
            "name": "Built-in Output",
            "index": 1,
            "hostapi": 0,
            "max_input_channels": 0,
            "max_output_channels": 2,
            "default_samplerate": 44100.0,
        },
    ]


class TestSoundDeviceMicrophone(unittest.TestCase):
    @patch("ovos_microphone_plugin_sounddevice.sd.query_devices")
    def test_find_input_device_supports_exact_substring_regex_and_index(
        self, mock_query_devices
    ):
        mock_query_devices.return_value = _fake_devices()

        self.assertEqual(
            SoundDeviceMicrophone.find_input_device("Built-in Microphone"), 0
        )
        self.assertEqual(SoundDeviceMicrophone.find_input_device("built"), 0)
        self.assertEqual(SoundDeviceMicrophone.find_input_device("regex:^built"), 0)
        self.assertEqual(SoundDeviceMicrophone.find_input_device("0"), 0)
        self.assertEqual(SoundDeviceMicrophone.find_input_device(0), 0)
        self.assertIsNone(SoundDeviceMicrophone.find_input_device("No Such Device"))

    @patch("ovos_microphone_plugin_sounddevice.sd.RawInputStream")
    @patch("ovos_microphone_plugin_sounddevice.sd.check_input_settings")
    @patch("ovos_microphone_plugin_sounddevice.sd.CoreAudioSettings")
    @patch("ovos_microphone_plugin_sounddevice.sd.query_hostapis")
    @patch("ovos_microphone_plugin_sounddevice.sd.query_devices")
    @patch("ovos_microphone_plugin_sounddevice.sys.platform", "darwin")
    def test_start_uses_coreaudio_settings_and_expected_stream_kwargs(
        self,
        mock_query_devices,
        mock_query_hostapis,
        mock_coreaudio_settings,
        mock_check_input_settings,
        mock_raw_stream,
    ):
        devices = _fake_devices()

        def _query_devices(device=None, kind=None):
            if device is None and kind is None:
                return devices
            if device == 0:
                return devices[0]
            raise ValueError(f"Unexpected device query: {device}, {kind}")

        mock_query_devices.side_effect = _query_devices
        mock_query_hostapis.return_value = {"name": "Core Audio"}
        mock_coreaudio_settings.return_value = object()
        fake_stream = Mock()
        mock_raw_stream.return_value = fake_stream

        mic = SoundDeviceMicrophone(device="Built-in Microphone")
        mic.start()

        self.assertIsNotNone(mic.stream)
        fake_stream.start.assert_called_once()
        mock_check_input_settings.assert_called_once()
        kwargs = mock_raw_stream.call_args.kwargs
        self.assertEqual(kwargs["samplerate"], mic.sample_rate)
        self.assertEqual(kwargs["channels"], mic.sample_channels)
        self.assertEqual(kwargs["blocksize"], mic.blocksize)
        self.assertEqual(kwargs["dtype"], "int16")
        self.assertEqual(kwargs["latency"], "low")
        self.assertIsNotNone(kwargs["extra_settings"])

        mic.stop()
        fake_stream.stop.assert_called_once()
        fake_stream.close.assert_called_once()
        self.assertIsNone(mic.stream)

    @patch("ovos_microphone_plugin_sounddevice.sd.RawInputStream")
    @patch("ovos_microphone_plugin_sounddevice.sd.check_input_settings")
    @patch("ovos_microphone_plugin_sounddevice.sd.CoreAudioSettings")
    @patch("ovos_microphone_plugin_sounddevice.sd.query_hostapis")
    @patch("ovos_microphone_plugin_sounddevice.sd.query_devices")
    @patch("ovos_microphone_plugin_sounddevice.sys.platform", "darwin")
    def test_start_falls_back_to_device_default_samplerate(
        self,
        mock_query_devices,
        mock_query_hostapis,
        mock_coreaudio_settings,
        mock_check_input_settings,
        mock_raw_stream,
    ):
        devices = _fake_devices()

        def _query_devices(device=None, kind=None):
            if device is None and kind is None:
                return devices
            if device == 0:
                return devices[0]
            raise ValueError(f"Unexpected device query: {device}, {kind}")

        def _check_settings(**kwargs):
            if kwargs["samplerate"] == 16000:
                raise ValueError("Unsupported sample rate")

        mock_query_devices.side_effect = _query_devices
        mock_query_hostapis.return_value = {"name": "Core Audio"}
        mock_coreaudio_settings.return_value = object()
        mock_check_input_settings.side_effect = _check_settings
        fake_stream = Mock()
        mock_raw_stream.return_value = fake_stream

        mic = SoundDeviceMicrophone(device="Built-in Microphone")
        mic.start()

        self.assertEqual(mic._capture_sample_rate, 44100)
        kwargs = mock_raw_stream.call_args.kwargs
        self.assertEqual(kwargs["samplerate"], 44100)
        mic.stop()

    def test_stream_callback_applies_gain(self):
        mic = SoundDeviceMicrophone(
            multiplier=2.0, chunk_size=4, sample_width=2, sample_channels=1
        )
        mic.stream = object()
        input_data = struct.pack("<hh", 1000, -1000)

        mic._stream_callback(input_data, frames=2, time_info=None, status=None)
        output = mic.read_chunk()

        self.assertEqual(output, struct.pack("<hh", 2000, -2000))

    @patch("ovos_microphone_plugin_sounddevice.audioop", None)
    def test_stream_callback_applies_gain_without_audioop(self):
        mic = SoundDeviceMicrophone(
            multiplier=2.0, chunk_size=4, sample_width=2, sample_channels=1
        )
        mic.stream = object()
        input_data = struct.pack("<hh", 1000, -1000)

        mic._stream_callback(input_data, frames=2, time_info=None, status=None)
        output = mic.read_chunk()

        self.assertEqual(output, struct.pack("<hh", 2000, -2000))

    def test_stream_callback_drops_oldest_chunk_when_queue_is_full(self):
        mic = SoundDeviceMicrophone(
            queue_maxsize=1, chunk_size=4, sample_width=2, sample_channels=1
        )
        mic.stream = object()
        first = struct.pack("<hh", 100, 200)
        second = struct.pack("<hh", 300, 400)

        mic._stream_callback(first, frames=2, time_info=None, status=None)
        mic._stream_callback(second, frames=2, time_info=None, status=None)

        self.assertEqual(mic._queue.qsize(), 1)
        self.assertEqual(mic.read_chunk(), second)

    def test_read_chunk_timeout_returns_none(self):
        mic = SoundDeviceMicrophone(timeout=0.01)
        mic.stream = object()
        self.assertIsNone(mic.read_chunk())

    def test_stream_callback_downmixes_stereo_to_mono(self):
        mic = SoundDeviceMicrophone(chunk_size=4, sample_width=2, sample_channels=1)
        mic.stream = object()
        mic._capture_channels = 2
        mic._capture_sample_rate = mic.sample_rate
        input_data = struct.pack("<hhhh", 1000, -1000, 2000, -2000)

        mic._stream_callback(input_data, frames=2, time_info=None, status=None)
        output = mic.read_chunk()

        self.assertEqual(output, struct.pack("<hh", 0, 0))


if __name__ == "__main__":
    unittest.main()
