# Third-party notices

The original work in Train Fleet Controller is released under the [MIT license](LICENSE).
Upstream copyright notices apply to the portions described below and must be
preserved with redistributed copies. Dependencies retain their own licenses.

## Pybricks communication example

`text.py` contains the Pybricks computer-to-hub communication example, with its
hub name changed. `simple.py` adapts that example for speed commands. The early
GUI versions `v2.py`, `v3.py`, `v3b.py`, and `v3c.py` also retain adapted portions
of its Bluetooth connection, notification, and ready-event code.

Source: [Pybricks Hub to PC Communication](https://pybricks.com/projects/tutorials/wireless/hub-to-device/pc-communication/).

The current controller in `train_controllers/pybricks.py` and the hub program in
`firmware/city_hub.py` build on the same stdin/stdout communication approach.
The following credit is retained for any portions carried forward from those examples:

```text
Copyright (c) 2020 Henrik Blidh
Copyright (c) 2022-2023 The Pybricks Authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

## Protocol and compiler references

`hub_program.py` implements the published
[Pybricks Bluetooth profile](https://github.com/pybricks/technical-info/blob/master/pybricks-ble-profile.md).
Its MPY container layout follows the format documented in
[`pybricksdev/compile.py`](https://github.com/pybricks/pybricksdev/blob/master/pybricksdev/compile.py),
which is MIT licensed, copyright (c) 2019-2025 The Pybricks Authors. The MIT
permission and warranty terms reproduced above also apply to any adapted
portions of that reference. The `pybricksdev` package is not bundled.

`firmware/city_hub.py` is an application program for a City Hub. It is not the
Pybricks firmware distribution. Users install Pybricks firmware separately with
[Pybricks Code](https://code.pybricks.com/). That firmware includes its own
third-party components and notices; this project's MIT license does not replace
them. See the [Pybricks firmware license](https://github.com/pybricks/pybricks-micropython/blob/master/LICENSE).

## Separately installed dependencies

The source release does not include the Python interpreter, a virtual environment,
installed Python packages, Pybricks firmware images, or Arduino libraries.
`requirements.txt` installs desktop dependencies from their own distributions:

| Dependency | Upstream license / information |
| --- | --- |
| Bleak | [MIT](https://github.com/hbldh/bleak/blob/develop/LICENSE) |
| mpy-cross-v6 | [MicroPython cross-compiler packaging](https://github.com/pybricks/python-mpy-cross); consult the compiler distribution and MicroPython notices when redistributing binaries |
| Pillow | [MIT-CMU and bundled component notices](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| python-zeroconf | [LGPL-2.1-or-later](https://github.com/python-zeroconf/python-zeroconf/blob/master/COPYING) |
| ifaddr | [MIT](https://github.com/p-stely/ifaddr/blob/master/LICENSE.txt) |
| keyring (macOS) | [MIT](https://github.com/jaraco/keyring/blob/main/LICENSE) |

The RFID sketch also needs separately installed
[Arduino-ESP32](https://github.com/espressif/arduino-esp32) and
[Seeed Studio PN532](https://github.com/Seeed-Studio/PN532) libraries.

These libraries are not relicensed by this repository. In particular, anyone
distributing a standalone executable containing python-zeroconf must meet its
LGPL obligations, including applicable notices, library source availability,
and the user's ability to replace or modify the library. Recheck all bundled
components when creating a binary installer or distributing compiled ESP32 firmware.

## Audio

`assets/train_alarm.wav` and `assets/train_chime.wav` are synthesized by
`alarm_audio.create_alarm_file()` and are covered by this project's MIT license.
Personal layout images and train photos are not included in the source release.
