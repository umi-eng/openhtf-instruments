"""Public enums and value objects for the ADCMT AD7461A plug."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from enum import Enum


def _normalize(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


class MeasurementFunction(str, Enum):
    """Measurement functions supported by the AD7461A (7461A model)."""

    DC_VOLTAGE = "VOLTage:DC"
    AC_VOLTAGE = "VOLTage:AC"
    RESISTANCE = "RESistance"
    FOUR_WIRE_RESISTANCE = "FRESistance"
    DC_CURRENT = "CURRent:DC"
    AC_CURRENT = "CURRent:AC"
    AC_DC_VOLTAGE = "VOLTage:ACDC"
    AC_DC_CURRENT = "CURRent:ACDC"
    DIODE = "DIODe"
    LOW_POWER_RESISTANCE = "RESistance:LPOWer"
    LOW_POWER_FOUR_WIRE_RESISTANCE = "FRESistance:LPOWer"
    CONTINUITY = "CONTinuity"
    FREQUENCY = "FREQuency"

    @classmethod
    def coerce(cls, value: MeasurementFunction | str) -> MeasurementFunction:
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise TypeError("function must be a MeasurementFunction or string")
        key = _normalize(value.strip().strip("\"'"))
        aliases = {
            "VOLT": cls.DC_VOLTAGE,
            "VOLTDC": cls.DC_VOLTAGE,
            "VOLTAGE": cls.DC_VOLTAGE,
            "DCV": cls.DC_VOLTAGE,
            "VOLTAC": cls.AC_VOLTAGE,
            "VOLTAGEAC": cls.AC_VOLTAGE,
            "ACV": cls.AC_VOLTAGE,
            "RES": cls.RESISTANCE,
            "RESISTANCE": cls.RESISTANCE,
            "2W": cls.RESISTANCE,
            "FRES": cls.FOUR_WIRE_RESISTANCE,
            "RESISTANCE4WIRE": cls.FOUR_WIRE_RESISTANCE,
            "4W": cls.FOUR_WIRE_RESISTANCE,
            "CURR": cls.DC_CURRENT,
            "CURRDC": cls.DC_CURRENT,
            "CURRENTDC": cls.DC_CURRENT,
            "DCI": cls.DC_CURRENT,
            "CURRAC": cls.AC_CURRENT,
            "CURRENTAC": cls.AC_CURRENT,
            "ACI": cls.AC_CURRENT,
            "VOLTACDC": cls.AC_DC_VOLTAGE,
            "VOLTAGEACDC": cls.AC_DC_VOLTAGE,
            "ACVACDC": cls.AC_DC_VOLTAGE,
            "CURRACDC": cls.AC_DC_CURRENT,
            "CURRENTACDC": cls.AC_DC_CURRENT,
            "ACIACDC": cls.AC_DC_CURRENT,
            "DIODE": cls.DIODE,
            "DIOD": cls.DIODE,
            "RESLPOW": cls.LOW_POWER_RESISTANCE,
            "RESISTANCELPOWER": cls.LOW_POWER_RESISTANCE,
            "LP2W": cls.LOW_POWER_RESISTANCE,
            "FRESLPOW": cls.LOW_POWER_FOUR_WIRE_RESISTANCE,
            "FRESISTANCELPOWER": cls.LOW_POWER_FOUR_WIRE_RESISTANCE,
            "LP4W": cls.LOW_POWER_FOUR_WIRE_RESISTANCE,
            "CONT": cls.CONTINUITY,
            "CONTINUITY": cls.CONTINUITY,
            "FREQ": cls.FREQUENCY,
            "FREQUENCY": cls.FREQUENCY,
        }
        for function in cls:
            if key in {_normalize(function.name), _normalize(function.value)}:
                return function
        try:
            return aliases[key]
        except KeyError as exc:
            raise ValueError(
                f"unsupported AD7461A measurement function: {value!r}"
            ) from exc


class SamplingRate(str, Enum):
    """Sampling-rate presets accepted by ``SENSe:<function>:SRATe``."""

    FREE = "FREE"
    SUPER_FAST = "SFASt"
    FAST = "FAST"
    MEDIUM = "MED"
    SLOW = "SLOW"
    SUPER_SLOW = "SSLow"

    @classmethod
    def coerce(cls, value: SamplingRate | str) -> SamplingRate:
        if isinstance(value, cls):
            return value
        key = _normalize(str(value))
        aliases = {
            "SFAST": cls.SUPER_FAST,
            "SFAS": cls.SUPER_FAST,
            "MEDIUM": cls.MEDIUM,
            "SSL": cls.SUPER_SLOW,
            "SSLOW": cls.SUPER_SLOW,
        }
        for item in cls:
            if key in {_normalize(item.name), _normalize(item.value)}:
                return item
        try:
            return aliases[key]
        except KeyError as exc:
            raise ValueError(f"unsupported sampling rate: {value!r}") from exc


class ACFilter(str, Enum):
    FAST = "FAST"
    MEDIUM = "MED"
    SLOW = "SLOW"

    @classmethod
    def coerce(cls, value: ACFilter | str) -> ACFilter:
        if isinstance(value, cls):
            return value
        key = _normalize(str(value))
        if key == "MEDIUM":
            key = "MED"
        for item in cls:
            if key in {_normalize(item.name), _normalize(item.value)}:
                return item
        raise ValueError(f"unsupported AC filter: {value!r}")


class TriggerSource(str, Enum):
    IMMEDIATE = "IMMediate"
    MANUAL = "MANual"
    EXTERNAL = "EXTernal"
    BUS = "BUS"
    LEVEL = "LEVel"
    DELTA = "DELTa"

    @classmethod
    def coerce(cls, value: TriggerSource | str) -> TriggerSource:
        if isinstance(value, cls):
            return value
        key = _normalize(str(value))
        aliases = {
            "IMM": cls.IMMEDIATE,
            "IMMEDIATE": cls.IMMEDIATE,
            "MAN": cls.MANUAL,
            "MANUAL": cls.MANUAL,
            "EXT": cls.EXTERNAL,
            "EXTERNAL": cls.EXTERNAL,
            "LEV": cls.LEVEL,
            "LEVEL": cls.LEVEL,
            "DELT": cls.DELTA,
            "DELTA": cls.DELTA,
        }
        for item in cls:
            if key == _normalize(item.value):
                return item
        try:
            return aliases[key]
        except KeyError as exc:
            raise ValueError(f"unsupported trigger source: {value!r}") from exc


class TriggerSlope(str, Enum):
    POSITIVE = "POSitive"
    NEGATIVE = "NEGative"
    BOTH = "BOTH"

    @classmethod
    def coerce(cls, value: TriggerSlope | str) -> TriggerSlope:
        if isinstance(value, cls):
            return value
        key = _normalize(str(value))
        aliases = {
            "POS": cls.POSITIVE,
            "POSITIVE": cls.POSITIVE,
            "NEG": cls.NEGATIVE,
            "NEGATIVE": cls.NEGATIVE,
        }
        for item in cls:
            if key == _normalize(item.value):
                return item
        try:
            return aliases[key]
        except KeyError as exc:
            raise ValueError(f"unsupported trigger slope: {value!r}") from exc


class EventRegister(str, Enum):
    OPERATION = "OPERation"
    QUESTIONABLE = "QUEStionable"
    MEASUREMENT = "MEASurement"


class InputTerminal(str, Enum):
    FRONT = "FRONt"
    REAR = "REAR"

    @classmethod
    def coerce(cls, value: InputTerminal | str) -> InputTerminal:
        if isinstance(value, cls):
            return value
        key = _normalize(str(value))
        if key in {"FRONT", "FRON"}:
            return cls.FRONT
        if key == "REAR":
            return cls.REAR
        raise ValueError(f"unsupported input terminal: {value!r}")


@dataclass(frozen=True)
class ErrorEntry:
    """One item from the instrument's SCPI error queue."""

    code: int
    message: str

    @classmethod
    def parse(cls, response: str) -> ErrorEntry:
        rows = list(csv.reader(io.StringIO(response.strip())))
        if not rows or len(rows[0]) < 2:
            raise ValueError(f"invalid SCPI error response: {response!r}")
        try:
            code = int(rows[0][0].strip())
        except ValueError as exc:
            raise ValueError(f"invalid SCPI error code: {response!r}") from exc
        return cls(code=code, message=rows[0][1].strip())
