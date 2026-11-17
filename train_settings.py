"""Live edits preserve the fleet, confirmed occupancy, and unrelated runtime state."""
import copy
from fleet_model import all_trains, is_external
from train_controllers import connection_identity


def can_apply_live(current, updated):
    def layout(config):
        result = {k: v for k, v in config.items() if k not in ('trains', 'saved_trains', 'automation')}
        automation = {k: v for k, v in config.get('automation', {}).items() if k != 'pace_train_id'}
        if automation:
            result['automation'] = automation
        return result
    return layout(current) == layout(updated)


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


def update_train_runtime(supervisor, updated, *, ready=False):
    """Reconcile the roster without replacing the loop or surviving train state.

    The caller stops removed/changed controllers first. Removal means the operator
    has confirmed physical removal; additions await placement or RFID detection.
    """
    tracker = supervisor.tracker
    old_tags = dict(tracker.tags)
    wanted = {train['id'] for train in updated['trains']}
    removed = supervisor.trains.keys() - wanted
    for tid in removed:
        supervisor.remove_from_track(tid)
        for name in ('trains', 'service', 'on_track', 'automatic', 'throttle', 'ready',
                     'moving_since', 'waiting', 'reasons', 'cruise_reasons', 'last_requested'):
            getattr(supervisor, name).pop(tid, None)
        for name in ('previous_passage', 'measurements', 'commands', 'speed_samples', 'timing_enabled'):
            getattr(tracker, name).pop(tid, None)
        tracker.clearance.enabled.discard(tid)
        tracker.learning.samples = {key: value for key, value in tracker.learning.samples.items() if key[0] != tid}

    for new in updated['trains']:
        tid = new['id']
        train = supervisor.trains.get(tid)
        if train is None:
            train = supervisor.trains[tid] = copy.deepcopy(new)
            supervisor.service[tid] = train.get('in_service', True)
            supervisor.on_track[tid] = supervisor.motor_only
            supervisor.automatic[tid] = False
            supervisor.throttle[tid] = 0
            supervisor.waiting[tid] = False
            supervisor.ready[tid] = ready or is_external(train)
            if supervisor.pace_train is not None and not is_external(train):
                supervisor.operator_paused.add(tid)
            for sample in train.get('section_history', []):
                if tracker.learning.valid_geometry(sample):
                    tracker.learning.samples[tid, sample['block'], sample['direction']] = dict(sample)
        ownership_changed = is_external(train) != is_external(new)
        identity_changed = (connection_identity(train) != connection_identity(new)
                            or train.get('forward_sign', 1) != new.get('forward_sign', 1))
        tags_changed = train.get('tags', []) != new.get('tags', [])
        had_rear = bool(train.get('rear_tag'))
        service_changed = train.get('in_service', True) != new.get('in_service', True)
        train.clear()
        train.update(new)
        if service_changed:
            supervisor.service[tid] = new.get('in_service', True)
        if tags_changed or identity_changed:
            tracker.passages.pop(tid, None)
            tracker.previous_passage.pop(tid, None)
            tracker.speeds.pop(tid, None)
            tracker.speed_samples.pop(tid, None)
        if identity_changed:
            tracker.speed_per_throttle.pop(tid, None)
            tracker.learning.samples = {k: v for k, v in tracker.learning.samples.items() if k[0] != tid}
        if ownership_changed:
            supervisor.automatic[tid] = False
            supervisor.throttle[tid] = 0
            supervisor.ready[tid] = ready or is_external(new)
            supervisor.operator_paused.add(tid)
            # Keep confirmed front/rear occupancy, but discard motion estimates
            # from the old control owner. External motion must come from RFID.
            tracker.reset_motion(tid, clear_model=True)
            tracker.journeys.pop(tid, None)
            tracker.commands.pop(tid, None)
            if not is_external(new):
                tracker.commands[tid] = 0
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
    supervisor.external = {tid for tid, train in supervisor.trains.items() if is_external(train)}
    tracker.external = set(supervisor.external)
    tracker.timing_enabled = {tid: tracker.timing_enabled.get(tid, True) for tid in tracker.external}
    tracker.learning.trains = dict(supervisor.trains)
    tracker.clearance.trains = dict(supervisor.trains)
    if removed:
        supervisor.alarms = {key: value for key, value in supervisor.alarms.items() if key[1] != 'collision'}
        supervisor.alarms.update(supervisor.collision_alarms())
    supervisor.config['trains'] = [supervisor.trains[t['id']] for t in updated['trains']]
    if 'saved_trains' in updated:
        supervisor.config['saved_trains'] = copy.deepcopy(updated['saved_trains'])
    else:
        supervisor.config.pop('saved_trains', None)
    if 'automation' in updated:
        supervisor.config['automation'] = copy.deepcopy(updated['automation'])
    else:
        supervisor.config.pop('automation', None)
    tracker.tags = {uid: t['id'] for t in updated['trains'] for uid in t['tags']}
    tracker.rear_tags = {t['rear_tag']: t['id'] for t in updated['trains'] if t.get('rear_tag')}
    tracker.tags.update(tracker.rear_tags)
    changed = {uid for uid in old_tags.keys() | tracker.tags.keys() if old_tags.get(uid) != tracker.tags.get(uid)}
    tracker.last_event = {key: when for key, when in tracker.last_event.items() if key[1] not in changed}
