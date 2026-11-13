"""Thread-safe live sensor inventory and identify commands; gate bindings live in fleet.json."""
import copy
import re
import threading
import time
from collections import OrderedDict
from fleet_model import normalize_uid

OFFLINE_SECONDS = 1.5


def sensor_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value):
        raise ValueError('Invalid sensor ID')
    return value


def bind_sensor(config, node, gate_id, replace=False):
    """Return a copy with a one-to-one assignment; blank gate means unassign."""
    sensor_id(node)
    result = copy.deepcopy(config)
    gate = next((r for r in result['readers'] if r['id'] == gate_id), None)
    if gate_id and gate is None:
        raise ValueError('Select an existing gate')
    if gate and gate.get('node') not in (None, '', node) and not replace:
        raise ValueError('This gate already has a sensor. Unassign it first.')
    for reader in result['readers']:
        if reader.get('node') == node:
            reader['node'] = ''
    if gate:
        gate['node'] = node
    return result


class SensorRegistry:
    def __init__(self, config, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.sensors = OrderedDict()
        self.tags = OrderedDict()
        self.command_seq = 0
        self.commands = {}
        self.configure(config)

    def configure(self, config):
        with self.lock:
            self.bindings = {r['node']: r['id'] for r in config['readers'] if r.get('node')}
            self.names = {r['id']: r['name'] for r in config['readers']}
            self.occupied_gates = set()

    def set_occupancy(self, occupied):
        """Mirror confirmed train positions, never predicted map positions or age."""
        with self.lock:
            self.occupied_gates = {gate for gate, trains in occupied.items() if trains}

    def gate_for(self, node):
        with self.lock:
            return self.bindings.get(node)

    def heartbeat(self, event, ip):
        node = sensor_id(event.get('node'))
        if (type(event.get('pn532')) is not bool or type(event.get('uptime_ms')) is not int
                or not 0 <= event['uptime_ms'] < 2**32 or type(event.get('rssi')) is not int
                or not -127 <= event['rssi'] <= 0 or type(event.get('poll_age_ms')) is not int
                or not 0 <= event['poll_age_ms'] < 2**32
                or type(event.get('identify_ack', 0)) is not int
                or event.get('led', 'blink') not in ('blink', 'none')):
            raise ValueError('Invalid sensor status')
        now = self.clock()
        with self.lock:
            old = self.sensors.get(node, {})
            interval = now - old['seen'] if 'seen' in old else None
            rate = old.get('hz', 0)
            if interval and interval > 0:
                rate = .8 * rate + .2 / interval if rate else 1 / interval
            self.sensors[node] = dict(node=node, ip=ip, seen=now, pn532=event['pn532'],
                                      rssi=event['rssi'], uptime_ms=event['uptime_ms'],
                                      poll_age_ms=event['poll_age_ms'], hz=rate,
                                      led=event.get('led', 'blink'),
                                      identify_ack=event.get('identify_ack', 0))
            self.sensors.move_to_end(node)
            # Bound inventory even if a misconfigured device keeps changing its ID.
            while len(self.sensors) > 256:
                discarded, _ = self.sensors.popitem(last=False)
                self.commands.pop(discarded, None)
            command = self.commands.get(node)
            remaining = max(0, int((command['until'] - now) * 1000)) if command else 0
            return {'gate': self.bindings.get(node, ''),
                    'occupied': self.bindings.get(node) in self.occupied_gates,
                    'identify_id': command['id'] if command and remaining else 0,
                    'identify_ms': remaining}

    def identify(self, node):
        with self.lock:
            item = self.sensors.get(node)
            if not item or self.clock() - item['seen'] > OFFLINE_SECONDS:
                raise ValueError('Sensor is offline')
            if item['led'] == 'none':
                raise ValueError('No identification LED configured on this sensor')
            self.command_seq += 1
            self.commands[node] = {'id': self.command_seq, 'until': self.clock() + 10}
            return self.command_seq

    def record_tag(self, event):
        node, uid = sensor_id(event.get('node')), normalize_uid(event.get('uid'))
        with self.lock:
            self.tags[uid] = {'uid': uid, 'node': node, 'seen': self.clock()}
            self.tags.move_to_end(uid)
            while len(self.tags) > 100:
                self.tags.popitem(last=False)

    def snapshot(self):
        now = self.clock()
        with self.lock:
            rows = []
            # Include configured but never-seen sensors so missing hardware is visible.
            for node in sorted(set(self.sensors) | set(self.bindings)):
                item = dict(self.sensors.get(node, {'node': node}))
                age = now - item['seen'] if 'seen' in item else None
                item.update(age=age, online=age is not None and age <= OFFLINE_SECONDS,
                            gate=self.bindings.get(node, ''),
                            occupied=self.bindings.get(node) in self.occupied_gates)
                command = self.commands.get(node)
                item['identifying'] = bool(command and command['until'] > now
                                           and item.get('identify_ack') == command['id'])
                item['identify_pending'] = bool(command and command['until'] > now
                                               and not item['identifying'])
                rows.append(item)
            return rows, [dict(tag, age=now-tag['seen']) for tag in reversed(self.tags.values())]
