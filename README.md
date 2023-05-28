## Description

Open Voice OS microphone plugin for [python-sounddevice](https://github.com/spatialaudio/python-sounddevice/) library.

`python-sounddevice` uses [PortAudio](http://www.portaudio.com/) as audio library to interact with audio components.

> PortAudio is a free, cross-platform, open-source, audio I/O library.  It lets you write simple audio programs in 'C' or C++ that will compile and run on many platforms including Windows, Macintosh OS X, and Unix (OSS/ALSA). It is intended to promote the exchange of audio software between developers on different platforms. Many applications use PortAudio for Audio I/O.

This plugin should be used when Open Voice OS is running on a non-Linux environment such as Mac OS but will work perfectly when running on Linux environment as well.

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
      "module": "ovos-microphone-plugin-sounddevice"
    }
  }
}
```

If you want to use a specific microphone, the `device` option must be specified.

```json
{
  "listener": {
    "microphone": {
      "module": "ovos-microphone-plugin-sounddevice"
    },
  "device": "Built-in Microphone"
  }
}
```