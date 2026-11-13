"""Explicit driver registration. Configuration never imports arbitrary code."""
from dataclasses import dataclass
import json
from typing import Callable

from .base import TrainController


@dataclass(frozen=True)
class ControllerCapabilities:
    power_telemetry: bool = False
    waiting_indicator: bool = False
    city_hub_program: bool = False


@dataclass(frozen=True)
class ControllerField:
    key: str
    label: str
    default: str = ''
    required: bool = True
    # Existing Pybricks configurations keep their top-level hub_name.
    legacy_key: str | None = None


@dataclass(frozen=True)
class ControllerSpec:
    type_id: str
    label: str
    factory: Callable
    fields: tuple[ControllerField, ...] = ()
    capabilities: ControllerCapabilities = ControllerCapabilities()
    validate_options: Callable | None = None
    endpoint_key: Callable | None = None

    def options(self, train):
        options = dict(train.get('controller_options', {}))
        for field in self.fields:
            options[field.key] = (train.get(field.legacy_key, field.default) if field.legacy_key
                                  else options.get(field.key, field.default))
        return options

    def validate(self, train):
        if not isinstance(train.get('controller_options', {}), dict):
            raise ValueError('Controller options must be an object')
        options = self.options(train)
        for field in self.fields:
            value = options[field.key]
            if not isinstance(value, str) or (field.required and not value.strip()):
                raise ValueError(f'{self.label}: {field.label} must be nonempty text' if field.required
                                 else f'{self.label}: {field.label} must be text')
        if self.validate_options:
            self.validate_options(options)

    def identity(self, train):
        return self.type_id, json.dumps(self.options(train), sort_keys=True, separators=(',', ':'), allow_nan=False)

    def endpoint(self, train):
        return (self.type_id, self.endpoint_key(self.options(train))) if self.endpoint_key else self.identity(train)


_CONTROLLERS = {}


def register_controller(spec):
    if not isinstance(spec, ControllerSpec) or not spec.type_id or spec.type_id in _CONTROLLERS:
        raise ValueError('Controller type must be nonempty and registered only once')
    _CONTROLLERS[spec.type_id] = spec


def controller_types():
    return tuple(_CONTROLLERS.values())


def controller_spec(train):
    type_id = train.get('controller_type', 'pybricks')
    if not isinstance(type_id, str) or type_id not in _CONTROLLERS:
        raise ValueError(f'Unknown train controller type: {type_id!r}')
    return _CONTROLLERS[type_id]


def create_controller(train, emit):
    spec = controller_spec(train)
    spec.validate(train)
    controller = spec.factory(dict(train), emit)
    if not isinstance(controller, TrainController):
        raise TypeError(f'{spec.type_id} must create a TrainController')
    return controller


def supports(train, capability):
    return (train.get('control_mode', 'fleet') != 'external'
            and getattr(controller_spec(train).capabilities, capability))


def connection_identity(train):
    if train.get('control_mode', 'fleet') == 'external':
        return ('external',)
    return controller_spec(train).identity(train)


def history_identity(train):
    return train['id'], connection_identity(train), train.get('forward_sign', 1)


def controller_label(train):
    return 'External controller' if train.get('control_mode', 'fleet') == 'external' else controller_spec(train).label
