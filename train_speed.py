"""Clearly labeled speed values for every train card."""
from map_geometry import STUD_M


class SpeedWarnings:
    """Warnings only: intentional braking, waits and stopped trains are excluded."""
    def __init__(self):
        self.limited_since = {}

    def warning(self, supervisor, tid):
        tracker = supervisor.tracker
        external = tid in supervisor.external
        moving = (external or (supervisor.throttle[tid] != 0
                  and supervisor.ready[tid] and supervisor.on_track[tid] and supervisor.service[tid]
                  and not supervisor.waiting[tid] and tid not in supervisor.operator_paused
                  and not supervisor.reasons.get(tid) and not supervisor.cruise_reasons.get(tid)
                  and tid not in supervisor.forced))
        moving = moving and supervisor.on_track[tid] and supervisor.service[tid] and tid in tracker.last_seen
        if not moving:
            self.limited_since.pop(tid, None)
            return ''
        now = tracker.clock()
        progress, seconds = tracker.timing(tid)
        if progress >= 1.3 and seconds >= 5:
            return 'Speed warning — slower than expected; awaiting checkpoint'
        sample = tracker.speed_samples.get(tid, {})
        if (sample.get('ratio') is not None and sample['ratio'] < .8 and sample['moving'] >= 5
                and now - sample['at'] <= max(6, tracker.expected_seconds(tid))):
            return ('Speed warning — measured speed below expected for this section' if external else
                    'Speed warning — measured speed below expected for this throttle')
        if external:
            return ''
        target, reason = supervisor.pace_speed()
        limited = (supervisor.enabled and supervisor.automatic[tid] and not reason and target > 0
                   and abs(supervisor.throttle[tid]) >= supervisor.trains[tid].get('max_throttle', 70)
                   and tracker.gain_for(tid) is not None
                   and tracker.estimated_speed(tid) < .8 * min(target, supervisor.max_speed))
        if limited:
            since = self.limited_since.setdefault(tid, now)
            if now - since >= 5:
                return 'Speed warning — cannot maintain target at maximum throttle'
        else:
            self.limited_since.pop(tid, None)
        return ''


def card_speed(supervisor, tid):
    tracker = supervisor.tracker
    if not supervisor.on_track[tid]:
        return '— studs/s · off track', 'Train is marked physically off track.'
    external = tid in supervisor.external
    if not external and tracker.commands.get(tid) == 0:
        return '0.0 studs/s · stopped', 'Motor command is zero; this is not a direct wheel-speed measurement.'
    if tid not in tracker.last_seen:
        return '— studs/s · no reads', 'Awaiting a checkpoint detection.'
    sample = tracker.learning.get(tid,tracker.block_for(tid),tracker.directions.get(tid,1))
    measured = tracker.speeds.get(tid)
    gain = tracker.gain_for(tid)
    known = measured is not None if external else bool(gain and tracker.commands.get(tid)) or measured is not None
    if not known:
        return '— studs/s · learning', 'Speed needs two consecutive timed checkpoint reads, or a learned section model.'
    value = tracker.estimated_speed(tid) * tracker.directions.get(tid,1) / STUD_M
    modeled = bool(sample) if external else bool(gain and tracker.commands.get(tid))
    stale = tid in supervisor.faults or any(key[0] == tid and key[1] != 'collision' for key in supervisor.alarms)
    basis = 'stale' if stale else 'est.' if modeled else 'avg.'
    age = max(0.,tracker.clock()-tracker.last_seen[tid][1])
    details = ('Estimated current speed from the section/load model.' if modeled else
               'Last checkpoint-to-checkpoint average; changes between sensors are not directly measured.')
    if measured is not None:
        details += f'\nLast section average: {measured/STUD_M:.1f} studs/s.'
    details += f'\nLast front read: {age:.1f}s ago.'
    if stale:
        details += '\nOVERDUE: this speed estimate is no longer reliable.'
    return f'{value:.1f} studs/s · {basis}', details
