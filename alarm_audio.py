"""Asynchronous alarm playback; does not change the computer's volume settings."""
from pathlib import Path
import subprocess
import sys

ALARM_FILE = Path(__file__).with_name('assets') / 'train_alarm.wav'
CHIME_FILE = Path(__file__).with_name('assets') / 'train_chime.wav'
ALARM_CHOICES = {'siren': 'Alternating siren', 'chime': 'Two-tone chime',
                 'bell': 'System bell', 'none': 'No sound (visual alerts remain)'}


class AlarmSound:
    def __init__(self, fallback, mode='siren'):
        self.fallback = fallback
        self.process = None
        self.cancelled = False
        self.mode = mode

    def set_mode(self, mode):
        if mode not in ALARM_CHOICES:
            raise ValueError('Unknown alarm sound')
        self.stop()
        self.mode = mode

    def play(self):
        if self.mode == 'none':
            return
        if self.mode == 'bell':
            self.fallback()
            return
        if self.process is not None:
            status = self.process.poll()
            if status is None:
                return  # One siren at a time, even with several simultaneous incidents.
            if status != 0 and not self.cancelled:
                self.fallback()
        path = CHIME_FILE if self.mode == 'chime' else ALARM_FILE
        if sys.platform != 'darwin' or not path.is_file():
            self.fallback()
            return
        try:
            self.process = subprocess.Popen(['/usr/bin/afplay', '-v', '1', str(path)],
                                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL)
            self.cancelled = False
        except OSError:
            self.process = None
            self.fallback()

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
                self.cancelled = True
            except ProcessLookupError:
                pass


def create_alarm_file(path=ALARM_FILE, chime=False):
    """Build the bundled 2.7-second alternating-tone siren with soft edges."""
    import math
    import struct
    import wave
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rate, duration = 22050, 1.5 if chime else 2.7
    frames = bytearray()
    phase = 0.
    for i in range(round(rate*duration)):
        t = i/rate
        frequency = 740 if int(t/.3) % 2 == 0 else 1040
        phase += 2*math.pi*frequency/rate
        edge = t % .3
        envelope = min(1., edge/.008, (.3-edge)/.008, t/.015, (duration-t)/.025)
        tone = .82*math.sin(phase) - .16*math.sin(3*phase)
        if chime:
            tone = sum(math.sin(2*math.pi*hz*(t-start)) * math.exp(-5*(t-start))
                       * min(1., (t-start)/.01)
                       for start, hz in ((0.,660),(.35,880)) if t >= start) * .55
            envelope = min(1., (duration-t)/.03)
        frames.extend(struct.pack('<h', round(30000*max(0.,envelope)*tone)))
    with wave.open(str(path),'wb') as sound:
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.setframerate(rate)
        sound.writeframes(frames)
