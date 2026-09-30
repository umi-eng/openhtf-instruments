"""OpenHTF/PyVISA plug for the Siglent SPD3303X power supply."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

import pyvisa
from openhtf.plugs import BasePlug


class OutputMode(str, Enum):
    """CH1/CH2 coupling mode."""

    INDEPENDENT = "independent"
    SERIES = "series"
    PARALLEL = "parallel"

    @classmethod
    def coerce(cls, value: OutputMode | str) -> OutputMode:
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise TypeError("mode must be an OutputMode or string")
        key = "".join(char for char in value.upper() if char.isalnum())
        aliases = {
            "0": cls.INDEPENDENT,
            "INDEPENDENT": cls.INDEPENDENT,
            "1": cls.SERIES,
            "SERIES": cls.SERIES,
            "2": cls.PARALLEL,
            "PARALLEL": cls.PARALLEL,
        }
        try:
            return aliases[key]
        except KeyError as exc:
            raise ValueError(f"unsupported output mode: {value!r}") from exc

    @property
    def scpi_value(self) -> int:
        return {
            OutputMode.INDEPENDENT: 0,
            OutputMode.SERIES: 1,
            OutputMode.PARALLEL: 2,
        }[self]


class Channel(str, Enum):
    """Physical output channels."""

    CH1 = "CH1"
    CH2 = "CH2"
    CH3 = "CH3"

    @classmethod
    def coerce(cls, value: Channel | str | int) -> Channel:
        if isinstance(value, cls):
            return value
        if isinstance(value, bool):
            raise TypeError("channel must be a Channel, channel name, or 1, 2, or 3")
        key = str(value).strip().upper()
        if key in {"1", "2", "3"}:
            key = f"CH{key}"
        try:
            return cls(key)
        except ValueError as exc:
            raise ValueError(f"unsupported channel: {value!r}") from exc


@dataclass(frozen=True)
class SupplyMeasurement:
    """Voltage, current, and power readings in SI units."""

    voltage: float
    current: float
    power: float


_STATUS_MODE_BITS = {
    0b00: OutputMode.SERIES,
    0b01: OutputMode.INDEPENDENT,
    0b10: OutputMode.PARALLEL,
}


class SiglentSPD3303XPlug(BasePlug):
    """Control an SPD3303X through a PyVISA resource."""

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
        if resource_name is None:
            resource_name = os.environ.get("SIGLENT_SPD3303X_RESOURCE")
        if resource is None and resource_name is None:
            raise ValueError(
                "resource_name is required (or set SIGLENT_SPD3303X_RESOURCE)"
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
        """Send a SCPI query and return its stripped response."""

        query = getattr(self._resource, "query", None)
        if callable(query):
            return str(query(command)).strip()
        self._resource.write(command)
        return str(self._resource.read()).strip()

    def identify(self) -> str:
        """Return the instrument identity string."""

        return self.query("*IDN?")

    @property
    def output_mode(self) -> OutputMode:
        """Read the current CH1/CH2 coupling mode."""

        return self._mode_from_status(self._status_register())

    def set_output_mode(self, mode: OutputMode | str) -> None:
        """Select independent, series, or parallel operation."""

        selected = OutputMode.coerce(mode)
        self.write(f"OUTPut:TRACK {selected.scpi_value}")

    def set_voltage(
        self, voltage: float, channel: Channel | str | int = Channel.CH1
    ) -> None:
        """Set a CH1/CH2 voltage setpoint in volts."""

        selected = self._setpoint_channel(channel)
        mode = self.output_mode
        self._check_controlled_channel(selected, mode)
        self._check_setpoint(voltage, "voltage", self._voltage_limit(mode))
        self.write(f"{selected.value}:VOLT {float(voltage)}")

    def voltage(self, channel: Channel | str | int = Channel.CH1) -> float:
        """Read a CH1/CH2 voltage setpoint in volts."""

        selected = self._setpoint_channel(channel)
        self._check_controlled_channel(selected)
        return float(self.query(f"{selected.value}:VOLT?"))

    def set_current(
        self, current: float, channel: Channel | str | int = Channel.CH1
    ) -> None:
        """Set a CH1/CH2 current limit in amperes."""

        selected = self._setpoint_channel(channel)
        mode = self.output_mode
        self._check_controlled_channel(selected, mode)
        self._check_setpoint(current, "current", self._current_limit(mode))
        self.write(f"{selected.value}:CURRent {float(current)}")

    def current(self, channel: Channel | str | int = Channel.CH1) -> float:
        """Read a CH1/CH2 current limit in amperes."""

        selected = self._setpoint_channel(channel)
        self._check_controlled_channel(selected)
        return float(self.query(f"{selected.value}:CURRent?"))

    def set_output(
        self, enabled: bool, channel: Channel | str | int = Channel.CH1
    ) -> None:
        """Enable or disable one output."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        selected = Channel.coerce(channel)
        if selected in {Channel.CH1, Channel.CH2}:
            self._check_controlled_channel(selected)
        self.write(f"OUTPut {selected.value},{'ON' if enabled else 'OFF'}")

    def enable_output(self, channel: Channel | str | int = Channel.CH1) -> None:
        """Enable one output."""

        self.set_output(True, channel)

    def disable_output(self, channel: Channel | str | int = Channel.CH1) -> None:
        """Disable one output."""

        self.set_output(False, channel)

    def set_all_outputs(self, enabled: bool) -> None:
        """Set all independent outputs, or both output groups when tracking."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        channels = [Channel.CH1, Channel.CH2, Channel.CH3]
        if self.output_mode is not OutputMode.INDEPENDENT:
            channels.remove(Channel.CH2)
        state = "ON" if enabled else "OFF"
        for channel in channels:
            self.write(f"OUTPut {channel.value},{state}")

    def is_output_enabled(self, channel: Channel | str | int = Channel.CH1) -> bool:
        """Read CH1/CH2 output state from the system status register."""

        selected = Channel.coerce(channel)
        if selected is Channel.CH3:
            raise ValueError("CH3 output state is not reported by the documented SCPI")
        status = self._status_register()
        mode = self._mode_from_status(status)
        bit = 4 if selected is Channel.CH1 or mode is not OutputMode.INDEPENDENT else 5
        return bool(status & (1 << bit))

    def measure_voltage(self, channel: Channel | str | int = Channel.CH1) -> float:
        """Read output voltage in volts."""

        selected = self._measurement_channel(channel)
        return float(self.query(f"MEASure:VOLTage? {selected.value}"))

    def measure_current(self, channel: Channel | str | int = Channel.CH1) -> float:
        """Read output current in amperes."""

        selected = self._measurement_channel(channel)
        return float(self.query(f"MEASure:CURRent? {selected.value}"))

    def measure_power(self, channel: Channel | str | int = Channel.CH1) -> float:
        """Read output power in watts."""

        selected = self._measurement_channel(channel)
        return float(self.query(f"MEASure:POWEr? {selected.value}"))

    def measure(self, channel: Channel | str | int = Channel.CH1) -> SupplyMeasurement:
        """Read voltage, current, and power for CH1 or CH2."""

        selected = self._measurement_channel(channel)
        return SupplyMeasurement(
            voltage=self.measure_voltage(selected),
            current=self.measure_current(selected),
            power=self.measure_power(selected),
        )

    def _status_register(self) -> int:
        response = self.query("SYSTem:STATus?")
        try:
            return int(response, 16)
        except ValueError as exc:
            raise ValueError(f"invalid system status response: {response!r}") from exc

    @staticmethod
    def _mode_from_status(status: int) -> OutputMode:
        mode_bits = (status >> 2) & 0b11
        try:
            return _STATUS_MODE_BITS[mode_bits]
        except KeyError as exc:
            raise ValueError(f"unknown output mode in status: 0x{status:X}") from exc

    @staticmethod
    def _voltage_limit(mode: OutputMode) -> float:
        return 60.0 if mode is OutputMode.SERIES else 32.0

    @staticmethod
    def _current_limit(mode: OutputMode) -> float:
        return 6.4 if mode is OutputMode.PARALLEL else 3.2

    @staticmethod
    def _check_setpoint(value: float, name: str, maximum: float) -> None:
        if isinstance(value, bool):
            raise TypeError(f"{name} must be a finite number")
        try:
            numeric = float(value)
        except (OverflowError, TypeError, ValueError) as exc:
            raise TypeError(f"{name} must be a finite number") from exc
        if not math.isfinite(numeric) or not 0 <= numeric <= maximum:
            raise ValueError(f"{name} must be between 0 and {maximum:g}")

    @staticmethod
    def _setpoint_channel(channel: Channel | str | int) -> Channel:
        selected = Channel.coerce(channel)
        if selected is Channel.CH3:
            raise ValueError("CH3 voltage is selected by its physical DIP switch")
        return selected

    @staticmethod
    def _measurement_channel(channel: Channel | str | int) -> Channel:
        selected = Channel.coerce(channel)
        if selected is Channel.CH3:
            raise ValueError(
                "CH3 measurements are not supported by the documented SCPI"
            )
        return selected

    def _check_controlled_channel(
        self, channel: Channel, mode: OutputMode | None = None
    ) -> None:
        if channel is Channel.CH2:
            if mode is None:
                mode = self.output_mode
            if mode is not OutputMode.INDEPENDENT:
                raise ValueError(
                    "CH1 controls the combined output in series and parallel modes"
                )

    def close(self) -> None:
        """Close the VISA resource and any owned resource manager."""

        resource, self._resource = self._resource, None
        manager, self._resource_manager = self._resource_manager, None
        try:
            if resource is not None:
                resource.close()
        finally:
            if self._owns_resource_manager and manager is not None:
                manager.close()

    def tearDown(self) -> None:
        """Turn outputs off and release the VISA connection."""

        if self._resource is not None:
            for channel in Channel:
                try:
                    self.write(f"OUTPut {channel.value},OFF")
                except (pyvisa.errors.VisaIOError, OSError, RuntimeError):
                    pass
        self.close()


__all__ = ["Channel", "OutputMode", "SiglentSPD3303XPlug", "SupplyMeasurement"]
