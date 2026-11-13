"""LAN HTTP receiver for RFID detections and 10 Hz sensor status."""
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import queue
import time
from fleet_model import normalize_uid
from sensor_registry import sensor_id


def create_server(host, port, token, readers, events, registry=None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def reply(self, status, data=None, headers=None):
            body = json.dumps(data or {}).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            for key, value in (headers or {}).items():
                self.send_header(key, str(value))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.connection.settimeout(2)
            if self.path not in ('/events', '/status'):
                self.send_error(404)
                return
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                self.send_error(401)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1024:
                    raise ValueError('Invalid size')
                event = json.loads(self.rfile.read(size))
                if not isinstance(event, dict):
                    raise ValueError('Invalid payload')
                if self.path == '/status':
                    if registry is None:
                        self.reply(503)
                        return
                    result = registry.heartbeat(event, self.client_address[0])
                    self.reply(200, result, {
                        'X-Identify-Id': result['identify_id'],
                        'X-Identify-Ms': result['identify_ms'],
                        'X-Segment-Occupied': int(result['occupied']),
                    })
                    return
                if registry is not None:
                    node = sensor_id(event.get('node'))
                    event['uid'] = normalize_uid(event.get('uid'))
                    if (not isinstance(event.get('boot'), str) or not 1 <= len(event['boot']) <= 32
                            or any(type(event.get(k)) is not int or not 0 <= event[k] < 2**32
                                   for k in ('seq', 't_ms'))):
                        raise ValueError('Invalid event identity')
                    registry.record_tag(event)
                    # Mac owns the binding; a sensor cannot choose or spoof its gate.
                    gate = registry.gate_for(node)
                    if not gate:
                        self.reply(202, {'registered': False, 'message': 'Assign sensor to a gate'})
                        return
                    event['reader'] = gate
                if (not isinstance(event.get('reader'), str) or event['reader'] not in readers
                        or not isinstance(event.get('node'), str)):
                    raise ValueError('Unknown reader')
                # All gate detections use the Mac's receipt time, never an NTP clock.
                event['unix_ms'] = time.time_ns() // 1_000_000
                event['time_source'] = 'controller_received'
                events.put_nowait(('rfid', event['reader'], json.dumps(event).encode('utf-8')))
            except (ValueError, TypeError, OSError):
                self.send_error(400)
                return
            except queue.Full:
                self.send_error(503)
                return
            self.reply(202)

        def log_message(self, *_):
            pass
    return ThreadingHTTPServer((host, port), Handler)
