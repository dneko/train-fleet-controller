"""Per-train, per-direction section history, persisted with the layout."""
import math


class SectionLearning:
    def __init__(self, config):
        self.trains = {t['id']: t for t in config['trains']}
        self.readers = config['readers']
        self.samples = {}
        self.dirty = False
        self.persist = True
        for tid, train in self.trains.items():
            for sample in train.get('section_history', []):
                if self.valid_geometry(sample):
                    self.samples[tid, sample['block'], sample['direction']] = dict(sample)

    def valid_geometry(self, sample):
        for i, reader in enumerate(self.readers):
            if reader['id'] == sample['block']:
                return (sample['next'] == self.readers[(i+1) % len(self.readers)]['id']
                        and math.isclose(sample['distance_m'], reader.get('distance_to_next_m', 1.), rel_tol=.001))
        return False

    def get(self, tid, block, direction=1):
        return self.samples.get((tid, block, direction))

    def record(self, tid, block, direction, seconds, effort):
        if not .5 <= seconds <= 3600:
            return
        reader_index = next(i for i,r in enumerate(self.readers) if r['id'] == block)
        distance = self.readers[reader_index].get('distance_to_next_m', 1.)
        old = self.get(tid, block, direction)
        sample = dict(block=block, direction=direction, distance_m=distance,
                      next=self.readers[(reader_index+1) % len(self.readers)]['id'],
                      seconds=seconds, samples=min(10000, old['samples']+1) if old else 1)
        if old:
            sample['seconds'] = .7 * old['seconds'] + .3 * seconds
        if effort > 0:
            gain = distance / effort
            sample['gain'] = .7 * old['gain'] + .3 * gain if old and 'gain' in old else gain
        self.samples[tid, block, direction] = sample
        self.sync(tid)

    def sync(self, tid):
        if self.persist:
            self.trains[tid]['section_history'] = [dict(value) for key,value in self.samples.items() if key[0] == tid]
        self.dirty = True

    def clear(self):
        self.samples.clear()
        for tid in self.trains:
            self.trains[tid].pop('section_history', None)
        self.dirty = True


def validate_history(train):
    history = train.get('section_history', [])
    if not isinstance(history, list):
        raise ValueError('Section history must be a list')
    keys = set()
    for sample in history:
        if not isinstance(sample, dict):
            raise ValueError('Invalid section history')
        if (not isinstance(sample.get('block'), str) or not isinstance(sample.get('next'), str)
                or type(sample.get('direction')) is not int or sample['direction'] not in (-1, 1)
                or type(sample.get('samples')) is not int or not 1 <= sample['samples'] <= 10000):
            raise ValueError('Invalid section history identity')
        for field in ('seconds', 'distance_m', *(['gain'] if 'gain' in sample else [])):
            value = sample.get(field)
            if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
                raise ValueError('Section history values must be positive and finite')
        key = (sample['block'], sample['direction'])
        if key in keys:
            raise ValueError('Duplicate section history')
        keys.add(key)
