"""Optional rear-tag clearance. Only sensor evidence releases retained blocks."""


class RearClearance:
    def __init__(self, tracker, config):
        self.tracker = tracker
        self.enabled = {t['id'] for t in config['trains'] if t.get('rear_tag')}
        self.trains = {t['id']: t for t in config['trains']}
        self.held = {}
        self.pending = {}
        self.last_front = {}
        self.faults = {}

    def reset(self, tid):
        for state in (self.held, self.pending, self.last_front, self.faults):
            state.pop(tid, None)

    def front(self, tid, reader, event):
        if tid not in self.enabled or len(self.tracker.order) < 2:
            return
        held = self.held.setdefault(tid, set())
        pending = self.pending.setdefault(tid, [])
        previous = self.last_front.get(tid)
        # Initial localization retains the approach block until the rear arrives.
        old = previous if previous is not None else self.tracker.next_reader(reader, -1)
        crossed = set()
        current = old
        while current != reader:
            crossed.add(current)
            current = self.tracker.next_reader(current)
        if previous is None:
            distance = self.tracker.distances[old]
            while distance < self.trains[tid].get('length_m', .5) and len(crossed) < len(self.tracker.order):
                old = self.tracker.next_reader(old, -1)
                crossed.add(old)
                distance += self.tracker.distances[old]
        held.update(crossed | {reader})
        if previous == reader or any(item['reader'] == reader for item in pending):
            # A repeat read is not another lap or clearance proof.
            if previous != reader:
                self.faults[tid] = 'Rear tag missing for a full lap; verify train and reposition'
            return
        pending.append(dict(reader=reader, release=crossed, began=self.tracker.clock(),
                            epoch=event.get('unix_ms'), progress=0., seconds=0.))
        self.last_front[tid] = reader

    def rear(self, tid, reader, event):
        if tid not in self.enabled or tid in self.faults:
            return
        pending = self.pending.get(tid, [])
        match = next((p for p in pending if p['reader'] == reader), None)
        if match is None:
            return  # An early/unmatched rear read cannot clear a block.
        epoch = event.get('unix_ms')
        if epoch is not None and match['epoch'] is not None and epoch <= match['epoch']:
            return
        self.held[tid].difference_update(match['release'])
        pending.remove(match)
        # A retained block may also belong to a later outstanding passage.
        for item in pending:
            self.held[tid].update(item['release'])
        if tid in self.tracker.last_seen:
            self.held[tid].add(self.tracker.block_for(tid))

    def occupied(self, tid):
        return set(self.held.get(tid, ()))
