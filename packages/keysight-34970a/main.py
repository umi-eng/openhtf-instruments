"""Compatibility entry point and public re-exports for the package."""

from keysight_34970a import (
    Agilent34970APlug,
    Channel,
    HP34970APlug,
    Keysight34970A,
    Keysight34970APlug,
    MeasurementConfig,
    MeasurementFunction,
    ModuleType,
    ReadingFormat,
    ScanProfile,
    TriggerSource,
)

__all__ = [
    "Agilent34970APlug",
    "Channel",
    "HP34970APlug",
    "Keysight34970A",
    "Keysight34970APlug",
    "MeasurementConfig",
    "MeasurementFunction",
    "ModuleType",
    "ReadingFormat",
    "ScanProfile",
    "TriggerSource",
]


def main() -> None:
    """Print a short usage hint when this module is run directly."""

    print("Import Keysight34970APlug from keysight_34970a to control a 34970A.")


if __name__ == "__main__":
    main()
