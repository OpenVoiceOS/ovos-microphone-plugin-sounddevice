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
from typing import Optional

import sounddevice as sd
from ovos_config import Configuration
from ovos_plugin_manager.templates.microphone import Microphone
from ovos_utils.log import LOG


@dataclass
class SoundDeviceMicrophone(Microphone):
    device: str = Configuration().get("listener", {}).get("device") or "default"
    timeout: float = 5.0
    multiplier: float = 1.0
    _queue: Queue[Optional[bytes]] = field(default_factory=Queue)
    stream: sd.RawInputStream = None

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
            if dev["max_input_channels"] > 0 and pattern.match(dev["name"]):
                LOG.debug("    ^-- matched")
                return device_index
        return None

    def start(self):
        assert self.stream is None, "Already started"
        LOG.debug(
            "Opening microphone (device=%s, rate=%s, width=%s, channels=%s)",
            self.device,
            self.sample_rate,
            self.sample_width,
            self.sample_channels,
        )

        index = self.find_input_device(self.device)
        self.stream = sd.RawInputStream(
            samplerate=self.sample_rate,
            device=index,
            channels=1,
            blocksize=2048,
            dtype="int16",
            callback=self._stream_callback,
        )
        self.stream.start()

    def read_chunk(self) -> Optional[bytes]:
        assert self.stream is not None, "Not running"
        return self._queue.get(timeout=self.timeout)

    def stop(self):
        if self.stream:
            self.stream.stop()
            self.stream = None

    def _stream_callback(self, in_data, frames, time, status):
        if self.multiplier != 1.0:
            in_data = audioop.mul(in_data, self.sample_width, self.multiplier)
        self._queue.put(in_data)
