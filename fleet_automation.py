"""Single-block occupancy with estimated approach braking and per-train speed control."""
import math
from block_style import block_name
from map_geometry import STUD_M
from fleet_model import is_external

DEFAULT_MAX_SPEED = 15.
DEFAULT_TARGET_SPEED = 10.
APPROACH_SECONDS = 3.
COMMAND_ALLOWANCE_SECONDS = .5


class Supervisor:
    def __init__(self, config, tracker, motor_only=False):
        self.config, self.tracker = config, tracker
        self.motor_only = motor_only
        self.trains = {t['id']: t for t in config['trains']}
        self.external = {tid for tid, train in self.trains.items() if is_external(train)}
        self.service = {tid: t.get('in_service', True) for tid, t in self.trains.items()}
        # Out of service does not imply physically removed from the track.
        self.on_track = {tid: True for tid in self.trains}
        self.automatic = {tid: False for tid in self.trains}
        self.throttle = {tid: 0 for tid in self.trains}
        self.reservations = {}
        self.faults = {}
        self.reasons = {}
        self.target = 0.0
        self.enabled = False
        self.ready = {tid: True for tid in self.trains}
        self.moving_since = {}
        self.waiting = {tid: False for tid in self.trains}
        self.waiting_for = {}
        self.held_throttle = {}
        self.max_speed = config.get('automation', {}).get('max_speed_studs_s', DEFAULT_MAX_SPEED) * STUD_M
        self.pace_train = None  # Selecting a saved source never starts a live fleet.
        self.operator_paused = set()
        self.stop_time_factor = config.get('automation', {}).get('stop_time_factor', 2.5)
        self.following_seconds = config.get('automation', {}).get('following_seconds', 3.)
        self.alarms = {}
        self.cruise_reasons = {}
        self.forced = {}  # Runtime only; never restored from configuration.
        self.last_requested = {}
        self.rear_intrusions = {}

    def set_pace_train(self, tid):
        if tid is not None and tid not in self.external:
            raise ValueError('Select an externally controlled train as the speed source')
        if self.pace_train is None or tid is None:
            self.operator_paused.clear()
        self.pace_train = tid
        if tid is not None:
            self.enabled = True
            self.force_automatic()

    def force_automatic(self):
        if self.pace_train is not None:
            for tid in self.trains:
                self.automatic[tid] = tid not in self.external

    def select_auto(self, tid):
        if tid in self.external or self.motor_only:
            return
        self.forced.pop(tid, None)
        self.operator_paused.discard(tid)
        self.held_throttle.pop(tid, None)
        self.reasons.pop(tid, None)
        self.enabled = self.automatic[tid] = True

    def auto_issues(self):
        """All actionable Auto prerequisites, without reserving or moving trains."""
        issues = []
        if not self.enabled:
            issues.append('Auto fleet is off')
        target, reason = self.pace_speed()
        if reason:
            issues.append(reason)
        elif target <= 0:
            issues.append('Target speed is zero')
        if len(self.tracker.order) < 2:
            issues.append('Configure at least two checkpoints')
        eligible = [tid for tid in self.trains if tid not in self.external and
                    (self.service[tid] and self.on_track[tid] or self.automatic[tid])]
        if not eligible:
            issues.append('No fleet trains in service on track')
        for tid in eligible:
            reasons = []
            if tid in self.forced:
                reasons.append('override active — click Auto')
            elif not self.automatic[tid]:
                reasons.append('Auto not selected')
            if not self.ready[tid]:
                reasons.append('hub not ready')
            if not self.service[tid]:
                reasons.append('out of service')
            if not self.on_track[tid]:
                reasons.append('off track')
            if tid in self.operator_paused:
                reasons.append('operator paused — Resume or Auto')
            if tid not in self.tracker.last_seen:
                reasons.append('awaiting first RFID / position')
            if tid in self.faults:
                reasons.append(self.faults[tid])
            if self.tracker.distances and min(self.tracker.distances.values()) <= self.trains[tid].get('stop_margin_m', .3):
                reasons.append('stopping margin exceeds block length')
            if not reasons:
                permitted, hold = self.allowed(tid, reserve=False)
                if not permitted:
                    reasons.append(hold)
            if reasons:
                issues.append(self.trains[tid]['name'] + ': ' + '; '.join(reasons))
        for tid in self.external:
            if self.on_track[tid] and tid not in self.tracker.last_seen:
                issues.append(self.trains[tid]['name'] + ': awaiting first RFID / position')
            elif self.on_track[tid] and tid in self.faults:
                issues.append(self.trains[tid]['name'] + ': ' + self.faults[tid])
        return issues

    def pace_speed(self):
        if self.pace_train is None:
            return self.target, ''
        tid = self.pace_train
        name = self.trains[tid]['name']
        if not self.on_track[tid] or not self.service[tid]:
            return 0., f'{name} is off track or out of service'
        if tid not in self.tracker.last_seen:
            return 0., f'Waiting to locate {name}'
        if self.tracker.directions.get(tid, 1) < 0:
            return 0., f'{name} is reversing; speed matching is forward-only'
        speed = self.tracker.speeds.get(tid)
        if not speed or not math.isfinite(speed) or speed <= 0:
            return 0., f'Waiting for two consecutive checkpoint reads from {name}'
        if self.tracker.timing(tid)[0] >= self.stop_time_factor:
            return 0., f'Waiting for a fresh checkpoint from {name}'
        return self.tracker.estimated_speed(tid) or speed, ''

    def pause(self, tid):
        if tid in self.external:
            return
        self.operator_paused.add(tid)
        self.request(tid, 0)

    def force_value(self, tid):
        value = (self.held_throttle.get(tid) or self.throttle[tid] or self.last_requested.get(tid)
                 or self.trains[tid].get('start_throttle',25))
        return max(-100,min(100,int(value)))

    def force_run(self, tid, value=None):
        if tid in self.external:
            return 0
        if not self.ready[tid]:
            self.reasons[tid] = 'Cannot force run: hub not ready'
            return 0
        if value is None:
            value = self.force_value(tid)
        value = max(-100,min(100,int(value)))
        if value == 0:
            return self.request(tid,0)
        self.forced[tid] = value
        self.operator_paused.discard(tid)
        if self.pace_train is None:
            self.automatic[tid] = False
        return self.request(tid,value)

    def end_force(self, tid):
        self.forced.pop(tid,None)
        if self.pace_train is not None:
            self.operator_paused.add(tid)
        else:
            self.automatic[tid] = False
        return self.request(tid,0)

    def observe(self, tid, previous_reader=None, rear=False):
        if tid is None:
            return
        self.on_track[tid] = True
        if rear:
            if (self.faults.get(tid) == 'Rear clearance overdue'
                    and all(p['progress'] < self.stop_time_factor for p in self.tracker.clearance.pending.get(tid, []))):
                self.faults.pop(tid, None)
            return
        current = self.tracker.last_seen.get(tid)
        if current and previous_reader and current[0] == self.tracker.next_reader(previous_reader, self.tracker.directions.get(tid, 1)):
            self.service[tid] = True
        self.faults.pop(tid, None)
        self.observe_entry(tid)

    def remove_from_track(self, tid):
        self.service[tid] = False
        self.automatic[tid] = False
        self.on_track[tid] = False
        self.throttle[tid] = 0
        self.tracker.last_seen.pop(tid, None)
        self.tracker.blocks.pop(tid, None)
        self.tracker.block_entries.pop(tid, None)
        self.tracker.directions.pop(tid, None)
        self.tracker.frozen.pop(tid, None)
        self.tracker.passages.pop(tid, None)
        self.tracker.speeds.pop(tid, None)
        self.tracker.reset_motion(tid, clear_model=True)
        self.tracker.journeys.pop(tid, None)
        self.tracker.clearance.reset(tid)
        self.tracker.rear_seen.pop(tid, None)
        self.tracker.rear_events.pop(tid, None)
        self.faults.pop(tid, None)
        self.alarms = {key: value for key,value in self.alarms.items() if key[0] != tid}
        self.waiting[tid] = False
        self.waiting_for.pop(tid, None)
        self.held_throttle.pop(tid, None)
        self.operator_paused.discard(tid)
        self.forced.pop(tid,None)
        self.rear_intrusions = {pair: state for pair,state in self.rear_intrusions.items() if tid not in pair}
        self.reservations = {b: owner for b, owner in self.reservations.items() if owner != tid}

    def occupied(self):
        blocks = {}
        order = self.tracker.order
        if not order:
            return blocks
        for tid, present in self.on_track.items():
            if not present or tid not in self.tracker.last_seen:
                continue
            current = self.tracker.block_for(tid)
            for block in {current} | self.tracker.clearance.occupied(tid):
                blocks.setdefault(block, set()).add(tid)
        return blocks

    def observe_entry(self, follower):
        """Record any front entering behind a moving fleet train."""
        if follower not in self.tracker.last_seen or len(self.tracker.order) < 2:
            return
        block = self.tracker.block_for(follower)
        gate, entered = self.tracker.last_seen[follower]
        direction = self.tracker.directions.get(follower,1)
        for leader in self.occupied().get(block,set()) - self.external - {follower}:
            if self.throttle[leader]*direction <= 0 or self.tracker.directions.get(leader,1) != direction:
                continue
            if self.tracker.block_for(leader) == block:
                seen = (*self.tracker.last_seen[leader],self.tracker.directions.get(leader,1))
            else:
                seen = self.tracker.block_entries.get(leader,{}).get(block)
            if seen and seen[0] == gate and seen[1] < entered and seen[2] == direction:
                self.rear_intrusions[leader,follower] = dict(direction=direction,entry=(gate,entered))

    def follower_behind(self, leader, follower):
        state = self.rear_intrusions.get((leader,follower))
        if not state or not self.on_track[leader] or not self.on_track[follower]:
            return False
        direction = state['direction']
        if (self.throttle[leader]*direction <= 0 or self.tracker.last_seen.get(follower) != state['entry']
                or self.tracker.directions.get(leader,1) != direction
                or self.tracker.directions.get(follower,1) != direction):
            return False
        return any({leader,follower} <= owners for owners in self.occupied().values())

    def collision_alarms(self):
        incidents = {}
        for block, owners in self.occupied().items():
            if len(owners) < 2:
                continue
            names = ', '.join(self.trains[tid]['name'] for tid in sorted(owners))
            continuing = [self.trains[leader]['name'] for leader in sorted(owners-self.external)
                          if any(self.follower_behind(leader,other) for other in owners - {leader})]
            message = f'POTENTIAL COLLISION: {names} share {block_name(self.config,block)}'
            if continuing:
                message += ' — ' + ', '.join(continuing) + ' continuing ahead'
            for tid in owners:
                incidents[tid,'collision',(block,tuple(sorted(owners))) ] = message
        return incidents

    def _guard(self, tid, manual=False):
        if tid in self.external:
            return False, 'External control — RFID tracking only'
        if not self.ready[tid]:
            return False, 'Hub not ready'
        if not self.service[tid]:
            return False, 'Out of service'
        if not self.on_track[tid]:
            return False, 'Train is physically off track'
        if tid in self.faults:
            return False, f'Tracking uncertain: {self.trains[tid]["name"]}'
        if manual and tid not in self.tracker.last_seen:
            return True, 'Manual locating — awaiting RFID; position not yet known'
        if len(self.tracker.order) < 2:
            return False, 'Configure at least two checkpoints'
        if tid not in self.tracker.last_seen or not self.on_track[tid]:
            return False, 'Place or detect this train at a checkpoint first'
        for other, present in self.on_track.items():
            if not present:
                continue
            if other not in self.tracker.last_seen:
                if not manual:
                    return False, f'Locate {self.trains[other]["name"]} or mark physically off track'
                continue
            if other in self.faults:
                return False, f'Tracking uncertain: {self.trains[other]["name"]}'
        if min(self.tracker.distances.values()) <= self.trains[tid].get('stop_margin_m', .3):
            return False, 'Blocks must exceed the stopping margin'
        for current in {self.tracker.block_for(tid)} | self.tracker.clearance.occupied(tid):
            reason = self._blocked(tid, current, current=True)
            if reason:
                return False, reason
        return True, ''

    def connection_state(self, tid, ready):
        """Recover transport faults without clearing unrelated tracking faults."""
        self.ready[tid] = ready
        if not ready and self.throttle[tid]:
            self.faults.setdefault(tid, 'Connection lost while moving')
            if tid in self.forced:
                self.end_force(tid)
            self.request(tid, 0)
            self.reasons[tid] = 'Connection lost — reconnecting automatically'
        if ready:
            if self.faults.get(tid) == 'Connection lost while moving':
                self.faults.pop(tid)
            if self.reasons.get(tid) == 'Connection lost — reconnecting automatically':
                self.reasons[tid] = 'Reconnected at zero — ready for throttle or Auto'

    def _blocked(self, tid, block, current=False):
        occupied = self.occupied()
        others = occupied.get(block, set()) - {tid}
        if current:
            others = {other for other in others if not self.follower_behind(tid,other)}
        if others:
            names = ', '.join(self.trains[other]['name'] for other in sorted(others))
            return f'Waiting for {names}: {block_name(self.config, block)} occupied'
        owner = self.external_reservations().get(block)
        if owner is None:
            owner = self.reservations.get(block)
            if owner in self.external:
                owner = None  # Recompute external routes from current detections.
        if owner is not None and owner != tid:
            return f'Waiting for {self.trains[owner]["name"]}: {block_name(self.config, block)} reserved'
        return ''

    def external_reservations(self):
        # An external train cannot obey a fleet hold. Give it priority into its
        # next empty block, while allowing a train already there to leave.
        occupied = self.occupied()
        routes = {}
        if len(self.tracker.order) < 2:
            return routes
        for tid in sorted(self.external):
            if self.on_track[tid] and tid in self.tracker.last_seen:
                block = self.tracker.next_reader(self.tracker.block_for(tid), self.tracker.directions.get(tid, 1))
                if not occupied.get(block):
                    # An uncontrolled follower must not reserve the escape block
                    # against the already-moving train it has just followed in.
                    ahead = next((leader for leader in sorted(self.trains.keys()-self.external)
                                  if self.follower_behind(leader,tid)
                                  and self.tracker.block_for(leader) == self.tracker.block_for(tid)),None)
                    routes.setdefault(block, ahead or tid)
        return routes

    def approach(self, tid, direction):
        position = self.tracker.position(tid)
        remaining = (1 - position['fraction'] if direction > 0 else position['fraction'])
        remaining *= self.tracker.distances[position['from']]
        speed = self.tracker.estimated_speed(tid)
        gain = self.tracker.gain_for(tid)
        margin = self.trains[tid].get('stop_margin_m', .3)
        return remaining, speed, gain, margin

    def allowed(self, tid, direction=1, reserve=True, manual=False):
        if direction < 0 and tid in self.tracker.clearance.enabled:
            return False, 'Rear-tag clearance requires forward travel'
        allowed, reason = self._guard(tid, manual=manual)
        if not allowed:
            return False, reason
        if tid not in self.tracker.last_seen:
            return True, reason
        next_block = self.tracker.next_reader(self.tracker.block_for(tid), direction)
        reason = self._blocked(tid, next_block)
        if reason:
            return False, reason
        remaining, speed, gain, margin = self.approach(tid, direction)
        # An uncalibrated train needs the next block reserved immediately;
        # a calibrated train only claims it near the boundary.
        if reserve and (gain is None or remaining <= margin + speed * APPROACH_SECONDS):
            self.reservations[next_block] = tid
        return True, ''

    def automatic_status(self, tid):
        """Current UI explanation without changing block reservations."""
        if tid in self.external:
            return 'External control — use its own controller to stop'
        if tid in self.forced:
            return f'FORCE RUN {self.forced[tid]:+d}% — holds bypassed'
        behind = [self.trains[other]['name'] for other in sorted(self.trains) if other != tid and self.follower_behind(tid,other)]
        if behind:
            return 'Collision risk — continuing ahead of ' + ', '.join(behind)
        if tid in self.operator_paused:
            return 'Stopped by operator — Resume when ready'
        if not self.automatic[tid]:
            if self.waiting[tid] and self.can_resume(tid):
                return f'Track clear — Resume {self.held_throttle[tid]:+d}%'
            return self.reasons.get(tid, '') if self.throttle[tid] == 0 or tid not in self.tracker.last_seen else ''
        if not self.enabled:
            return 'Auto stopped: enable Auto fleet'
        target, pace_reason = self.pace_speed()
        if pace_reason:
            return 'Auto waiting: ' + pace_reason
        if target <= 0:
            return 'Auto stopped: target speed is zero'
        if self.throttle[tid] != 0:
            return self.reasons.get(tid) or self.cruise_reasons.get(tid) or (f"Auto matching {self.trains[self.pace_train]['name']}"
                                            if self.pace_train is not None else 'Auto running')
        if self.waiting[tid]:
            return self.reasons[tid]
        allowed, reason = self.allowed(tid, reserve=False)
        return 'Auto ready — waiting to start' if allowed else f'Auto stopped: {reason}'

    def can_resume(self, tid):
        """Read-only permission check; clicking Resume rechecks before moving."""
        if tid in self.operator_paused:
            target, reason = self.pace_speed()
            return (self.enabled and self.pace_train is not None and target > 0 and not reason
                    and self.allowed(tid, reserve=False)[0])
        value = self.held_throttle.get(tid, 0)
        if not self.waiting[tid] or self.throttle[tid] != 0 or not value:
            return False
        return self.allowed(tid, -1 if value < 0 else 1, reserve=False,
                            manual=not self.automatic[tid] and self.pace_train is None)[0]

    def resume(self, tid):
        if tid in self.operator_paused:
            if self.can_resume(tid):
                self.operator_paused.remove(tid)
            return self.throttle[tid]  # The next Auto tick supplies the paced command.
        value = self.held_throttle.get(tid, 0)
        if not value or not self.can_resume(tid):
            return self.throttle[tid]
        return self.request(tid, value)

    def request(self, tid, value):
        if tid in self.external:
            return 0
        if self.motor_only:
            value = int(value)
            if value and (not self.ready[tid] or not self.service[tid]):
                self.reasons[tid] = 'Hub not ready' if not self.ready[tid] else 'Out of service'
                value = 0
            else:
                self.reasons.pop(tid, None)
            limit = self.trains[tid].get('max_throttle', 70)
            self.throttle[tid] = max(-limit, min(limit, value))
            return self.throttle[tid]
        if value:
            self.last_requested[tid] = int(value)
        if tid in self.operator_paused and value != 0:
            return 0
        held_for = self.waiting_for.pop(tid, None)
        self.waiting[tid] = False
        if value == 0:
            self.forced.pop(tid,None)
            self.rear_intrusions = {pair: state for pair,state in self.rear_intrusions.items() if pair[0] != tid}
            self.held_throttle.pop(tid, None)
            self.reasons.pop(tid, None)
            self.moving_since.pop(tid, None)
            self.throttle[tid] = 0
            self.tracker.set_command(tid, 0)
            self.reservations = {b: owner for b, owner in self.reservations.items() if owner != tid}
            return 0
        if tid in self.forced:
            if not self.ready[tid]:
                self.end_force(tid)
                self.reasons[tid] = 'Force ended: hub not ready'
                return 0
            value = max(-100,min(100,int(value)))
            self.forced[tid] = value
            self.held_throttle.pop(tid,None)
            self.waiting_for.pop(tid,None)
            self.cruise_reasons.pop(tid,None)
            self.reservations = {b:owner for b,owner in self.reservations.items() if owner != tid}
            self.reasons[tid] = f'FORCE RUN {value:+d}% — holds bypassed'
            self.throttle[tid] = value
            self.tracker.set_command(tid,value)
            return value
        direction = -1 if value < 0 else 1
        manual = not self.automatic[tid] and self.pace_train is None
        allowed, reason = self._guard(tid, manual=manual)
        if direction < 0 and tid in self.tracker.clearance.enabled:
            allowed, reason = False, 'Rear-tag clearance requires forward travel'
        value = direction * min(abs(value), self.trains[tid].get('max_throttle', 70))
        if allowed and tid in self.tracker.last_seen:
            next_block = self.tracker.next_reader(self.tracker.block_for(tid), direction)
            reason = self._blocked(tid, next_block)
            remaining, speed, gain, margin = self.approach(tid, direction)
            approach_speed = max(speed, gain * abs(value)) if gain else speed
            if reason:
                self.held_throttle.setdefault(tid, int(value))
                if (held_for == (next_block, direction) or gain is None
                        or remaining <= margin + max(.02, approach_speed * COMMAND_ALLOWANCE_SECONDS)):
                    value = 0
                    self.waiting[tid] = True
                else:
                    # Do not accelerate toward a blocked boundary. The allowed
                    # speed falls with remaining room, independently of load.
                    safe_speed = max(0., (remaining - margin) / APPROACH_SECONDS)
                    limited = min(abs(value), math.floor(safe_speed / gain))
                    value = direction * limited
                    self.waiting[tid] = limited == 0
                    if limited:
                        reason = reason.replace('Waiting for', 'Slowing for')
                if self.waiting[tid]:
                    self.waiting_for[tid] = (next_block, direction)
            elif gain is None or remaining <= margin + approach_speed * APPROACH_SECONDS:
                self.reservations[next_block] = tid
            if not reason:
                self.held_throttle.pop(tid, None)
            # Drop reservations for a previous direction, or after leaving the
            # approach zone. Occupancy itself is released only by a checkpoint.
            self.reservations = {b: owner for b, owner in self.reservations.items()
                                 if owner != tid or (b == next_block and not reason
                                    and (gain is None or remaining <= margin + approach_speed * APPROACH_SECONDS))}
        self.reasons[tid] = reason
        value = value if allowed else 0
        if value == 0:
            self.reservations = {b: owner for b, owner in self.reservations.items() if owner != tid}
        if value != 0 and (self.throttle[tid] == 0 or value * self.throttle[tid] < 0):
            self.moving_since[tid] = self.tracker.clock()
        elif value == 0:
            self.moving_since.pop(tid, None)
        self.throttle[tid] = int(value)
        self.tracker.set_command(tid, int(value))
        return int(value)

    def adaptive_target(self, tid, target):
        """Ease toward the nearest train's speed as the estimated gap closes."""
        self.cruise_reasons.pop(tid, None)
        position = self.tracker.position(tid)
        if not position or position['direction'] < 0:
            return target
        order = self.tracker.order
        origin = order.index(position['from'])
        closest = None
        for other, present in self.on_track.items():
            if other == tid or not present:
                continue
            ahead = self.tracker.position(other)
            if not ahead or ahead['direction'] < 0:
                continue
            steps = (order.index(ahead['from']) - origin) % len(order)
            if steps == 0:
                continue  # Confirmed same-block conflicts are handled by _guard.
            gap = (1-position['fraction']) * self.tracker.distances[position['from']]
            gap += sum(self.tracker.distances[order[(origin+i) % len(order)]] for i in range(1,steps))
            gap += ahead['fraction'] * self.tracker.distances[ahead['from']]
            gap -= self.trains[other].get('length_m', .5)
            if closest is None or gap < closest[0]:
                closest = (gap, other)
        if closest:
            gap, other = closest
            desired_gap = self.trains[tid].get('stop_margin_m', .3) + target * self.following_seconds
            speed = self.tracker.estimated_speed(other)
            # A short desired headway is a reason to slow toward a moving
            # leader's speed, not to cut its follower's motor to zero.
            room_speed = max(0., (gap-self.trains[tid].get('stop_margin_m', .3))/self.following_seconds)
            catch_up_speed = speed + max(0., (gap-desired_gap)/self.following_seconds)
            limited = min(room_speed, catch_up_speed)
            if limited < target:
                self.cruise_reasons[tid] = (f"Auto following {self.trains[other]['name']}: "
                                           f"{block_name(self.config,self.tracker.block_for(other))}")
                return limited
        return target

    def regulation_gain(self, tid):
        """Preview a faster upcoming section while still moving in this block."""
        gain = self.tracker.gain_for(tid)
        position = self.tracker.position(tid)
        if not gain or not position or position['direction'] < 0:
            return gain
        following = self.tracker.next_reader(position['from'])
        sample = self.tracker.learning.get(tid, following, 1)
        next_gain = sample.get('gain', gain) if sample else gain
        if next_gain <= gain:
            return gain  # Ramp up after entering a slower section; respect the speed cap here.
        distance = self.tracker.distances[position['from']]
        remaining = (1-position['fraction'])*distance
        preview = min(distance*.5, max(self.trains[tid].get('stop_margin_m',.3),
                                       self.tracker.estimated_speed(tid)*APPROACH_SECONDS))
        blend = max(0., min(1., 1-remaining/preview))
        return gain + blend*(next_gain-gain)

    def update_timing(self):
        alarms = {}
        for tid, present in self.on_track.items():
            if not present or tid not in self.tracker.last_seen:
                continue
            progress, seconds = self.tracker.timing(tid)
            block = self.tracker.block_for(tid)
            candidates = [('front', progress, seconds, self.tracker.last_seen[tid], block)]
            candidates += [('rear', p['progress'], p['seconds'], (p['reader'],p['began']),
                            next(iter(p['release']), block)) for p in self.tracker.clearance.pending.get(tid, [])]
            for kind, progress, seconds, passage, section in candidates:
                if progress >= 2:
                    key = (tid,kind,passage)
                    expected = seconds/progress
                    alarms[key] = (f"{self.trains[tid]['name']} · {block_name(self.config,section)} · "
                                   f"{'rear clearance' if kind == 'rear' else 'checkpoint'} overdue "
                                   f"({seconds:.0f}s moving; expected {expected:.0f}s)")
                if progress >= self.stop_time_factor:
                    self.faults[tid] = 'Rear clearance overdue' if kind == 'rear' else 'Missed checkpoint'
            if tid in self.tracker.clearance.faults:
                self.faults[tid] = self.tracker.clearance.faults[tid]
                alarms[(tid,'clearance',())] = f"{self.trains[tid]['name']}: {self.faults[tid]}"
        self.alarms = alarms
        self.alarms.update(self.collision_alarms())

    def tick(self):
        if self.motor_only:
            return {tid: self.request(tid, 0) for tid in self.trains if tid not in self.external
                    and self.throttle[tid] and (not self.ready[tid] or not self.service[tid])}
        self.force_automatic()
        self.rear_intrusions = {pair: state for pair,state in self.rear_intrusions.items()
                                if self.follower_behind(*pair)}
        for tid in self.external:
            self.tracker.timing_enabled[tid] = self.on_track[tid] and self.service[tid]
        self.update_timing()
        pace_target, pace_reason = self.pace_speed()
        # Keep only the next block in the owner's current direction.
        self.reservations = {b: tid for b, tid in self.reservations.items()
                             if tid not in self.external and tid in self.tracker.last_seen and self.on_track[tid]
                             and b == self.tracker.next_reader(self.tracker.block_for(tid), -1 if self.throttle[tid] < 0 else self.tracker.directions.get(tid, 1))}
        self.reservations.update(self.external_reservations())
        commands = {}
        for tid, train in self.trains.items():
            if tid in self.external:
                continue
            old = self.throttle[tid]
            cruise_hold = False
            if tid in self.forced:
                wanted = self.forced[tid]
            elif self.enabled and self.automatic[tid]:
                if pace_target <= 0 or tid in self.operator_paused:
                    wanted = (old if pace_reason and self.pace_train is not None
                              and self.follower_behind(tid,self.pace_train) and tid not in self.operator_paused else 0)
                else:
                    target = min(pace_target, self.max_speed)
                    target = self.adaptive_target(tid, target)
                    cruise_hold = target <= 0 and tid in self.cruise_reasons
                    gain = self.regulation_gain(tid)
                    if gain:
                        wanted = max(0, math.floor(target / gain))
                        # Speed decreases take effect immediately; ramp starts
                        # and increases to avoid a sudden full-power departure.
                        wanted = min(wanted, old + 2 if old > 0 else train.get('start_throttle', 25))
                    else:
                        wanted = train.get('start_throttle', 25)
                    cruise_hold = wanted == 0 and tid in self.cruise_reasons
            else:
                wanted = old
                if old == 0:
                    if self.waiting[tid]:
                        direction = -1 if self.held_throttle.get(tid, 0) < 0 else 1
                        _, reason = self.allowed(tid, direction, reserve=False, manual=True)
                        self.reasons[tid] = reason
                    # No new manual request. Keep the last refusal visible until
                    # the operator retries or explicitly stops the train.
                    continue
            actual = self.request(tid, wanted)
            if cruise_hold and actual == 0 and self._guard(tid)[0]:
                self.waiting[tid] = True
                self.held_throttle.setdefault(tid, old or train.get('start_throttle', 25))
                self.reasons[tid] = self.cruise_reasons[tid].replace('Auto following', 'Waiting for')
            if actual != old:
                commands[tid] = actual
        return commands
