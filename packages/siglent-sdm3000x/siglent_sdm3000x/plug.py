"""OpenHTF/PyVISA plug for the Siglent SDM3000X multimeter."""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from enum import Enum
from typing import Any

import pyvisa
from openhtf.plugs import BasePlug


class MeasurementFunction(Enum):
    DC_VOLTAGE = "VOLTage:DC"
    AC_VOLTAGE = "VOLTage:AC"
    DC_CURRENT = "CURRent:DC"
    AC_CURRENT = "CURRent:AC"
    RESISTANCE = "RESistance"
    FOUR_WIRE_RESISTANCE = "FRESistance"
    CAPACITANCE = "CAPacitance"
    FREQUENCY = "FREQuency"
    PERIOD = "PERiod"
    CONTINUITY = "CONTinuity"
    DIODE = "DIODe"
    TEMPERATURE = "TEMPerature"

    @classmethod
    def coerce(cls, value: MeasurementFunction | str) -> MeasurementFunction:
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise TypeError("function must be a MeasurementFunction or string")
        key = _normalize(value)
        for function in cls:
            if key in (_normalize(function.name), _normalize(function.value)):
                return function
        raise ValueError(f"unsupported measurement function: {value!r}")


def _normalize(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


_RANGE_FUNCTIONS = {
    MeasurementFunction.DC_VOLTAGE,
    MeasurementFunction.AC_VOLTAGE,
    MeasurementFunction.DC_CURRENT,
    MeasurementFunction.AC_CURRENT,
    MeasurementFunction.RESISTANCE,
    MeasurementFunction.FOUR_WIRE_RESISTANCE,
    MeasurementFunction.CAPACITANCE,
}


def _format_range(value: float | str, function: MeasurementFunction) -> str:
    if isinstance(value, str):
        text = value.strip()
        keyword = text.upper()
        if keyword in {"AUTO", "DEF", "MIN", "MAX"}:
            return keyword
        numeric_pattern = r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
        numeric = re.fullmatch(numeric_pattern, text)
        capacitance = function is MeasurementFunction.CAPACITANCE and re.fullmatch(
            numeric_pattern + r"[pnumµμ]F", text, re.IGNORECASE
        )
        if not numeric and not capacitance:
            raise ValueError("range must be positive, or AUTO, DEF, MIN, or MAX")
        number_match = re.match(numeric_pattern, text)
        if number_match is None:
            raise ValueError("range must start with a number")
        number = float(number_match.group())
        if not math.isfinite(number) or number <= 0:
            raise ValueError("range must be positive and finite")
        return text
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("range must be a number or SCPI range keyword")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("range must be a positive finite number")
    return format(value, ".12g")


def _format_temperature_option(value: str, name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_+.-]+", value.strip()):
        raise ValueError(f"invalid {name}: {value!r}")
    return value.strip()


class SiglentSDM3000XPlug(BasePlug):
    """Take single SDM3000X measurements over any PyVISA interface.

    ``resource_name`` is a PyVISA resource string, or set
    ``SIGLENT_SDM3000X_RESOURCE``. A resource or resource manager can be
    injected for tests.
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
        resource_name = resource_name or os.environ.get("SIGLENT_SDM3000X_RESOURCE")
        if resource is None and resource_name is None:
            raise ValueError(
                "resource_name is required when resource is not supplied "
                "(or set SIGLENT_SDM3000X_RESOURCE)"
            )

        self._owns_resource_manager = resource_manager is None and resource is None
        self._resource_manager: Any = resource_manager
        self._resource: Any = resource
        if self._resource is None:
            assert resource_name is not None
            self._resource_manager = resource_manager or pyvisa.ResourceManager(
                visa_library
            )
            self._resource = self._resource_manager.open_resource(
                resource_name, **(resource_kwargs or {})
            )
        self.resource_name = resource_name or getattr(
            self._resource, "resource_name", "injected"
        )
        self._configure_resource(timeout_ms, read_termination, write_termination)

    @property
    def resource(self) -> Any:
        """The underlying PyVISA resource."""

        return self._resource

    def _configure_resource(
        self, timeout_ms: int, read_termination: str, write_termination: str
    ) -> None:
        for attribute, value in (
            ("timeout", timeout_ms),
            ("read_termination", read_termination),
            ("write_termination", write_termination),
        ):
            try:
                setattr(self._resource, attribute, value)
            except (AttributeError, TypeError, ValueError):
                pass

    def write(self, command: str) -> None:
        """Send a SCPI command without reading a response."""

        self._resource.write(command)

    def query(self, command: str) -> str:
        """Send a SCPI query and return its response."""

        query = getattr(self._resource, "query", None)
        if callable(query):
            return str(query(command)).strip()
        self.write(command)
        return str(self._resource.read()).strip()

    def identify(self) -> str:
        """Return the instrument's IEEE-488 identity string."""

        return self.query("*IDN?")

    def measure(
        self,
        function: MeasurementFunction | str,
        *,
        range: float | str | None = None,
        temperature_transducer: str | None = None,
        temperature_type: str | None = None,
    ) -> float:
        """Configure, trigger, and return one reading for ``function``.

        ``range`` applies to voltage, current, resistance, and capacitance.
        Temperature readings may specify ``RTD`` or ``THER`` and a probe type.
        """

        measurement = MeasurementFunction.coerce(function)
        if range is not None and measurement not in _RANGE_FUNCTIONS:
            raise ValueError(f"range is not supported for {measurement.name}")
        if measurement is not MeasurementFunction.TEMPERATURE and (
            temperature_transducer is not None or temperature_type is not None
        ):
            raise ValueError("temperature options are only valid for temperature")

        parameters = []
        if range is not None:
            parameters.append(_format_range(range, measurement))
        if measurement is MeasurementFunction.TEMPERATURE:
            if temperature_type is not None and temperature_transducer is None:
                raise ValueError(
                    "temperature_transducer is required with temperature_type"
                )
            if temperature_transducer is not None:
                transducer = temperature_transducer.strip().upper()
                if transducer not in {"RTD", "THER", "DEF", "DEFAULT"}:
                    raise ValueError(
                        "temperature_transducer must be RTD, THER, or DEFault"
                    )
                transducer = (
                    "DEFault" if transducer in {"DEF", "DEFAULT"} else transducer
                )
                parameters.append(transducer)
                if temperature_type is not None:
                    type_name = _format_temperature_option(
                        temperature_type, "temperature type"
                    )
                    parameters.append(
                        "DEFault"
                        if type_name.upper() in {"DEF", "DEFAULT"}
                        else type_name
                    )
        command = f"MEASure:{measurement.value}?"
        if parameters:
            command += " " + ",".join(parameters)
        return float(self.query(command))

    def close(self) -> None:
        """Close the VISA resource and any resource manager owned by this plug."""

        try:
            if self._resource is not None:
                self._resource.close()
        finally:
            self._resource = None
            if self._owns_resource_manager and self._resource_manager is not None:
                try:
                    self._resource_manager.close()
                finally:
                    self._resource_manager = None

    def tearDown(self) -> None:
        self.close()


__all__ = ["MeasurementFunction", "SiglentSDM3000XPlug"]
