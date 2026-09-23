"""OpenHTF support for the HP/Agilent/Keysight 34970A."""

from .exceptions import (
    InvalidChannelError,
    Keysight34970AError,
    ScpiError,
    UnsupportedOperationError,
)
from .models import (
    Channel,
    MeasurementConfig,
    MeasurementFunction,
    ReadingFormat,
    ScanProfile,
    ScanReading,
    TriggerSource,
)
from .modules import ModuleSpec, ModuleType, supported_module_models
from .plug import Agilent34970APlug, HP34970APlug, Keysight34970A, Keysight34970APlug

__all__ = [
    "Agilent34970APlug",
    "Channel",
    "HP34970APlug",
    "InvalidChannelError",
    "Keysight34970A",
    "Keysight34970AError",
    "Keysight34970APlug",
    "MeasurementConfig",
    "MeasurementFunction",
    "ModuleSpec",
    "ModuleType",
    "ReadingFormat",
    "ScanProfile",
    "ScanReading",
    "ScpiError",
    "TriggerSource",
    "UnsupportedOperationError",
    "supported_module_models",
]
