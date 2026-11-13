# SPDX-License-Identifier: MIT
# Pybricks communication example credits: see THIRD_PARTY_NOTICES.md.
"""Pybricks City Hub Bluetooth protocol, writer, watchdog and program upload."""
import asyncio
import re
import time

from battery_monitor import BatteryReading
from hub_program import compile_program, source_revision, upload_program, write_command
from hub_discovery import scan_hubs
from .base import TrainController

CHAR_UUID = "c5f50002-8280-46da-89f4-6d8051e4aeef"


class PybricksController(TrainController):
    """One writer per hub; a new speed replaces any unsent slider value."""
    def __init__(self, train, emit):
        super().__init__(train, emit)
        self.ready = asyncio.Event()
        self.pending = None
        self.stop_pending = False
        self.accepting = False
        self.last_speed = 0
        self.last_write = 0
        self.rx = b""
        self.rx_discarding = False
        self.hub_error = None
        self.waiting = False
        self.supports_waiting_led = False
        self.uploading = False
        self.program_stopped = asyncio.Event()
        self.program_id = None
        self.program_touched = False
        self.upload_finished = asyncio.Event()
        self.upload_phase = None
        self.recording_history = False

    @property
    def busy(self):
        return self.uploading

    def debug(self, text):
        self.emit(('program_debug', self.train['id'], text))

    def upload_status(self, text, phase='progress'):
        self.upload_phase = phase
        self.debug(text)
        self.emit(('program_upload', self.train['id'], phase, text))
        if phase != 'progress':
            self.upload_finished.set()

    def set_speed(self, speed):
        if (self.train.get('control_mode', 'fleet') == 'external') or (self.uploading and speed != 0):
            return
        if self.accepting:
            if speed == 0:
                self.stop_pending = True
            self.pending = speed

    def set_waiting(self, waiting):
        if (self.train.get('control_mode', 'fleet') == 'external'):
            return
        waiting = bool(waiting)
        if waiting != self.waiting:
            self.waiting = waiting
            if self.accepting and self.pending is None:
                self.pending = self.last_speed

    def speed_command(self, speed):
        suffix = 'w1' if speed == 0 and self.waiting and self.supports_waiting_led else ''
        return b'\x06' + f's{speed}{suffix}\n'.encode()

    def receive(self, _, data):
        if data and data[0] == 0 and len(data) >= 5:
            flags = int.from_bytes(data[1:5], 'little')
            running = bool(flags & (1 << 6))
            if self.uploading:
                self.debug(f'Hub status: flags=0x{flags:08x}; program running={running}')
            self.program_stopped.clear() if running else self.program_stopped.set()
            return
        if not data or data[0] != 1:
            return
        # stdout can split a line across notifications, or combine several lines.
        # Parse complete lines so battery data cannot swallow or impersonate rdy.
        for char in data[1:]:
            if char == 10:
                if not self.rx_discarding:
                    self.receive_line(self.rx.rstrip(b"\r"))
                self.rx = b""
                self.rx_discarding = False
            elif not self.rx_discarding:
                if len(self.rx) < 256:
                    self.rx += bytes((char,))
                else:
                    self.rx = b""
                    self.rx_discarding = True

    def receive_line(self, line):
        if self.uploading and line:
            self.debug(f'Hub stdout: {line.decode("utf-8", errors="replace")}')
        if line.startswith(b'program '):
            self.program_id = line[8:].decode('ascii', errors='replace')
        elif line == b'features waiting-led':
            self.supports_waiting_led = True
        elif line == b"timeout":
            self.record_sent_throttle(None)
            self.last_speed = 0
            self.pending = 0
            self.stop_pending = True
            self.accepting = False
            self.ready.clear()
            self.emit(("ready", self.train["id"], False))
        elif line == b"rdy":
            self.hub_error = None
            self.ready.set()
        elif line.startswith(b"battery "):
            reading = BatteryReading.from_line(line)
            if reading is not None:
                self.emit(("battery", self.train["id"], reading))
        elif line:
            output = line.decode('utf-8', errors='replace')
            self.emit(("hub_output", self.train["id"], output))
            if line.startswith(b'Traceback ') or re.match(rb'\w*(?:Error|Exception)(?::|$)', line):
                self.record_sent_throttle(None)
                self.hub_error = output
                self.accepting = False
                self.last_speed = 0
                self.pending = 0
                self.stop_pending = True
                self.ready.clear()
                self.emit(("ready", self.train["id"], False))
                self.status(self.waiting_status())

    def waiting_status(self):
        if self.hub_error:
            return f"Hub program error: {self.hub_error} — see activity log"
        return "Bluetooth connected; no reply from program. Press hub button to run city_hub.py; check activity log for errors."

    async def run(self, program=None, *, source=None, device=None):
        if (self.train.get('control_mode', 'fleet') == 'external'):
            return
        from bleak import BleakClient, BleakScanner
        self.session_ready = False
        self.last_connection_status = 'Disconnected'
        uploaded = False
        updating = program is not None or source is not None
        try:
            self.accepting = False
            self.ready.clear()
            self.last_speed = 0
            self.pending = 0
            self.stop_pending = True
            self.rx = b""
            self.rx_discarding = False
            self.hub_error = None
            self.supports_waiting_led = False
            self.waiting = False
            self.program_stopped.clear()
            self.program_id = None
            self.program_touched = False
            self.emit(("battery", self.train["id"], None))
            self.record_sent_throttle(None)
            if device is None:
                self.status("Scanning…")
                if updating:
                    self.upload_status(f"Scanning for active hub {self.train['hub_name']!r}…")
                    found, errors = await scan_hubs([self.train], self.debug)
                    if self.train['id'] in errors:
                        raise RuntimeError(errors[self.train['id']])
                    device = found.get(self.train['id'])
                else:
                    device = await BleakScanner.find_device_by_name(self.train["hub_name"], timeout=10)
            if device is None:
                raise RuntimeError("Hub not found; check name and power")
            if updating:
                self.upload_status(f"Connecting to {self.train['hub_name']!r} "
                                   f"at {getattr(device, 'address', 'unknown address')} (30s timeout)…")
            async with BleakClient(device) as client:
                self.client = client
                if updating:
                    self.upload_status('Bluetooth connected. Checking Pybricks services…')
                    services = client.services
                    self.debug(f'GATT services: {[service.uuid for service in services]!r}; MTU={client.mtu_size}')
                    if services.get_characteristic(CHAR_UUID) is None:
                        raise RuntimeError('No Pybricks command service on this hub. A factory-default LEGO hub '
                                           'needs Pybricks firmware installed using Pybricks Code first.')
                    self.debug('Subscribing to Pybricks status and stdout notifications (5s timeout)…')
                    await asyncio.wait_for(client.start_notify(CHAR_UUID, self.receive), 5)
                else:
                    await client.start_notify(CHAR_UUID, self.receive)
                if updating:
                    try:
                        if source is not None:
                            self.upload_status('Connected. Compiling the City Hub program…')
                            self.debug(f'Source revision: {source_revision(source)}')
                            program = await asyncio.to_thread(compile_program, source)
                            self.debug(f'Compiled program: {len(program)} bytes')
                        await asyncio.wait_for(upload_program(client, self, program), 90)
                    except BaseException:
                        if self.program_touched:
                            self.debug('Upload interrupted; sending stop-program command before disconnecting.')
                            try:
                                await write_command(client, b'\x00')
                            except Exception as exc:
                                self.debug(f'Stop command failed: {type(exc).__name__}: {exc}')
                        raise
                    uploaded = True
                    self.record_sent_throttle(0)
                    self.uploading = False
                    self.upload_status('Uploaded and verified. Train is stopped; resume when ready.', 'complete')
                else:
                    self.status("Bluetooth connected; press hub button to start city_hub.py")
                # Always establish zero before allowing motion on a new connection.
                self.pending = 0
                missed_replies = 0
                while client.is_connected:
                    if self.pending is None and not self.stop_pending:
                        if time.monotonic() - self.last_write >= .5:
                            self.pending = self.last_speed
                        else:
                            await asyncio.sleep(.05)
                            continue
                    try:
                        await asyncio.wait_for(self.ready.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        self.accepting = False
                        self.emit(("ready", self.train["id"], False))
                        self.last_speed = 0
                        self.pending = 0
                        self.stop_pending = False
                        self.status(self.waiting_status())
                        missed_replies += 1
                        if missed_replies >= 2:
                            raise RuntimeError('Hub program did not reply; reconnecting')
                        # Recover a lost rdy notification or watchdog stop through
                        # the sole writer. A probe can only command zero.
                        await asyncio.wait_for(client.write_gatt_char(
                            CHAR_UUID, self.speed_command(0), response=True), timeout=3)
                        self.record_sent_throttle(0)
                        continue
                    missed_replies = 0
                    speed = 0 if self.stop_pending else self.pending
                    self.stop_pending = False
                    self.pending = None if speed == self.pending else self.pending
                    self.ready.clear()
                    await asyncio.wait_for(client.write_gatt_char(
                        CHAR_UUID, self.speed_command(speed), response=True
                    ), timeout=3)
                    self.last_write = time.monotonic()
                    # A timeout/error may arrive during the write. Do not undo
                    # its stop request or publish readiness in that case.
                    if self.stop_pending:
                        self.last_speed = 0
                        continue
                    self.last_speed = speed
                    self.record_sent_throttle(speed)
                    if not self.accepting:
                        self.emit(("ready", self.train["id"], True))
                    self.accepting = True
                    self.session_ready = True
                    self.status(f"Sent {speed}; awaiting next command")
                self.status("Disconnected; reconnecting automatically")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_connection_status = f"Connection error: {exc}"
            self.status(self.last_connection_status)
            if updating and not uploaded:
                raise
        finally:
            self.record_sent_throttle(None)
            self.emit(("battery", self.train["id"], None))
            self.emit(("ready", self.train["id"], False))
            self.accepting = False
            self.last_speed = 0
            self.pending = None
            self.stop_pending = False
            self.ready.clear()
        return uploaded

