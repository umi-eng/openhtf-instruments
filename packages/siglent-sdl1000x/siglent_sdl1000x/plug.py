"""OpenHTF plug for Siglent SDL1000X-series electronic loads."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

import pyvisa
from openhtf.plugs import BasePlug


class LoadMode(str, Enum):
    """Static operating modes supported by the SDL1000X series."""

    CC = "CURRent"
    CV = "VOLTage"
    CP = "POWer"
    CR = "RESistance"
    LED = "LED"

    @classmethod
    def coerce(cls, value: LoadMode | str) -> LoadMode:
        if isinstance(value, cls):
            return value
        key = str(value).strip().strip('"').upper()
        aliases = {
            "CC": cls.CC,
            "CUR": cls.CC,
            "CURRENT": cls.CC,
            "CV": cls.CV,
            "VOLT": cls.CV,
            "VOLTAGE": cls.CV,
            "CP": cls.CP,
            "POW": cls.CP,
            "POWER": cls.CP,
            "CR": cls.CR,
            "RES": cls.CR,
            "RESISTANCE": cls.CR,
            "LED": cls.LED,
        }
        try:
            return aliases[key]
        except KeyError as exc:
            raise ValueError(f"unsupported load mode: {value!r}") from exc


@dataclass(frozen=True)
class LoadMeasurement:
    """Real-time readings returned by the load."""

    voltage: float
    current: float
    power: float
    resistance: float


_LEVEL_COMMANDS = {
    LoadMode.CC: ("SOUR:CURR", "SOUR:CURR?"),
    LoadMode.CV: ("SOUR:VOLT", "SOUR:VOLT?"),
    LoadMode.CP: ("SOUR:POW", "SOUR:POW?"),
    LoadMode.CR: ("SOUR:RES", "SOUR:RES?"),
}
_MEASURE_COMMANDS = {
    "voltage": "MEAS:VOLT?",
    "current": "MEAS:CURR?",
    "power": "MEAS:POW?",
    "resistance": "MEAS:RES?",
}


class SiglentSDL1000XPlug(BasePlug):
    """Control an SDL1000X-series electronic load through a PyVISA resource.

    ``resource_name`` is a VISA resource string, or can be supplied through
    ``SIGLENT_SDL1000X_RESOURCE``. A resource can be injected for testing.
    """

    def __init__(
        self,
        resource_name: str | None = None,
        *,
        visa_library: str = "",
        resource_manager: Any | None = None,
        resource: Any | None = None,
        resource_kwargs: Mapping[str, Any] | None = None,
        timeout_ms: int = 5000,
        read_termination: str = "\n",
        write_termination: str = "\n",
    ) -> None:
        super().__init__()
        resource_name = (
            resource_name
            if resource_name is not None
            else os.environ.get("SIGLENT_SDL1000X_RESOURCE")
        )
        if resource is None and resource_name is None:
            raise ValueError(
                "resource_name is required (or set SIGLENT_SDL1000X_RESOURCE)"
            )

        self._resource_manager = resource_manager
        self._owns_resource_manager = resource_manager is None and resource is None
        self._resource: Any = resource
        if self._resource is None:
            assert resource_name is not None
            self._resource_manager = resource_manager or pyvisa.ResourceManager(
                visa_library
            )
            try:
                self._resource = self._resource_manager.open_resource(
                    resource_name, **(resource_kwargs or {})
                )
            except Exception:
                if self._owns_resource_manager:
                    self._resource_manager.close()
                raise
        self.resource_name = resource_name or getattr(
            self._resource, "resource_name", "injected"
        )
        for attribute, value in (
            ("timeout", timeout_ms),
            ("read_termination", read_termination),
            ("write_termination", write_termination),
        ):
            try:
                setattr(self._resource, attribute, value)
            except (AttributeError, TypeError, ValueError):
                pass

    @property
    def resource(self) -> Any:
        """The underlying PyVISA resource."""

        return self._resource

    def write(self, command: str) -> None:
        """Send a SCPI command."""

        self._resource.write(command)

    def query(self, command: str) -> str:
        """Send a SCPI query and return its stripped text response."""

        if hasattr(self._resource, "query"):
            return str(self._resource.query(command)).strip()
        self._resource.write(command)
        return str(self._resource.read()).strip()

    def identify(self) -> str:
        """Return the instrument's IEEE-488 identity string."""

        return self.query("*IDN?")

    @property
    def mode(self) -> LoadMode:
        """Return the configured static load mode."""

        return LoadMode.coerce(self.query("SOUR:FUNC?"))

    def set_mode(self, mode: LoadMode | str) -> None:
        """Select CC, CV, CP, CR, or LED static operation."""

        self.write(f"SOUR:FUNC {LoadMode.coerce(mode).value}")

    def set_level(self, value: float, mode: LoadMode | str | None = None) -> None:
        """Set the selected mode's static setpoint, in SI units."""

        selected_mode = self.mode if mode is None else LoadMode.coerce(mode)
        commands = _LEVEL_COMMANDS.get(selected_mode)
        if commands is None:
            raise ValueError(f"{selected_mode.name} mode has no scalar setpoint")
        if isinstance(value, bool):
            raise TypeError("setpoint must be a non-negative finite number")
        try:
            numeric_value = float(value)
        except (OverflowError, TypeError, ValueError) as exc:
            raise TypeError("setpoint must be a non-negative finite number") from exc
        if not math.isfinite(numeric_value) or numeric_value < 0:
            raise ValueError("setpoint must be a non-negative finite number")
        self.write(f"{commands[0]} {numeric_value}")

    def level(self, mode: LoadMode | str | None = None) -> float:
        """Read the configured mode's static setpoint."""

        selected_mode = self.mode if mode is None else LoadMode.coerce(mode)
        commands = _LEVEL_COMMANDS.get(selected_mode)
        if commands is None:
            raise ValueError(f"{selected_mode.name} mode has no scalar setpoint")
        return float(self.query(commands[1]))

    @property
    def input_enabled(self) -> bool:
        """Whether the electronic load input is enabled."""

        response = self.query("SOUR:INP?").strip().upper().strip('"')
        if response in {"1", "ON", "TRUE"}:
            return True
        if response in {"0", "OFF", "FALSE"}:
            return False
        raise ValueError(f"invalid input state response: {response!r}")

    def set_input(self, enabled: bool) -> None:
        """Enable or disable the load input."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self.write(f"SOUR:INP {'ON' if enabled else 'OFF'}")

    def enable_input(self) -> None:
        """Enable the load input."""

        self.set_input(True)

    def disable_input(self) -> None:
        """Disable the load input."""

        self.set_input(False)

    def measure_voltage(self) -> float:
        """Read the real-time input voltage in volts."""

        return float(self.query(_MEASURE_COMMANDS["voltage"]))

    def measure_current(self) -> float:
        """Read the real-time sink current in amperes."""

        return float(self.query(_MEASURE_COMMANDS["current"]))

    def measure_power(self) -> float:
        """Read the real-time input power in watts."""

        return float(self.query(_MEASURE_COMMANDS["power"]))

    def measure_resistance(self) -> float:
        """Read the real-time resistance in ohms."""

        return float(self.query(_MEASURE_COMMANDS["resistance"]))

    def measure(self) -> LoadMeasurement:
        """Read voltage, current, power, and resistance."""

        return LoadMeasurement(
            voltage=self.measure_voltage(),
            current=self.measure_current(),
            power=self.measure_power(),
            resistance=self.measure_resistance(),
        )

    def close(self) -> None:
        """Close the VISA resource and any resource manager owned by this plug."""

        resource, self._resource = self._resource, None
        manager, self._resource_manager = self._resource_manager, None
        try:
            if resource is not None:
                resource.close()
        finally:
            if self._owns_resource_manager and manager is not None:
                manager.close()

    def tearDown(self) -> None:
        """Disable the input, then release the VISA connection."""

        try:
            if self._resource is not None:
                try:
                    self.disable_input()
                except (pyvisa.errors.VisaIOError, OSError, RuntimeError):
                    pass
        finally:
            self.close()
