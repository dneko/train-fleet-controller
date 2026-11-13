# Train Fleet Controller

An open-source desktop controller for LEGO trains using Pybricks City Hubs,
with optional ESP32 Wi-Fi RFID checkpoints, a track map, speed estimates,
and automatic block control. Try the demo without any hardware.

Released under the [MIT license](LICENSE): free to use, modify, redistribute,
and use commercially. Preserve the license and upstream notices. See
[third-party credits and dependency licenses](THIRD_PARTY_NOTICES.md) and
[contributing and source releases](CONTRIBUTING.md).

Developed on macOS. The Finder launcher, saved Keychain token, and WAV alarm
playback are macOS features. Other desktops can use the Python entry points;
hardware operation on Windows and Linux has not been verified here, and alarms
fall back to the system bell. This is a prototype for supervised model railways.
This project is independent of the LEGO Group and Pybricks.

Designed around **up to four City Hub trains and 2–6 Wi-Fi checkpoints**, with
**one PN532 reader per ESP32**. Original exploratory scripts are preserved;
use the entry points below for the current application.

- `fleet_controller.py`: one Tkinter window with independent train controls,
  STOP ALL, last-seen checkpoint/age, estimated segment speed and direction.
- `examples/fleet.example.json`: a portable two-train, four-checkpoint demo layout.
  Copy it to the ignored `fleet.json` for your own trains, tags and track distances.
- `firmware/rfid_reader/`: ESP32 Dev Module + PN532 V3 I²C Wi-Fi reader sketch.
- `firmware/city_hub.py`: compatible City Hub motor controller with local watchdog.
- `train_controllers/`: interchangeable motor drivers; Pybricks City Hub is the
  built-in implementation. See [adding a controller](train_controllers/README.md)
  for another Bluetooth protocol or a computer-controlled 9-volt output.

## Run

Use Python 3.10+ with Tkinter and Bluetooth support for live trains. Check Tkinter
with `python3 -m tkinter`; a small window should appear. If the module is missing,
install Tk support for your Python distribution before continuing.

Clone this repository, or download and extract its source ZIP. On macOS/Linux:

```sh
git clone https://github.com/dneko/train-fleet-controller.git
cd train-fleet-controller
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp -n examples/fleet.example.json fleet.json
chmod +x "Launch Train Fleet.command"
python fleet_controller.py --demo
```

If using a ZIP, open a terminal in its extracted folder and start at the
`python3 -m venv .venv` step. `cp -n` preserves an existing configuration.

For Windows PowerShell, open the downloaded folder and use:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path fleet.json)) { Copy-Item examples/fleet.example.json fleet.json }
.\.venv\Scripts\python.exe fleet_controller.py --demo
```

The example uses fictional RFID UIDs and no reader hardware assignments. Before
live operation, set your hub names, register your actual tags, assign readers,
and measure the layout distances in the setup screens. Your saved configurations,
map images, train photos, and secrets are excluded from Git and the source release.
For reader firmware, edit a local copy of the sketch with your Wi-Fi credentials
and shared token; keep the published sketch's placeholder values.

On subsequent runs, activate `.venv` and run `python fleet_controller.py --demo`.
Click **Start demo** to simulate configured trains. A green **DEMO RUNNING** badge
and **Pause demo** button indicate active simulation; an amber **DEMO PAUSED**
badge indicates it is stopped. Pause keeps Auto selections and freezes movement;
Start demo resumes. STOP ALL pauses the demo and disables automatic control. After two
successive checkpoints, train markers animate using their measured speed. Demo
mode makes no Bluetooth or Wi-Fi connections and does not persist simulated tags.

For hardware:

```sh
export TRAIN_READER_TOKEN='your-own-long-random-secret-at-least-16-characters'
python fleet_controller.py
```

Set the same secret in each reader sketch's `READER_TOKEN` setting. The launcher can
remember it in Mac Keychain. Without a token in the environment or Keychain, the
controller still opens for setup and train control, but the Wi-Fi receiver is disabled.
Use a trusted LAN: HTTP is not encrypted. Allow inbound TCP 8765 and local mDNS
(UDP 5353). The app advertises its receiver using Bonjour/mDNS; readers discover
it automatically. Keep computer time synchronized for speed estimates.

## Double-click launcher and saved configurations

Double-click **Launch Train Fleet.command** in Finder. Choose **Demo**, **Live**, or **Motor only**,
select a configuration JSON file (defaults to `fleet.json`), and click **Launch**.
The launcher uses this folder's `.venv`; complete the one-time installation above
first. Terminal displays startup errors if anything is missing.

**Motor only** provides manual train controls, available power readings, train
settings, Connect, and Stop/Stop All. It opens no map, Wi-Fi receiver, or discovery
service and requires no RFID reads or initial positions. Throttle limits, motor
polarity, connection recovery, and the hub watchdog still apply. There is no Auto,
speed matching, or block protection in this mode. External-controller roster entries
remain external. From Terminal, use `python fleet_controller.py --motor-only`.
Mode is chosen for the session and remains unchanged when loading another configuration.

Open **App settings → Alarm sound** to choose **Alternating siren**, **Two-tone
chime**, **System bell**, or **No sound (visual alerts remain)**. Preview a sound,
then Save; the choice is stored with the active configuration and applies without
stopping trains. Sound off retains collision/overdue banners, card indications, and
red map circles. **Silence alarm** still silences only currently active incidents.
Collision warnings draw one shared circle around all involved train markers,
following their displayed positions as they move or the map is zoomed or panned.
The circle and its label alternate red and orange every half-second while the
circle pulses in size, including when the alarm sound is silenced.
Overdue warnings continue to circle the expected checkpoint.

Live mode optionally accepts your Wi-Fi reader token. Enter it once and launch
Live to save it in your Mac login Keychain; future launches fill the masked field
automatically. **Forget saved secret** removes it from Keychain and clears the field.
The token is never saved in a fleet configuration. Leaving the field blank disables
Wi-Fi readers for that launch while retaining the saved secret and train controls.
Launching Demo does not save or change the secret. If Keychain cannot save, the launcher warns
you and uses the entered token for that launch only. macOS may ask you to allow
Python access to this Keychain item.

Terminal launches also use the saved secret when `TRAIN_READER_TOKEN` is unset.
An explicitly set value overrides Keychain, including an empty value to disable
readers. Install updated dependencies with `.venv/bin/python -m pip install -r requirements.txt`.
Demo makes no hardware connections. To switch modes, close the controller and launch again.

The controller's top row provides:

- **Save configuration**: save the active fleet and saved train library, sensor assignments, track geometry,
  calibration, and map image reference to the current JSON file.
- **Save configuration as…**: save a named configuration and make it the active file.
  When saving to another folder, the map image and train photos are copied into
  `map_images/` and `train_photos/` beside that JSON. Keep those image folders with
  the JSON when moving layouts.
- **Load configuration…**: choose a saved layout. Unsaved changes offer Save,
  Discard, or Cancel. Loading stops and disconnects trains, resets tracking, and
  applies the loaded reader/network settings. Reconnect trains afterward.

The active filename appears in the window title and top row. Configurations do not
restore running trains or enable automatic control; live positions must be established
again. The existing **Save map positions** button also saves the active configuration.

## Set up trains inside the controller

Each fleet-controlled train has a **Motor controller** selection and **Settings…**
button in the train library. Existing trains use **Pybricks City Hub** automatically.
Installed drivers declare their connection fields and whether they provide power
telemetry or waiting indicators. Only compatible City Hubs appear in **Update hubs**;
other controllers keep their current sessions. Additional hardware needs its own
driver implementation; selecting a controller does not install firmware or add a protocol.

Click **Settings** on a train card to edit its name, Bluetooth hub name, polarity,
length, stopping margin, starting/maximum throttle, and front/rear RFID tags.
Click **Save train settings** to save and apply immediately. Tag and tuning edits
retain connections, confirmed positions, Auto selections, and other trains' motion.
Hub-name or polarity changes stop only that train; a changed hub name reconnects
automatically if its connection was already active. Resume that train explicitly.
A lower maximum throttle takes effect immediately. Removing a rear tag does not
release previously retained blocks; verify and reposition that train to clear them.

Click **Train library…** above the train cards, or **Fleet setup → Active trains**:

1. Click **New train**, or select an existing train to edit it.
2. Set its display name and exact Pybricks Bluetooth hub name.
3. Set **Forward polarity** to `+1` or `-1`, according to motor installation.
4. Click **Scan tag for selected train**, remove the tag from the reader for at least one second, then scan it. The captured UID is added to that train. Or enter its UID manually. A tag already assigned to another train is rejected.
5. Click **Update**, then **Save and apply library** (or **Save and apply setup**).

The library has **Active trains** and **Saved trains** tabs. Your existing trains
start in Active trains. To put one away, select **Remove from active…**, confirm it
is physically off the track, then save and apply. Its complete record moves to
Saved trains: name, Bluetooth settings, front and rear RFID tags, control ownership,
photo, dimensions, throttle settings, and learned section times. Select it in
Saved trains and click **Add to active** to bring it back without registering its
tags again. Both fleet-controlled and externally controlled trains use this workflow.

To delete an inactive train, select it in **Saved trains**, click **Delete train…**,
confirm, then **Save and apply library**. This removes its saved record and releases
its RFID assignments without stopping the active fleet. Closing the library without
saving discards the deletion.

Saved trains do not connect, appear on the map, occupy blocks, or participate in
automatic control. Their RFID assignments remain reserved, so another train cannot
accidentally take their tags. You can edit saved trains, create new ones directly in
Saved trains, and register their tags there or in **Sensors → Tags & trains** without
activating them. External trains keep using their own controller when active;
the app tracks them and can use them as a speed source.

Each configuration saves its own active list and library; **Save configuration as…**
carries both lists and their photos to the new file. Adding or removing active trains
stops and disconnects the fleet and resets tracking. Reconnect and establish positions
after applying; Auto stays off. Stop external trains with their own controller.
Removing the selected external speed source also clears that selection. Editing only
saved trains leaves the active fleet running. Closing the library without saving
discards its edits.

Train controllers are compact cards in the left sidebar, with no scrolling.
Only active trains appear: two active trains show two cards.
Move unused trains to **Saved trains** to hide their cards. Up to four cards stack vertically;
larger rosters use additional columns. The map fills the right pane.
Each card has a background tint matching its train's map marker. Marking a train
physically **off track** turns the whole card dark gray with light text and an
explicit off-track label. Placing it at a checkpoint or detecting its RFID tag
restores its color. **In service** and Bluetooth connection status do not imply
physical removal, so they do not turn the card dark gray.

Click **Add photo** on a train card, then **Choose photo…** to select a picture
from your computer (PNG, JPEG, WebP, GIF, BMP, or TIFF). A thumbnail appears on
the card; click it to see a larger preview, replace the picture, or **Remove photo**.
Photo changes save automatically without restarting or disconnecting trains.
The app keeps its own resized copy in `train_photos/` beside the configuration,
so moving or deleting the original picture does not break the card. Missing image
files show **Photo unavailable**, which you can click to choose a replacement.

Card icons: **↻** connect/retry, **■** stop, **⌖** set position, **↗** physically
remove from track, and **i** view details. Hover for labels and full status text.
The compact speed line shows studs/s and time since the last read. Auto and In service
remain directly accessible. **File** holds configuration save/load commands.
Train markers rotate with the track tangent on straights and curves; their white
front stripe indicates travel direction, including reverse.

Click anywhere on a train card to select it; a blue border marks the selected train.
While that card has keyboard focus, **↑ / →** increase throttle by one percentage
point and **↓ / ←** decrease it, including through zero into reverse. Arrow keys
use the same limits and holds as the slider. Fields and dialogs keep their normal
keyboard controls. These controls are available in Demo, Live, and Motor only.
The larger slider has a wider track and handle for easier dragging.

The red **EMERGENCY STOP ALL** button is always available in the main window in
every mode. It immediately sends zero throttle to every fleet-controlled train,
cancels Auto, external-speed matching and Force Run, and pauses the demo.
Externally controlled trains must be stopped with their own controller.

Each manual throttle is vertical: **+100 forward** at the top, **0 stop** in the
middle, and **−100 reverse** at the bottom. The configured maximum throttle caps
both directions. With polarity -1, +40 sends `s-40` and −40 sends `s40`; polarity
+1 sends the same sign as the slider. STOP always returns the slider to zero.
The vertical master target controls **forward-only automatic mode** in studs/s;
manual reverse disables Automatic for that train.
**Auto max · studs/s → Set** changes the target slider's upper limit and saves it
with the layout. Existing layouts start with a 15 studs/s maximum and a 10 studs/s
target. Lowering the maximum also lowers a higher target.

Reverse movement reserves the preceding block and uses reverse checkpoint order
for speed measurements. Switching direction keeps the last confirmed occupied
block, clears the previous speed sample, and waits for new RFID detections to
establish position and speed again. Signed measured speed is negative in reverse.
Test polarity with wheels lifted. IDs, hub names and tag assignments must be unique.

Train-only edits in fleet setup also apply live. Changing the roster, control
ownership, or loop/network structure still stops and disconnects the fleet and
resets tracking; reconnect and locate trains after those structural changes.
Saves are atomic, and validation/save failures leave the running configuration
unchanged. No app restart or hand-editing JSON is required.

Open **File → City Hub program…** to view the bundled `firmware/city_hub.py`, copy
the complete source, or **Save a copy…**. The same window is available through
**Settings → City Hub program…** on a train card. The source fingerprint identifies
the displayed file; it is not a readback of code already stored on the hub.

In **Live** or **Motor only** mode, click **File → Update hubs**, or **Update hubs**
in the program window. The controller stops the active Pybricks City Hub trains and
disconnects their existing Bluetooth sessions, scans for 10 seconds using the exact
hub names in the **active train list**, then connects to each matching hub in turn.
After Bluetooth connects, it checks for Pybricks services, compiles, uploads, and
starts the displayed program. You do not need to connect the train first or have
the train program already running. Missing hubs and duplicate advertised names are
reported per train; a failure does not prevent the next hub from being updated.
External trains and saved, inactive trains are excluded.

The **Update progress / debug** tab shows each train's result and a timestamped
verbose log: target names, discovered names/addresses, signal strength, advertised
services, connection stages, Pybricks compatibility reads, Bluetooth transfer limits,
chunk offsets, hub stdout, startup verification, and full error tracebacks.
Use **Copy debug log** or **Save debug log…** when troubleshooting. The recent log
survives closing/reopening this window within the same controller session.

To update just one hub, select an active fleet-controlled train and click
**Upload hub program…**. Confirm the train and Bluetooth hub name. That train stops,
its existing Bluetooth writer disconnects, and the controller scans, connects, then
compiles and uploads the displayed source to that hub.
The controller starts the new program, verifies its identity and zero-throttle reply,
then keeps the connection open for motor control. The train remains paused until you
explicitly resume it. Other trains retain their connections and confirmed positions.
Configuration changes wait until the upload finishes. Closing the program window
does not cancel the upload; reopen it to see its status. Closing the controller
cancels the upload and stops/disconnects its connections.

The motor must be on **port A** of a **City Hub** already running Pybricks firmware.
**A blank Pybricks hub with no saved program is supported.** Its advertised name
must match the active train's configured hub name. A factory-default LEGO hub, or
a hub in firmware recovery/update mode, needs initial Pybricks installation first;
**Update hubs** installs the train program, not the hub firmware.
Use [Pybricks Code](https://code.pybricks.com/) for initial firmware installation or
firmware upgrades, and disconnect it before uploading from the fleet controller.
The uploader checks the actual hub type, protocol compatibility and available memory
before replacing its program. It supports the current command/stdout protocol
(Pybricks BLE profile 1.3 or newer within version 1). Failed uploads never retry
automatically or resume motion; inspect the error and retry explicitly. Demo mode
allows viewing/copying/saving only. External and inactive saved trains are excluded
from upload targets.

Uploading requires the `mpy-cross-v6` compiler included in `requirements.txt`.
For an existing installation, run `.venv/bin/python -m pip install -r requirements.txt`.
You can also continue loading `firmware/city_hub.py` through Pybricks Code. After that,
disconnect Pybricks Code, connect the hub in this app, then press its button to run
the saved program. Connect hubs one at a time. New connections establish zero first. The
hub watchdog stops after two seconds without valid commands; the computer repeats
commands every half-second. Verify actual watchdog behavior on your hardware.

After **↻ Connect**, a train keeps trying to reconnect after a failed scan or lost
Bluetooth connection, with delays from 2 to 15 seconds between attempts. A lost
program reply triggers a zero-throttle probe; repeated missing replies reconnect
Bluetooth. Recovery always establishes zero before accepting motion commands and
discards old manual/Force Run throttle. Auto may resume after readiness returns,
subject to its existing block and tracking checks. Explicit Stop remains stopped;
missed-checkpoint/collision faults are not cleared by reconnecting. Confirmed
occupancy is retained. Retries end when closing the app or replacing the layout.
This does not power on a hub or restart a crashed hub program; if the program is
not running, start it with the hub button. The Mac controller need not be restarted.

### Train batteries

Upload the updated `firmware/city_hub.py` to **each** hub and restart the Mac
controller. While its program runs, each hub reports battery voltage and current
draw at startup and every two seconds, including while the motor is stopped.
The train card shows **volts, amps, and calculated watts**, for example
**8.12 V · 0.145 A · 1.18 W**. Watts are voltage × current. Hover over the reading
or click **i** for its age and current in mA. Below **6.00 V**, the reading and
warning turn yellow: battery power is getting low. Below **5.50 V**, they turn red:
power loss may be imminent. Exactly 5.50 V remains yellow; exactly 6.00 V has no
low-voltage warning. A reading at least six seconds old is marked **stale**;
any low-voltage warning is explicitly labeled as the last reading.
Disconnecting/reconnecting clears telemetry.
**Battery —** means no reading has arrived. Older hub programs still support
train controls, but need this update to report batteries. Demo shows **Battery: demo**
and does not invent hardware measurements. Readings are not saved in configuration.

Click a train's battery reading, or **File → Battery history…**, for voltage,
current, power and throttle graphs. **Record power history (supported controllers)**
is **off until you first enable it**. Your on/off choice is saved immediately with
the configuration and restored automatically on later Live or Motor only launches,
and when loading that configuration. Demo does not record or change the saved choice.
Uncheck it to stop collection while keeping the existing graphs available. Closing the graph window
keeps an explicitly enabled recording running. Normal battery readings on train
cards continue whether history recording is enabled or not.

The **Over time** tab aligns volts, amps, watts, controller demand after safety
limits, and the last throttle command written to the controller. Throttle is signed
relative to the train's forward direction; it is not measured wheel speed. Choose
a time range and hover for exact values. **Current vs demand** plots individual
amp readings against throttle magnitude, separating forward and reverse by color.
The summary estimates Wh and mAh only across consecutive measured intervals;
disconnects, pauses in recording, and gaps of six seconds or more are excluded.
History is bounded to the most recent four hours while recording (with additional
sample/event count limits). **Export visible samples…** saves CSV with UTC timestamps,
volts, amps, watts, demand, sent throttle and recording/connection segment IDs.
History and profiles are held in memory for this app session; export before exiting
to keep the measurements.

While recording, each train also learns a **current-based load profile**. Run the train
normally to teach it its usual load. Forward and reverse each have separate 10-point
throttle bands. Learning/checking waits for at least five seconds of steady demand,
at least 10% throttle, and a matching sent command. A band needs 12 baseline readings
before it can produce an advisory. Expected current is the median of up to 120 normal
readings. Alarm limits default to **10% above and 10% below** that expectation;
there is no minimum amp margin or automatic widening for variability.
For example, an expected 0.250 A alarms above 0.275 A or below 0.225 A.
Dashed boxes on **Current vs demand** show the configured alarm limits.
Learning, percentage deviations and load alarms use **amps only**. Changing voltage
at unchanged amps cannot trigger a load alarm. Volts, watts, Wh and mAh remain in
the history, graphs and CSV; the separate low-battery voltage warning still applies.

Current is a load indicator, not a direct torque measurement here. The
[City Hub API reports battery current](https://docs.pybricks.com/en/stable/hubs/cityhub.html#using-the-battery),
which includes the hub and its connected loads. Motor current relates to torque,
but battery current and operating speed can still change with supply voltage and
PWM throttle. Compare similar demand/direction and relearn after intentional load
changes; switching from watts does not remove every battery-discharge effect.

In the battery-history window, select a train and set **High: above expected %**,
**Low: below expected %**, and **Readings**, then click **Apply setpoints**.
High accepts 1–500%, low accepts 1–100%, and the consecutive-reading count accepts
1–10 (default 3). These settings apply only to that train, save with its configuration,
and survive restarting the app or resetting its recorded data. Applying limits
keeps the learned baseline and does not stop the train or clear an active alarm.

**Current advisories while recording** can be toggled separately. The configured number
of consecutive readings outside the alarm limits opens an informational, nonmodal popup. Higher draw
can indicate extra load such as dragging wheels or pushing a train; lower draw can
indicate reduced load such as wheels lifting or a disconnected motor. These are
possible causes, not a diagnosis. The advisory does not stop trains or change
throttle. Startup transients, missing telemetry and commanded stops do not trigger
it. Suspect readings are excluded from learning.

**Current alarms latch until manually cleared.** Normal readings, stops, reconnects,
recording/alert toggles, and setpoint changes never clear them. The first triggering
reading and limits are saved with the train so the latch also survives restarting.
Closing the popup only hides it; the train card keeps its red **CURRENT ALARM** label.
Older saved alarms remain latched in their original watts and retain **POWER ALARM**
labels until manually cleared. They are never reinterpreted as amps. New alarms
save an explicit `metric: "amps"`; existing percentage setpoints are retained.
Use **View alarm** in battery history to reopen it, then **Clear alarm** after
checking the train. Clearing keeps the baseline and setpoints; checks resume after
the load settles, and an ongoing issue can trigger another alarm.

**Reset train history & profile** clears the selected train's recorded samples and
learned expectations immediately; the popup also offers **Reset train data**.
This explicit reset also clears that train's latched alarm but keeps its setpoints.
Recording continues if enabled, starting a fresh profile. Reset after intentionally
changing the consist or load. Resetting does not change throttle or other trains'
data. Changing a train's hub or polarity starts a new history/profile automatically.

The values come from Pybricks' [`hub.battery.voltage()` and `current()` APIs](https://docs.pybricks.com/en/stable/hubs/cityhub.html).
Voltage depends on battery chemistry and motor load, so this monitor does not
estimate percentage or remaining runtime. It is informational and adds no
automatic low-voltage stop. Battery reports never reset the motor watchdog or
authorize motion. The text protocol adds `battery <millivolts> <milliamps>` lines
alongside the existing `rdy` and `timeout` lines.

The speed row also turns yellow with a **slow** indication if a measured section
falls more than 20% below its learned throttle response, a checkpoint takes at
least 1.3× its expected moving time, or Auto remains at maximum throttle for five
seconds with estimated speed more than 20% below target. Measured/overdue warnings
require at least five seconds of moving time. Intentional block braking, following,
operator stops, and waiting do not trigger these speed warnings. External trains
are compared with their learned section times, since they have no fleet target.
Warnings clear when the condition resolves or the controlled train stops. These
are checkpoint/model-based warnings, not continuous wheel-speed measurements;
the existing overdue alarms and holds remain in force. No firmware change is
needed if the hub already reports battery telemetry.

If Bluetooth connects but the battery stays blank, press the hub button to start
the saved program. The hub light turns green after the controller establishes
zero throttle. If it stays blue, run `city_hub.py` in Pybricks Code and inspect its
terminal for a startup error; the motor must be connected to port A. Disconnect
Pybricks Code before reconnecting the fleet controller. The controller also shows
hub Python errors in the train status and activity log.

Manual throttle can start a connected, in-service train before its first RFID
read. Its status says **Manual locating — awaiting RFID**. Other unlocated trains
do not prevent this manual startup. The first accepted front tag establishes the
train's block automatically; known occupancy and approach checks then apply.
Auto still requires localization. Explicit off-track state, known tracking faults,
connection readiness, throttle limits, and rear-tag reverse restrictions remain.
An unlocated train has no block protection until its first read.

If manual throttle is held, read the reason beneath the train controls or in **i**.
Use **⌖** for a verified position, and **↗** only for trains actually removed. Unchecking
**In service** alone retains that train's occupancy. Manual hold reasons remain
visible until another throttle attempt or an explicit stop; resolving a hold does
not automatically restart a manual train.

Automated tests cover fragmented/coalesced Bluetooth
messages, reconnects, stale readings, temporary battery-read failures, and a
simulated hub verifying that telemetry does not feed the motor watchdog. A local
GUI smoke test checked four-card layout, live/stale/missing readings, details,
disconnects, configuration rebuilds, and demo mode. Physical hub readings and
Bluetooth behavior still need hardware verification.

Startup diagnostics and manual-hold fixes passed 38 focused tests and a GUI
check confirming that refused throttle stays at zero, the reason remains visible,
and movement requires a fresh manual request after the hold is resolved.

## Add and arrange checkpoints

In **Train & loop setup → Loop checkpoints**, add/remove reader points and set:

- Reader ID and ESP32 node ID matching the firmware on that device.
- Display name.
- **Track distance to next point**, in LEGO studs (manual until map calibration).

Use **Earlier in loop / Later in loop** to set travel order. The final checkpoint
connects to the first. At least two checkpoints are required for speed measurement.

### Calibrated track map

1. Click **Map image…** to import a picture of your layout. Use an overhead image
   with uniform scale; perspective distortion cannot be corrected by one reference.
2. Click **Set known distance…**, then click the two ends of a measured reference
   on the image. Enter that distance in **inches**. One LEGO stud is 8 mm.
3. Drag checkpoints onto the track. Press **A** while the map has focus, then click
   to add one shaping point. The new point remains selected. The **Add track point**
   checkbox still supports placing several points, and Option/Alt-click adds a point.
   Right-click a shaping point to remove it.
4. Click a point to select it; **Shift-click** adds/removes points from the selection.
   Selected points have purple outlines and stay selected after dragging or panning.
   With **two points selected**, press **C** (or **Curve between selected**) to create
   a 90° curve. The radius is calculated from the endpoint spacing and shown in studs.
   Press **C** again to flip the bend. Moving either endpoint updates the curve and
   radius. Both endpoints must belong to the same checkpoint-to-checkpoint block;
   intermediate shaping points are replaced, but checkpoints are never removed.
5. Use **Zoom + / Zoom −** or the mouse wheel to zoom, and **drag an empty part of
   the map** to pan. Wheel zoom stays centered on the cursor. **Reset view** restores
   the initial view. **Esc** cancels point placement. Click the map to focus keyboard
   shortcuts; typing in setup fields does not trigger map shortcuts.
6. Click **Save map positions** to retain track edits. Calibration is saved immediately.

Press **S** while the map has focus, then click to add a sensor checkpoint exactly
at that position. It is inserted into the nearest track block in travel order and
stays selected for dragging. **Esc** cancels placement. New points receive unique
IDs such as `sensor_1`; rename them in **Fleet setup → Loop checkpoints** and assign
a physical reader in **Sensors**. Click **Save map** to keep the new checkpoint.
Adding a sensor stops trains and resets tracking because block boundaries changed.

Straight sections and endpoint-defined curves are split around the new sensor;
curves can become two partial arcs whose angles add to the original 90° turn.
Uncalibrated layouts divide the existing manual block distance proportionally.
Legacy explicit-radius curves in the edited block are converted to finely sampled
shaping points when adding a sensor. Placement works with zoom and pan.


After calibration, block lengths are calculated from straight sections plus exact
quarter-circle arc lengths (π × radius / 2); manual block-distance fields become
read-only. Map edits stop trains and discard previous speed samples. New RFID
passages establish speed using the updated distances. Endpoint curves follow their endpoints when recalibrating. Older curves created
with an explicit radius retain that physical radius. Existing uncalibrated layouts retain manual
lengths. For legacy two-checkpoint loops, the existing separate return paths are retained.

All displayed track distances, curve radii, train lengths, and stopping margins use
**studs**; measured speeds and the master target use **studs/second**. Existing JSON
meter fields remain compatible and are converted at the UI boundary. Zoom and pan
never alter measurements. The original image is retained for detailed zooming.

Blocks have consistent color names across the map, train cards, placement dialog,
and dispatch messages: Blue block, Orange block, Green block, and so on. A thicker
colored line is occupied; a dashed line is reserved for an approaching train.
The block keeps its color when occupancy changes.

Use **View → Hide all extra information** for a simple operating display, or turn
distances, curve radii, checkpoint names, sensor status, editing points, checkpoint
read age, map help, and the activity log on or off individually. Card speed readings
remain visible. View choices
are saved with the layout. **Show all details** restores the editing display.
Replacing the image retains the track and calibration; set the known distance again
if the replacement uses a different scale. Clearing the image retains track geometry.


## Blocks and animated estimates

Each block spans two consecutive checkpoints in the configured loop order.
A valid forward RFID read places the train's **tag** at the start of the outgoing
block; a reverse read places it at the end of the preceding block. Two consecutive reader
hits give average speed = previous block distance / detection-time difference.
The Mac timestamps detections on receipt using its own clock; no NTP server is
used by the ESP32s. Wi-Fi delivery and queue delays affect speed estimates.

Markers animate from the last reader toward the next using that measured speed.
They **never cross a checkpoint or change block solely because time elapsed**.
At the predicted arrival time, the marker turns orange and waits for RFID. The
label changes to **Last confirmed block**, with an uncertainty warning. Without a
speed estimate, the last-read block becomes uncertain after 60 seconds. A stop command
freezes the estimate until the next speed measurement. Nonzero throttle corrections
retain the previous measured speed as an approximate prediction. A new detection
corrects the position. A skipped checkpoint updates the detected block but discards
the old speed estimate. The display always includes the age of the latest read.

Red map segments contain a train's last detected tag block; grey means no tracked
tag is assigned there, **not confirmed clear**. The block display cannot be definite
if a physical detection is missed. Nor does a tag crossing a boundary prove the
whole train has cleared the previous block. Physical block-signal LEDs and whole-
train clearance logic are still pending (see planned light section below).

Speed is an approximate average, not instantaneous speed. A stop inside a block
reduces its average. Automatic speed regulation now adjusts throttle from these average measurements;
see the controls below. State resets on app restart or
setup changes. Reader delivery may lose events during outages; delayed detections
are discarded. Live reader health reports target 10 Hz; scan history is kept only
for the current app session.

## Reader wiring and installation

One classic **ESP32 Dev Module + ELECHOUSE PN532 V3** per checkpoint.
Keep the working four-wire I²C wiring. Power off to wire:

| ESP32 label | PN532 label |
|---|---|
| 3V3 | VCC |
| GND | GND |
| D21 (GPIO21) | SDA |
| D22 (GPIO22) | SCL |

Set PN532 **switch 1 ON, switch 2 OFF**, then power cycle it.

1. Select **ESP32 Dev Module** in Arduino IDE. Install **PN532-Arduino 1.1.0+**
   from [Seeed Studio](https://github.com/Seeed-Studio/PN532), accepting its dependencies.
   This sketch uses that library, not Adafruit PN532.
2. Open `firmware/rfid_reader/rfid_reader.ino`. All settings are at the top of
   this single file; no secrets.h is needed. Set your **2.4 GHz** Wi-Fi name/password.
   The Mac running the controller must be reachable on the same LAN; it can use
   Ethernet or the router's 5 GHz network. Avoid guest/client-isolated networks.
3. Leave `SERVER_URL` empty (`""`) for automatic discovery. The Mac advertises
   `_trainrfid._tcp.local.` with its current IP and port on each network interface.
   Each interface advertises only its own address, so you can connect the Mac by
   Ethernet to the sensor router while using a separate Wi-Fi network for Internet.
   The reader looks for
   `CONTROLLER_ID "train-fleet"` by default. No fixed IP or Mac hostname is needed.
   A nonempty SERVER_URL uses that manual address;
   clear it to enable discovery. Install updated dependencies with
   `python -m pip install -r requirements.txt` before restarting the app.
   If multiple controller apps run on the LAN, close extras or set a unique
   `network.controller_id` in fleet.json and the same `CONTROLLER_ID` at the top of the sketch.
   Duplicate matching controllers are rejected instead of arbitrarily choosing one.
   Discovery is local and needs no Internet; multicast/client isolation can block it.
   For those networks, an explicit `http://MAC_IP:8765/events` remains supported.
4. Choose a shared token of at least 16 characters. Put it in `READER_TOKEN` and
   set the identical `TRAIN_READER_TOKEN` environment variable before starting
   `python fleet_controller.py` (without `--demo`). Allow incoming connections
   through the Mac firewall when prompted. The app log must say the receiver is
   listening on port 8765. Tokens travel over HTTP; use a trusted local network.
5. Upload the same sketch to each ESP32. Each derives a permanent `esp32-...`
   sensor ID from its hardware; no gate ID needs to be entered in firmware.
6. Open Serial Monitor at 115200 baud: expect `Sensor ID`, `PN532 ready`,
   `Wi-Fi connected`, `Controller found`, and after a scan, `Tag delivered to Mac`.
   The app log shows `Automatic reader discovery ready` when advertising.
7. Open **Sensors & tags → Sensors & gates** on the Mac. Newly connected sensors
   appear automatically, even when they have not been assigned to a gate. Select
   one and click **Blink LED for 10 seconds** to identify the physical board.
   The screen shows command pending until the sensor acknowledges it.
8. Choose a gate and click **Assign selected sensor**. Replacing an existing sensor
   asks for confirmation. A sensor can belong to one gate, and a gate can have one
   sensor. **Unassign sensor** removes the binding without disabling status/tag scans.
9. Open **Tags & trains**, scan a tag, select its UID, choose a train, and click
   **Assign selected tag**. This also works at an unassigned sensor. One train can
   have multiple tags; one tag can belong to only one train. To move a tag to a
   different train, unassign it first. Previously saved tags are also listed.

Gate and tag assignments save immediately to the active configuration. Tag
assignments apply live without stopping or disconnecting trains. Gate changes
reset tracking and require reconnecting/localizing afterward. Closing the screen
does not undo saved assignments. No sketch
re-upload is needed when moving sensors between gates or tags between trains.
Old placeholder sensor IDs such as `node_a` may appear as never seen; replace
those bindings with the new hardware-derived sensor IDs using the gate selector.

The main map also labels every gate with sensor health: green **ONLINE**, red
**OFFLINE**, amber **READER MISSING** or **READER STALLED**, and gray **NO SENSOR**.
Assigned sensors with no reports yet are offline. Demo mode displays **DEMO**.
Badges refresh with the map; they do not change segment occupancy or train control.

**Status and identification:** firmware targets a report every 100 ms (10 Hz),
and the management screen refreshes every 100 ms. It shows online/offline state,
PN532 health, Wi-Fi signal strength, measured reports/second, IP address, age of
last report, and identify-command state. No report for 1.5 seconds marks a sensor
offline. RFID scanning runs independently of HTTP; the PN532 chip is also checked
for presence every two seconds and retried when missing. A stalled RFID loop is
reported separately. Wi-Fi failures/HTTP delays can reduce the actual report rate.
These status displays do not guarantee detection or trigger a new automatic stop
policy; the existing tracking/dispatch rules still apply.

The built-in LED also shows confirmed segment occupancy: a registered train tag
at gate A turns A's LED on; its next gate detection moves occupancy and turns A
off unless another train remains in that segment. The Mac sends the desired LED
state in every status response, typically within a few 100 ms cycles. Prediction,
stopping, or a missed read never clears the light. Explicit Off track/Set position
operations update it too. Identify blinking temporarily overrides the steady light,
then restores the latest occupancy state. The management screen displays the LED
command, not a physical light measurement.

This is tag-based occupancy, not proof that the train's last carriage has cleared
a boundary. During a network outage the ESP32 retains the last commanded state;
a reboot starts with the light off until it hears from the Mac. Restarting the
Mac or applying setup resets tracking, so LEDs return off until trains are located
again. Off therefore does not certify that a physical segment is clear.

Identification blinks a fixed-color LED, not an RGB color change. The sketch uses
GPIO2 by default (`IDENTIFY_LED_PIN`); adjust it and `LED_ACTIVE_LOW` if your ESP32
board differs. A power-only LED cannot be controlled. Set the pin to -1 to disable
identification on boards without a controllable LED. Acknowledgment confirms the
firmware applied the command, not that a physical LED is wired to that pin.

The ESP32 sends detections to the Mac; the Mac identifies the registered train
and continues controlling its hub over Bluetooth. A single reader establishes
location; successive configured checkpoints provide travel/speed information.
After upload, power the reader locally by USB; its data travels over Wi-Fi.

Troubleshooting: HTTP 401 means the tokens differ; 400 means malformed input; connection failures mean check the receiver, IP, port, and firewall.
If a sensor on `192.168.0.x` discovers the Mac's separate Internet Wi-Fi address
(for example `172.18.x.x`), restart the Mac app with the updated discovery code.
The app log now lists the advertised endpoints. The sensor should discover the
Mac's Ethernet address on the sensor LAN; no firmware update is needed for this fix.
As a manual fallback, set `SERVER_URL` to `http://ETHERNET_IP:8765/events` in the
sketch, or set `network.host` to the Mac's Ethernet IPv4 address in the active
configuration JSON and restart the app. A manually configured IP must be updated
if DHCP changes it. Status error `-1` is a connection failure, before token checking.
If discovery fails, check that the app is running without `--demo`, dependencies
are installed, and the LAN/firewall permits mDNS (UDP 5353). The Mac refreshes
advertised addresses after network changes; readers rediscover when disconnected
or after repeated status delivery failures. Healthy connections do not pause for
periodic discovery queries. A short interruption may lose scans; detections are
not replayed indefinitely after an outage. Rescan once the connection is restored.
A delivered event can still be rejected by the app for a mismatched node ID or
invalid event fields; inspect the app's event log. No NTP setup is needed.

Firmware reads only the UID. A stationary tag is suppressed until absent for
about one second; very fast repeat passes can be suppressed. Adjust after testing.
The network sender runs separately so HTTP waits do not block RFID polling.
Example event fields: `uid`, `node`, random `boot` ID, increasing `seq`,
`t_ms` (uptime), and `unix_ms` (sent as null). The Mac replaces unix_ms with its
receipt time, supplies the assigned gate as reader, and marks time_source as controller_received. Speed estimates use
those arrival times, so network delays affect the result.

Mount the tag flat beneath a carriage, facing the reader at the smallest practical
gap, away from the motor, battery and metal. Prototype at your actual track depth,
speeds and orientations. Test at least 100 passes in both directions before buying
all six nodes. A PN532 may miss fast passes depending on antenna/gap/mounting.

## What to buy

US planning allowances as of October 14, 2026; tax/shipping excluded. Only the
PN532 price below was available as a vendor listing; other prices are estimates.
Start with **two complete nodes and tags**, to test both detection and speed.

| Item | Two-node trial | Full six-node layout | Budget per item |
|---|---:|---:|---:|
| [Arduino Nano ESP32 with headers](https://store-usa.arduino.cc/products/nano-esp32-with-headers) | 2 | 6 | Allow $20–30 |
| [Adafruit PN532 breakout, #364](https://www.adafruit.com/product/364) | 2 | 6 | Listed $39.95; Adafruit showed out of stock—check distributor stock |
| [13.56 MHz ISO14443A/MIFARE Classic sticker tags](https://www.adafruit.com/product/362) | 4 + spares | 4 + spares | Allow $5–15 total for test tags; check physical fit |
| USB power supply + USB-C cable | 2 | 6 | Allow $10–15 per node |
| Jumper wires, breadboard/perfboard, mounts/enclosure | 2 sets | 6 sets | Allow $5–10 per node |

Budget approximately **$155–205 for two nodes** or **$455–585 for six**, plus
soldering tools if needed. One USB data cable can serve for programming all nodes.
Breakout headers may need soldering. Existing 2.4 GHz Wi-Fi must cover the layout;
no radio or Arduino needs to ride on the trains. Do not buy 125 kHz or UHF tags for
the PN532. Start with the linked documented board before considering cheaper
compatible modules whose wiring and performance need separate checks.

Sources:

- [Arduino Nano ESP32 documentation](https://docs.arduino.cc/hardware/nano-esp32/): Wi-Fi and USB-C.
- [Adafruit SPI wiring guide](https://learn.adafruit.com/adafruit-pn532-rfid-nfc/breakout-wiring).
- [Adafruit PN532 library](https://github.com/adafruit/Adafruit-PN532).
- [Espressif time synchronization example](https://github.com/espressif/arduino-esp32/blob/master/libraries/ESP32/examples/Time/SimpleTime/SimpleTime.ino).
- [Pybricks PC communication](https://pybricks.com/projects/tutorials/wireless/hub-to-device/pc-communication/).

## Verification

```sh
python3 -m unittest discover -s tests -v
```

Automated tests cover configuration, UID handling, checkpoint association,
readiness framing, stop priority, polarity, loop wraparound, prediction limits,
throttle changes and atomic configuration saves. A live GUI smoke test verified
adding a checkpoint, applying setup, demo detection, saving and clean shutdown.
Arduino firmware has not been compiled here (Arduino CLI is unavailable), and
physical Bluetooth/RFID/City Hub behavior still needs bench testing. The sample
map is not your actual layout: coordinates, topology, distances and real tag IDs
must be supplied before meaningful speed measurements are possible.

## Revised reader choice: 1–2 inch mounting gap

With the proposed gap reduced from 3–6 inches to **1–2 inches (2.5–5.1 cm)**,
a lower-cost PN532 is a reasonable prototype candidate. Aim for about one inch;
do not assume two inches will be reliable with a small sticker on a moving train.

- [ELECHOUSE PN532 V4](https://www.elechouse.com/product/pn532-nfc-rfid-module-v4/):
  $23.90 listed October 14, 2026; maker specifies 6–8 cm with standard Mifare S50
  cards under standard conditions. Range depends on tag size, alignment and nearby
  materials. This is the preferred lower-cost **trial**, not a proven layout-wide
  replacement. Two readers save $32.10 relative to two $39.95 Adafruit boards;
  six save $96.30 before shipping/tax.
- [ELECHOUSE purple PN532](https://www.elechouse.com/product/pn532-nfc-rfid-module-purple-version/):
  listed $15, but the maker recommends V4 for product use. No verified moving-train
  range advantage; favor the V4 for the first comparison.
- [ELECHOUSE PN5180](https://www.elechouse.com/product/pn5180-nfc-module/): listed
  $12.80, but needs a different driver and wiring. ISO15693 tags also use 8-byte
  UIDs, which the current PN532-oriented parser does not support. Not a drop-in
  replacement; no verified 1–2 inch performance for this particular installation.

The existing firmware is written for the Adafruit PN532 library/board. Before
using V4, verify the selected module's power inputs and SPI mode selection, then
bench-test library compatibility; do not blindly reuse the Adafruit jumper or
power-label instructions. Buy one reader plus several tag sizes first, then add a
second for speed measurements once the mounting works. Prefer the largest flat
tag that comfortably fits underneath the carriage. Check adjacent-track reads as
well as missed reads. Maximum advertised range is not a reliable operating gap.

## Planned block-status light (not implemented yet)

The [Nano ESP32 has a controllable RGB LED](https://docs.arduino.cc/tutorials/nano-esp32/cheat-sheet/),
so no extra LED is needed for an onboard status indicator. For a visible trackside
signal, use separate red/green LEDs with individual current-limiting resistors;
LED pin behavior varies on other ESP32 boards.

Proposed behavior:

- Solid red: protected block occupied.
- Solid green: protected block positively established clear.
- Flashing red: occupancy unknown, startup, or stale/lost communication.

The computer must calculate block state and send it back over Wi-Fi; the ESP32
must expire a green indication when updates stop. The present firmware only sends
RFID events and does **not** yet drive occupancy signals. Missing detections cannot
prove clearance. Detecting a locomotive leaving also does not prove its final
carriage has cleared. Before implementing automatic green indications, define the
block boundaries, entry/exit observations and whole-train clearance rule. On a
bidirectional line, a signal must protect a specific block/direction; one ambiguous
reader hit cannot choose which adjacent block is the "next" one.


## Automatic fleet control, block holds and service

Install updated dependencies with `python -m pip install -r requirements.txt`.
Pillow supports the map image picker. **Map image…** copies a PNG/JPEG/GIF/WebP
into `map_images/` beside the configuration; a saved relative path restores it on
next launch. The image initially fits within 1080×450 pixels. Zoom and pan support precise
placement; point coordinates remain in the same map space at every zoom level.
**Clear image** removes the backdrop without deleting the original or imported file.

### Using automatic mode

Each Auto train displays its current running or stopped status directly in its
controller. Stopped reasons include demo paused, Auto fleet disabled, zero target,
hub not ready, out of service, unknown position, tracking faults, and occupied or
reserved blocks (including the other train's name). Reasons refresh as conditions
change. The two-line message is visible below the buttons; hover or use **i** for
full details when a long train name or message exceeds the available space.


1. Configure each train's length, stopping margin, starting throttle and maximum
   throttle in **Train & loop setup**. Defaults (62.5 studs length, 37.5 studs margin, 25% start,
   70% maximum) are placeholders, not calibrated values.
2. Locate every train by RFID or **Set initial checkpoint…** after physically placing
   its tag at that reader. Use **Physically off track** for trains actually removed.
3. Connect hubs, choose the master target in **studs/second**, then click the larger
   **AUTO** control on each train you want regulated. Clicking it also enables
   **Auto fleet**. You can go directly from Force Run to Auto with this one click;
   protection is evaluated immediately, without inserting a stop when the route
   is clear. If prerequisites fail, the train holds under ordinary Auto rules.
4. The controller starts at each train's configured starting throttle. After two
   consecutive checkpoint reads, it learns that train's speed per throttle unit.
   Later passages update the model separately for each train, section and direction,
   accounting for curves, different loads and battery conditions. It uses throttle exposure during the
   interval, excluding stationary time, so a long wait does not cause a surge.
5. Setting master speed to zero stops automatic trains. STOP disables automatic
   control for that train. STOP ALL disables fleet automation and stops every train.
   Moving a train's slider switches that train to manual mode; block checks remain.
   Controller-driven slider updates do not switch Auto off.

The **AUTO** panel at the top of the map lists what needs attention: the fleet
switch, target, individual Auto selections, overrides, hub readiness, missing RFID
positions, service/paused states, track holds, and reader availability. It turns
green when its checks are satisfied. Sensor availability is diagnostic; it does
not add a new automatic stop beyond the existing missed-checkpoint protection.

Once calibrated, target or maximum reductions lower the command on the next control
cycle; increases ramp upward. The displayed last interval average can still include
a wait, while regulation uses the moving-time model. Learned section history is
saved with the layout and reused after restart; each direction has separate samples.
Before calibration the actual speed is
unknown: the configured starting throttle determines motion, and the requested
studs/s maximum cannot yet be enforced. Set that starting throttle for each train
and load. This is checkpoint-based estimated speed control, without continuous motor
speed feedback. Automatic mode starts disabled when the app opens or setup is reapplied.

### Adaptive cruise, learned section times and overdue alarms

Auto learns both moving travel time and throttle response for each train in each
section, separately in each direction. A long train can therefore have a different
model in a curve than on a straight. New valid passages gradually update that
model as loads or battery conditions change. Commanded stops are excluded from
learning and timing. Duplicate reads at the same checkpoint do not reset a journey;
skipped checkpoints and passages exceeding the alarm allowance do not teach a new
normal section time.

While running forward, Auto reduces its target toward the nearest leading train's
estimated speed as the gap closes, then ramps up again as room opens. The gap uses
configured train length, stopping margin and a time allowance. Occupied-block
braking and reservations still take precedence. This uses RFID checkpoints and
predictions, so it cannot observe a speed change continuously between sensors.

Each train card has an always-visible, bold speed reading in **studs/s**, including
externally controlled trains. **est.** is the current section/load estimate;
**avg.** is the last checkpoint-to-checkpoint measured average. Hover over the
reading for measurement age and connection details. Unmeasured speeds show
**learning** or **no reads**, and overdue external estimates are marked **stale**.
A controlled train commanded to zero shows **0.0 · stopped**; an external train
is never assumed stopped just because the fleet has stopped. Reverse readings are
negative. Hiding extra information does not hide speed.

Auto evaluates speed and following distance every **100 ms throughout each block**.
It previews a learned faster section near the end of the current block, reducing
throttle before the checkpoint while continuing to move. Entering a slower section
ramps throttle upward subject to Auto max. RFID reads update the models and confirmed
positions without inserting a stop command for the update. A moving leader at a
short headway causes a speed reduction while usable room remains. Block/rear
clearance, explicit Stop, zero target and tracking faults still require stops.

Open **Auto timing…** above the map to adjust the following gap (default **3 seconds**)
and power-down threshold. At **2× expected travel time**, the Mac plays a strong
2.7-second alternating-tone siren every three seconds, shows a large flashing
red/yellow banner naming the train and colored block, flashes its card deep red,
and marks the window title **TRAIN ALARM**. Playback runs separately from motor
control and uses full application playback volume while respecting the computer's
output volume and mute setting. **Silence alarm** immediately stops sound for current incidents; it
does not clear the fault, release any block, or silence a new incident. Use
**Test alarm sound** to check the new siren. Playback also stops when incidents
clear or the app closes. A system bell is the fallback if the audio player is unavailable.
The sound follows **App settings → Alarm sound**. A pulsing red circle marks the
checkpoint being awaited for a front/rear timeout, or the affected block for a
collision/clearance fault. Collision circles mark the block, not a measured impact
point. Circles remain when sound is disabled/silenced, follow map zoom/pan, and
disappear when the incident clears.

The requested power-down threshold is **2.5× expected time**. Between 2× and 2.5×,
the train retains power subject to normal block protection. At 2.5×, tracking is
marked uncertain and fleet-controlled trains are held. The external train still
requires its own controller to stop. Genuine checkpoint evidence, verified
repositioning, or marking a physically removed train off track resolves the
corresponding incident; silencing or stopping alone does not manufacture clearance.

For a learned section, expected time follows that section's speed/throttle model.
Changing the commanded throttle changes the remaining allowance without resetting
progress already accumulated. A commanded stationary wait pauses the timer. An
unlearned section gets a conservative allowance of at least its distance divided
by 6.25 studs/s; expected durations have a 2.5-second minimum. External trains use
their learned section times, falling back to their latest measured speed. Because
the app cannot observe an external stop directly, it continues their travel timer.

The train's **i** button lists learned moving times and sample counts. Live history
saves automatically to the current configuration without saving unrelated map/UI
edits; demo learning is temporary. Changed section distances/order invalidate
affected history. **Reset learned times and stop fleet** clears learned models when
you want to retrain after a substantial load or layout change. Geometry edits clear
current learned models. No firmware update is needed for these features.

### Optional rear RFID tag and full-train clearance

Keep the locomotive tag in **Front RFID tags**. Enter a **different UID** in
**Fleet setup → Active trains → Rear RFID tag**, or scan the tag and select **Rear** in
**Sensors → Tags & trains** before assigning it. Set the train's actual length in
Fleet setup; this determines its initial occupied extent and rear-clearance allowance.
Each tag can belong to only one train and one role. A rear tag requires a front tag.

For a rear-tag train, the front crossing a checkpoint occupies the next block while
retaining the block behind it. That previous block opens only after the rear tag
crosses the same checkpoint after the front. Longer trains may occupy several
blocks. Rear reads do not move the front marker, change front-to-front speed samples,
or reset the front's overdue timer. Missed, stale, duplicate or unmatched rear
reads cannot release a block. The sensor occupancy LEDs include retained blocks.

Outstanding rear clearance has its own 2× alarm and 2.5× hold threshold, allowing
at least a whole section's travel time or the configured train length, whichever
is longer. Time intentionally stopped pauses this allowance as well. A missed
rear read keeps its block held even if subsequent front/rear reads arrive elsewhere.
Verify the train and reposition it if clearance evidence was lost; do not mark a
train off track unless it has actually been removed.

Rear-tag clearance currently supports **forward travel**. Reverse commands for a
rear-tag train are refused with an explanation; detected external reverse travel
holds the loop for verification. Trains without a rear tag retain ordinary
single-tag block transfer and existing reverse controls. Demo mode simulates rear
passages using configured train length.

### Following an externally controlled train

In **Train library → Active trains** (or **Saved trains**), set **Controlled by** to **External controller**,
assign its RFID tag, click **Update**, then **Save and apply setup**. Its Bluetooth
hub name can be blank. The app tracks this train's position and occupied block but
never connects to its hub or sends motor/LED commands. Use its own controller to
run or stop it. Switching ownership through setup stops and disconnects the old
fleet connection before the new setup takes effect.

Select that train beside **Match external speed** above the map, then enable the
checkbox. All fleet-controlled trains are forced into **Auto**; their Auto boxes,
manual throttles and the manual target slider are locked while matching. Each
train uses its own learned speed/throttle response to follow the source's predicted
current-section speed (latest checkpoint interval speed until learned), limited by **Auto max**, its throttle limit, and block
protection. The status line shows both measured source speed and capped fleet
target. The usual startup calibration limitations still apply to each follower.

Matching waits for two consecutive timed RFID reads from the same tag. Missing or
invalid speed, an off-track/out-of-service source, or overdue source readings hold
the followers. New valid readings allow Auto to resume. Matching is forward-only;
with three or more checkpoints, reverse passage is detected and holds the fleet.
Two checkpoints cannot establish direction, so use them only for forward travel.
The measurement is an interval average: changes or stops between checkpoints are
not immediately visible. The source shares the 2× alarm and configurable power-down
hold threshold (2.5× by default) described above.

An external train retains its single occupied block and has priority into its next
empty block. A fleet train already in that next block may leave it. There is no
previous-block hold. The app cannot stop an externally controlled train entering
an occupied block; its operator remains responsible for stopping it when needed.

During matching, a train's **Stop** button pauses that follower while leaving Auto
selected. **Resume** returns it to matching once the route is clear and the source
has a valid speed. Changing the selected source does not cancel this pause.
Turning matching off or disabling Auto fleet stops the followers. **STOP FLEET**
cancels matching and stops only fleet-controlled trains; it does not stop the
external train. Block holds retain the existing flashing cards and Auto resumption.

Ownership is stored as `control_mode: "external"` (omitted or `"fleet"` means fleet
control). The source selection is saved in `automation.pace_train_id`, but matching
starts **off** after launch or applying setup. No hub firmware update is needed.
In demo mode an external train moves independently at 10 studs/s while the demo is
running; simulated checkpoint readings feed the same matching logic.

### What block protection does

A block is exactly the track interval between **two consecutive sensors**. A
checkpoint read transfers a single-tag train from the old interval to the new one;
only that one interval is marked occupied. Rear-tag trains retain prior intervals
until rear clearance is confirmed, as described above. Shaping points do not create blocks.

A calibrated train may travel within its own block while the next block is occupied.
Estimated remaining distance and its own speed model progressively reduce throttle
as it approaches the occupied/reserved block. It stops before the checkpoint using
the configured stopping margin plus command-delay allowance, then holds without
creeping. Auto resumes after the next block clears. A clear next block is reserved
in the approach zone so two trains cannot claim it simultaneously. A detected
same-block conflict normally prevents either controlled train receiving a motion
command. The external-train entry exception below lets a moving leader continue
away from an uncontrolled follower while alarming.

An uncalibrated train requires the next block to be clear and reserves it from the
start; if it is occupied, the train waits immediately. Checks apply in both directions
and to manual and automatic commands. Only the stopping margin must fit within
the block; train length does not hold the previous block. A train stopped by block
protection retains its prior throttle for an explicit **Resume** in manual mode.
STOP and STOP ALL cancel that saved request. Prediction never releases confirmed occupancy.

Without a rear tag, this is **locomotive-tag occupancy**: releasing the old block
can leave carriages physically behind. With rear clearance enabled, put the rear
tag at the actual back of the train so its passage represents the final carriage.

Unlocated configured trains hold Auto; manual startup can acquire their positions
from RFID. Moving hubs losing readiness or overdue
checkpoint passages hold movement. Missed-passage faults latch until a new detection
or explicit repositioning. Unidentified RFID tags are noted in the activity log and
Sensors & tags; they do not stop trains or alter occupancy. Assign them live through
train Settings or Sensors & tags if they belong to a train you want to track.
The timeout uses the learned section model when available, otherwise a conservative startup
allowance based on 6.25 studs/s. The requested target is not treated as a measurement
of an uncalibrated train's speed. Very slow/stalled trains may cause a conservative
hold. Network and braking delays still exist; estimated speed and checkpoint
reads do not guarantee physical separation. Check stopping margins with the actual
loads. Trackside ESP32 LEDs continue to show their confirmed block occupancy.

### Force Run override

Each controlled train card has a **Force run** button. It immediately runs at its
held throttle, current throttle, last requested throttle, or configured starting
throttle (in that order). Hover over the button to see the selected value first.
The card turns orange, shows **FORCE RUN** and the signed throttle, and the button
changes to **End force**. The slider remains usable, including during external-speed
matching. This is an individual train override; other trains remain under their
normal controls.

Force Run bypasses software occupancy/reservation checks, approach braking, rear
clearance restrictions, tracking holds, in-service/on-track gates, Auto target/max
and the configured per-train throttle limit. The hardware command range remains
−100% to +100%, and the hub must be connected and ready. Firmware watchdogs and
connection-loss handling still work. Collision and overdue alarms remain active;
the override never clears occupancy or fabricates a checkpoint detection.

**Stop**, **End force**, moving the slider to zero, **STOP FLEET/ALL**, applying
setup, loading a layout, or closing the app cancels the override and sends zero.
A connection loss cancels it too; reconnecting does not restore it. Overrides are
never saved. Ending Force during external-speed matching leaves the train paused
until **Resume**, so Auto cannot immediately restart it. Force Run is disabled
for external trains because the app has no control connection to their motors.

### A train entering behind a controlled train

If any train enters a block already occupied by a moving controlled train,
the controller checks front-checkpoint entry order and direction. When the controlled
train passed the same entry first and both are traveling in the same direction,
the leader keeps running and the collision alarm remains active. A fleet-controlled
follower is held under ordinary protection; an external follower needs its own
controller to stop. The leader's next clear block takes priority over an external
follower's reservation. This also works when the leader's rear tag still retains
an earlier block, provided the earlier front passage is known.

The selected alarm sound and collision banner activate immediately, naming both trains and
the colored block: **POTENTIAL COLLISION**. Both cards show the alarm. Occupancy
continues to include both trains until actual detections establish clearance.
Silencing the sound does not clear the collision or stop the leading train. An
external source's first read, before a new speed can be measured, does not zero an
already-running matching train ahead of it in the shared block.

This exception applies only to a leader already moving away from a confirmed
rear entry behind it. It does not start a parked train, waive an occupied block
ahead, or allow head-on/ambiguous entry. Explicit Stop cancels the exception.
Those situations retain ordinary
holds unless the operator explicitly selects Force Run. Any confirmed shared-block
occupancy now produces a collision alarm, including while Force Run is active.

### Waiting indication on each train

The whole train card flashes **amber** while block protection is holding the train.
With Auto enabled it stays enabled and resumes automatically after the block clears.
In manual mode the card flashes **green** when the block is clear and the hub is ready;
**Resume** restores the throttle from before braking (including reverse direction).
The button is disabled while blocked and rechecks permission when clicked. An explicit
STOP cancels the held request and flashing. These card controls only require restarting
the Mac app, with no hub-program update.

Upload the updated `firmware/city_hub.py` to each City Hub. A train waiting for an
occupied/reserved next block blinks its hub LED amber. Normal commands restore green;
the watchdog/error indication remains red. Heartbeats do not restart the blink
pattern. The map train marker also pulses amber and its status names the train and
colored block it is waiting for.

The hub advertises `features waiting-led`; the controller sends `s0w1` only to hubs
that advertise support. Older hub programs retain ordinary speed/stop commands,
with the waiting indication available on screen. The waiting command always sets
zero motor output and keeps the existing watchdog behavior.

### In/out of service

Unchecking **In service** stops a fleet-controlled train and excludes it from regulation, but retains
its physical occupancy. **Physically off track** is an operator assertion that the
train has been removed: it stops a fleet-controlled train and releases its occupancy/reservations.
Do not use that action for a parked train still on the loop. Physical-removal state
is intentionally not trusted across application restarts.
Neither action sends a stop command to an external train; use its own controller.

A new RFID detection puts a removed train back on the track. Consecutive checkpoint detections in the commanded direction restore **In service**, providing evidence it is moving.
This does not automatically enable its Automatic checkbox or issue a start command.
Service changes from the checkbox are saved; automatic read-based restoration is
runtime state until configuration is next saved.

### Validation of these additions

Automated tests cover per-load speed limits, target reductions, adjacent-block motion,
forward/reverse approach braking, stop/resume without creeping, competing reservations,
same-block conflict stops, unknown/unassigned trains, and stopping-margin constraints.
A two-train simulation exercises different loads and a parked leading train. Hub
simulation checks waiting blink behavior, heartbeat stability, capability negotiation,
zero-only waiting commands, and watchdog stops. Physical stopping remains unverified.
The optional desktop tests (`TRAINSTUFF_GUI_TESTS=1 .venv/bin/python -m unittest discover -s tests -p test_waiting_ui.py -v`)
exercise actual Tk callbacks, Auto preservation through a block stop, flashing card
colors, Resume availability, and restored throttle without connecting to hardware.

A demo-only live GUI test also passed for image loading, Alt-click shaping, saving,
automatic movement, master-zero stopping and off-track occupancy release.

## macOS map clicks and demo starting positions

On macOS, Option-click uses Tk's native `Option` modifier. There is also an
**Add track point** checkbox: enable it, click normally on the map, then disable
it to drag points. Save map positions afterward. Both event paths were tested in
a live Mac GUI with generated events, adding exactly one point per click.

Demo initialization now spaces trains two blocks apart, leaving one empty
sensor-to-sensor interval between trains. If there is insufficient space, extra simulated
trains are marked off track for that session, with a log message; saved train
configuration is unchanged. The demo enables automatic regulation so blocked
trains can resume after space clears. Demo markers show simulated movement even
before the first measured speed is available. Real hardware markers remain
estimates based on detections.

Hold messages name the other train and block responsible. A train's own occupied
blocks and reservations do not block it. Stationary waiting time no longer counts
as a missed-checkpoint journey; the timeout starts from resumed motion or the last
reader detection, whichever is later. 30 automated tests pass, including these
regressions; a live demo test verified two spaced trains advancing through the
current eight-checkpoint map without self-blocking.

Controller widgets, setup fields and map labels now explicitly use black text on a
light theme, independent of the macOS appearance setting.


### Sidebar and reverse-control verification

57 automated tests pass, including signed motor polarity, reverse checkpoint speed,
direction-change sample invalidation, reverse block occupancy/reservations, and
reverse missed-checkpoint stopping. A demo GUI smoke test verified the vertical
sidebar and sliders, independent scrolling, larger map, reverse simulation, and STOP.
Reverse operation with physical trains has not been tested.
