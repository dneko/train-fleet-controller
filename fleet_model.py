"""Configuration and checkpoint tracking, independent of GUI and hardware."""
import math
import json
import re
import time
from map_geometry import STUD_M, update_distances
from section_learning import validate_history
from power_profile import PowerAlert, PowerSetpoints
from train_controllers import controller_spec


def is_external(train):
    return train.get('control_mode', 'fleet') == 'external'


def all_trains(config):
    """The full collection; only config['trains'] participates in live control."""
    return [*config.get('trains', []), *config.get('saved_trains', [])]


def normalize_uid(value):
    if not isinstance(value, str):
        raise ValueError("Tag UID must be text")
    uid = re.sub(r"[ :\-]", "", value).upper()
    if len(uid) not in (8, 14, 20) or not re.fullmatch(r"[0-9A-F]+", uid):
        raise ValueError("Tag UID must contain 4, 7, or 10 hexadecimal bytes")
    return uid


def load_config(path):
    with open(path) as source:
        config = json.load(source)
    if not isinstance(config, dict):
        raise ValueError("Configuration must be an object")
    if type(config.get('battery_recording', False)) is not bool:
        raise ValueError('Battery history recording must be on/off')
    alerts = config.get('alerts', {})
    if not isinstance(alerts, dict) or alerts.get('sound', 'siren') not in ('siren', 'chime', 'bell', 'none'):
        raise ValueError('Alarm sound must be siren, chime, bell, or none')
    automation = config.get('automation', {})
    if not isinstance(automation, dict):
        raise ValueError('Automation settings must be an object')
    maximum = automation.get('max_speed_studs_s', 15.)
    target = automation.get('target_speed_studs_s', min(10., maximum) if type(maximum) in (int, float) else 10.)
    if (type(maximum) not in (int, float) or not math.isfinite(maximum) or maximum <= 0
            or type(target) not in (int, float) or not math.isfinite(target) or not 0 <= target <= maximum):
        raise ValueError('Auto speeds must be finite: 0 <= target <= maximum, with maximum > 0')
    for field, default, minimum, maximum in (('stop_time_factor', 2.5, 2.1, 10.),
                                            ('following_seconds', 3., 1., 30.)):
        value = automation.get(field, default)
        if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f'{field} must be between {minimum} and {maximum}')
    view = config.get('view', {})
    if not isinstance(view, dict) or any(type(value) is not bool for value in view.values()):
        raise ValueError('View settings must be on/off values')
    hubs, tags, ports = set(), set(), set()
    train_ids = set()
    for section in ("trains", "saved_trains", "readers"):
        entries = config.get(section, [])
        if not isinstance(entries, list):
            raise ValueError(f"{section} must be a list")
        if section != 'saved_trains' or section in config:
            config[section] = entries
        ids = set() if section == 'readers' else train_ids
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError(f"Each {section} entry must be an object")
            for key in ("id", "name"):
                if not isinstance(entry.get(key), str) or not entry[key].strip():
                    raise ValueError(f"Each {section} entry needs {key}")
            if entry["id"] in ids:
                raise ValueError(f"Duplicate {section} ID: {entry['id']}")
            ids.add(entry["id"])
            if section != "readers":
                if 'power_setpoints' in entry:
                    PowerSetpoints.from_config(entry['power_setpoints'])
                if 'power_alarm' in entry:
                    PowerAlert.from_config(entry['power_alarm'])
                if entry.get('control_mode', 'fleet') not in ('fleet', 'external'):
                    raise ValueError('Train control mode must be fleet or external')
                if 'photo' in entry and not isinstance(entry['photo'], str):
                    raise ValueError('Train photo must be an image path')
                if not is_external(entry):
                    controller = controller_spec(entry)
                    controller.validate(entry)
                    endpoint = controller.endpoint(entry)
                    if endpoint in hubs:
                        raise ValueError('Controller addresses must be unique (including saved trains)')
                    hubs.add(endpoint)
                for field, default in (('length_m', .5), ('stop_margin_m', .3)):
                    value = entry.get(field, default)
                    if type(value) not in (int,float) or not math.isfinite(value) or value <= 0:
                        raise ValueError(f'{field} must be a positive number')
                start, maximum = entry.get('start_throttle',25), entry.get('max_throttle',70)
                if type(start) is not int or type(maximum) is not int or not 1 <= start <= maximum <= 100:
                    raise ValueError('Require 1 <= starting throttle <= maximum throttle <= 100')
                if type(entry.get('forward_sign', 1)) is not int or entry.get('forward_sign', 1) not in (-1, 1):
                    raise ValueError('Forward polarity must be +1 or -1')
                if not isinstance(entry.get("tags", []), list):
                    raise ValueError("tags must be a list")
                entry["tags"] = [normalize_uid(tag) for tag in entry.get("tags", [])]
                rear = entry.get('rear_tag', '')
                if not isinstance(rear, str):
                    raise ValueError('Rear tag must be a UID or blank')
                if rear:
                    entry['rear_tag'] = rear = normalize_uid(rear)
                    if not entry['tags']:
                        raise ValueError('A rear tag also requires a front tag')
                validate_history(entry)
                for tag in [*entry["tags"], *([rear] if rear else [])]:
                    if tag in tags:
                        raise ValueError(f"Tag assigned more than once: {tag}")
                    tags.add(tag)
            else:
                port = entry.get("port", "")
                if not isinstance(port, str) or (port and port in ports):
                    raise ValueError("Reader ports must be strings and unique when enabled")
                if port:
                    ports.add(port)
    pace_train = automation.get('pace_train_id', '')
    if not isinstance(pace_train, str) or (pace_train and not any(
            t['id'] == pace_train and is_external(t) for t in config['trains'])):
        raise ValueError('The speed source must be an externally controlled train')
    readers = {r["id"] for r in config["readers"]}
    segments = config.get("segments", [])
    if not isinstance(segments, list):
        raise ValueError("segments must be a list")
    for segment in segments:
        if not isinstance(segment, dict) or segment.get("from") not in readers or segment.get("to") not in readers:
            raise ValueError("Segment endpoints must be configured readers")
        distance = segment.get("distance_m")
        if (isinstance(distance, bool) or not isinstance(distance, (int, float))
                or not math.isfinite(distance) or distance <= 0):
            raise ValueError("Segment distance_m must be positive")
        if segment["from"] == segment["to"]:
            raise ValueError("A timing segment needs two different readers")
    nodes = set()
    for reader in config["readers"]:
        node = reader.get('node', reader['id'])
        if not isinstance(node, str) or (node and (not node.strip() or node in nodes)):
            raise ValueError('Each reader needs a unique ESP32 node ID')
        if node:
            nodes.add(node)
        distance = reader.get('distance_to_next_m', 1.0)
        if type(distance) not in (int, float) or not math.isfinite(distance) or distance <= 0:
            raise ValueError('Distance to next checkpoint must be positive')
        via = reader.get('via', [])
        if not isinstance(via, list) or any(not isinstance(p,dict) or
            any(type(p.get(axis)) not in (int,float) or not math.isfinite(p[axis]) for axis in ('x','y')) for p in via):
            raise ValueError('Track shaping points need numeric x and y coordinates')
        for axis in ("x", "y"):
            value = reader.get(axis, 100)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("Map coordinates must be finite numbers")
    for reader in config['readers']:
        for point in [reader, *reader.get('via', [])]:
            if 'curve_sweep' in point:
                sweep = point['curve_sweep']
                if type(sweep) not in (int,float) or not math.isfinite(sweep) or not 0 < sweep <= 90:
                    raise ValueError('Curve sweep must be greater than zero and at most 90 degrees')
            if 'curve_to_next' in point:
                if type(point['curve_to_next']) is not int or point['curve_to_next'] not in (-1, 1):
                    raise ValueError('Curve direction must be left or right')
                if not config.get('map_m_per_unit'):
                    raise ValueError('Curves require map calibration')
            if 'radius_studs' in point:
                radius, heading = point['radius_studs'], point.get('heading', 0)
                if (type(radius) not in (int, float) or not math.isfinite(radius) or radius <= 0
                        or type(heading) not in (int, float) or not math.isfinite(heading)
                        or type(point.get('turn', 1)) is not int or point.get('turn', 1) not in (-1, 1)):
                    raise ValueError('Curves need a positive radius, finite heading and left/right turn')
                if not config.get('map_m_per_unit'):
                    raise ValueError('Curves require map calibration')
    update_distances(config)
    return config


class Tracker:
    def __init__(self, config, clock=time.monotonic):
        self.clock = clock
        self.tags = {tag: train["id"] for train in config["trains"] for tag in train["tags"]}
        self.rear_tags = {train['rear_tag']: train['id'] for train in config['trains'] if train.get('rear_tag')}
        self.tags.update(self.rear_tags)
        self.rear_seen = {}
        self.rear_events = {}
        self.readers = {reader["id"]: reader["name"] for reader in config["readers"]}
        self.last_seen = {}
        self.last_event = {}
        self.segments = config.get("segments", [])
        self.previous_passage = {}
        self.measurements = {}
        self.node_sequences = {}
        self.reader_nodes = {r["id"]: r.get("node") for r in config["readers"]}

    def ingest(self, line, expected_reader):
        event = json.loads(line)
        if not isinstance(event, dict) or event.get("reader") != expected_reader:
            raise ValueError("Reader identity mismatch")
        if expected_reader not in self.readers:
            raise ValueError("Unknown reader")
        uid = normalize_uid(event.get("uid"))
        event["uid"] = uid
        if "node" in event:
            if event["node"] != self.reader_nodes[expected_reader]:
                raise ValueError("Wrong node for this reader")
            for field in ("seq", "t_ms"):
                if type(event.get(field)) is not int or not 0 <= event[field] < 2**32:
                    raise ValueError("Invalid node timing")
            if not isinstance(event.get("boot"), str) or not 1 <= len(event["boot"]) <= 32:
                raise ValueError("Invalid boot ID")
            epoch = event.get("unix_ms")
            if epoch is not None:
                if type(epoch) is not int or abs(epoch / 1000 - time.time()) > 30:
                    raise ValueError("Reader clock is invalid or event is over 30 seconds old")
            key = event["node"]
            previous_sequence = self.node_sequences.get(key)
            if previous_sequence and previous_sequence[0] == event["boot"] and event["seq"] <= previous_sequence[1]:
                return None
            self.node_sequences[key] = (event["boot"], event["seq"])
        now = self.clock()
        key = (expected_reader, uid)
        previous = self.last_event.get(key)
        # Bound memory and suppress repeat reads without confusing different readers.
        self.last_event = {k: t for k, t in self.last_event.items() if now - t < 1}
        self.last_event[key] = now
        if previous is not None and now - previous < 1:
            return None
        train_id = self.tags.get(uid)
        if train_id and uid in self.rear_tags:
            prior = self.rear_events.get(train_id)
            if (prior and event.get('unix_ms') is not None and prior.get('unix_ms') is not None
                    and event['unix_ms'] <= prior['unix_ms']):
                return None
            self.rear_seen[train_id] = (expected_reader, now)
            self.rear_events[train_id] = event
            return train_id, uid, self.readers[expected_reader]
        if train_id:
            prior = self.previous_passage.get(train_id)
            if (prior and event.get("unix_ms") is not None and prior[0].get("unix_ms") is not None
                    and event["unix_ms"] <= prior[0]["unix_ms"]):
                return None
            self.last_seen[train_id] = (expected_reader, now)
            previous = self.previous_passage.get(train_id)
            self.measurements[train_id] = "Direction/speed unknown (single checkpoint)"
            if previous:
                old, old_time = previous
                candidates = [s for s in self.segments if
                              {s["from"], s["to"]} == {old["reader"], expected_reader}]
                if len(candidates) == 1 and candidates[0].get("unambiguous") is True and now - old_time <= 60:
                    direction = f"{self.readers[old['reader']]} → {self.readers[expected_reader]}"
                    self.measurements[train_id] = direction + "; speed unknown"
                    elapsed = None
                    if uid == old.get("uid"):
                        if event.get("unix_ms") is not None and old.get("unix_ms") is not None:
                            elapsed = (event["unix_ms"] - old["unix_ms"]) / 1000
                        elif (event.get("node") and event.get("node") == old.get("node")
                                and event.get("boot") == old.get("boot")):
                            elapsed = ((event["t_ms"] - old["t_ms"]) % 2**32) / 1000
                    if elapsed is not None and .5 <= elapsed <= 60:
                        speed = candidates[0]["distance_m"] / elapsed
                        self.measurements[train_id] = f"{direction}: ~{speed / STUD_M:.2f} studs/s avg"
            self.previous_passage[train_id] = (event, now)
        return train_id, uid, self.readers[expected_reader]
