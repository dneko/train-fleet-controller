# Adding a train controller

The fleet owns throttle limits, polarity, RFID tracking, automatic operation and
operator controls. A `TrainController` owns one motor output and its transport.
The built-in driver is **Pybricks City Hub**. Other Bluetooth protocols and
computer-controlled 9-volt outputs can implement the same interface; they are
not implemented by this refactor.

## Files

| File | Responsibility |
| --- | --- |
| `base.py` | Driver contract, retry loop and normalized sent-throttle events |
| `registry.py` | Driver factories, settings, endpoint identity and capabilities |
| `pybricks.py` | Pybricks BLE scanning, command writer, handshake and telemetry |
| `pybricks_updates.py` | City Hub batch discovery and program replacement |
| `__init__.py` | Explicit registration of installed drivers |
| `../fleet_controller.py` | Thread/event loop, per-train lifecycle and command dispatch |

The `TrainConnection` import from `fleet_controller` remains an alias for
`PybricksController`. New code should use `create_controller()` or the base interface.

## Configuration and setup

Existing records with `hub_name` and no `controller_type` still mean Pybricks.
They do not need migration. Explicit Pybricks records use:

```json
{"id": "a", "name": "Train A", "controller_type": "pybricks", "hub_name": "Train A", "tags": []}
```

Other drivers use a registered `controller_type` and their own `controller_options`:

```json
{
  "id": "track-a",
  "name": "Track A train",
  "controller_type": "my-track-controller",
  "controller_options": {"port": "/dev/example", "channel": "1"},
  "forward_sign": 1,
  "tags": []
}
```

That example needs an actual driver registered before it can load. A JSON file
cannot import code. Unknown types, invalid settings and duplicate endpoints are
rejected for both active and saved trains. `control_mode: "external"` continues
to mean RFID tracking only; it never creates a driver.

**Train library → Motor controller** lists registered drivers. **Settings…**
edits the fields declared by that driver. Edits are staged until the library's
Save/apply action. Changing driver, endpoint/options or polarity stops that train;
connection changes restart only its active session. Other trains keep their
connections. Display-name/tag edits do not reconnect hardware.

## Implement and register a driver

1. Add a module under `train_controllers` implementing `TrainController`.
2. Import transport dependencies inside `run()` so loading a configuration or
   using Demo does not require opening hardware or importing optional libraries.
3. Register a `ControllerSpec` in `__init__.py`, before the app loads configuration.
   For example, after implementing `MyTrackController`:

```python
register_controller(ControllerSpec(
    type_id='my-track-controller',
    label='My 9-volt controller',
    factory=MyTrackController,
    fields=(
        ControllerField('port', 'Connection port'),
        ControllerField('channel', 'Output channel', default='1'),
    ),
    validate_options=validate_track_options,
    endpoint_key=lambda options: (options['port'], options['channel']),
    capabilities=ControllerCapabilities(),
))
```

Fields are text inputs. `validate_options(options)` must raise `ValueError` for
invalid values, including numeric settings outside hardware limits. Convert text
to protocol values in the driver. `controller_spec(self.train).options(self.train)`
returns normalized defaults; read current settings each time `run()` begins.
`endpoint_key` must return a hashable identifier for the physical output. Exclude
settings such as retry timing from this key so they cannot bypass duplicate-output
checks. Without a custom key, all options together identify the endpoint. All
options participate in connection identity and trigger a restart if changed.

Constructors must be quick and perform no I/O. The backend invokes the factory
on its asyncio thread with a train record and a thread-safe `emit(event)` callback.
Drivers must not access Tk widgets. The fleet sends already limited, signed
hardware throttle from **−100 to +100**, with **0 = stop**. It has already applied
`forward_sign`; do not apply polarity again in the driver.

Implement these methods:

- `set_speed(speed)`: enqueue without blocking. One writer per output, newest
  motion replaces unsent motion, and zero takes priority over pending motion.
- `async run()`: one connection session. Open transport, discard stale queued
  motion, establish zero, then emit readiness. Wait for commands/telemetry. Close
  the transport and discard pending motion in `finally`, including cancellation.
- `set_waiting(waiting)`: optional stopped/waiting indicator; the base is a no-op.
- `busy`: optional property for an exclusive hardware operation; defaults false.

The inherited `run_reconnecting()` retries failures with a bounded delay, reports
loss of readiness and clears telemetry. It never resumes an old throttle. Set
`session_ready = True` once the session successfully establishes zero, and keep
`accepting` and `last_speed` accurate. A watchdog/protocol error within a live
session must immediately stop accepting motion, discard queued motion, and emit
`('ready', train_id, False)`. Re-establish zero before advertising readiness again.
Perform bounded transport operations and propagate cancellation. Implement the
hardware's stop/watchdog behavior; closing a serial/Bluetooth connection alone
does not necessarily remove motor power.

For a 9-volt supply, one driver instance represents one independently controlled
output. Several trains sharing an electrically continuous track are not
independent outputs: channel registration alone does not add block-power routing
or independent control of those trains. Shared multi-channel transports must
serialize writes and avoid closing another active output's session.

## Events and optional features

| Event or helper | Meaning |
| --- | --- |
| `status(text)` | Display connection/protocol status for this train |
| `emit(('ready', id, True/False))` | Safe to accept motor commands / lost readiness |
| `record_sent_throttle(speed)` | Record an actual successful hardware write; converts back to forward-relative demand |
| `record_sent_throttle(None)` | Actual output is unknown, ending the continuous history segment |
| `emit(('battery', id, reading))` | Normalized voltage/current telemetry, or `None` when unavailable |

`power_telemetry=True` enables recording and the current-load profile/graph UI. Emit
`BatteryReading(voltage_mv, current_ma, time.monotonic())`; watts are computed in
the app. Load alarms learn and compare amps only; voltage and watts remain available
for display and energy tracking. Its default low-voltage limits are for City Hubs. Supply your own
`low_voltage_mv`/`critical_voltage_mv`, or set both to `None` for variable track
voltage so normal low throttle does not become a low-battery warning. Only
measured voltage/current should enter this stream; do not invent telemetry from
throttle. `record_sent_throttle()` is a no-op while optional recording is off.

`waiting_indicator=True` enables calls to the optional waiting indicator.
`city_hub_program=True` is reserved for the Pybricks City Hub program/update
contract, not a generic firmware-upload flag. Keep it false for other hardware.
Update hubs scans, stops and uploads only compatible active City Hubs.

The fleet retains a latched load alarm even if a controller is changed; it still
requires explicit clearing/reset. Samples and learning use train/controller/
options/polarity identity, so they are not reused for different hardware.

## Verification

`tests/controller_fakes.py` demonstrates a non-Bluetooth driver registered only
inside tests. `test_controllers.py` exercises mixed fleets, dispatch, independent
reconnection, type changes, shutdown and upload isolation. `test_controllers_ui.py`
checks selection, settings, telemetry capability handling and retained alarms.
These do not validate a real 9-volt device; test a new driver's protocol, stop,
lost-link behavior, polarity and physical output before using it on a layout.
