"""Learn normal battery current at similar throttle; report sustained deviations.

The Power* names and configuration keys are retained for compatibility. New
profiles use amps exclusively; voltage and watts remain history/energy metrics.
"""
from collections import deque
from dataclasses import asdict, dataclass
import math
from statistics import median

from battery_monitor import BATTERY_STALE_SECONDS

MIN_SAMPLES = 12
SETTLE_SECONDS = 5
CONFIRM_SAMPLES = 3


@dataclass(frozen=True)
class PowerSetpoints:
    high_percent: float = 10
    low_percent: float = 10
    confirm_samples: int = CONFIRM_SAMPLES

    @classmethod
    def from_config(cls, value=None):
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError('Current alarm setpoints must be an object')
        high, low = value.get('high_percent', 10), value.get('low_percent', 10)
        count = value.get('confirm_samples', CONFIRM_SAMPLES)
        for name, number, maximum in (('High alarm percentage', high, 500), ('Low alarm percentage', low, 100)):
            if type(number) not in (int, float) or not math.isfinite(number) or not 1 <= number <= maximum:
                raise ValueError(f'{name} must be between 1 and {maximum}')
        if type(count) is not int or not 1 <= count <= 10:
            raise ValueError('Consecutive readings must be a whole number from 1 to 10')
        return cls(high, low, count)

    def to_config(self):
        return asdict(self)


@dataclass(frozen=True)
class PowerAlert:
    direction: str
    measured: float
    expected: float
    lower: float
    upper: float
    demand: float
    confirm_samples: int = CONFIRM_SAMPLES
    metric: str = 'amps'

    @property
    def label(self):
        return 'CURRENT ALARM' if self.metric == 'amps' else 'POWER ALARM'

    def formatted(self, value):
        return f'{value:.3f} A' if self.metric == 'amps' else f'{value:.2f} W'

    def to_config(self):
        return asdict(self)

    @classmethod
    def from_config(cls, value):
        if not isinstance(value, dict) or value.get('direction') not in ('higher', 'lower'):
            raise ValueError('Invalid saved power alarm')
        for field in ('measured', 'expected', 'lower', 'upper', 'demand'):
            number = value.get(field)
            if type(number) not in (int, float) or not math.isfinite(number):
                raise ValueError('Saved power alarm values must be finite numbers')
            if field != 'demand' and number < 0:
                raise ValueError('Saved load alarm measurements cannot be negative')
        if not -100 <= value['demand'] <= 100 or not value['lower'] <= value['expected'] <= value['upper']:
            raise ValueError('Invalid saved power alarm limits')
        count = value.get('confirm_samples', CONFIRM_SAMPLES)
        if type(count) is not int or not 1 <= count <= 10:
            raise ValueError('Invalid saved power alarm reading count')
        # Pre-current-monitoring alarms contain watts without a metric marker.
        # Keep them latched and accurately labelled; never reinterpret as amps.
        metric = value.get('metric', 'watts')
        if metric not in ('amps', 'watts'):
            raise ValueError('Saved load alarm metric must be amps or watts')
        return cls(*(value[field] for field in ('direction', 'measured', 'expected', 'lower', 'upper', 'demand')), count, metric)


class PowerProfile:
    def __init__(self, setpoints=None):
        self.setpoints = setpoints or PowerSetpoints()
        self.bands = {}
        self.stable_key = None
        self.stable_since = None
        self.last_at = None
        self.pending = None
        self.pending_count = 0
        self.latched = False
        self.status = 'Learning starts while demand is steady and the train is moving.'

    @staticmethod
    def band(demand):
        # Separate forward/reverse loads and 10-percentage-point demand bands.
        return (1 if demand >= 0 else -1, min(9, int(abs(demand) // 10)))

    def expected(self, key):
        samples = self.bands.get(key, ())
        if len(samples) < MIN_SAMPLES:
            return None
        center = median(samples)
        return (center, center * (1-self.setpoints.low_percent/100),
                center * (1+self.setpoints.high_percent/100))

    def configure(self, setpoints):
        if setpoints != self.setpoints:
            self.setpoints = setpoints
            self.break_observation()
            self.status = 'Setpoints updated; waiting for steady demand.'

    def clear_alarm(self):
        self.latched = False
        self.break_observation()
        self.status = 'Alarm manually cleared; waiting for steady demand.'

    def break_observation(self):
        self.stable_key = None
        self.stable_since = None
        self.pending = None
        self.pending_count = 0
        # Stops, gaps, demand changes and settings changes never clear a latch.

    def observe(self, row, stable_demand=True, alerting=True):
        if self.latched:
            self.status = 'Load alarm latched — inspect the train, then use Clear alarm.'
            return None
        gap = self.last_at is not None and row.at-self.last_at >= BATTERY_STALE_SECONDS
        self.last_at = row.at
        if gap:
            self.break_observation()
        if (row.demand is None or row.sent is None or abs(row.demand) < 10
                or abs(row.demand-row.sent) > 2 or not stable_demand):
            self.break_observation()
            self.status = 'Waiting for steady demand of at least 10% and a matching sent command.'
            return None
        key = self.band(row.demand)
        if self.stable_key != (row.segment, key):
            self.break_observation()
            self.stable_key = (row.segment, key)
            self.stable_since = row.at
        if row.at-self.stable_since < SETTLE_SECONDS:
            self.status = 'Letting the motor load settle before learning or checking current.'
            return None
        samples = self.bands.setdefault(key, deque(maxlen=120))
        expected = self.expected(key)
        if expected is None:
            samples.append(row.amps)
            self.status = f'Learning current at this demand: {len(samples)}/{MIN_SAMPLES} baseline readings.'
            return None
        center, lower, upper = expected
        side = 'higher' if row.amps > upper else 'lower' if row.amps < lower else None
        self.status = f'Expected {center:.3f} A; alarm below {lower:.3f} A or above {upper:.3f} A at this demand.'
        if side:
            self.pending_count = self.pending_count+1 if side == self.pending else 1
            self.pending = side
            count = self.setpoints.confirm_samples
            self.status += f' Current is {side}: {min(count, self.pending_count)}/{count} checks.'
            # Suspect readings never become the normal baseline.
            if alerting and self.pending_count >= count:
                self.latched = True
                return PowerAlert(side, row.amps, center, lower, upper, row.demand, count)
        else:
            self.pending = None
            self.pending_count = 0
            samples.append(row.amps)
        return None
