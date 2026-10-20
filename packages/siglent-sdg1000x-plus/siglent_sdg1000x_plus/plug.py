"""OpenHTF/PyVISA plug for the Siglent SDG1000X Plus."""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

import pyvisa
from openhtf.plugs import BasePlug


class Channel(str, Enum):
    """Generator output channels."""

    C1 = "C1"
    C2 = "C2"

    @classmethod
    def coerce(cls, value: Channel | str | int) -> Channel:
        if isinstance(value, cls):
            return value
        if isinstance(value, bool):
            raise TypeError("channel must be a Channel, C1/C2, or 1/2")
        key = str(value).strip().upper()
        key = {"1": "C1", "2": "C2", "CH1": "C1", "CH2": "C2"}.get(key, key)
        try:
            return cls(key)
        except ValueError as exc:
            raise ValueError(f"unsupported channel: {value!r}") from exc


class Waveform(str, Enum):
    """Basic waveform types supported by the BSWV subsystem."""

    SINE = "SINE"
    SQUARE = "SQUARE"
    RAMP = "RAMP"
    PULSE = "PULSE"
    NOISE = "NOISE"
    ARBITRARY = "ARB"
    DC = "DC"
    PRBS = "PRBS"
    MULTIPULSE = "MULTPULSE"
    SEQUENCE = "SEQUENCE"

    @classmethod
    def coerce(cls, value: Waveform | str) -> Waveform:
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise TypeError("waveform must be a Waveform or string")
        key = "".join(char for char in value.upper() if char.isalnum())
        aliases = {
            "ARB": cls.ARBITRARY,
            "ARBITRARY": cls.ARBITRARY,
            "MULTIPULSE": cls.MULTIPULSE,
        }
        if key in aliases:
            return aliases[key]
        try:
            return cls(key)
        except ValueError as exc:
            raise ValueError(f"unsupported waveform: {value!r}") from exc


class OutputPolarity(str, Enum):
    """Output connector polarity."""

    NORMAL = "NOR"
    INVERTED = "INVT"

    @classmethod
    def coerce(cls, value: OutputPolarity | str) -> OutputPolarity:
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise TypeError("polarity must be an OutputPolarity or string")
        key = value.strip().upper()
        aliases = {
            "NOR": cls.NORMAL,
            "NORMAL": cls.NORMAL,
            "INVT": cls.INVERTED,
            "INVERTED": cls.INVERTED,
            "INVERT": cls.INVERTED,
        }
        try:
            return aliases[key]
        except KeyError as exc:
            raise ValueError(f"unsupported output polarity: {value!r}") from exc


@dataclass(frozen=True)
class WaveformSettings:
    """Basic waveform settings read from one channel."""

    waveform: Waveform
    frequency_hz: float | None = None
    period_s: float | None = None
    amplitude_vpp: float | None = None
    amplitude_vrms: float | None = None
    amplitude_dbm: float | None = None
    offset_v: float | None = None
    phase_deg: float | None = None
    symmetry_pct: float | None = None
    duty_cycle_pct: float | None = None
    pulse_width_s: float | None = None
    rise_time_s: float | None = None
    fall_time_s: float | None = None


@dataclass(frozen=True)
class OutputSettings:
    """Output state, configured load, and polarity."""

    enabled: bool
    load_ohms: float | None
    polarity: OutputPolarity


_PARAMETER_KEYS = {
    "frequency_hz": ("FRQ", "frequency"),
    "period_s": ("PERI", "period"),
    "amplitude_vpp": ("AMP", "amplitude"),
    "amplitude_vrms": ("AMPVRMS", "amplitude"),
    "amplitude_dbm": ("AMPDBM", "amplitude"),
    "offset_v": ("OFST", "offset"),
    "phase_deg": ("PHSE", "phase"),
    "symmetry_pct": ("SYM", "symmetry"),
    "duty_cycle_pct": ("DUTY", "duty_cycle"),
    "pulse_width_s": ("WIDTH", "pulse_width"),
    "rise_time_s": ("RISE", "rise_time"),
    "fall_time_s": ("FALL", "fall_time"),
}


class SiglentSDG1000XPlusPlug(BasePlug):
    """Control an SDG1000X Plus over any PyVISA-supported interface."""

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
            resource_name = os.environ.get("SIGLENT_SDG1000X_PLUS_RESOURCE")
        if resource is None and resource_name is None:
            raise ValueError(
                "resource_name is required (or set SIGLENT_SDG1000X_PLUS_RESOURCE)"
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
        self.write(command)
        return str(self._resource.read()).strip()

    def identify(self) -> str:
        """Return the instrument identity string."""

        return self.query("*IDN?")

    @property
    def tracking_enabled(self) -> bool:
        """Read whether C2 tracks C1."""

        fields = _parse_pairs(self.query("COUPling?"), "COUP")
        try:
            return _parse_bool(fields["TRACE"])
        except KeyError as exc:
            raise ValueError("coupling response has no TRACE field") from exc

    def set_tracking(self, enabled: bool) -> None:
        """Enable or disable C1-to-C2 tracking."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self.write(f"COUP TRACE,{'ON' if enabled else 'OFF'}")

    def configure_waveform(
        self,
        waveform: Waveform | str,
        *,
        channel: Channel | str | int = Channel.C1,
        frequency_hz: float | None = None,
        period_s: float | None = None,
        amplitude_vpp: float | None = None,
        offset_v: float | None = None,
        phase_deg: float | None = None,
        symmetry_pct: float | None = None,
        duty_cycle_pct: float | None = None,
        pulse_width_s: float | None = None,
        rise_time_s: float | None = None,
        fall_time_s: float | None = None,
    ) -> None:
        """Set a waveform and any supplied basic parameters."""

        selected_waveform = Waveform.coerce(waveform)
        selected_channel = Channel.coerce(channel)
        if frequency_hz is not None and period_s is not None:
            raise ValueError("specify frequency_hz or period_s, not both")
        values = {
            "FRQ": frequency_hz,
            "PERI": period_s,
            "AMP": amplitude_vpp,
            "OFST": offset_v,
            "PHSE": phase_deg,
            "SYM": symmetry_pct,
            "DUTY": duty_cycle_pct,
            "WIDTH": pulse_width_s,
            "RISE": rise_time_s,
            "FALL": fall_time_s,
        }
        for parameter, value in values.items():
            if value is not None:
                self._validate_wave_parameter(selected_waveform, parameter, value)
        self._ensure_editable_channel(selected_channel)

        prefix = selected_channel.value
        self.write(f"{prefix}:BSWV WVTP,{selected_waveform.value}")
        for parameter, value in values.items():
            if value is not None:
                self.write(f"{prefix}:BSWV {parameter},{float(value)}")

    def set_waveform(
        self, waveform: Waveform | str, channel: Channel | str | int = Channel.C1
    ) -> None:
        """Select a basic waveform type."""

        self.configure_waveform(waveform, channel=channel)

    def waveform_settings(
        self, channel: Channel | str | int = Channel.C1
    ) -> WaveformSettings:
        """Read basic waveform settings."""

        selected = Channel.coerce(channel)
        fields = _parse_pairs(self.query(f"{selected.value}:BSWV?"), "BSWV")
        try:
            waveform = Waveform.coerce(fields["WVTP"])
        except KeyError as exc:
            raise ValueError("waveform response has no WVTP field") from exc
        values = {}
        for attribute, (key, _) in _PARAMETER_KEYS.items():
            if key in fields:
                values[attribute] = _parse_number(fields[key])
        return WaveformSettings(waveform=waveform, **values)

    def set_frequency(
        self, frequency_hz: float, channel: Channel | str | int = Channel.C1
    ) -> None:
        """Set frequency in hertz."""

        self._set_basic_parameter(channel, "FRQ", frequency_hz)

    def frequency(self, channel: Channel | str | int = Channel.C1) -> float:
        """Read frequency in hertz."""

        return self._read_basic_parameter(channel, "FRQ")

    def set_period(
        self, period_s: float, channel: Channel | str | int = Channel.C1
    ) -> None:
        """Set period in seconds."""

        self._set_basic_parameter(channel, "PERI", period_s)

    def period(self, channel: Channel | str | int = Channel.C1) -> float:
        """Read period in seconds."""

        return self._read_basic_parameter(channel, "PERI")

    def set_amplitude(
        self, amplitude_vpp: float, channel: Channel | str | int = Channel.C1
    ) -> None:
        """Set amplitude in peak-to-peak volts."""

        self._set_basic_parameter(channel, "AMP", amplitude_vpp)

    def amplitude(self, channel: Channel | str | int = Channel.C1) -> float:
        """Read amplitude in peak-to-peak volts."""

        return self._read_basic_parameter(channel, "AMP")

    def set_offset(
        self, offset_v: float, channel: Channel | str | int = Channel.C1
    ) -> None:
        """Set DC offset in volts."""

        self._set_basic_parameter(channel, "OFST", offset_v)

    def offset(self, channel: Channel | str | int = Channel.C1) -> float:
        """Read DC offset in volts."""

        return self._read_basic_parameter(channel, "OFST")

    def set_phase(
        self, phase_deg: float, channel: Channel | str | int = Channel.C1
    ) -> None:
        """Set phase in degrees."""

        self._set_basic_parameter(channel, "PHSE", phase_deg)

    def phase(self, channel: Channel | str | int = Channel.C1) -> float:
        """Read phase in degrees."""

        return self._read_basic_parameter(channel, "PHSE")

    def set_output(
        self, enabled: bool, channel: Channel | str | int = Channel.C1
    ) -> None:
        """Enable or disable one channel output."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        selected = Channel.coerce(channel)
        self._ensure_editable_channel(selected)
        self.write(f"{selected.value}:OUTP {'ON' if enabled else 'OFF'}")

    def is_output_enabled(self, channel: Channel | str | int = Channel.C1) -> bool:
        """Read one channel's output state."""

        selected = Channel.coerce(channel)
        response = self.query(f"{selected.value}:OUTP?")
        match = re.search(r"\bOUTP(?:UT)?\s+(ON|OFF)\b", response, re.IGNORECASE)
        if match is None:
            raise ValueError(f"invalid output state response: {response!r}")
        return match.group(1).upper() == "ON"

    def set_outputs(self, enabled: bool) -> None:
        """Enable or disable both channel outputs together."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self.write(f"OUT_BOTHCH {'ON' if enabled else 'OFF'}")

    def output_settings(
        self, channel: Channel | str | int = Channel.C1
    ) -> OutputSettings:
        """Read output state, load, and polarity."""

        selected = Channel.coerce(channel)
        response = self.query(f"{selected.value}:OUTP?")
        state = re.search(r"\bOUTP(?:UT)?\s+(ON|OFF)\b", response, re.IGNORECASE)
        load = re.search(r"\bLOAD\s*,\s*([^,\s]+)", response, re.IGNORECASE)
        polarity = re.search(r"\bPLRT\s*,\s*([^,\s]+)", response, re.IGNORECASE)
        if state is None or load is None or polarity is None:
            raise ValueError(f"invalid output settings response: {response!r}")
        load_text = load.group(1).upper()
        load_ohms = None if load_text in {"HZ", "HIZ"} else _parse_number(load_text)
        return OutputSettings(
            enabled=state.group(1).upper() == "ON",
            load_ohms=load_ohms,
            polarity=OutputPolarity.coerce(polarity.group(1)),
        )

    def set_load(
        self, load_ohms: float | str | None, channel: Channel | str | int = Channel.C1
    ) -> None:
        """Set the displayed load to HiZ or 50 Ω through 100 kΩ."""

        selected = Channel.coerce(channel)
        self._ensure_editable_channel(selected)
        if load_ohms is None or (
            isinstance(load_ohms, str) and load_ohms.strip().upper() in {"HZ", "HIZ"}
        ):
            value = "HZ"
        else:
            numeric = _finite_number(load_ohms, "load_ohms")
            if not 50 <= numeric <= 100_000:
                raise ValueError("load_ohms must be HiZ or between 50 and 100000")
            value = str(numeric)
        self.write(f"{selected.value}:OUTP LOAD,{value}")

    def set_polarity(
        self,
        polarity: OutputPolarity | str,
        channel: Channel | str | int = Channel.C1,
    ) -> None:
        """Set normal or inverted output polarity."""

        selected = Channel.coerce(channel)
        self._ensure_editable_channel(selected)
        selected_polarity = OutputPolarity.coerce(polarity)
        self.write(f"{selected.value}:OUTP PLRT,{selected_polarity.value}")

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
        """Disable both outputs and release the VISA connection."""

        if self._resource is not None:
            try:
                self.set_outputs(False)
            except (pyvisa.errors.VisaIOError, OSError, RuntimeError):
                pass
        self.close()

    def _set_basic_parameter(
        self, channel: Channel | str | int, parameter: str, value: float
    ) -> None:
        numeric = _validate_basic_parameter(parameter, value)
        selected = Channel.coerce(channel)
        self._ensure_editable_channel(selected)
        self.write(f"{selected.value}:BSWV {parameter},{numeric}")

    def _read_basic_parameter(
        self, channel: Channel | str | int, parameter: str
    ) -> float:
        selected = Channel.coerce(channel)
        fields = _parse_pairs(self.query(f"{selected.value}:BSWV?"), "BSWV")
        try:
            return _parse_number(fields[parameter])
        except KeyError as exc:
            raise ValueError(f"waveform response has no {parameter} field") from exc

    def _ensure_editable_channel(self, channel: Channel) -> None:
        if channel is Channel.C2 and self.tracking_enabled:
            raise ValueError("C1 controls C2 while channel tracking is enabled")

    @staticmethod
    def _validate_wave_parameter(
        waveform: Waveform, parameter: str, value: float
    ) -> None:
        _validate_basic_parameter(parameter, value)
        allowed = {
            "FRQ": waveform not in {Waveform.NOISE, Waveform.DC},
            "PERI": waveform not in {Waveform.NOISE, Waveform.DC},
            "AMP": waveform not in {Waveform.NOISE, Waveform.DC},
            "OFST": waveform is not Waveform.NOISE,
            "PHSE": waveform not in {Waveform.NOISE, Waveform.PULSE, Waveform.DC},
            "SYM": waveform is Waveform.RAMP,
            "DUTY": waveform in {Waveform.SQUARE, Waveform.PULSE},
            "WIDTH": waveform is Waveform.PULSE,
            "RISE": waveform is Waveform.PULSE,
            "FALL": waveform is Waveform.PULSE,
        }[parameter]
        if not allowed:
            raise ValueError(f"{parameter} is not valid for {waveform.value}")


def _validate_basic_parameter(parameter: str, value: float) -> float:
    numeric = _finite_number(value, parameter)
    if parameter in {"FRQ", "PERI", "WIDTH", "RISE", "FALL"} and numeric <= 0:
        raise ValueError(f"{parameter} must be positive")
    if parameter == "AMP" and numeric < 0:
        raise ValueError("AMP must be non-negative")
    if parameter in {"PHSE", "SYM", "DUTY"} and not 0 <= numeric <= 360:
        raise ValueError(f"{parameter} is outside its valid range")
    if parameter in {"SYM", "DUTY"} and numeric > 100:
        raise ValueError(f"{parameter} must be between 0 and 100")
    return numeric


def _finite_number(value: float | str, name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a finite number")
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be a finite number") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite")
    return numeric


def _parse_pairs(response: str, header: str) -> dict[str, str]:
    match = re.search(re.escape(header), response, re.IGNORECASE)
    payload = response[match.end() :] if match else response
    tokens = [token.strip() for token in payload.split(",") if token.strip()]
    if len(tokens) % 2:
        raise ValueError(f"invalid {header} response: {response!r}")
    return {
        tokens[index].upper(): tokens[index + 1] for index in range(0, len(tokens), 2)
    }


def _parse_number(value: str) -> float:
    match = re.fullmatch(
        r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-zµμ%]*)\s*",
        value,
    )
    if match is None:
        raise ValueError(f"invalid numeric SCPI value: {value!r}")
    numeric = float(match.group(1))
    unit = match.group(2).upper().replace("µ", "U").replace("Μ", "U")
    factors = {
        "": 1.0,
        "%": 1.0,
        "HZ": 1.0,
        "KHZ": 1e3,
        "MHZ": 1e6,
        "GHZ": 1e9,
        "S": 1.0,
        "SEC": 1.0,
        "MS": 1e-3,
        "MSEC": 1e-3,
        "US": 1e-6,
        "USEC": 1e-6,
        "NS": 1e-9,
        "NSEC": 1e-9,
        "PS": 1e-12,
        "V": 1.0,
        "MV": 1e-3,
        "UV": 1e-6,
        "NV": 1e-9,
        "VPP": 1.0,
        "MVPP": 1e-3,
        "VRMS": 1.0,
        "MVRMS": 1e-3,
        "DBM": 1.0,
        "OHM": 1.0,
        "OHMS": 1.0,
        "KOHM": 1e3,
        "DEG": 1.0,
        "DEGREE": 1.0,
        "DEGREES": 1.0,
    }
    if unit not in factors:
        raise ValueError(f"unsupported numeric SCPI unit: {unit!r}")
    numeric *= factors[unit]
    if not math.isfinite(numeric):
        raise ValueError(f"non-finite numeric SCPI value: {value!r}")
    return numeric


def _parse_bool(value: str) -> bool:
    normalized = value.strip().upper().strip('"')
    if normalized in {"ON", "1", "TRUE"}:
        return True
    if normalized in {"OFF", "0", "FALSE"}:
        return False
    raise ValueError(f"invalid boolean SCPI value: {value!r}")


__all__ = [
    "Channel",
    "OutputPolarity",
    "OutputSettings",
    "SiglentSDG1000XPlusPlug",
    "Waveform",
    "WaveformSettings",
]
