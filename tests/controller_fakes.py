"""Non-Bluetooth test driver. Never registered in the running application."""
import asyncio
from contextlib import contextmanager
from unittest.mock import patch

from train_controllers import (TrainController, ControllerSpec, ControllerField,
                               register_controller)
from train_controllers.registry import _CONTROLLERS


class OutputController(TrainController):
    def __init__(self, train, emit):
        super().__init__(train, emit)
        self.writes = []
        self.sessions = []
        self.closed = 0

    def set_speed(self, speed):
        if self.accepting:
            self.writes.append(speed)
            self.last_speed = speed
            self.record_sent_throttle(speed)

    async def run(self):
        self.sessions.append(dict(self.train['controller_options']))
        self.writes.append(0)
        self.last_speed = 0
        self.accepting = self.session_ready = True
        self.emit(('ready', self.train['id'], True))
        try:
            await asyncio.Event().wait()
        finally:
            self.writes.append(0)
            self.closed += 1
            self.accepting = False


class OtherOutputController(OutputController):
    pass


def validate_output(options):
    if options['channel'] not in ('1', '2'):
        raise ValueError('Output channel must be 1 or 2')


def output_train(tid='track', channel='1'):
    return dict(id=tid, name=tid, tags=[], controller_type='test-output',
                controller_options=dict(port='test-port', channel=channel))


@contextmanager
def registered_outputs():
    with patch.dict(_CONTROLLERS):
        for type_id, label, factory in (('test-output', 'Test track output', OutputController),
                                        ('test-other', 'Test other output', OtherOutputController)):
            register_controller(ControllerSpec(type_id, label, factory,
                fields=(ControllerField('port', 'Connection port'), ControllerField('channel', 'Output channel', '1')),
                validate_options=validate_output,
                endpoint_key=lambda options: (options['port'], options['channel'])))
        yield
