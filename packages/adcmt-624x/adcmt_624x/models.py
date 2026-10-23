"""Public enums and value objects for the ADCMT 6241A/6242 plug."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum

_NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?")
_INTEGER_RE = re.compile(r"[-+]?\d+")
_MEASUREMENT_HEADERS = frozenset({"DV", "DI", "RM", "EE"})
_SUB_HEADERS = frozenset({"U", "B", "O", "Z", "F", "E", "H", "G", "L", "C", "N"})


def _normalize(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _coerce_enum(
    enum_type: type[Enum],
    value: object,
    aliases: dict[str, Enum],
    label: str,
) -> Enum:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a {enum_type.__name__} or string")
    text = value.strip().strip("\"'")
    exact = text.upper()
    for item in enum_type:
        if exact in {item.name.upper(), str(item.value).upper()}:
            return item
    key = _normalize(text)
    for item in enum_type:
        if key in {_normalize(item.name), _normalize(str(item.value))}:
            return item
    try:
        return aliases[key]
    except KeyError as exc:
        raise ValueError(f"unsupported {label}: {value!r}") from exc


class SourceFunction(str, Enum):
    """Source function selected with ``VF`` or ``IF``."""

    VOLTAGE = "VF"
    CURRENT = "IF"

    @classmethod
    def coerce(cls, value: SourceFunction | str) -> SourceFunction:
        return _coerce_enum(
            cls,
            value,
            {
                "V": cls.VOLTAGE,
                "VOLT": cls.VOLTAGE,
                "VOLTAGE": cls.VOLTAGE,
                "VS": cls.VOLTAGE,
                "I": cls.CURRENT,
                "CURR": cls.CURRENT,
                "CURRENT": cls.CURRENT,
                "IS": cls.CURRENT,
            },
            "source function",
        )


class SourceMode(str, Enum):
    """Source mode selected with ``MD0`` through ``MD3``."""

    DC = "MD0"
    PULSE = "MD1"
    DC_SWEEP = "MD2"
    PULSE_SWEEP = "MD3"

    @classmethod
    def coerce(cls, value: SourceMode | str) -> SourceMode:
        return _coerce_enum(
            cls,
            value,
            {
                "DCSWEEP": cls.DC_SWEEP,
                "SWEEP": cls.DC_SWEEP,
                "PULSESWEEP": cls.PULSE_SWEEP,
            },
            "source mode",
        )


class MeasurementFunction(str, Enum):
    """Measurement function selected with ``F0`` through ``F3``."""

    OFF = "F0"
    DC_VOLTAGE = "F1"
    DC_CURRENT = "F2"
    RESISTANCE = "F3"

    @classmethod
    def coerce(cls, value: MeasurementFunction | str) -> MeasurementFunction:
        return _coerce_enum(
            cls,
            value,
            {
                "NONE": cls.OFF,
                "DCV": cls.DC_VOLTAGE,
                "VOLT": cls.DC_VOLTAGE,
                "VOLTAGE": cls.DC_VOLTAGE,
                "DCI": cls.DC_CURRENT,
                "CURR": cls.DC_CURRENT,
                "CURRENT": cls.DC_CURRENT,
                "OHM": cls.RESISTANCE,
                "RES": cls.RESISTANCE,
                "RESISTANCE": cls.RESISTANCE,
            },
            "measurement function",
        )


class TriggerMode(str, Enum):
    """Trigger mode selected with ``M0`` (auto) or ``M1`` (hold)."""

    AUTO = "M0"
    HOLD = "M1"

    @classmethod
    def coerce(cls, value: TriggerMode | str) -> TriggerMode:
        return _coerce_enum(
            cls,
            value,
            {"MANUAL": cls.HOLD, "MAN": cls.HOLD},
            "trigger mode",
        )


class MeasurementRange(str, Enum):
    """Measurement range selected with ``R0`` (auto) or ``R1`` (fixed)."""

    AUTO = "R0"
    FIXED = "R1"

    @classmethod
    def coerce(cls, value: MeasurementRange | str) -> MeasurementRange:
        return _coerce_enum(
            cls,
            value,
            {"AUTORANGE": cls.AUTO, "LIMITER": cls.FIXED},
            "measurement range",
        )


class IntegrationTime(str, Enum):
    """Integration time selected with ``IT0`` through ``IT8``."""

    T100US = "IT0"
    T500US = "IT1"
    T1MS = "IT2"
    T5MS = "IT3"
    T10MS = "IT4"
    T1PLC = "IT5"
    T100MS = "IT6"
    T200MS = "IT7"
    SAMPLE_HOLD = "IT8"

    @classmethod
    def coerce(cls, value: IntegrationTime | str) -> IntegrationTime:
        return _coerce_enum(
            cls,
            value,
            {
                "100US": cls.T100US,
                "500US": cls.T500US,
                "1MS": cls.T1MS,
                "5MS": cls.T5MS,
                "10MS": cls.T10MS,
                "1PLC": cls.T1PLC,
                "100MS": cls.T100MS,
                "200MS": cls.T200MS,
                "SH": cls.SAMPLE_HOLD,
                "SAMPLEHOLD": cls.SAMPLE_HOLD,
            },
            "integration time",
        )


class DisplayDigits(str, Enum):
    """Displayed measurement digits selected with ``RE3`` through ``RE5``."""

    D3_5 = "RE3"
    D4_5 = "RE4"
    D5_5 = "RE5"

    @classmethod
    def coerce(cls, value: DisplayDigits | str) -> DisplayDigits:
        return _coerce_enum(
            cls,
            value,
            {
                "3": cls.D3_5,
                "35": cls.D3_5,
                "4": cls.D4_5,
                "45": cls.D4_5,
                "5": cls.D5_5,
                "55": cls.D5_5,
            },
            "display digits",
        )


class UnitDisplay(str, Enum):
    """Unit display mode selected with ``DM0`` or ``DM1``."""

    DECIMAL = "DM0"
    EXPONENT = "DM1"

    @classmethod
    def coerce(cls, value: UnitDisplay | str) -> UnitDisplay:
        return _coerce_enum(
            cls,
            value,
            {"DEC": cls.DECIMAL, "EXP": cls.EXPONENT},
            "unit display",
        )


class RemoteSensing(str, Enum):
    """Remote sensing mode selected with ``RS0`` or ``RS1``."""

    TWO_WIRE = "RS0"
    FOUR_WIRE = "RS1"

    @classmethod
    def coerce(cls, value: RemoteSensing | str) -> RemoteSensing:
        return _coerce_enum(
            cls,
            value,
            {
                "2W": cls.TWO_WIRE,
                "2WIRE": cls.TWO_WIRE,
                "4W": cls.FOUR_WIRE,
                "4WIRE": cls.FOUR_WIRE,
            },
            "remote sensing mode",
        )


class OutputState(str, Enum):
    """Output state reported by ``OPR?``, ``SBY?``, or ``SUS?``."""

    STANDBY = "SBY"
    OPERATE = "OPR"
    SUSPEND = "SUS"


class BufferMemory(str, Enum):
    """Measurement buffer mode selected with ``ST0`` through ``ST2``."""

    OFF = "ST0"
    NORMAL = "ST1"
    BURST = "ST2"

    @classmethod
    def coerce(cls, value: BufferMemory | str) -> BufferMemory:
        return _coerce_enum(
            cls,
            value,
            {"ON": cls.NORMAL, "BURSTON": cls.BURST},
            "buffer memory mode",
        )


class BuzzerMode(str, Enum):
    """Buzzer mode selected with ``BZ0`` through ``BZ4``."""

    OFF = "BZ0"
    COMPARATOR_HI = "BZ1"
    COMPARATOR_GO = "BZ2"
    COMPARATOR_LO = "BZ3"
    COMPARATOR_HI_LO = "BZ4"

    @classmethod
    def coerce(cls, value: BuzzerMode | str) -> BuzzerMode:
        return _coerce_enum(
            cls,
            value,
            {
                "HI": cls.COMPARATOR_HI,
                "GO": cls.COMPARATOR_GO,
                "LO": cls.COMPARATOR_LO,
                "HILO": cls.COMPARATOR_HI_LO,
            },
            "buzzer mode",
        )


class VoltageSourceRange(str, Enum):
    """Voltage source range selected with ``SVR3`` through ``SVR5``.

    ``SVR5`` is the 30 V range on the 6241A and the 6 V range on the 6242.
    """

    AUTO = "SVRX"
    RANGE_300MV = "SVR3"
    RANGE_3V = "SVR4"
    RANGE_30V = "SVR5"

    @classmethod
    def coerce(cls, value: VoltageSourceRange | str) -> VoltageSourceRange:
        return _coerce_enum(
            cls,
            value,
            {
                "300MV": cls.RANGE_300MV,
                "03V": cls.RANGE_300MV,
                "3V": cls.RANGE_3V,
                "30V": cls.RANGE_30V,
                "6V": cls.RANGE_30V,
                "SVRX3": cls.AUTO,
                "SVRX4": cls.AUTO,
                "SVRX5": cls.AUTO,
            },
            "voltage source range",
        )


class CurrentSourceRange(str, Enum):
    """Current source range selected with ``SIR-1`` through ``SIR5``.

    ``SIR4`` is the 500 mA range on the 6241A and the 3 A range on the 6242.
    ``SIR5`` is the 5 A range and is available only on the 6242.
    """

    AUTO = "SIRX"
    RANGE_30UA = "SIR-1"
    RANGE_300UA = "SIR0"
    RANGE_3MA = "SIR1"
    RANGE_30MA = "SIR2"
    RANGE_300MA = "SIR3"
    RANGE_500MA = "SIR4"
    RANGE_5A = "SIR5"

    @classmethod
    def coerce(cls, value: CurrentSourceRange | str) -> CurrentSourceRange:
        return _coerce_enum(
            cls,
            value,
            {
                "30UA": cls.RANGE_30UA,
                "300UA": cls.RANGE_300UA,
                "3MA": cls.RANGE_3MA,
                "30MA": cls.RANGE_30MA,
                "300MA": cls.RANGE_300MA,
                "500MA": cls.RANGE_500MA,
                "3A": cls.RANGE_500MA,
                "5A": cls.RANGE_5A,
                "SIRX0": cls.AUTO,
                "SIRX1": cls.AUTO,
                "SIRX2": cls.AUTO,
                "SIRX3": cls.AUTO,
                "SIRX4": cls.AUTO,
                "SIRX5": cls.AUTO,
            },
            "current source range",
        )


class SweepRange(str, Enum):
    """Sweep range selected with ``SR0`` (auto) or ``SR1`` (fixed)."""

    AUTO = "SR0"
    FIXED = "SR1"

    @classmethod
    def coerce(cls, value: SweepRange | str) -> SweepRange:
        return _coerce_enum(
            cls,
            value,
            {"AUTORANGE": cls.AUTO},
            "sweep range",
        )


class FilterMode(str, Enum):
    """Source filter selected with ``FL0`` (slow) or ``FL1`` (fast)."""

    SLOW = "FL0"
    FAST = "FL1"

    @classmethod
    def coerce(cls, value: FilterMode | str) -> FilterMode:
        return _coerce_enum(cls, value, {}, "filter mode")


class SuspendMode(str, Enum):
    """Suspend output mode selected with ``SUZ0`` (HiZ) or ``SUZ1`` (LoZ)."""

    HIGH_IMPEDANCE = "SUZ0"
    LOW_IMPEDANCE = "SUZ1"

    @classmethod
    def coerce(cls, value: SuspendMode | str) -> SuspendMode:
        return _coerce_enum(
            cls,
            value,
            {"HIZ": cls.HIGH_IMPEDANCE, "LOZ": cls.LOW_IMPEDANCE},
            "suspend mode",
        )


class SyncOutputMode(str, Enum):
    """Synchronous control output selected with ``CP0`` through ``CP6``."""

    MEASURE_START = "CP0"
    MEASURE_END = "CP1"
    COMPARATOR_HI = "CP2"
    COMPARATOR_GO = "CP3"
    COMPARATOR_LO = "CP4"
    COMPARATOR_HI_LO = "CP5"
    SYNC_OUT = "CP6"

    @classmethod
    def coerce(cls, value: SyncOutputMode | str) -> SyncOutputMode:
        return _coerce_enum(
            cls,
            value,
            {
                "MEASFRONT": cls.MEASURE_START,
                "MEASSTART": cls.MEASURE_START,
                "MEASEND": cls.MEASURE_END,
                "HI": cls.COMPARATOR_HI,
                "GO": cls.COMPARATOR_GO,
                "LO": cls.COMPARATOR_LO,
                "HILO": cls.COMPARATOR_HI_LO,
                "SYNCOUT": cls.SYNC_OUT,
            },
            "synchronous output mode",
        )


class SyncWidth(str, Enum):
    """Synchronous control output width selected with ``CW0`` or ``CW1``."""

    T10US = "CW0"
    T100US = "CW1"

    @classmethod
    def coerce(cls, value: SyncWidth | str) -> SyncWidth:
        return _coerce_enum(
            cls,
            value,
            {"10US": cls.T10US, "100US": cls.T100US},
            "synchronous output width",
        )


class InterlockMode(str, Enum):
    """Interlock input/output mode selected with ``OP0`` through ``OP4``."""

    STANDBY_IN = "OP0"
    OPERATE_STANDBY_IN = "OP1"
    INTERLOCK_IN = "OP2"
    OPERATE_OUT = "OP3"
    OPERATE_SUSPEND_IN = "OP4"

    @classmethod
    def coerce(cls, value: InterlockMode | str) -> InterlockMode:
        return _coerce_enum(
            cls,
            value,
            {
                "STBYIN": cls.STANDBY_IN,
                "OPRSRBYIN": cls.OPERATE_STANDBY_IN,
                "INTERLOCKIN": cls.INTERLOCK_IN,
                "OPROUT": cls.OPERATE_OUT,
                "OPRSUSIN": cls.OPERATE_SUSPEND_IN,
            },
            "interlock mode",
        )


class BlockDelimiter(str, Enum):
    """Talker block delimiter selected with ``DL0`` through ``DL3``."""

    CRLF = "DL0"
    LF = "DL1"
    EOI = "DL2"
    LF_EOI = "DL3"

    @classmethod
    def coerce(cls, value: BlockDelimiter | str) -> BlockDelimiter:
        return _coerce_enum(
            cls,
            value,
            {"CRLFEOI": cls.CRLF, "LFEOI": cls.LF_EOI},
            "block delimiter",
        )


@dataclass(frozen=True)
class Measurement:
    """One parsed 6241A/6242 measurement value.

    ``header`` is the two-character main header (``DV``, ``DI``, ``RM``, or
    ``EE``) when the instrument's header output is enabled. ``flags`` contains
    the one-character sub-header flags such as ``U`` (high limit), ``B`` (low
    limit), ``O`` (range over), or ``E`` (calculation error).
    """

    value: float
    header: str | None = None
    flags: frozenset[str] = frozenset()
    raw: str = ""

    @property
    def is_empty(self) -> bool:
        """True when the instrument had no data for the requested memory slot."""

        return self.header == "EE" or self.value == 8.88888e30

    @property
    def is_over_range(self) -> bool:
        return "O" in self.flags or self.value == 9.99999e35

    @property
    def is_high_limit(self) -> bool:
        return "U" in self.flags or self.value == 9.99999e37

    @property
    def is_low_limit(self) -> bool:
        return "B" in self.flags or self.value == 9.99999e36

    @property
    def is_count_too_low(self) -> bool:
        return "F" in self.flags or self.value == 9.99999e34

    @property
    def is_source_zero(self) -> bool:
        return "Z" in self.flags or self.value == 9.99999e33

    @property
    def is_scaling_error(self) -> bool:
        return "E" in self.flags or self.value == 9.99999e32

    @property
    def is_total_error(self) -> bool:
        return self.value == 9.99999e31

    @property
    def is_valid(self) -> bool:
        """True when the value is a normal reading without error flags."""

        return not (
            self.is_empty
            or self.is_over_range
            or self.is_high_limit
            or self.is_low_limit
            or self.is_count_too_low
            or self.is_source_zero
            or self.is_scaling_error
            or self.is_total_error
        )


@dataclass(frozen=True)
class Statistics:
    """Results of the 6241A/6242 MAX/MIN calculation."""

    average: float
    maximum: float
    minimum: float
    total: float
    count: int


def _parse_measurement_field(field: str) -> Measurement:
    header: str | None = None
    flags: set[str] = set()
    number_text = field

    main_header = field[:2].upper()
    if main_header in _MEASUREMENT_HEADERS:
        header = main_header
        rest = field[2:]
        if rest and rest[0] in _SUB_HEADERS:
            flags.add(rest[0])
            number_text = rest[1:]
        elif rest and rest[0] == " ":
            number_text = rest[1:]
        else:
            number_text = rest

    number_text = number_text.strip()
    if not number_text:
        raise ValueError(f"missing numeric value in measurement field {field!r}")
    try:
        value = float(number_text)
    except ValueError:
        matches = _NUMBER_RE.findall(number_text)
        if not matches:
            raise ValueError(
                f"could not parse measurement value from {field!r}"
            ) from None
        value = float(matches[-1])

    return Measurement(
        value=value,
        header=header,
        flags=frozenset(flags),
        raw=field,
    )


def parse_measurements(response: str) -> tuple[Measurement, ...]:
    """Parse one or more values from the instrument's talker output.

    The 6241A/6242 can prefix each value with a two-character main header and a
    one-character sub-header. Headers may be disabled with ``OH0``. Multiple
    values are comma-delimited, and the transport may return CR/LF or NUL
    padding around them.
    """

    if not isinstance(response, str):
        raise TypeError("response must be a string")
    normalized = (
        response.replace("\x00", "").replace("\r", "").replace("\n", ",").strip()
    )
    if not normalized.strip(",").strip():
        raise ValueError("the instrument returned an empty measurement response")

    measurements = [
        _parse_measurement_field(field.strip())
        for field in normalized.split(",")
        if field.strip()
    ]
    if not measurements:
        raise ValueError(f"could not parse measurement response: {response!r}")
    return tuple(measurements)


def parse_measurement(response: str) -> Measurement:
    """Parse exactly one measurement value."""

    measurements = parse_measurements(response)
    if len(measurements) != 1:
        raise ValueError(f"expected one measurement, received {len(measurements)}")
    return measurements[0]


def parse_number(response: str, name: str = "response") -> float:
    """Return the last numeric token in a response."""

    matches = _NUMBER_RE.findall(response)
    if not matches:
        raise ValueError(f"could not parse a number from {name}: {response!r}")
    return float(matches[-1])


def parse_integer(response: str, name: str = "response") -> int:
    """Return the first integer token in a response."""

    matches = _INTEGER_RE.findall(response)
    if not matches:
        raise ValueError(f"could not parse an integer from {name}: {response!r}")
    return int(matches[0])


def format_number(
    value: float,
    name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> str:
    """Format a finite numeric command parameter."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and number < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{name} must not exceed {maximum}")
    return format(number, ".12g")


def check_integer(value: int, name: str, minimum: int, maximum: int) -> int:
    """Validate an integer command parameter."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


__all__ = [
    "BlockDelimiter",
    "BufferMemory",
    "BuzzerMode",
    "CurrentSourceRange",
    "DisplayDigits",
    "FilterMode",
    "IntegrationTime",
    "InterlockMode",
    "Measurement",
    "MeasurementFunction",
    "MeasurementRange",
    "OutputState",
    "RemoteSensing",
    "SourceFunction",
    "SourceMode",
    "Statistics",
    "SuspendMode",
    "SweepRange",
    "SyncOutputMode",
    "SyncWidth",
    "TriggerMode",
    "UnitDisplay",
    "VoltageSourceRange",
    "check_integer",
    "format_number",
    "parse_integer",
    "parse_measurement",
    "parse_measurements",
    "parse_number",
]
