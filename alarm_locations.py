"""Group collision warnings by involved trains and overdue warnings by checkpoint."""


def alarm_locations(supervisor):
    locations = {}
    tracker = supervisor.tracker
    for (tid, kind, incident), message in supervisor.alarms.items():
        if tid not in tracker.last_seen:
            continue
        if kind == 'collision':
            location = ('trains', tuple(sorted(incident[1])))
        elif kind == 'rear':
            location = ('checkpoint', incident[0])
        elif kind == 'front':
            block = tracker.block_for(tid)
            reader = tracker.next_reader(block) if tracker.directions.get(tid, 1) > 0 else block
            location = ('checkpoint', reader)
        else:
            location = ('block', tracker.block_for(tid))
        locations.setdefault(location, set()).add(message)
    return locations
