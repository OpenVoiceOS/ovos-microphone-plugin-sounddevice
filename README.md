## Description

Open Voice OS microphone plugin for [python-sounddevice](https://github.com/spatialaudio/python-sounddevice/) library.

`python-sounddevice` uses [PortAudio](http://www.portaudio.com/) as audio library to interact with audio components.

> PortAudio is a free, cross-platform, open-source, audio I/O library.  It lets you write simple audio programs in 'C' or C++ that will compile and run on many platforms including Windows, Macintosh OS X, and Unix (OSS/ALSA). It is intended to promote the exchange of audio software between developers on different platforms. Many applications use PortAudio for Audio I/O.

This plugin is ideal for macOS (Intel and Apple Silicon) and also works on Linux.
On macOS it uses CoreAudio stream settings tuned for clear wake-word and STT capture.

## Install

```bash
pip install ovos-microphone-plugin-sounddevice
```

## Configuration

In order to inform the listener which plugin to use *(default is set to `ovos-microphone-plugin-alsa`)*, the `mycroft.conf` configuration file should be updated.

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

### macOS Recommended (Clear Capture)

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

### Device Selection

- Exact name: `"device": "Built-in Microphone"`
- Substring match: `"device": "Built-in"`
- Regex match: `"device": "regex:^MacBook.*Microphone"`
- Numeric index: `"device": 0`
- Default input device: omit `device` or set `"device": "default"`

### Notes

- Keep `multiplier` near `1.0` for the cleanest signal.
- Increase `multiplier` only if the microphone is too quiet.
- If clipping/distortion appears, reduce `multiplier` below `1.0`.
- If `16000 Hz` input is not supported by hardware, the plugin can open the device at native sample rate and resample to `16000 Hz` for OVOS.
- `blocksize` controls callback cadence; lower values usually improve wakeword responsiveness.
