"""Data models and reply parsers for the Heraeus Voetsch VRSU.

The VRSU does not speak SCPI.  Its commands and talk strings are documented in
the service manual (HVB-E 0099 SM GB).  The talk strings use fixed-width fields
separated by spaces, so the parsers below are deliberately lenient about the
exact number of spaces while still validating the field contents.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from enum import IntEnum

from .exceptions import VrsuProtocolError


class VrsuChannel(IntEnum):
    """One of the three analog channels of the VRSU."""

    CHANNEL_1 = 1
    CHANNEL_2 = 2
    CHANNEL_3 = 3

    @classmethod
    def coerce(cls, value: VrsuChannel | float | str) -> VrsuChannel:
        """Return *value* as a channel, accepting ``1``, ``"1"``, or an enum."""

        if isinstance(value, cls):
            return value
        if isinstance(value, bool):
            raise TypeError(f"channel must be 1, 2, or 3, got {value!r}")
        if isinstance(value, float) and not value.is_integer():
            raise ValueError(f"channel must be 1, 2, or 3, got {value!r}")
        try:
            return cls(int(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"channel must be 1, 2, or 3, got {value!r}") from exc


class VrsuErrorCode(IntEnum):
    """Error numbers reported in the ``ER.x`` field of a talk string."""

    NO_ERROR = 0
    OVERFLOW_CHANNEL_1 = 1
    OVERFLOW_CHANNEL_2 = 2
    OVERFLOW_CHANNEL_3 = 3
    SYNTAX_ERROR = 4
    ILLEGAL_SETPOINT = 5
    NO_POWER_FAILURE_DATA = 6

    @property
    def message(self) -> str:
        """Human-readable description from the service manual."""

        return _ERROR_MESSAGES[self]


_ERROR_MESSAGES = {
    VrsuErrorCode.NO_ERROR: "no error",
    VrsuErrorCode.OVERFLOW_CHANNEL_1: (
        "overflow on actual value channel 1 (> 3000 mV)"
    ),
    VrsuErrorCode.OVERFLOW_CHANNEL_2: (
        "overflow on actual value channel 2 (> 3000 mV)"
    ),
    VrsuErrorCode.OVERFLOW_CHANNEL_3: (
        "overflow on actual value channel 3 (> 3000 mV)"
    ),
    VrsuErrorCode.SYNTAX_ERROR: "syntax error in the last command",
    VrsuErrorCode.ILLEGAL_SETPOINT: "attempt to enter an illegal setpoint",
    VrsuErrorCode.NO_POWER_FAILURE_DATA: (
        "PF call without valid data (clock accumulator may be flat)"
    ),
}


@dataclass(frozen=True)
class VrsuClock:
    """The VRSU calendar clock.

    The instrument stores a two-digit year.  ``date()`` maps it into the
    2000-2099 century by default, which matches the manual's leap-year rule
    (``JJ modulo 4 == 0``).
    """

    hour: int
    minute: int
    second: int
    day: int
    month: int
    year: int

    def __post_init__(self) -> None:
        if not 0 <= self.hour <= 23:
            raise ValueError(f"hour out of range: {self.hour}")
        if not 0 <= self.minute <= 59:
            raise ValueError(f"minute out of range: {self.minute}")
        if not 0 <= self.second <= 59:
            raise ValueError(f"second out of range: {self.second}")
        if not 0 <= self.year <= 99:
            raise ValueError(f"year out of range: {self.year}")
        # Validates the day/month combination, including leap years.
        dt.date(2000 + self.year, self.month, self.day)

    @property
    def time(self) -> dt.time:
        """The clock's time of day."""

        return dt.time(self.hour, self.minute, self.second)

    def date(self, year_base: int = 2000) -> dt.date:
        """The clock's date, with the two-digit year mapped into *year_base*."""

        return dt.date(year_base + self.year, self.month, self.day)

    def datetime(self, year_base: int = 2000) -> dt.datetime:
        """The clock's date and time as a :class:`datetime.datetime`."""

        return dt.datetime.combine(self.date(year_base), self.time)


def _channel_values(
    channel_1_mv: int, channel_2_mv: int, channel_3_mv: int
) -> tuple[int, int, int]:
    return channel_1_mv, channel_2_mv, channel_3_mv


def _relay_states(relays: int) -> tuple[bool, ...]:
    """Return relay states in relay 1 ... relay 8 order."""

    return tuple(bool(relays & (1 << (relay - 1))) for relay in range(1, 9))


def _relay(relays: int, relay: int) -> bool:
    if not 1 <= relay <= 8:
        raise ValueError("relay must be 1..8")
    return bool(relays & (1 << (relay - 1)))


@dataclass(frozen=True)
class VrsuStatus:
    """The reply to a normal talk call (``ST`` in serial mode)."""

    channel_1_mv: int
    channel_2_mv: int
    channel_3_mv: int
    digital_inputs: int
    digital_input_bits: str
    clock: VrsuClock
    error: VrsuErrorCode

    def channel_mv(self, channel: VrsuChannel | int) -> int:
        """Actual value of *channel* in millivolts."""

        channel = VrsuChannel.coerce(channel)
        return _channel_values(self.channel_1_mv, self.channel_2_mv, self.channel_3_mv)[
            channel - 1
        ]

    def channel_value(self, channel: VrsuChannel | int) -> float:
        """Actual value of *channel* in engineering units (10 mV = 1 unit)."""

        return self.channel_mv(channel) / 10.0

    @property
    def temperature(self) -> float:
        """Channel 1 in degrees Celsius (10 mV = 1 degC)."""

        return self.channel_1_mv / 10.0

    @property
    def humidity(self) -> float:
        """Channel 2 in percent relative humidity (10 mV = 1 %RH)."""

        return self.channel_2_mv / 10.0

    def digital_input(self, bit: int) -> bool:
        """State of parallel input *bit* (0 = 2^0 ... 7 = 2^7)."""

        if not 0 <= bit <= 7:
            raise ValueError("bit must be 0..7")
        return bool(self.digital_inputs & (1 << bit))


@dataclass(frozen=True)
class VrsuSetpoints:
    """The reply to ``LL``: current setpoints, relays, and clock."""

    channel_1_mv: int
    channel_2_mv: int
    channel_3_mv: int
    relays: int
    clock: VrsuClock

    def channel_mv(self, channel: VrsuChannel | int) -> int:
        """Setpoint of *channel* in millivolts."""

        channel = VrsuChannel.coerce(channel)
        return _channel_values(self.channel_1_mv, self.channel_2_mv, self.channel_3_mv)[
            channel - 1
        ]

    def channel_value(self, channel: VrsuChannel | int) -> float:
        """Setpoint of *channel* in engineering units (10 mV = 1 unit)."""

        return self.channel_mv(channel) / 10.0

    def relay(self, relay: int) -> bool:
        """State of *relay* (1 ... 8)."""

        return _relay(self.relays, relay)

    def relay_states(self) -> tuple[bool, ...]:
        """Relay states in relay 1 ... relay 8 order."""

        return _relay_states(self.relays)


@dataclass(frozen=True)
class VrsuPowerFailure:
    """The reply to ``PF`` followed by a talk call.

    The fields have the same positions as :class:`VrsuStatus`, but the analog
    values are the setpoints at the failure instant and the ``E`` field holds
    the relay states instead of the digital inputs.
    """

    channel_1_mv: int
    channel_2_mv: int
    channel_3_mv: int
    relays: int
    clock: VrsuClock
    error: VrsuErrorCode

    def channel_mv(self, channel: VrsuChannel | int) -> int:
        """Setpoint of *channel* at the failure instant, in millivolts."""

        channel = VrsuChannel.coerce(channel)
        return _channel_values(self.channel_1_mv, self.channel_2_mv, self.channel_3_mv)[
            channel - 1
        ]

    def relay(self, relay: int) -> bool:
        """State of *relay* (1 ... 8) at the failure instant."""

        return _relay(self.relays, relay)

    def relay_states(self) -> tuple[bool, ...]:
        """Relay states at the failure instant, in relay 1 ... relay 8 order."""

        return _relay_states(self.relays)


@dataclass(frozen=True)
class VrsuProgramLine:
    """One line of the program table, as returned by ``LP``."""

    number: int
    wait_for_actual: bool
    time_minutes: int
    channel_1_mv: int
    channel_2_mv: int
    channel_3_mv: int
    relays: int

    def channel_mv(self, channel: VrsuChannel | int) -> int:
        """Setpoint of *channel* in millivolts."""

        channel = VrsuChannel.coerce(channel)
        return _channel_values(self.channel_1_mv, self.channel_2_mv, self.channel_3_mv)[
            channel - 1
        ]

    def relay(self, relay: int) -> bool:
        """State of *relay* (1 ... 8)."""

        return _relay(self.relays, relay)

    def relay_states(self) -> tuple[bool, ...]:
        """Relay states in relay 1 ... relay 8 order."""

        return _relay_states(self.relays)


@dataclass(frozen=True)
class VrsuLoop:
    """One entry of the loop table, as returned by ``LC``."""

    number: int
    start_line: int
    end_line: int
    count: int


@dataclass(frozen=True)
class VrsuProgramStatus:
    """The reply to ``LS``: current program execution status."""

    loop_1_remaining: int
    loop_2_remaining: int
    loop_3_remaining: int
    current_line: int
    remaining_minutes: int
    waiting: bool
    running: bool

    @property
    def loop_remaining(self) -> tuple[int, int, int]:
        """Remaining loop counts for loops 1, 2, and 3."""

        return (
            self.loop_1_remaining,
            self.loop_2_remaining,
            self.loop_3_remaining,
        )


_STATUS_RE = re.compile(
    r"U1\s*=\s*(\d{4})\s*mV\s+"
    r"U2\s*=\s*(\d{4})\s*mV\s+"
    r"E\s*=\s*([01]{8})\s+"
    r"U3\s*=\s*(\d{4})\s*mV\s+"
    r"T(\d{2})\.(\d{2})\.(\d{2})\s+"
    r"D(\d{2})\.(\d{2})\.(\d{2})\s+"
    r"ER\.(\d)"
)

_SETPOINTS_RE = re.compile(
    r"U1\s*=\s*(\d{4})\s*mV\s+"
    r"U2\s*=\s*(\d{4})\s*mV\s+"
    r"R\s*=\s*([01]{8})\s+"
    r"U3\s*=\s*(\d{4})\s*mV\s+"
    r"T(\d{2})\.(\d{2})\.(\d{2})\s+"
    r"D(\d{2})\.(\d{2})\.(\d{2})"
)

_PROGRAM_LINE_RE = re.compile(
    r"(\d{2})\s+(W\s+)?(\d{4})\s+(\d{4})\s+(\d{4})\s+(\d{4})\s+([01]{8})"
)

_LOOP_RE = re.compile(r"([123])\s+(\d{2})\s+(\d{2})\s+(\d{2})")

_PROGRAM_STATUS_RE = re.compile(
    r"(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d{4})\s+([01])\s+([01])"
)


def _clock(
    hour: str, minute: str, second: str, day: str, month: str, year: str
) -> VrsuClock:
    try:
        return VrsuClock(
            hour=int(hour),
            minute=int(minute),
            second=int(second),
            day=int(day),
            month=int(month),
            year=int(year),
        )
    except ValueError as exc:
        raise VrsuProtocolError(f"invalid VRSU clock value: {exc}") from exc


def _error_code(value: str) -> VrsuErrorCode:
    try:
        return VrsuErrorCode(int(value))
    except ValueError as exc:
        raise VrsuProtocolError(f"unknown VRSU error code ER.{value}") from exc


def parse_status(text: str) -> VrsuStatus:
    """Parse a normal talk string into a :class:`VrsuStatus`."""

    match = _STATUS_RE.search(text)
    if match is None:
        raise VrsuProtocolError(f"invalid VRSU status string: {text!r}")
    (
        channel_1,
        channel_2,
        digital,
        channel_3,
        hour,
        minute,
        second,
        day,
        month,
        year,
        error,
    ) = match.groups()
    return VrsuStatus(
        channel_1_mv=int(channel_1),
        channel_2_mv=int(channel_2),
        channel_3_mv=int(channel_3),
        digital_inputs=int(digital, 2),
        digital_input_bits=digital,
        clock=_clock(hour, minute, second, day, month, year),
        error=_error_code(error),
    )


def parse_setpoints(text: str) -> VrsuSetpoints:
    """Parse the reply to ``LL`` into a :class:`VrsuSetpoints`."""

    match = _SETPOINTS_RE.search(text)
    if match is None:
        raise VrsuProtocolError(f"invalid VRSU setpoint string: {text!r}")
    (
        channel_1,
        channel_2,
        relays,
        channel_3,
        hour,
        minute,
        second,
        day,
        month,
        year,
    ) = match.groups()
    return VrsuSetpoints(
        channel_1_mv=int(channel_1),
        channel_2_mv=int(channel_2),
        channel_3_mv=int(channel_3),
        relays=int(relays, 2),
        clock=_clock(hour, minute, second, day, month, year),
    )


def parse_power_failure(text: str) -> VrsuPowerFailure:
    """Parse the reply to ``PF`` into a :class:`VrsuPowerFailure`."""

    match = _STATUS_RE.search(text)
    if match is None:
        raise VrsuProtocolError(f"invalid VRSU power-failure string: {text!r}")
    (
        channel_1,
        channel_2,
        relays,
        channel_3,
        hour,
        minute,
        second,
        day,
        month,
        year,
        error,
    ) = match.groups()
    return VrsuPowerFailure(
        channel_1_mv=int(channel_1),
        channel_2_mv=int(channel_2),
        channel_3_mv=int(channel_3),
        relays=int(relays, 2),
        clock=_clock(hour, minute, second, day, month, year),
        error=_error_code(error),
    )


def parse_program_lines(text: str) -> tuple[VrsuProgramLine, ...]:
    """Parse the reply to ``LP`` into program lines."""

    lines = tuple(
        VrsuProgramLine(
            number=int(match.group(1)),
            wait_for_actual=match.group(2) is not None,
            time_minutes=int(match.group(3)),
            channel_1_mv=int(match.group(4)),
            channel_2_mv=int(match.group(5)),
            channel_3_mv=int(match.group(6)),
            relays=int(match.group(7), 2),
        )
        for match in _PROGRAM_LINE_RE.finditer(text)
    )
    if not lines:
        raise VrsuProtocolError(f"invalid VRSU program-line string: {text!r}")
    return lines


def parse_loop_table(text: str) -> tuple[VrsuLoop, ...]:
    """Parse the reply to ``LC`` into loop-table entries.

    The instrument's reply lists the end line (``VV``) before the start line
    (``NN``); this parser normalises that into ``start_line``/``end_line``.
    """

    loops = tuple(
        VrsuLoop(
            number=int(match.group(1)),
            end_line=int(match.group(2)),
            start_line=int(match.group(3)),
            count=int(match.group(4)),
        )
        for match in _LOOP_RE.finditer(text)
    )
    if not loops:
        raise VrsuProtocolError(f"invalid VRSU loop-table string: {text!r}")
    return loops


def parse_program_status(text: str) -> VrsuProgramStatus:
    """Parse the reply to ``LS`` into a :class:`VrsuProgramStatus`."""

    match = _PROGRAM_STATUS_RE.search(text)
    if match is None:
        raise VrsuProtocolError(f"invalid VRSU program-status string: {text!r}")
    return VrsuProgramStatus(
        loop_1_remaining=int(match.group(1)),
        loop_2_remaining=int(match.group(2)),
        loop_3_remaining=int(match.group(3)),
        current_line=int(match.group(4)),
        remaining_minutes=int(match.group(5)),
        waiting=match.group(6) == "1",
        running=match.group(7) == "1",
    )
