"""Normalized voltage/current telemetry; values are not saved in fleet configuration."""
from dataclasses import dataclass
import time


BATTERY_STALE_SECONDS = 6.0


@dataclass(frozen=True)
class BatteryReading:
    voltage_mv: int
    current_ma: int
    received_at: float
    # City Hub defaults. Other drivers may supply their own thresholds, or None
    # for variable track voltage / a supply where low voltage is not a battery fault.
    low_voltage_mv: int | None = 6000
    critical_voltage_mv: int | None = 5500

    @classmethod
    def from_line(cls, line):
        fields = line.split()
        if (len(fields) != 3 or fields[0] != b'battery'
                or not all(value.isdigit() for value in fields[1:])):
            return None
        voltage, current = map(int, fields[1:])
        # Reject corrupt telemetry without affecting the motor command handshake.
        if not 0 < voltage <= 20000 or not 0 <= current <= 20000:
            return None
        return cls(voltage, current, time.monotonic())

    def age(self, now=None):
        return max(0, (time.monotonic() if now is None else now) - self.received_at)

    def stale(self, now=None):
        return self.age(now) >= BATTERY_STALE_SECONDS

    @property
    def watts(self):
        return self.voltage_mv * self.current_ma / 1_000_000

    @property
    def warning_level(self):
        if self.critical_voltage_mv is not None and self.voltage_mv < self.critical_voltage_mv:
            return 'critical'
        if self.low_voltage_mv is not None and self.voltage_mv < self.low_voltage_mv:
            return 'low'
        return ''

    def warning(self, now=None):
        message = {'critical': 'Battery critical — power loss may be imminent',
                   'low': 'Low battery — battery power is getting low'}.get(self.warning_level, '')
        return ('Last reading: ' if self.stale(now) and message else '') + message

    def card_text(self, now=None):
        return (f'{self.voltage_mv / 1000:.2f} V · {self.current_ma / 1000:.3f} A · {self.watts:.2f} W'
                + (' · stale' if self.stale(now) else ''))

    def details(self, now=None):
        age = self.age(now)
        return (f'Battery: {self.voltage_mv / 1000:.2f} V\n'
                f'Current draw: {self.current_ma / 1000:.3f} A ({self.current_ma} mA)\n'
                f'Power: {self.watts:.2f} W (voltage × current)\n'
                f'Last reading: {age:.0f}s ago' +
                (' — stale; check hub connection/program' if self.stale(now) else '') +
                ('\n' + self.warning(now) if self.warning(now) else '') +
                '\nVoltage varies with motor load and battery type; it is not a charge percentage.')
