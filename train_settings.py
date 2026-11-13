"""Live edits preserve the fleet, confirmed occupancy, and unrelated runtime state."""
import copy
from fleet_model import all_trains, is_external
from train_controllers import connection_identity


def can_apply_live(current, updated):
    if {k: v for k, v in current.items() if k not in ('trains', 'saved_trains')} != {
            k: v for k, v in updated.items() if k not in ('trains', 'saved_trains')}:
        return False
    before, after = current['trains'], updated['trains']
    return ([(t['id'], is_external(t)) for t in before] ==
            [(t['id'], is_external(t)) for t in after])


def merge_train_edits(current, original, edited):
    """Keep learning, service and photo updates made while a dialog was open."""
    result = copy.deepcopy(current)
    for key in original.keys() | edited.keys():
        if key in ('trains', 'saved_trains') or original.get(key) == edited.get(key):
            continue
        if key in edited:
            result[key] = copy.deepcopy(edited[key])
        else:
            result.pop(key, None)
    originals = {t['id']: t for t in all_trains(original)}
    latest = {t['id']: t for t in all_trains(current)}
    for section in ('trains', 'saved_trains'):
        if section not in edited:
            result.pop(section, None)
            continue
        result[section] = []
        for after in edited[section]:
            before = originals.get(after['id'], {})
            train = copy.deepcopy(latest.get(after['id'], after))
            for key in before.keys() | after.keys():
                if before.get(key) != after.get(key):
                    if key in after:
                        train[key] = copy.deepcopy(after[key])
                    else:
                        train.pop(key, None)
            result[section].append(train)
    return result


def update_train_runtime(supervisor, updated):
    tracker = supervisor.tracker
    old_tags = dict(tracker.tags)
    for new in updated['trains']:
        tid = new['id']
        train = supervisor.trains[tid]
        identity_changed = (connection_identity(train) != connection_identity(new)
                            or train.get('forward_sign', 1) != new.get('forward_sign', 1))
        tags_changed = train.get('tags', []) != new.get('tags', [])
        had_rear = bool(train.get('rear_tag'))
        train.clear()
        train.update(new)
        if tags_changed or identity_changed:
            tracker.passages.pop(tid, None)
            tracker.previous_passage.pop(tid, None)
            tracker.speeds.pop(tid, None)
            tracker.speed_samples.pop(tid, None)
        if identity_changed:
            tracker.speed_per_throttle.pop(tid, None)
            tracker.learning.samples = {k: v for k, v in tracker.learning.samples.items() if k[0] != tid}
        if train.get('rear_tag'):
            tracker.clearance.enabled.add(tid)
            if not had_rear and tid in tracker.last_seen:
                # Retain the approach blocks until the newly enabled rear tag
                # confirms clearance. A settings change never proves clearance.
                tracker.clearance.front(tid, tracker.last_seen[tid][0], {})
        else:
            tracker.clearance.enabled.discard(tid)
            # Existing held blocks survive removal of a rear tag; explicit
            # repositioning/off-track confirmation can release them.
    tracker.tags = {uid: t['id'] for t in updated['trains'] for uid in t['tags']}
    tracker.rear_tags = {t['rear_tag']: t['id'] for t in updated['trains'] if t.get('rear_tag')}
    tracker.tags.update(tracker.rear_tags)
    changed = {uid for uid in old_tags.keys() | tracker.tags.keys() if old_tags.get(uid) != tracker.tags.get(uid)}
    tracker.last_event = {key: when for key, when in tracker.last_event.items() if key[1] not in changed}
