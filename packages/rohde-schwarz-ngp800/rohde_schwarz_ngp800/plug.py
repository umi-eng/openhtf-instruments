"""OpenHTF plug for the Rohde & Schwarz NGP800 power supply series.

The NGP800 is controlled through its SCPI interface. Connections can use any
PyVISA resource (USB TMC, VXI-11, HiSLIP, or a raw TCP socket), or the built-in
raw TCP socket transport on port 5025.
"""

from __future__ import annotations

import math
import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

import pyvisa
from openhtf.plugs import BasePlug


class Channel(str, Enum):
    """Physical output channels."""

    CH1 = "CH1"
    CH2 = "CH2"
    CH3 = "CH3"
    CH4 = "CH4"

    @classmethod
    def coerce(cls, value: Channel | str | int) -> Channel:
        if isinstance(value, cls):
            return value
        if isinstance(value, bool):
            raise TypeError("channel must be a Channel, channel name, or 1 to 4")
        key = str(value).strip().upper()
        if key in {"1", "2", "3", "4"}:
            key = f"CH{key}"
        try:
            return cls(key)
        except ValueError as exc:
            raise ValueError(f"unsupported channel: {value!r}") from exc

    @property
    def number(self) -> int:
        """Channel number as used in SCPI commands."""

        return int(self.value[2:])


class RegulationMode(str, Enum):
    """Output regulation state reported by the instrument."""

    OFF = "OFF"
    CV = "CV"
    CC = "CC"


@dataclass(frozen=True)
class SupplyMeasurement:
    """Voltage, current, and power readings in SI units."""

    voltage: float
    current: float
    power: float


@dataclass(frozen=True)
class Readback:
    """Synchronized voltage and current readback from ``READ?``."""

    voltage: float
    current: float


@dataclass(frozen=True)
class StatisticsMeasurement:
    """Accumulated statistics for one channel."""

    voltage_min: float
    voltage_max: float
    voltage_avg: float
    current_min: float
    current_max: float
    current_avg: float
    power_min: float
    power_max: float
    power_avg: float
    energy: float
    sample_count: int


@dataclass(frozen=True)
class ScpiError:
    """One entry from the instrument error queue."""

    code: int
    message: str


@dataclass(frozen=True)
class ModelInfo:
    """Published channel count and setpoint limits for an NGP800 model."""

    name: str
    channel_count: int
    voltage_limits: tuple[float, ...]
    current_limits: tuple[float, ...]

    def voltage_limit(self, channel: Channel) -> float:
        """Maximum voltage setpoint for ``channel`` in volts."""

        return self.voltage_limits[channel.number - 1]

    def current_limit(self, channel: Channel) -> float:
        """Maximum current setpoint for ``channel`` in amperes."""

        return self.current_limits[channel.number - 1]


MODELS: dict[str, ModelInfo] = {
    "NGP802": ModelInfo("NGP802", 2, (32.05, 32.05), (20.01, 20.01)),
    "NGP822": ModelInfo("NGP822", 2, (64.05, 64.05), (10.01, 10.01)),
    "NGP804": ModelInfo(
        "NGP804", 4, (32.05, 32.05, 32.05, 32.05), (20.01, 20.01, 20.01, 20.01)
    ),
    "NGP824": ModelInfo(
        "NGP824", 4, (64.05, 64.05, 64.05, 64.05), (10.01, 10.01, 10.01, 10.01)
    ),
    "NGP814": ModelInfo(
        "NGP814",
        4,
        (32.05, 32.05, 64.05, 64.05),
        (20.01, 20.01, 10.01, 10.01),
    ),
}

_FALLBACK_VOLTAGE_LIMIT = 64.05
_FALLBACK_CURRENT_LIMIT = 20.01
_FALLBACK_CHANNEL_COUNT = 4


class _SocketResource:
    """Minimal PyVISA-like raw TCP socket resource for SCPI.

    The NGP800 accepts SCPI commands on TCP port 5025. Commands and responses
    are terminated with a line feed.
    """

    def __init__(self, host: str, port: int = 5025, timeout_ms: int = 5000) -> None:
        self.resource_name = f"TCPIP::{host}::{port}::SOCKET"
        self.read_termination = "\n"
        self.write_termination = "\n"
        self._timeout_ms = int(timeout_ms)
        self._buffer = b""
        timeout_s = None if self._timeout_ms <= 0 else self._timeout_ms / 1000
        self._socket = socket.create_connection((host, port), timeout=timeout_s)
        self._socket.settimeout(timeout_s)

    @property
    def timeout(self) -> int:
        """Socket timeout in milliseconds."""

        return self._timeout_ms

    @timeout.setter
    def timeout(self, value: int) -> None:
        self._timeout_ms = int(value)
        timeout_s = None if self._timeout_ms <= 0 else self._timeout_ms / 1000
        self._socket.settimeout(timeout_s)

    def write(self, command: str) -> None:
        """Send one SCPI command."""

        payload = command + self.write_termination
        self._socket.sendall(payload.encode("ascii"))

    def read(self) -> str:
        """Read one line-terminated SCPI response."""

        while b"\n" not in self._buffer:
            chunk = self._socket.recv(4096)
            if not chunk:
                raise ConnectionError("instrument closed the SCPI connection")
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\n")
        return line.decode("ascii", errors="replace") + "\n"

    def query(self, command: str) -> str:
        """Send a SCPI query and return its response."""

        self.write(command)
        return self.read()

    def close(self) -> None:
        """Close the socket."""

        try:
            self._socket.close()
        finally:
            self._buffer = b""


class RohdeSchwarzNGP800Plug(BasePlug):
    """Control an NGP800 power supply through SCPI.

    ``resource_name`` is a PyVISA resource string, or set
    ``ROHDE_SCHWARZ_NGP800_RESOURCE``. For a raw TCP connection without a VISA
    backend, pass ``host`` (and optionally ``port``). A resource can be
    injected for tests.
    """

    def __init__(
        self,
        resource_name: str | None = None,
        *,
        host: str | None = None,
        port: int = 5025,
        visa_library: str = "",
        resource_manager: Any | None = None,
        resource: Any | None = None,
        resource_kwargs: Mapping[str, Any] | None = None,
        timeout_ms: int = 5000,
        read_termination: str = "\n",
        write_termination: str = "\n",
        model: str | None = None,
    ) -> None:
        super().__init__()
        if resource is not None and host is not None:
            raise ValueError("resource and host are mutually exclusive")
        if host is not None and resource_name is not None:
            raise ValueError("host and resource_name are mutually exclusive")
        if host is not None and resource_manager is not None:
            raise ValueError("resource_manager is not used with host")

        if model is not None:
            normalized_model = model.strip().upper()
            if normalized_model not in MODELS:
                raise ValueError(f"unsupported model: {model!r}")
            self._model: str | None = normalized_model
            self._model_queried = True
        else:
            self._model = None
            self._model_queried = False

        self._resource_manager: Any = resource_manager
        self._owns_resource_manager = False
        self._resource: Any = resource
        if self._resource is None and host is not None:
            self._resource = _SocketResource(host, port, timeout_ms)
        elif self._resource is None:
            resource_name = resource_name or os.environ.get(
                "ROHDE_SCHWARZ_NGP800_RESOURCE"
            )
            if resource_name is None:
                raise ValueError(
                    "resource_name or host is required "
                    "(or set ROHDE_SCHWARZ_NGP800_RESOURCE)"
                )
            self._owns_resource_manager = resource_manager is None
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
        self._configure_resource(timeout_ms, read_termination, write_termination)

    @property
    def resource(self) -> Any:
        """The underlying PyVISA or socket resource."""

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
        """Send a SCPI query and return its stripped response."""

        query = getattr(self._resource, "query", None)
        if callable(query):
            return self._as_text(query(command)).strip()
        self.write(command)
        return self._as_text(self._resource.read()).strip()

    @staticmethod
    def _as_text(response: Any) -> str:
        if isinstance(response, bytes):
            return response.decode("ascii", errors="replace")
        return str(response)

    def identify(self) -> str:
        """Return the instrument's IEEE-488 identity string."""

        return self.query("*IDN?")

    @property
    def model(self) -> str | None:
        """Instrument model parsed from ``*IDN?``, if available."""

        if self._model is None and not self._model_queried:
            self._model_queried = True
            try:
                identity = self.identify()
            except (
                pyvisa.errors.VisaIOError,
                OSError,
                RuntimeError,
                ValueError,
            ):
                return None
            parts = [part.strip() for part in identity.split(",")]
            if len(parts) >= 2 and parts[1]:
                self._model = parts[1].upper()
        return self._model

    @property
    def model_info(self) -> ModelInfo | None:
        """Published limits for the detected model, if it is known."""

        model = self.model
        if model is None:
            return None
        return MODELS.get(model)

    @property
    def channel_count(self) -> int:
        """Number of output channels on the instrument."""

        info = self.model_info
        return info.channel_count if info is not None else _FALLBACK_CHANNEL_COUNT

    @property
    def channels(self) -> tuple[Channel, ...]:
        """Channels available on the instrument."""

        return tuple(Channel)[: self.channel_count]

    def select_channel(self, channel: Channel | str | int = Channel.CH1) -> None:
        """Select the channel used by commands without a channel list."""

        selected = self._check_channel(channel)
        self.write(f"INST:NSEL {selected.number}")

    def selected_channel(self) -> Channel:
        """Return the currently selected channel."""

        return Channel.coerce(self.query("INST:NSEL?"))

    def set_voltage(
        self, voltage: float, channel: Channel | str | int = Channel.CH1
    ) -> None:
        """Set a channel voltage setpoint in volts."""

        selected = self._check_channel(channel)
        value = self._check_setpoint(
            voltage, "voltage", 0.0, self._voltage_limit(selected)
        )
        self.write(f"VOLT {self._format_number(value)},(@{selected.number})")

    def voltage(self, channel: Channel | str | int = Channel.CH1) -> float:
        """Read a channel voltage setpoint in volts."""

        selected = self._check_channel(channel)
        return self._parse_float(self.query(f"VOLT? (@{selected.number})"), "voltage")

    def set_current(
        self, current: float, channel: Channel | str | int = Channel.CH1
    ) -> None:
        """Set a channel current limit in amperes."""

        selected = self._check_channel(channel)
        value = self._check_setpoint(
            current, "current", 0.001, self._current_limit(selected)
        )
        self.write(f"CURR {self._format_number(value)},(@{selected.number})")

    def current(self, channel: Channel | str | int = Channel.CH1) -> float:
        """Read a channel current limit in amperes."""

        selected = self._check_channel(channel)
        return self._parse_float(self.query(f"CURR? (@{selected.number})"), "current")

    def apply(
        self,
        voltage: float,
        current: float,
        channel: Channel | str | int = Channel.CH1,
    ) -> None:
        """Set voltage and current together with ``APPLy``.

        ``APPLy`` operates on the selected channel, so this method changes the
        instrument's channel selection.
        """

        selected = self._check_channel(channel)
        voltage_value = self._check_setpoint(
            voltage, "voltage", 0.0, self._voltage_limit(selected)
        )
        current_value = self._check_setpoint(
            current, "current", 0.001, self._current_limit(selected)
        )
        self.write(f"INST:NSEL {selected.number}")
        self.write(
            f"APPL {self._format_number(voltage_value)},"
            f"{self._format_number(current_value)}"
        )

    def set_output(
        self, enabled: bool, channel: Channel | str | int = Channel.CH1
    ) -> None:
        """Enable or disable one channel output."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        selected = self._check_channel(channel)
        self.write(f"OUTP {int(enabled)},(@{selected.number})")

    def enable_output(self, channel: Channel | str | int = Channel.CH1) -> None:
        """Enable one channel output."""

        self.set_output(True, channel)

    def disable_output(self, channel: Channel | str | int = Channel.CH1) -> None:
        """Disable one channel output."""

        self.set_output(False, channel)

    def output_enabled(self, channel: Channel | str | int = Channel.CH1) -> bool:
        """Return whether a channel output is enabled."""

        selected = self._check_channel(channel)
        return self._parse_bool(
            self.query(f"OUTP? (@{selected.number})"), "output state"
        )

    def set_all_outputs(self, enabled: bool) -> None:
        """Enable or disable every output on the instrument."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        for channel in self.channels:
            self.set_output(enabled, channel)

    def measure_voltage(self, channel: Channel | str | int = Channel.CH1) -> float:
        """Read the measured output voltage in volts."""

        selected = self._check_channel(channel)
        return self._parse_float(
            self.query(f"MEAS:VOLT? (@{selected.number})"), "voltage"
        )

    def measure_current(self, channel: Channel | str | int = Channel.CH1) -> float:
        """Read the measured output current in amperes."""

        selected = self._check_channel(channel)
        return self._parse_float(
            self.query(f"MEAS:CURR? (@{selected.number})"), "current"
        )

    def measure_power(self, channel: Channel | str | int = Channel.CH1) -> float:
        """Read the measured output power in watts."""

        selected = self._check_channel(channel)
        return self._parse_float(self.query(f"MEAS:POW? (@{selected.number})"), "power")

    def measure(self, channel: Channel | str | int = Channel.CH1) -> SupplyMeasurement:
        """Read voltage, current, and power for one channel."""

        selected = self._check_channel(channel)
        return SupplyMeasurement(
            voltage=self.measure_voltage(selected),
            current=self.measure_current(selected),
            power=self.measure_power(selected),
        )

    def measure_all(self) -> dict[Channel, SupplyMeasurement]:
        """Read voltage, current, and power for every channel."""

        return {channel: self.measure(channel) for channel in self.channels}

    def read(self, channel: Channel | str | int = Channel.CH1) -> Readback:
        """Read voltage and current together with ``READ?``.

        ``READ?`` operates on the selected channel, so this method changes the
        instrument's channel selection.
        """

        selected = self._check_channel(channel)
        self.write(f"INST:NSEL {selected.number}")
        response = self.query("READ?")
        parts = [part.strip() for part in response.split(",")]
        if len(parts) != 2:
            raise ValueError(f"invalid READ? response: {response!r}")
        return Readback(
            voltage=self._parse_float(parts[0], "voltage"),
            current=self._parse_float(parts[1], "current"),
        )

    def regulation_mode(
        self, channel: Channel | str | int = Channel.CH1
    ) -> RegulationMode:
        """Return the channel's current CV/CC regulation state."""

        selected = self._check_channel(channel)
        status = self._parse_int(
            self.query(f"STAT:QUES:INST:ISUM{selected.number}:COND?"),
            "regulation status",
        )
        if status & 0b01:
            return RegulationMode.CC
        if status & 0b10:
            return RegulationMode.CV
        return RegulationMode.OFF

    def set_ocp(
        self, enabled: bool, channel: Channel | str | int = Channel.CH1
    ) -> None:
        """Enable or disable overcurrent protection (OCP)."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        selected = self._check_channel(channel)
        self.write(f"FUSE {int(enabled)},(@{selected.number})")

    def ocp_enabled(self, channel: Channel | str | int = Channel.CH1) -> bool:
        """Return whether OCP is enabled."""

        selected = self._check_channel(channel)
        return self._parse_bool(self.query(f"FUSE? (@{selected.number})"), "OCP state")

    def ocp_tripped(self, channel: Channel | str | int = Channel.CH1) -> bool:
        """Return whether OCP has tripped."""

        selected = self._check_channel(channel)
        return self._parse_bool(
            self.query(f"FUSE:TRIP? (@{selected.number})"), "OCP trip state"
        )

    def clear_ocp(self, channel: Channel | str | int = Channel.CH1) -> None:
        """Clear a tripped OCP state."""

        selected = self._check_channel(channel)
        self.write(f"FUSE:TRIP:CLE (@{selected.number})")

    def set_ocp_blowing_delay(
        self, seconds: float, channel: Channel | str | int = Channel.CH1
    ) -> None:
        """Set the OCP blowing delay in seconds."""

        selected = self._check_channel(channel)
        value = self._check_setpoint(seconds, "OCP blowing delay", 0.0, 10.0)
        self.write(f"FUSE:DEL {self._format_number(value)},(@{selected.number})")

    def ocp_blowing_delay(self, channel: Channel | str | int = Channel.CH1) -> float:
        """Read the OCP blowing delay in seconds."""

        selected = self._check_channel(channel)
        return self._parse_float(
            self.query(f"FUSE:DEL? (@{selected.number})"),
            "OCP blowing delay",
        )

    def set_ocp_initial_delay(
        self, seconds: float, channel: Channel | str | int = Channel.CH1
    ) -> None:
        """Set the OCP delay after the output is switched on, in seconds."""

        selected = self._check_channel(channel)
        value = self._check_setpoint(seconds, "OCP initial delay", 0.0, 60.0)
        self.write(f"FUSE:DEL:INIT {self._format_number(value)},(@{selected.number})")

    def ocp_initial_delay(self, channel: Channel | str | int = Channel.CH1) -> float:
        """Read the OCP initial delay in seconds."""

        selected = self._check_channel(channel)
        return self._parse_float(
            self.query(f"FUSE:DEL:INIT? (@{selected.number})"),
            "OCP initial delay",
        )

    def set_ovp(
        self, enabled: bool, channel: Channel | str | int = Channel.CH1
    ) -> None:
        """Enable or disable overvoltage protection (OVP)."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        selected = self._check_channel(channel)
        self.write(f"VOLT:PROT {int(enabled)},(@{selected.number})")

    def ovp_enabled(self, channel: Channel | str | int = Channel.CH1) -> bool:
        """Return whether OVP is enabled."""

        selected = self._check_channel(channel)
        return self._parse_bool(
            self.query(f"VOLT:PROT? (@{selected.number})"), "OVP state"
        )

    def set_ovp_level(
        self, voltage: float, channel: Channel | str | int = Channel.CH1
    ) -> None:
        """Set the OVP level in volts."""

        selected = self._check_channel(channel)
        value = self._check_setpoint(
            voltage, "OVP level", 0.0, self._voltage_limit(selected)
        )
        self.write(f"VOLT:PROT:LEV {self._format_number(value)},(@{selected.number})")

    def ovp_level(self, channel: Channel | str | int = Channel.CH1) -> float:
        """Read the OVP level in volts."""

        selected = self._check_channel(channel)
        return self._parse_float(
            self.query(f"VOLT:PROT:LEV? (@{selected.number})"), "OVP level"
        )

    def ovp_tripped(self, channel: Channel | str | int = Channel.CH1) -> bool:
        """Return whether OVP has tripped."""

        selected = self._check_channel(channel)
        return self._parse_bool(
            self.query(f"VOLT:PROT:TRIP? (@{selected.number})"),
            "OVP trip state",
        )

    def clear_ovp(self, channel: Channel | str | int = Channel.CH1) -> None:
        """Clear a tripped OVP state."""

        selected = self._check_channel(channel)
        self.write(f"VOLT:PROT:CLE (@{selected.number})")

    def set_opp(
        self, enabled: bool, channel: Channel | str | int = Channel.CH1
    ) -> None:
        """Enable or disable overpower protection (OPP)."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        selected = self._check_channel(channel)
        self.write(f"POW:PROT {int(enabled)},(@{selected.number})")

    def opp_enabled(self, channel: Channel | str | int = Channel.CH1) -> bool:
        """Return whether OPP is enabled."""

        selected = self._check_channel(channel)
        return self._parse_bool(
            self.query(f"POW:PROT? (@{selected.number})"), "OPP state"
        )

    def set_opp_level(
        self, power: float, channel: Channel | str | int = Channel.CH1
    ) -> None:
        """Set the OPP level in watts."""

        selected = self._check_channel(channel)
        value = self._check_setpoint(power, "OPP level", 1.0, 200.0)
        self.write(f"POW:PROT:LEV {self._format_number(value)},(@{selected.number})")

    def opp_level(self, channel: Channel | str | int = Channel.CH1) -> float:
        """Read the OPP level in watts."""

        selected = self._check_channel(channel)
        return self._parse_float(
            self.query(f"POW:PROT:LEV? (@{selected.number})"), "OPP level"
        )

    def opp_tripped(self, channel: Channel | str | int = Channel.CH1) -> bool:
        """Return whether OPP has tripped."""

        selected = self._check_channel(channel)
        return self._parse_bool(
            self.query(f"POW:PROT:TRIP? (@{selected.number})"),
            "OPP trip state",
        )

    def clear_opp(self, channel: Channel | str | int = Channel.CH1) -> None:
        """Clear a tripped OPP state."""

        selected = self._check_channel(channel)
        self.write(f"POW:PROT:CLE (@{selected.number})")

    def clear_protection(self, channel: Channel | str | int = Channel.CH1) -> None:
        """Clear all tripped protection states on one channel."""

        selected = self._check_channel(channel)
        self.write(f"PROT:CLE (@{selected.number})")

    def set_safety_limits_enabled(
        self, enabled: bool, channel: Channel | str | int = Channel.CH1
    ) -> None:
        """Enable or disable the channel safety limits."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        selected = self._check_channel(channel)
        self.write(f"ALIM {int(enabled)},(@{selected.number})")

    def safety_limits_enabled(self, channel: Channel | str | int = Channel.CH1) -> bool:
        """Return whether the channel safety limits are enabled."""

        selected = self._check_channel(channel)
        return self._parse_bool(
            self.query(f"ALIM? (@{selected.number})"), "safety limit state"
        )

    def set_voltage_safety_limits(
        self,
        lower: float,
        upper: float,
        channel: Channel | str | int = Channel.CH1,
    ) -> None:
        """Set the lower and upper voltage safety limits in volts."""

        selected = self._check_channel(channel)
        maximum = self._voltage_limit(selected)
        lower_value = self._check_setpoint(
            lower, "lower voltage safety limit", 0.0, maximum
        )
        upper_value = self._check_setpoint(
            upper, "upper voltage safety limit", 0.0, maximum
        )
        if lower_value > upper_value:
            raise ValueError("lower voltage safety limit must not exceed upper")
        self.write(f"VOLT:ALIM:UPP {self._format_number(maximum)},(@{selected.number})")
        self.write(
            f"VOLT:ALIM:LOW {self._format_number(lower_value)},(@{selected.number})"
        )
        self.write(
            f"VOLT:ALIM:UPP {self._format_number(upper_value)},(@{selected.number})"
        )

    def voltage_safety_limits(
        self, channel: Channel | str | int = Channel.CH1
    ) -> tuple[float, float]:
        """Read the lower and upper voltage safety limits in volts."""

        selected = self._check_channel(channel)
        lower = self._parse_float(
            self.query(f"VOLT:ALIM:LOW? (@{selected.number})"),
            "lower voltage safety limit",
        )
        upper = self._parse_float(
            self.query(f"VOLT:ALIM:UPP? (@{selected.number})"),
            "upper voltage safety limit",
        )
        return lower, upper

    def set_current_safety_limits(
        self,
        lower: float,
        upper: float,
        channel: Channel | str | int = Channel.CH1,
    ) -> None:
        """Set the lower and upper current safety limits in amperes."""

        selected = self._check_channel(channel)
        maximum = self._current_limit(selected)
        lower_value = self._check_setpoint(
            lower, "lower current safety limit", 0.001, maximum
        )
        upper_value = self._check_setpoint(
            upper, "upper current safety limit", 0.001, maximum
        )
        if lower_value > upper_value:
            raise ValueError("lower current safety limit must not exceed upper")
        self.write(f"CURR:ALIM:UPP {self._format_number(maximum)},(@{selected.number})")
        self.write(
            f"CURR:ALIM:LOW {self._format_number(lower_value)},(@{selected.number})"
        )
        self.write(
            f"CURR:ALIM:UPP {self._format_number(upper_value)},(@{selected.number})"
        )

    def current_safety_limits(
        self, channel: Channel | str | int = Channel.CH1
    ) -> tuple[float, float]:
        """Read the lower and upper current safety limits in amperes."""

        selected = self._check_channel(channel)
        lower = self._parse_float(
            self.query(f"CURR:ALIM:LOW? (@{selected.number})"),
            "lower current safety limit",
        )
        upper = self._parse_float(
            self.query(f"CURR:ALIM:UPP? (@{selected.number})"),
            "upper current safety limit",
        )
        return lower, upper

    def set_tracking(self, enabled: bool) -> None:
        """Enable or disable the master tracking function."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self.write(f"TRAC:ENAB:GEN {int(enabled)}")

    def tracking_enabled(self) -> bool:
        """Return whether master tracking is enabled."""

        return self._parse_bool(self.query("TRAC:ENAB:GEN?"), "tracking state")

    def set_channel_tracking(self, channel: Channel | str | int, enabled: bool) -> None:
        """Enable or disable tracking for one channel."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        selected = self._check_channel(channel)
        self.write(f"TRAC:ENAB:CH{selected.number} {int(enabled)}")

    def channel_tracking(self, channel: Channel | str | int) -> bool:
        """Return whether tracking is enabled for one channel."""

        selected = self._check_channel(channel)
        return self._parse_bool(
            self.query(f"TRAC:ENAB:CH{selected.number}?"),
            "channel tracking state",
        )

    def set_remote_sense(
        self, enabled: bool, channel: Channel | str | int = Channel.CH1
    ) -> None:
        """Enable or disable remote (four-wire) sensing."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        selected = self._check_channel(channel)
        mode = "EXT" if enabled else "INT"
        self.write(f"VOLT:SENS {mode},(@{selected.number})")

    def remote_sense(self, channel: Channel | str | int = Channel.CH1) -> bool:
        """Return whether remote sensing is enabled."""

        selected = self._check_channel(channel)
        response = self.query(f"VOLT:SENS? (@{selected.number})")
        text = response.strip().strip('"').upper()
        if text == "EXT":
            return True
        if text == "INT":
            return False
        raise ValueError(f"invalid remote sense response: {response!r}")

    def remote_sense_tripped(self, channel: Channel | str | int = Channel.CH1) -> bool:
        """Return whether remote sense protection has tripped."""

        selected = self._check_channel(channel)
        return self._parse_bool(
            self.query(f"VOLT:SENS:TRIP? (@{selected.number})"),
            "remote sense trip state",
        )

    def clear_remote_sense_trip(
        self, channel: Channel | str | int = Channel.CH1
    ) -> None:
        """Clear a tripped remote sense state."""

        selected = self._check_channel(channel)
        self.write(f"VOLT:SENS:TRIP:CLE (@{selected.number})")

    def set_output_delay_enabled(
        self, enabled: bool, channel: Channel | str | int = Channel.CH1
    ) -> None:
        """Enable or disable the output delay."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        selected = self._check_channel(channel)
        self.write(f"OUTP:DEL {int(enabled)},(@{selected.number})")

    def output_delay_enabled(self, channel: Channel | str | int = Channel.CH1) -> bool:
        """Return whether the output delay is enabled."""

        selected = self._check_channel(channel)
        return self._parse_bool(
            self.query(f"OUTP:DEL? (@{selected.number})"),
            "output delay state",
        )

    def set_output_delay(
        self, seconds: float, channel: Channel | str | int = Channel.CH1
    ) -> None:
        """Set the output delay in seconds."""

        selected = self._check_channel(channel)
        value = self._check_setpoint(seconds, "output delay", 0.0, 10.0)
        self.write(f"OUTP:DEL:DUR {self._format_number(value)},(@{selected.number})")

    def output_delay(self, channel: Channel | str | int = Channel.CH1) -> float:
        """Read the output delay in seconds."""

        selected = self._check_channel(channel)
        return self._parse_float(
            self.query(f"OUTP:DEL:DUR? (@{selected.number})"),
            "output delay",
        )

    def set_high_impedance(
        self, enabled: bool, channel: Channel | str | int = Channel.CH1
    ) -> None:
        """Enable or disable high-impedance output mode."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        selected = self._check_channel(channel)
        self.write(f"OUTP:IMP {int(enabled)},(@{selected.number})")

    def high_impedance(self, channel: Channel | str | int = Channel.CH1) -> bool:
        """Return whether high-impedance output mode is enabled."""

        selected = self._check_channel(channel)
        return self._parse_bool(
            self.query(f"OUTP:IMP? (@{selected.number})"),
            "high impedance state",
        )

    def set_slew_rate_reduction(
        self, enabled: bool, channel: Channel | str | int = Channel.CH1
    ) -> None:
        """Enable or disable reduced output slew rate."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        selected = self._check_channel(channel)
        self.write(f"OUTP:SRAT {int(enabled)},(@{selected.number})")

    def slew_rate_reduction(self, channel: Channel | str | int = Channel.CH1) -> bool:
        """Return whether reduced output slew rate is enabled."""

        selected = self._check_channel(channel)
        return self._parse_bool(
            self.query(f"OUTP:SRAT? (@{selected.number})"),
            "slew rate state",
        )

    def set_ramp(
        self,
        enabled: bool,
        duration: float | None = None,
        channel: Channel | str | int = Channel.CH1,
    ) -> None:
        """Enable or disable EasyRamp, optionally setting its duration."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        selected = self._check_channel(channel)
        if duration is not None:
            self.set_ramp_duration(duration, selected)
        self.write(f"VOLT:RAMP {int(enabled)},(@{selected.number})")

    def ramp_enabled(self, channel: Channel | str | int = Channel.CH1) -> bool:
        """Return whether EasyRamp is enabled."""

        selected = self._check_channel(channel)
        return self._parse_bool(
            self.query(f"VOLT:RAMP? (@{selected.number})"), "ramp state"
        )

    def set_ramp_duration(
        self, seconds: float, channel: Channel | str | int = Channel.CH1
    ) -> None:
        """Set the EasyRamp duration in seconds."""

        selected = self._check_channel(channel)
        value = self._check_setpoint(seconds, "ramp duration", 0.0, 60.0)
        self.write(f"VOLT:RAMP:DUR {self._format_number(value)},(@{selected.number})")

    def ramp_duration(self, channel: Channel | str | int = Channel.CH1) -> float:
        """Read the EasyRamp duration in seconds."""

        selected = self._check_channel(channel)
        return self._parse_float(
            self.query(f"VOLT:RAMP:DUR? (@{selected.number})"),
            "ramp duration",
        )

    def energy(self, channel: Channel | str | int = Channel.CH1) -> float:
        """Read the accumulated energy in watt-hours."""

        selected = self._check_channel(channel)
        return self._parse_float(
            self.query(f"MEAS:ENER? (@{selected.number})"), "energy"
        )

    def sample_count(self, channel: Channel | str | int = Channel.CH1) -> int:
        """Read the number of samples in the channel statistics."""

        selected = self._check_channel(channel)
        response = self.query(f"MEAS:STAT:COUN? (@{selected.number})")
        try:
            return int(float(response))
        except ValueError as exc:
            raise ValueError(f"invalid sample count response: {response!r}") from exc

    def measure_statistics(
        self, channel: Channel | str | int = Channel.CH1
    ) -> StatisticsMeasurement:
        """Read min/max/average statistics, energy, and sample count."""

        selected = self._check_channel(channel)
        number = selected.number
        return StatisticsMeasurement(
            voltage_min=self._parse_float(
                self.query(f"MEAS:VOLT:MIN? (@{number})"), "voltage minimum"
            ),
            voltage_max=self._parse_float(
                self.query(f"MEAS:VOLT:MAX? (@{number})"), "voltage maximum"
            ),
            voltage_avg=self._parse_float(
                self.query(f"MEAS:VOLT:AVG? (@{number})"), "voltage average"
            ),
            current_min=self._parse_float(
                self.query(f"MEAS:CURR:MIN? (@{number})"), "current minimum"
            ),
            current_max=self._parse_float(
                self.query(f"MEAS:CURR:MAX? (@{number})"), "current maximum"
            ),
            current_avg=self._parse_float(
                self.query(f"MEAS:CURR:AVG? (@{number})"), "current average"
            ),
            power_min=self._parse_float(
                self.query(f"MEAS:POW:MIN? (@{number})"), "power minimum"
            ),
            power_max=self._parse_float(
                self.query(f"MEAS:POW:MAX? (@{number})"), "power maximum"
            ),
            power_avg=self._parse_float(
                self.query(f"MEAS:POW:AVG? (@{number})"), "power average"
            ),
            energy=self.energy(selected),
            sample_count=self.sample_count(selected),
        )

    def reset_statistics(self, channel: Channel | str | int = Channel.CH1) -> None:
        """Reset the channel statistics and accumulated energy."""

        selected = self._check_channel(channel)
        self.write(f"MEAS:STAT:RES (@{selected.number})")

    def reset(self) -> None:
        """Reset the instrument to its default settings."""

        self.write("*RST")

    def clear_status(self) -> None:
        """Clear the status registers and output buffer."""

        self.write("*CLS")

    def next_error(self) -> ScpiError | None:
        """Pop one entry from the instrument error queue."""

        response = self.query("SYST:ERR?")
        code_text, _, message = response.partition(",")
        try:
            code = int(code_text.strip())
        except ValueError as exc:
            raise ValueError(f"invalid error queue response: {response!r}") from exc
        if code == 0:
            return None
        return ScpiError(code=code, message=message.strip().strip('"'))

    def check_errors(self) -> list[ScpiError]:
        """Drain and return all entries in the instrument error queue."""

        errors = []
        while True:
            error = self.next_error()
            if error is None:
                return errors
            errors.append(error)

    def _check_channel(self, channel: Channel | str | int) -> Channel:
        selected = Channel.coerce(channel)
        info = self.model_info
        if info is not None and selected.number > info.channel_count:
            raise ValueError(f"{selected.value} is not available on {info.name}")
        return selected

    def _voltage_limit(self, channel: Channel) -> float:
        info = self.model_info
        if info is None:
            return _FALLBACK_VOLTAGE_LIMIT
        return info.voltage_limit(channel)

    def _current_limit(self, channel: Channel) -> float:
        info = self.model_info
        if info is None:
            return _FALLBACK_CURRENT_LIMIT
        return info.current_limit(channel)

    @staticmethod
    def _check_setpoint(
        value: float, name: str, minimum: float, maximum: float
    ) -> float:
        if isinstance(value, bool):
            raise TypeError(f"{name} must be a finite number")
        try:
            numeric = float(value)
        except (OverflowError, TypeError, ValueError) as exc:
            raise TypeError(f"{name} must be a finite number") from exc
        if not math.isfinite(numeric) or not minimum <= numeric <= maximum:
            raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
        return numeric

    @staticmethod
    def _format_number(value: float) -> str:
        return format(value, ".12g")

    @staticmethod
    def _parse_float(response: str, name: str) -> float:
        try:
            return float(response)
        except ValueError as exc:
            raise ValueError(f"invalid {name} response: {response!r}") from exc

    @staticmethod
    def _parse_int(response: str, name: str) -> int:
        try:
            return int(response)
        except ValueError as exc:
            raise ValueError(f"invalid {name} response: {response!r}") from exc

    @staticmethod
    def _parse_bool(response: str, name: str) -> bool:
        text = response.strip().strip('"').upper()
        if text in {"1", "ON", "TRUE"}:
            return True
        if text in {"0", "OFF", "FALSE"}:
            return False
        raise ValueError(f"invalid {name} response: {response!r}")

    def close(self) -> None:
        """Close the resource and any resource manager owned by this plug."""

        resource, self._resource = self._resource, None
        manager, self._resource_manager = self._resource_manager, None
        try:
            if resource is not None:
                resource.close()
        finally:
            if self._owns_resource_manager and manager is not None:
                manager.close()

    def tearDown(self) -> None:
        """Switch all outputs off and release the connection."""

        if self._resource is not None:
            for channel in self.channels:
                try:
                    self.set_output(False, channel)
                except (
                    pyvisa.errors.VisaIOError,
                    OSError,
                    RuntimeError,
                    ValueError,
                ):
                    pass
        self.close()


__all__ = [
    "MODELS",
    "Channel",
    "ModelInfo",
    "Readback",
    "RegulationMode",
    "RohdeSchwarzNGP800Plug",
    "ScpiError",
    "StatisticsMeasurement",
    "SupplyMeasurement",
]
