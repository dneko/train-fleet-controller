"""Nonblocking repeated alarm with per-incident silencing."""
import time


class TrainAlarm:
    def __init__(self, bell, clock=time.monotonic, stop=lambda:None):
        self.bell, self.clock = bell, clock
        self.stop = stop
        self.sounding = False
        self.active = set()
        self.silenced = set()
        self.last_ring = float('-inf')

    def update(self, incidents):
        active = set(incidents)
        new = active - self.active
        self.silenced.intersection_update(active)
        self.active = active
        if not active - self.silenced and self.sounding:
            self.stop()
            self.sounding = False
        if active - self.silenced and (new or self.clock()-self.last_ring >= 3):
            self.bell()
            self.last_ring = self.clock()
            self.sounding = True

    def silence(self):
        self.silenced.update(self.active)
        self.stop()
        self.sounding = False

    def close(self):
        self.stop()
        self.active.clear()
        self.sounding = False
