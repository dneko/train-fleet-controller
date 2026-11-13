# Contributing

Bug reports, documentation improvements, and pull requests are welcome. Include
your operating system, Python version, launch mode, and steps to reproduce an
issue. For hardware issues, include the hub type and Pybricks version or ESP32
board and reader model. Remove reader tokens, Wi-Fi passwords, and private
layout data from logs before posting them.

Follow the setup in [README.md](README.md), then run:

```sh
python -m unittest discover -s tests -v
```

The normal suite uses fake hardware and skips desktop GUI tests. With an active
desktop and Tkinter, opt into those tests using `TRAINSTUFF_GUI_TESTS=1`.
The hub program can be compiled without connecting to hardware:

```sh
python -c "from hub_program import compile_program, read_program; print(len(compile_program(read_program())), 'bytes')"
```

Keep motor writes serialized and preserve startup zero-throttle verification,
STOP priority, disconnect handling, and the hub watchdog. Explain hardware
validation in pull requests that change motor control. Controller extension
documentation is in [train_controllers/README.md](train_controllers/README.md).

Contributions are provided under the project's MIT license. Preserve existing
copyright headers and add the source and license of any third-party code to
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Building a source release

```sh
python scripts/build_release.py
```

This creates `dist/train-fleet-controller-source.zip` and a SHA-256 checksum.
Only files explicitly listed in `release-files.txt` are included. Add new source
files to that list when needed. Local configurations, photos, credentials,
virtual environments, installed dependencies, and build outputs are excluded.
The ZIP contains source code and requires the Python setup in the README.

The original exploratory scripts (`first.py`, `simple.py`, `text.py`,
`traincontroller.py`, `v2.py`, `v3.py`, `v3b.py`, `v3c.py`, and `otrpy`) are retained
for reference. The supported entry points are `fleet_controller.py` and
`fleet_launcher.py`.
