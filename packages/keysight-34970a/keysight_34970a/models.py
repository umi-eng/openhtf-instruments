"""Public data types used by the 34970A plug."""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MeasurementFunction(str, Enum):
    """Measurement functions understood by the internal DMM."""

    TEMPERATURE = "TEMP"
    THERMOCOUPLE = "TEMP:TC"
    RTD = "TEMP:RTD"
    FRTD = "TEMP:FRTD"
    THERMISTOR = "TEMP:THERMISTOR"
    DC_VOLTAGE = "VOLT:DC"
    AC_VOLTAGE = "VOLT:AC"
    RESISTANCE = "RES"
    FOUR_WIRE_RESISTANCE = "FRES"
    DC_CURRENT = "CURR:DC"
    AC_CURRENT = "CURR:AC"
    FREQUENCY = "FREQ"
    PERIOD = "PER"
    DIGITAL_BYTE = "DIG:BYTE"
    TOTALIZER = "TOTALIZE"

    @classmethod
    def coerce(cls, value: MeasurementFunction | str) -> MeasurementFunction:
        if isinstance(value, cls):
            return value
        text = str(value).strip().upper().replace(" ", "")
        aliases = {
            "TEMP": cls.TEMPERATURE,
            "TEMPERATURE": cls.TEMPERATURE,
            "TC": cls.THERMOCOUPLE,
            "THERMOCOUPLE": cls.THERMOCOUPLE,
            "TEMP:TC": cls.THERMOCOUPLE,
            "TEMP:RTD": cls.RTD,
            "TEMP:FRTD": cls.FRTD,
            "TEMP:THERMISTOR": cls.THERMISTOR,
            "VOLT": cls.DC_VOLTAGE,
            "VOLT:DC": cls.DC_VOLTAGE,
            "DCV": cls.DC_VOLTAGE,
            "VOLT:AC": cls.AC_VOLTAGE,
            "ACV": cls.AC_VOLTAGE,
            "RES": cls.RESISTANCE,
            "RESISTANCE": cls.RESISTANCE,
            "FRES": cls.FOUR_WIRE_RESISTANCE,
            "4WRES": cls.FOUR_WIRE_RESISTANCE,
            "CURR": cls.DC_CURRENT,
            "CURR:DC": cls.DC_CURRENT,
            "DCI": cls.DC_CURRENT,
            "CURR:AC": cls.AC_CURRENT,
            "ACI": cls.AC_CURRENT,
            "FREQ": cls.FREQUENCY,
            "FREQUENCY": cls.FREQUENCY,
            "PER": cls.PERIOD,
            "PERIOD": cls.PERIOD,
            "DIGITAL": cls.DIGITAL_BYTE,
            "DIG:BYTE": cls.DIGITAL_BYTE,
            "TOTALIZER": cls.TOTALIZER,
            "TOTALIZE": cls.TOTALIZER,
        }
        try:
            return aliases[text]
        except KeyError as exc:
            raise ValueError(f"Unknown 34970A measurement function: {value!r}") from exc


class TriggerSource(str, Enum):
    """Source controlling the start of each scan sweep."""

    IMMEDIATE = "IMM"
    BUS = "BUS"
    EXTERNAL = "EXT"
    TIMER = "TIM"
    ALARM1 = "ALAR1"
    ALARM2 = "ALAR2"
    ALARM3 = "ALAR3"
    ALARM4 = "ALAR4"

    @classmethod
    def coerce(cls, value: TriggerSource | str) -> TriggerSource:
        if isinstance(value, cls):
            return value
        text = str(value).strip().upper()
        aliases = {
            "IMMEDIATE": cls.IMMEDIATE,
            "IMM": cls.IMMEDIATE,
            "EXTERNAL": cls.EXTERNAL,
            "EXT": cls.EXTERNAL,
            "TIMER": cls.TIMER,
            "TIM": cls.TIMER,
            "ALARM": cls.ALARM1,
        }
        if text in aliases:
            return aliases[text]
        for source in cls:
            if text == source.value:
                return source
        raise ValueError(f"Unknown 34970A trigger source: {value!r}")


@dataclass(frozen=True, order=True)
class Channel:
    """A mainframe slot and local two-digit channel number.

    The integer representation is the SCPI channel number: slot 100 and
    local channel 1 become 101, while local matrix channel 11 becomes 111.
    """

    slot: int
    number: int

    def __post_init__(self) -> None:
        if self.slot not in (100, 200, 300):
            raise ValueError("slot must be one of 100, 200, or 300")
        if not 1 <= self.number <= 99:
            raise ValueError("channel number must be between 1 and 99")

    @property
    def scpi_number(self) -> int:
        return self.slot + self.number

    def __int__(self) -> int:
        return self.scpi_number

    def __str__(self) -> str:
        return str(self.scpi_number)

    @classmethod
    def from_value(cls, value: Channel | int | str) -> Channel:
        if isinstance(value, cls):
            return value
        text = str(value).strip()
        text = text.removeprefix("@")
        try:
            integer = int(text)
        except ValueError as exc:
            raise ValueError(f"Invalid SCPI channel: {value!r}") from exc
        slot = (integer // 100) * 100
        if slot not in (100, 200, 300):
            raise ValueError(f"Invalid SCPI channel slot: {value!r}")
        return cls(slot, integer - slot)


ChannelLike = Channel | int | str


def coerce_channels(
    channels: ChannelLike | Iterable[ChannelLike],
) -> tuple[Channel, ...]:
    if isinstance(channels, (Channel, int, str)):
        channels = (channels,)
    result = tuple(Channel.from_value(channel) for channel in channels)
    if not result:
        raise ValueError("at least one channel is required")
    return result


def format_channel_list(
    channels: Sequence[ChannelLike], *, allow_empty: bool = False
) -> str:
    if not channels:
        if allow_empty:
            return "(@)"
        raise ValueError("at least one channel is required")
    parsed = tuple(Channel.from_value(channel) for channel in channels)
    return "(@" + ",".join(str(channel) for channel in parsed) + ")"


@dataclass(frozen=True)
class MeasurementConfig:
    """Configuration for one channel in a scan profile."""

    channel: ChannelLike
    function: MeasurementFunction | str
    range: float | str | None = "AUTO"
    resolution: float | str | None = None
    temperature_type: str = "J"
    thermistor_type: int | str = 5000
    totalizer_reset: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "channel", Channel.from_value(self.channel))
        object.__setattr__(self, "function", MeasurementFunction.coerce(self.function))

    @classmethod
    def thermocouple(
        cls, channel: ChannelLike, kind: str = "J", resolution: Any = None
    ) -> MeasurementConfig:
        return cls(
            channel,
            MeasurementFunction.THERMOCOUPLE,
            resolution=resolution,
            temperature_type=kind,
        )

    @classmethod
    def rtd(
        cls,
        channel: ChannelLike,
        alpha: int | str = 85,
        four_wire: bool = False,
        resolution: Any = None,
    ) -> MeasurementConfig:
        function = MeasurementFunction.FRTD if four_wire else MeasurementFunction.RTD
        return cls(
            channel, function, resolution=resolution, temperature_type=str(alpha)
        )

    @classmethod
    def thermistor(
        cls, channel: ChannelLike, resistance: int | str = 5000, resolution: Any = None
    ) -> MeasurementConfig:
        return cls(
            channel,
            MeasurementFunction.THERMISTOR,
            resolution=resolution,
            thermistor_type=resistance,
        )


@dataclass(frozen=True)
class ReadingFormat:
    """Optional fields returned with scan readings."""

    units: bool = False
    time: bool = False
    channel: bool = False
    alarm: bool = False
    absolute_time: bool = False

    @property
    def field_count(self) -> int:
        time_fields = 6 if self.time and self.absolute_time else int(self.time)
        return 1 + int(self.units) + time_fields + int(self.channel) + int(self.alarm)


@dataclass(frozen=True)
class ScanProfile:
    """A reusable instrument-side scan configuration.

    ``measurements`` may be omitted when channels have already been configured
    on the instrument. This is useful for recalling a profile prepared from
    the front panel or by another application.
    """

    channels: Sequence[ChannelLike] | None = None
    measurements: Sequence[MeasurementConfig] | None = None
    trigger_source: TriggerSource | str = TriggerSource.IMMEDIATE
    interval: float = 0.0
    count: int | None = 1
    channel_delay: float | None = None
    channel_delays: Mapping[ChannelLike, float] = field(default_factory=dict)
    reading_format: ReadingFormat = field(default_factory=ReadingFormat)

    def __post_init__(self) -> None:
        measurements = None
        if self.measurements is not None:
            measurements = tuple(
                item
                if isinstance(item, MeasurementConfig)
                else MeasurementConfig(**item)
                for item in self.measurements
            )
            if not measurements:
                raise ValueError("measurements cannot be empty")
        if self.channels is None:
            if measurements is None:
                raise ValueError("channels or measurements must be supplied")
            channels = tuple(item.channel for item in measurements)
        else:
            channels = coerce_channels(self.channels)
        if measurements is not None:
            measurement_channels = tuple(item.channel for item in measurements)
            if tuple(channels) != measurement_channels:
                raise ValueError("channels must match the order of measurements")
        if self.interval < 0 or self.interval > 359999:
            raise ValueError("scan interval must be between 0 and 359999 seconds")
        if self.channel_delay is not None and not 0 <= self.channel_delay <= 60:
            raise ValueError("channel delay must be between 0 and 60 seconds")
        if self.count is not None and not 1 <= self.count <= 50000:
            raise ValueError(
                "scan count must be between 1 and 50000, or None for continuous"
            )
        object.__setattr__(self, "channels", channels)
        object.__setattr__(self, "measurements", measurements)
        object.__setattr__(
            self, "trigger_source", TriggerSource.coerce(self.trigger_source)
        )
        object.__setattr__(
            self,
            "channel_delays",
            {
                Channel.from_value(channel): float(delay)
                for channel, delay in self.channel_delays.items()
            },
        )


@dataclass(frozen=True)
class ScanReading:
    """One reading returned from scan memory or ``READ?``."""

    value: float | str
    unit: str | None = None
    timestamp: str | None = None
    channel: int | None = None
    alarm: int | None = None
    raw_fields: tuple[str, ...] = ()


def parse_csv_response(response: str) -> list[str]:
    """Parse an ASCII SCPI response, including a definite-length block."""
    text = response.decode() if isinstance(response, bytes) else str(response)
    text = text.strip()
    if text.startswith("#") and len(text) >= 2 and text[1].isdigit():
        digits = int(text[1])
        start = 2 + digits
        try:
            length = int(text[2:start])
            text = text[start : start + length]
        except ValueError:
            pass
    if not text:
        return []
    return [field.strip() for field in next(csv.reader(io.StringIO(text)), [])]


def parse_scalar(value: str) -> float | str:
    unquoted = value.strip().strip('"').strip("'")
    try:
        number = float(unquoted)
    except ValueError:
        return unquoted
    if (
        math.isfinite(number)
        and number.is_integer()
        and unquoted.lstrip("+-").isdigit()
    ):
        return int(number)
    return number


def parse_scan_readings(
    response: str, reading_format: ReadingFormat, channels: Sequence[ChannelLike]
) -> list[ScanReading]:
    fields = parse_csv_response(response)
    if not fields:
        return []
    parsed_channels = tuple(
        Channel.from_value(channel).scpi_number for channel in channels
    )
    stride = reading_format.field_count
    readings: list[ScanReading] = []
    for offset in range(0, len(fields), stride):
        group = tuple(fields[offset : offset + stride])
        if not group:
            continue
        index = 0
        value = parse_scalar(group[index])
        index += 1
        unit = (
            group[index].strip('"')
            if reading_format.units and index < len(group)
            else None
        )
        index += int(reading_format.units)
        timestamp = None
        if reading_format.time and index < len(group):
            time_width = 6 if reading_format.absolute_time else 1
            timestamp = ",".join(
                field.strip('"') for field in group[index : index + time_width]
            )
            index += time_width
        channel = None
        if reading_format.channel and index < len(group):
            try:
                channel = int(float(group[index]))
            except ValueError:
                pass
        else:
            channel = (
                parsed_channels[len(readings) % len(parsed_channels)]
                if parsed_channels
                else None
            )
        index += int(reading_format.channel)
        alarm = None
        if reading_format.alarm and index < len(group):
            try:
                alarm = int(float(group[index]))
            except ValueError:
                pass
        readings.append(ScanReading(value, unit, timestamp, channel, alarm, group))
    return readings
