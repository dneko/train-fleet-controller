"""Bidirectional loop state. Predictions never manufacture checkpoint detections."""
import time
from fleet_model import Tracker, is_external
from section_learning import SectionLearning
from train_clearance import RearClearance


def motor_speed(train, forward_speed):
    return max(-100, min(100, int(forward_speed))) * train.get('forward_sign', 1)


class LoopTracker(Tracker):
    def __init__(self, config, clock=time.monotonic):
        super().__init__(config, clock)
        self.order = [r['id'] for r in config['readers']]
        self.distances = {r['id']: r.get('distance_to_next_m', 1.0) for r in config['readers']}
        self.speeds = {}
        self.speed_samples = {}
        self.commands = {}
        self.passages = {}
        self.frozen = {}
        self.directions = {}
        self.blocks = {}
        self.block_entries = {}  # Accepted front passages; used to establish entry order.
        self.motion = {}
        self.effort = {}
        self.speed_per_throttle = {}
        self.external = {t['id'] for t in config['trains'] if is_external(t)}
        self.learning = SectionLearning(config)
        self.clearance = RearClearance(self, config)
        self.moving_seconds = {}
        self.journeys = {}
        self.timing_enabled = {tid: True for tid in self.external}

    def reset_motion(self, tid, clear_model=False):
        self.motion.pop(tid, None)
        self.effort.pop(tid, None)
        self.moving_seconds.pop(tid, None)
        if clear_model:
            self.speed_per_throttle.pop(tid, None)

    def gain_for(self, tid, block=None, direction=None):
        if tid not in self.last_seen:
            return self.speed_per_throttle.get(tid)
        sample = self.learning.get(tid, block or self.block_for(tid),
                                   direction or self.directions.get(tid, 1))
        return sample.get('gain', self.speed_per_throttle.get(tid)) if sample else self.speed_per_throttle.get(tid)

    def expected_seconds(self, tid, command=None):
        block = self.block_for(tid)
        distance = self.distances[block]
        sample = self.learning.get(tid, block, self.directions.get(tid, 1))
        command = self.commands.get(tid, 0) if command is None else command
        gain = self.gain_for(tid)
        if tid in self.external:
            seconds = sample['seconds'] if sample else distance / (self.speeds.get(tid) or .05)
        elif gain and command:
            seconds = distance / (gain * abs(command))
            if not sample:
                seconds = max(seconds, distance / .05)
        else:
            seconds = sample['seconds'] if sample else distance / .05
        return max(2.5, seconds)

    def estimated_speed(self, tid):
        command = self.commands.get(tid)
        if command == 0:
            return 0.
        gain = self.gain_for(tid)
        if gain is not None and command is not None:
            return gain * abs(command)
        if tid in self.external and tid in self.last_seen:
            sample = self.learning.get(tid, self.block_for(tid), self.directions.get(tid, 1))
            if sample:
                return self.distances[self.block_for(tid)] / sample['seconds']
        return self.speeds.get(tid, 0.)

    def _anchor(self, tid):
        signature = (self.last_seen[tid], self.block_for(tid), self.directions.get(tid, 1))
        anchor = self.motion.get(tid)
        if anchor is None or anchor[0] != signature:
            fraction = self.frozen.get(tid, 0. if signature[2] > 0 else 1.)
            return signature, self.last_seen[tid][1], fraction
        return anchor

    def _advance(self, tid):
        if tid not in self.last_seen:
            return
        signature, began, fraction = self._anchor(tid)
        now = self.clock()
        elapsed = max(0., now - began)
        distance = elapsed * self.estimated_speed(tid)
        fraction += signature[2] * distance / self.distances[signature[1]]
        self.motion[tid] = (signature, now, max(0., min(1., fraction)))
        self.effort[tid] = self.effort.get(tid, 0.) + elapsed * abs(self.commands.get(tid, 0))
        moving = self.timing_enabled.get(tid, False) if tid in self.external else bool(self.commands.get(tid))
        self.moving_seconds[tid] = self.moving_seconds.get(tid, 0.) + (elapsed if moving else 0.)
        journey = self.journeys.get(tid)
        if journey is None or journey['signature'] != signature:
            journey = dict(signature=signature, progress=0., seconds=0.)
            self.journeys[tid] = journey
        if moving:
            expected = self.expected_seconds(tid)
            journey['progress'] += elapsed / expected
            journey['seconds'] += elapsed
            # Rear clearance gets a full section's allowance, even for long trains.
            for pending in self.clearance.pending.get(tid, []):
                length = self.learning.trains[tid].get('length_m', .5)
                clearance_time = expected * max(1., length / self.distances[self.block_for(tid)])
                pending['progress'] += elapsed / clearance_time
                pending['seconds'] += elapsed

    def timing(self, tid):
        self._advance(tid)
        journey = self.journeys.get(tid, {})
        return journey.get('progress', 0.), journey.get('seconds', 0.)

    def next_reader(self, reader, direction=1):
        return self.order[(self.order.index(reader) + direction) % len(self.order)]

    def block_for(self, tid):
        return self.blocks.get(tid, self.last_seen[tid][0])

    def ingest(self, line, expected_reader):
        import json
        event = json.loads(line)
        for train in self.last_seen:
            self._advance(train)
        old_seen = dict(self.last_seen)
        old_passage = dict(self.previous_passage)
        result = super().ingest(line, expected_reader)
        if not result or not result[0]:
            return result
        tid = result[0]
        event['uid'] = result[1]
        if event['uid'] in self.rear_tags:
            self.clearance.rear(tid, expected_reader, event)
            return result
        previous = self.passages.get(tid)
        if previous and previous['reader'] == expected_reader:
            self.last_seen[tid] = old_seen[tid]
            self.previous_passage[tid] = old_passage[tid]
            return None
        self.frozen.pop(tid, None)
        direction = self.directions.get(tid, 1)
        if tid in self.external and previous and len(self.order) > 2:
            if expected_reader == self.next_reader(previous['reader'], 1):
                direction = 1
            elif expected_reader == self.next_reader(previous['reader'], -1):
                direction = -1
            self.directions[tid] = direction
        if tid in self.clearance.enabled and direction < 0:
            self.clearance.faults[tid] = 'Rear-tag train reversed; verify both ends and reposition'
            self.clearance.held[tid] = set(self.order)
        self.blocks[tid] = expected_reader if direction > 0 else self.next_reader(expected_reader, -1)
        self.block_entries.setdefault(tid, {})[self.blocks[tid]] = (expected_reader,self.clock(),direction)
        if direction > 0:
            self.clearance.front(tid, expected_reader, event)
        self.speeds.pop(tid, None)
        if previous and len(self.order) >= 2:
            old = previous
            if self.next_reader(old['reader'], direction) == expected_reader:
                start, end = old.get('unix_ms'), event.get('unix_ms')
                if start is not None and end is not None and old.get('uid') == event.get('uid'):
                    elapsed = (end - start) / 1000
                    if .5 <= elapsed <= 600:
                        distance = self.distances[old['reader'] if direction > 0 else expected_reader]
                        self.speeds[tid] = distance / elapsed
                        effort = self.effort.get(tid, 0.)
                        moving = self.moving_seconds.get(tid, elapsed) if tid not in self.external else elapsed
                        block = old['reader'] if direction > 0 else expected_reader
                        gain = self.gain_for(tid, block, direction)
                        learned = self.learning.get(tid, block, direction)
                        # Compare against the model BEFORE this passage teaches
                        # it a new speed. Integrated effort excludes planned stops.
                        self.speed_samples[tid] = dict(at=self.clock(), moving=moving,
                            ratio=(learned['seconds'] / elapsed if tid in self.external and learned else
                                   distance / (gain * effort) if gain and effort > 0 else None))
                        if effort > 0 and self.journeys.get(tid, {}).get('progress', 0.) < 2:
                            # Integrate throttle, excluding stopped time. Each train
                            # learns its own response as load/battery conditions change.
                            gain = distance / effort
                            previous_gain = self.speed_per_throttle.get(tid, gain)
                            self.speed_per_throttle[tid] = .5 * previous_gain + .5 * gain
                        # Do not teach a missed-reader/stall alarm as normal performance.
                        if self.journeys.get(tid, {}).get('progress', 0.) < 2:
                            self.learning.record(tid, old['reader'] if direction > 0 else expected_reader,
                                                 direction, moving, effort)
        self.passages[tid] = event
        self.reset_motion(tid)
        self.journeys.pop(tid, None)
        return result

    def set_command(self, tid, value):
        if tid in self.external:
            return  # Fleet stop/display updates must not freeze an external train.
        # Integrate the old command before changing speed. Braking and resuming
        # must not jump the prediction or count stationary time as travel.
        previous = self.commands.get(tid)
        self._advance(tid)
        position = self.position(tid)
        direction = -1 if value < 0 else 1
        reversing = value != 0 and direction != self.directions.get(tid, 1)
        self.commands[tid] = value
        if value != 0:
            self.directions[tid] = direction
        if reversing or (previous != value and value == 0):
            if position:
                self.frozen[tid] = position['fraction']
            self.speeds.pop(tid, None)
            self.speed_samples.pop(tid, None)
        if reversing:
            self.passages.pop(tid, None)
            self.reset_motion(tid, clear_model=True)
            self.journeys.pop(tid, None)
        if position:
            signature = (self.last_seen[tid], self.block_for(tid), self.directions.get(tid, 1))
            self.motion[tid] = (signature, self.clock(), position['fraction'])

    def position(self, tid):
        if tid not in self.last_seen or len(self.order) < 2:
            return None
        reader, detected = self.last_seen[tid]
        age = max(0, self.clock() - detected)
        speed = self.speeds.get(tid)
        block = self.block_for(tid)
        direction = self.directions.get(tid, 1)
        _, began, fraction = self._anchor(tid)
        estimated_speed = self.estimated_speed(tid)
        fraction += direction * max(0., self.clock() - began) * estimated_speed / self.distances[block]
        overdue = bool(estimated_speed and (fraction >= 1 if direction > 0 else fraction <= 0))
        fraction = max(0., min(1., fraction))
        return {'from': block, 'to': self.next_reader(block), 'fraction': fraction,
                'direction': direction,
                'speed': speed, 'age': age, 'overdue': overdue,
                'estimated_speed': estimated_speed,
                'estimated': bool(estimated_speed)}
