## Description

This plugin lets OpenVoiceOS (OVOS) capture microphone audio through [python-sounddevice](https://github.com/spatialaudio/python-sounddevice/).

`python-sounddevice` uses [PortAudio](http://www.portaudio.com/) to talk to audio hardware. PortAudio is a free, cross-platform, open-source audio I/O library. It works in C or C++ on Windows, macOS, and Unix (OSS/ALSA).

The plugin works best on macOS (Intel and Apple Silicon) and also runs on Linux. On macOS it uses CoreAudio stream settings tuned for clear wake-word and speech-to-text (STT) capture.

## Install

```bash
pip install ovos-microphone-plugin-sounddevice
```

## Configuration

The listener defaults to the `ovos-microphone-plugin-alsa` plugin. To use this plugin instead, update the `mycroft.conf` configuration file:

```json
{
  "listener": {
    "microphone": {
      "module": "ovos-microphone-plugin-sounddevice",
      "ovos-microphone-plugin-sounddevice": {}
    }
  }
}
```

### macOS recommended settings (clear capture)

```json
{
  "listener": {
    "microphone": {
      "module": "ovos-microphone-plugin-sounddevice",
      "ovos-microphone-plugin-sounddevice": {
        "device": "Built-in Microphone",
        "latency": "low",
        "multiplier": 1.0,
        "blocksize": 1024,
        "queue_maxsize": 8,
        "use_coreaudio_settings": true,
        "coreaudio_conversion_quality": "max",
        "coreaudio_change_device_parameters": false,
        "coreaudio_fail_if_conversion_required": false,
        "auto_sample_rate_fallback": true,
        "auto_channel_fallback": true,
        "auto_latency_fallback": true
      }
    }
  }
}
```

### Device selection

Set `device` to one of these forms:

- Exact name: `"device": "Built-in Microphone"`
- Substring match: `"device": "Built-in"`
- Regex match: `"device": "regex:^MacBook.*Microphone"`
- Numeric index: `"device": 0`
- Default input device: omit `device`, or set `"device": "default"`

### Notes

- Keep `multiplier` near `1.0` for the cleanest signal.
- Increase `multiplier` only if the microphone is too quiet.
- Reduce `multiplier` below `1.0` if clipping or distortion appears.
- If the hardware does not support `16000 Hz` input, the plugin can open the device at its native sample rate and resample to `16000 Hz` for OVOS.
- `blocksize` controls callback cadence. Lower values usually improve wake-word responsiveness.

## Related projects

- [OpenVoiceOS/ovos-plugin-manager](https://github.com/OpenVoiceOS/ovos-plugin-manager) — loads and manages this plugin.
- [OpenVoiceOS/ovos-dinkum-listener](https://github.com/OpenVoiceOS/ovos-dinkum-listener) — the OVOS listener that consumes microphone plugins.
- [spatialaudio/python-sounddevice](https://github.com/spatialaudio/python-sounddevice/) — the audio library this plugin wraps.

## License

Apache-2.0, see [LICENSE](LICENSE).
