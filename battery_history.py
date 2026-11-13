"""Optional, bounded battery and throttle history for the current app session."""
from bisect import bisect_right
from collections import deque
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import time

from battery_monitor import BATTERY_STALE_SECONDS

HISTORY_SECONDS = 4 * 60 * 60


@dataclass(frozen=True)
class HistoryRow:
    at: float
    volts: float
    amps: float
    watts: float
    demand: float | None
    sent: float | None
    segment: int


class BatteryHistory:
    def __init__(self, clock=time.monotonic, wall_clock=time.time):
        self.clock = clock
        self.wall_offset = wall_clock() - clock()
        self.samples = deque(maxlen=8000)
        self.demand = deque(maxlen=60000)
        self.sent = deque(maxlen=60000)
        self.segment = 0
        self.accept_after = float('-inf')

    def prune(self, now):
        cutoff = now - HISTORY_SECONDS
        while self.samples and self.samples[0][0].received_at < cutoff:
            self.samples.popleft()
        for series in (self.demand, self.sent):
            # Preserve the value already in effect at the left edge.
            while len(series) > 1 and series[1][0] <= cutoff:
                series.popleft()

    def command(self, kind, value, at=None):
        at = self.clock() if at is None else at
        if at < self.accept_after:
            return
        series = getattr(self, kind)
        if series and at < series[-1][0]:
            return  # A queued event from before a recording/connection boundary.
        if not series or series[-1][1] != value:
            series.append((at, value))
        self.prune(at)

    def gap(self, at=None):
        self.segment += 1
        self.command('sent', None, at)

    def add(self, reading):
        if (reading.received_at < self.accept_after
                or self.samples and reading.received_at <= self.samples[-1][0].received_at):
            return False
        self.samples.append((reading, self.segment))
        self.prune(reading.received_at)
        return True

    def rows(self, start, end):
        series = [list(self.demand), list(self.sent)]
        times = [[at for at, _ in values] for values in series]
        rows = []
        for reading, segment in self.samples:
            at = reading.received_at
            if not start <= at <= end:
                continue
            values = []
            for points, stamps in zip(series, times):
                index = bisect_right(stamps, at) - 1
                values.append(points[index][1] if index >= 0 else None)
            rows.append(HistoryRow(at, reading.voltage_mv / 1000, reading.current_ma / 1000,
                                   reading.watts, *values, segment))
        return rows

    def steps(self, kind, start, end):
        points = list(getattr(self, kind))
        index = max(0, bisect_right([at for at, _ in points], start) - 1)
        visible = [(max(start, at), value) for at, value in points[index:] if at <= end]
        if visible:
            visible.append((end, visible[-1][1]))
        return visible

    @staticmethod
    def summary(rows):
        watt_seconds = amp_seconds = covered = 0.
        for before, after in zip(rows, rows[1:]):
            dt = after.at - before.at
            if before.segment == after.segment and 0 < dt < BATTERY_STALE_SECONDS:
                covered += dt
                watt_seconds += (before.watts + after.watts) / 2 * dt
                amp_seconds += (before.amps + after.amps) / 2 * dt
        return dict(wh=watt_seconds / 3600, mah=amp_seconds / 3.6, seconds=covered,
                    peak=max((row.watts for row in rows), default=0),
                    average=watt_seconds / covered if covered else None)

    def export_csv(self, path, start, end):
        rows = self.rows(start, end)
        with open(path, 'w', newline='', encoding='utf-8') as output:
            writer = csv.writer(output)
            writer.writerow(['timestamp_utc', 'elapsed_seconds', 'volts', 'amps', 'watts',
                             'controller_demand_percent', 'sent_throttle_percent', 'recording_segment'])
            for row in rows:
                stamp = datetime.fromtimestamp(self.wall_offset + row.at, timezone.utc).isoformat(timespec='milliseconds')
                writer.writerow([stamp, round(row.at - rows[0].at, 3), row.volts, row.amps, row.watts,
                                 row.demand, row.sent, row.segment])
        return len(rows)
