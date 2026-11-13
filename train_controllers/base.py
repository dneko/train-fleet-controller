"""Hardware-independent contract for one train's motor connection.

All methods run on the backend's asyncio thread. Speeds are signed hardware
percentages (-100..100); the fleet has already applied forward polarity.
"""
from abc import ABC, abstractmethod
import asyncio
import time


class TrainController(ABC):
    def __init__(self, train, emit):
        self.train = train
        self.emit = emit
        self.accepting = False
        self.last_speed = 0
        self.recording_history = False
        self.session_ready = False
        self.last_connection_status = 'Disconnected'

    @property
    def busy(self):
        """True while an exclusive operation prevents a new control session."""
        return False

    def status(self, text):
        self.emit(('status', self.train['id'], text))

    def record_sent_throttle(self, speed):
        """Call after a successful write, or with None when output is unknown."""
        if self.recording_history:
            value = None if speed is None else speed * self.train.get('forward_sign', 1)
            self.emit(('sent_throttle', self.train['id'], time.monotonic(), value))

    @abstractmethod
    def set_speed(self, speed):
        """Queue a speed; prioritize zero and discard superseded motion requests."""

    def set_waiting(self, waiting):
        """Optional stopped/waiting indicator. Unsupported hardware does nothing."""

    @abstractmethod
    async def run(self):
        """Run one session; establish zero before ready, clean up on cancellation.

        Own exactly one command writer. Emit ('ready', id, True) only after
        establishing zero. On lost communication, discard queued motion and
        close the transport in finally; never resume an old speed on reconnect.
        """

    async def run_reconnecting(self):
        if self.train.get('control_mode', 'fleet') == 'external':
            return
        delay = 2
        while True:
            self.session_ready = False
            self.last_connection_status = 'Disconnected'
            try:
                await self.run()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_connection_status = f'Connection error: {exc}'
            finally:
                self.accepting = False
                self.last_speed = 0
                self.record_sent_throttle(None)
                self.emit(('ready', self.train['id'], False))
                self.emit(('battery', self.train['id'], None))
            self.status(f'{self.last_connection_status}; retrying in {delay}s')
            await asyncio.sleep(delay)
            delay = 2 if self.session_ready else min(15, delay * 2)
