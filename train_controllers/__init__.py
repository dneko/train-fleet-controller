"""Motor drivers available to the fleet. Register new drivers here at startup."""
from .base import TrainController
from .registry import (ControllerCapabilities, ControllerField, ControllerSpec,
                       connection_identity, controller_label, controller_spec,
                       controller_types, create_controller, history_identity,
                       register_controller, supports)
from .pybricks import PybricksController

register_controller(ControllerSpec(
    type_id='pybricks', label='Pybricks City Hub', factory=PybricksController,
    fields=(ControllerField('hub_name', 'Bluetooth hub name', legacy_key='hub_name'),),
    capabilities=ControllerCapabilities(power_telemetry=True, waiting_indicator=True, city_hub_program=True),
    endpoint_key=lambda options: options['hub_name'],
))
