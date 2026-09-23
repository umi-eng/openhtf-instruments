"""OpenHTF/PyVISA plug for the HP/Agilent/Keysight 34970A."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from typing import Any, cast

import pyvisa
from openhtf.plugs import BasePlug

from .exceptions import InvalidChannelError, ScpiError, UnsupportedOperationError
from .models import (
    Channel,
    ChannelLike,
    MeasurementConfig,
    MeasurementFunction,
    ReadingFormat,
    ScanProfile,
    ScanReading,
    TriggerSource,
    coerce_channels,
    format_channel_list,
    parse_csv_response,
    parse_scan_readings,
)
from .modules import ModuleSpec, ModuleType, module_spec


class Keysight34970APlug(BasePlug):
    """Control a 34970A over any PyVISA-supported interface.

    ``resource_name`` is passed directly to PyVISA. Examples include
    ``ASRL/dev/cu.usbserial-0001::INSTR`` for RS-232 and
    ``TCPIP::192.0.2.10::5025::SOCKET`` for a serial-to-Ethernet adapter.
    A resource object or resource manager can be injected for testing.
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
        baud_rate: int = 57600,
        xonxoff: bool = True,
        auto_discover: bool = False,
    ) -> None:
        super().__init__()
        resource_name = resource_name or os.environ.get("KEYSIGHT_34970A_RESOURCE")
        if resource is None and resource_name is None:
            raise ValueError(
                "resource_name is required when resource is not supplied "
                "(or set KEYSIGHT_34970A_RESOURCE)"
            )
        self._resource_manager: Any = resource_manager
        self._owns_resource_manager = resource_manager is None and resource is None
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
        self._module_cache: dict[int, ModuleSpec] = {}
        self._configure_resource(
            timeout_ms=timeout_ms,
            read_termination=read_termination,
            write_termination=write_termination,
            baud_rate=baud_rate,
            xonxoff=xonxoff,
        )
        if auto_discover:
            self.discover_modules()

    @property
    def resource(self) -> Any:
        """The underlying PyVISA resource, exposed for advanced use."""

        return self._resource

    def _configure_resource(
        self,
        *,
        timeout_ms: int,
        read_termination: str,
        write_termination: str,
        baud_rate: int,
        xonxoff: bool,
    ) -> None:
        for attribute, value in (
            ("timeout", timeout_ms),
            ("read_termination", read_termination),
            ("write_termination", write_termination),
        ):
            try:
                setattr(self._resource, attribute, value)
            except (AttributeError, ValueError, TypeError):
                pass
        # These attributes are ignored by non-serial VISA resources. Keeping
        # them best-effort also makes TCP serial adapters straightforward.
        for attribute, value in (
            ("baud_rate", baud_rate),
            ("data_bits", 8),
            ("xonxoff", xonxoff),
            ("rtscts", False),
            ("dsrdtr", False),
        ):
            try:
                setattr(self._resource, attribute, value)
            except (AttributeError, ValueError, TypeError):
                pass

    def write(self, command: str) -> None:
        """Send one SCPI command without reading a response."""

        self._resource.write(command)

    def query(self, command: str) -> str:
        """Send a SCPI query and return its textual response."""

        if hasattr(self._resource, "query"):
            return str(self._resource.query(command)).strip()
        self._resource.write(command)
        return str(self._resource.read()).strip()

    def identify(self) -> str:
        """Return the IEEE-488 identity string."""

        return self.query("*IDN?")

    def discover_modules(self) -> dict[int, ModuleType | None]:
        """Query all three slots and cache their supported capabilities."""

        result: dict[int, ModuleType | None] = {}
        for slot in (100, 200, 300):
            response = self.query(f"SYST:CTYP? {slot}")
            try:
                model = ModuleType.coerce(response)
            except ValueError:
                result[slot] = None
                self._module_cache.pop(slot, None)
            else:
                result[slot] = model
                self._module_cache[slot] = module_spec(model)
        return result

    @property
    def modules(self) -> dict[int, ModuleType]:
        """Return cached module models, discovering them if necessary."""

        if not self._module_cache:
            self.discover_modules()
        return {slot: spec.model for slot, spec in self._module_cache.items()}

    def set_module(self, slot: int, model: ModuleType | str) -> None:
        """Set a known module model without querying the instrument.

        This is useful when the instrument firmware does not implement the
        card-type query or when writing deterministic unit tests.
        """

        if slot not in (100, 200, 300):
            raise ValueError("slot must be one of 100, 200, or 300")
        self._module_cache[slot] = module_spec(model)

    def _module_for_channel(self, channel: Channel) -> ModuleSpec:
        if channel.slot not in self._module_cache:
            response = self.query(f"SYST:CTYP? {channel.slot}")
            try:
                self._module_cache[channel.slot] = module_spec(response)
            except ValueError as exc:
                raise InvalidChannelError(
                    f"no supported module was found in slot {channel.slot}: {response!r}"
                ) from exc
        spec = self._module_cache[channel.slot]
        if not spec.has_channel(channel.number):
            raise InvalidChannelError(
                f"channel {channel} is not valid on {spec.model.value}"
            )
        return spec

    def _modules_for_channels(
        self, channels: Iterable[ChannelLike]
    ) -> tuple[Channel, ...]:
        parsed = coerce_channels(channels)
        for channel in parsed:
            self._module_for_channel(channel)
        return parsed

    def configure_measurement(
        self,
        channels: Iterable[ChannelLike] | MeasurementConfig,
        function: MeasurementFunction | str | None = None,
        *,
        range: float | str | None = "AUTO",
        resolution: float | str | None = None,
        temperature_type: str = "J",
        thermistor_type: int | str = 5000,
        totalizer_reset: bool = False,
    ) -> None:
        """Configure one or more channels for an internal-DMM measurement.

        Calling this method follows the instrument manual: it also replaces
        the active scan list. ``configure_scan`` should be used when several
        differently configured channels belong to one profile.
        """

        configs: tuple[MeasurementConfig, ...]
        if isinstance(channels, MeasurementConfig):
            configs = (channels,)
        elif function is None:
            candidate = tuple(channels)  # type: ignore[arg-type]
            if not candidate or not all(
                isinstance(item, MeasurementConfig) for item in candidate
            ):
                raise TypeError(
                    "function is required unless channels are MeasurementConfig objects"
                )
            configs = cast(tuple[MeasurementConfig, ...], candidate)
        else:
            configs = tuple(
                MeasurementConfig(
                    channel=channel,
                    function=function,
                    range=range,
                    resolution=resolution,
                    temperature_type=temperature_type,
                    thermistor_type=thermistor_type,
                    totalizer_reset=totalizer_reset,
                )
                for channel in coerce_channels(channels)
            )
        if not configs:
            raise ValueError("at least one measurement configuration is required")
        for config in configs:
            channel = Channel.from_value(config.channel)
            measurement_function = MeasurementFunction.coerce(config.function)
            spec = self._module_for_channel(channel)
            if not spec.supports_measurement(channel.number, measurement_function):
                raise UnsupportedOperationError(
                    f"{measurement_function.value} is not supported on channel {channel} ({spec.model.value})"
                )
        command = self._measurement_command(configs)
        self.write(command)

    def _measurement_command(self, configs: Iterable[MeasurementConfig]) -> str:
        configs = tuple(configs)
        first = configs[0]
        channels = format_channel_list([config.channel for config in configs])
        function = MeasurementFunction.coerce(first.function)
        if any(
            MeasurementFunction.coerce(config.function) != function
            for config in configs
        ):
            raise ValueError(
                "one SCPI configuration command cannot mix measurement functions"
            )
        if function in {
            MeasurementFunction.TEMPERATURE,
            MeasurementFunction.THERMOCOUPLE,
            MeasurementFunction.RTD,
            MeasurementFunction.FRTD,
            MeasurementFunction.THERMISTOR,
        }:
            if function in {
                MeasurementFunction.TEMPERATURE,
                MeasurementFunction.THERMOCOUPLE,
            }:
                sensor = "TC"
                sensor_type = first.temperature_type.upper()
            elif function == MeasurementFunction.RTD:
                sensor = "RTD"
                sensor_type = first.temperature_type
            elif function == MeasurementFunction.FRTD:
                sensor = "FRTD"
                sensor_type = first.temperature_type
            else:
                sensor = "THERM"
                sensor_type = str(first.thermistor_type)
            parameters = [sensor, sensor_type, "1"]
            if first.resolution is not None:
                parameters.append(self._format_value(first.resolution))
            return f"CONF:TEMP {','.join(parameters)},{channels}"
        if function == MeasurementFunction.DIGITAL_BYTE:
            return f"CONF:DIG:BYTE {channels}"
        if function == MeasurementFunction.TOTALIZER:
            mode = "RRESET" if first.totalizer_reset else "READ"
            return f"CONF:TOTALIZE {mode},{channels}"
        command_name = {
            MeasurementFunction.DC_VOLTAGE: "VOLT:DC",
            MeasurementFunction.AC_VOLTAGE: "VOLT:AC",
            MeasurementFunction.RESISTANCE: "RES",
            MeasurementFunction.FOUR_WIRE_RESISTANCE: "FRES",
            MeasurementFunction.DC_CURRENT: "CURR:DC",
            MeasurementFunction.AC_CURRENT: "CURR:AC",
            MeasurementFunction.FREQUENCY: "FREQ",
            MeasurementFunction.PERIOD: "PER",
        }[function]
        parameters = []
        if first.range is not None:
            parameters.append(self._format_value(first.range))
        if first.resolution is not None:
            parameters.append(self._format_value(first.resolution))
        if not parameters:
            parameters.append("DEF")
        return f"CONF:{command_name} {','.join(parameters)},{channels}"

    @staticmethod
    def _format_value(value: Any) -> str:
        if isinstance(value, bool):
            return "1" if value else "0"
        return str(value)

    def configure_scan(self, profile: ScanProfile) -> None:
        """Apply a deterministic, instrument-timed scan profile."""

        self._modules_for_channels(profile.channels or ())
        if profile.measurements:
            for measurement in profile.measurements:
                self.configure_measurement(measurement)
        self.write(f"ROUT:SCAN {format_channel_list(profile.channels or ())}")
        trigger_source = TriggerSource.coerce(profile.trigger_source)
        self.write(f"TRIG:SOUR {trigger_source.value}")
        if trigger_source == TriggerSource.TIMER:
            self.write(f"TRIG:TIMER {self._format_value(profile.interval)}")
        self.write(
            f"TRIG:COUNT {'INFINITY' if profile.count is None else profile.count}"
        )
        if profile.channel_delay is not None:
            self.write(f"ROUT:CHAN:DELAY {self._format_value(profile.channel_delay)}")
        for channel, delay in profile.channel_delays.items():
            if not 0 <= delay <= 60:
                raise ValueError("channel delay must be between 0 and 60 seconds")
            parsed_channel = Channel.from_value(channel)
            self._module_for_channel(parsed_channel)
            self.write(
                f"ROUT:CHAN:DELAY {self._format_value(delay)},"
                f"{format_channel_list((parsed_channel,))}"
            )
        self._configure_reading_format(profile.reading_format)

    def _configure_reading_format(self, reading_format: ReadingFormat) -> None:
        for command, enabled in (
            ("FORM:READ:UNIT", reading_format.units),
            ("FORM:READ:TIME", reading_format.time),
            ("FORM:READ:CHAN", reading_format.channel),
            ("FORM:READ:ALARM", reading_format.alarm),
        ):
            self.write(f"{command} {'ON' if enabled else 'OFF'}")
        if reading_format.time:
            self.write(
                f"FORM:READ:TIME:TYPE {'ABS' if reading_format.absolute_time else 'REL'}"
            )

    def initiate(self) -> None:
        """Arm the configured scan; readings are stored in scan memory."""

        self.write("INIT")

    def trigger(self) -> None:
        """Issue a software trigger for a BUS-triggered scan."""

        self.write("*TRG")

    def abort(self) -> None:
        """Stop a scan after the current measurement completes."""

        self.write("ABOR")

    def fetch_scan(
        self,
        channels: Iterable[ChannelLike] | None = None,
        *,
        reading_format: ReadingFormat | None = None,
    ) -> list[ScanReading]:
        """Fetch all readings in scan memory without erasing them."""

        selected = (
            tuple(channels)
            if channels is not None
            else tuple(self._cached_scan_channels())
        )
        response = self.query("FETC?")
        return parse_scan_readings(
            response, reading_format or ReadingFormat(), selected
        )

    def read_scan(self, profile: ScanProfile) -> list[ScanReading]:
        """Configure, initiate, and fetch one profile's scan results."""

        self.configure_scan(profile)
        self.initiate()
        return self.fetch_scan(profile.channels, reading_format=profile.reading_format)

    def measure(self, config: MeasurementConfig) -> list[ScanReading]:
        """Take one immediate scan using ``MEASure?``."""

        channel = Channel.from_value(config.channel)
        measurement_function = MeasurementFunction.coerce(config.function)
        spec = self._module_for_channel(channel)
        if not spec.supports_measurement(channel.number, measurement_function):
            raise UnsupportedOperationError(
                f"{measurement_function.value} is not supported on channel {channel}"
            )
        command = self._measurement_command((config,)).replace("CONF:", "MEAS:", 1)
        response = self.query(command + "?")
        return parse_scan_readings(response, ReadingFormat(), (channel,))

    def _cached_scan_channels(self) -> tuple[int, ...]:
        # The mainframe exposes ROUT:SCAN? as a definite-length block. This
        # parser intentionally accepts the same response as normal CSV data.
        response = self.query("ROUT:SCAN?")
        fields = parse_csv_response(response)
        result: list[int] = []
        for field in fields:
            result.extend(int(match) for match in re.findall(r"\d{3}", field))
        return tuple(result)

    def scan_points(self) -> int:
        return int(float(self.query("DATA:POINTS?")))

    def remove_readings(self, count: int | None = None) -> list[ScanReading]:
        command = "R?" if count is None else f"R? {count}"
        response = self.query(command)
        return parse_scan_readings(
            response, ReadingFormat(), self._cached_scan_channels()
        )

    def check_errors(
        self, *, raise_on_error: bool = True, maximum: int = 32
    ) -> list[ScpiError]:
        """Drain the SCPI error queue and optionally raise the first error."""

        errors: list[ScpiError] = []
        for _ in range(maximum):
            response = self.query("SYST:ERR?")
            match = re.match(r"\s*([+-]?\d+)\s*,\s*[\"']?(.*?)[\"']?\s*$", response)
            if not match:
                break
            code = int(match.group(1))
            if code == 0:
                break
            error = ScpiError(code, match.group(2).strip())
            errors.append(error)
        if errors and raise_on_error:
            raise errors[0]
        return errors

    def close(self) -> None:
        """Close the VISA resource and owned resource manager."""

        if self._resource is not None:
            try:
                self._resource.close()
            finally:
                self._resource = None
        if self._owns_resource_manager and self._resource_manager is not None:
            self._resource_manager.close()
            self._resource_manager = None

    def tearDown(self) -> None:
        try:
            if self._resource is not None:
                try:
                    self.abort()
                except (pyvisa.errors.VisaIOError, OSError, RuntimeError):
                    pass
        finally:
            self.close()

    # Switching and control-module operations ---------------------------

    def _validate_switch_channels(
        self, channels: Iterable[ChannelLike]
    ) -> tuple[Channel, ...]:
        parsed = self._modules_for_channels(channels)
        for channel in parsed:
            spec = self._module_for_channel(channel)
            if channel.number not in spec.switch_channels:
                raise UnsupportedOperationError(
                    f"{channel} on {spec.model.value} is not a switch channel"
                )
        return parsed

    def close_channels(
        self, channels: Iterable[ChannelLike], *, exclusive: bool = False
    ) -> None:
        parsed = self._validate_switch_channels(channels)
        command = "ROUT:CLOSE:EXCL" if exclusive else "ROUT:CLOSE"
        self.write(f"{command} {format_channel_list(parsed)}")

    def open_channels(self, channels: Iterable[ChannelLike]) -> None:
        parsed = self._validate_switch_channels(channels)
        for channel in parsed:
            if self._module_for_channel(channel).model in {
                ModuleType.RF_MUX_34905A,
                ModuleType.RF_MUX_34906A,
            }:
                raise UnsupportedOperationError(
                    "RF multiplexer channels are opened by closing another channel in the bank"
                )
        self.write(f"ROUT:OPEN {format_channel_list(parsed)}")

    def channel_states(
        self, channels: Iterable[ChannelLike], *, closed: bool = True
    ) -> list[int]:
        parsed = self._validate_switch_channels(channels)
        return [
            int(float(value))
            for value in parse_csv_response(
                self.query(
                    f"ROUT:{'CLOSE' if closed else 'OPEN'}? {format_channel_list(parsed)}"
                )
            )
        ]

    def reset_module(self, slot: int | str = "ALL") -> None:
        if str(slot).upper() == "ALL":
            self.write("SYST:CPON ALL")
            return
        integer = int(slot)
        if integer not in (100, 200, 300):
            raise ValueError("slot must be one of 100, 200, or 300")
        self.write(f"SYST:CPON {integer}")

    def relay_done(self) -> bool:
        return bool(int(float(self.query("ROUT:DONE?"))))

    def read_digital(self, channel: ChannelLike, *, word: bool = False) -> int:
        parsed = Channel.from_value(channel)
        spec = self._module_for_channel(parsed)
        if (
            spec.model != ModuleType.MULTIFUNCTION_34907A
            or parsed.number not in spec.digital_channels
        ):
            raise UnsupportedOperationError(
                f"{parsed} is not a 34907A digital input channel"
            )
        if word and parsed.number != 1:
            raise InvalidChannelError("a 16-bit word read must address port 01")
        response = self.query(
            f"SENS:DIG:DATA:{'WORD' if word else 'BYTE'}? {format_channel_list((parsed,))}"
        )
        return int(float(parse_csv_response(response)[0]))

    def write_digital(
        self, channel: ChannelLike, value: int, *, word: bool = False
    ) -> None:
        parsed = Channel.from_value(channel)
        spec = self._module_for_channel(parsed)
        if (
            spec.model != ModuleType.MULTIFUNCTION_34907A
            or parsed.number not in spec.digital_channels
        ):
            raise UnsupportedOperationError(
                f"{parsed} is not a 34907A digital output channel"
            )
        maximum = 65535 if word else 255
        if not 0 <= value <= maximum:
            raise ValueError(f"digital value must be between 0 and {maximum}")
        if word and parsed.number != 1:
            raise InvalidChannelError("a 16-bit word write must address port 01")
        self.write(
            f"SOUR:DIG:DATA:{'WORD' if word else 'BYTE'} {value},{format_channel_list((parsed,))}"
        )

    def digital_output_state(self, channel: ChannelLike) -> bool:
        parsed = Channel.from_value(channel)
        spec = self._module_for_channel(parsed)
        if (
            spec.model != ModuleType.MULTIFUNCTION_34907A
            or parsed.number not in spec.digital_channels
        ):
            raise UnsupportedOperationError(f"{parsed} is not a 34907A digital channel")
        return bool(
            int(float(self.query(f"SOUR:DIG:STATE? {format_channel_list((parsed,))}")))
        )

    def configure_totalizer(
        self,
        channel: ChannelLike,
        *,
        reset_on_read: bool = False,
        falling_edge: bool = False,
    ) -> None:
        parsed = Channel.from_value(channel)
        spec = self._module_for_channel(parsed)
        if (
            spec.model != ModuleType.MULTIFUNCTION_34907A
            or parsed.number not in spec.totalizer_channels
        ):
            raise UnsupportedOperationError(
                f"{parsed} is not a 34907A totalizer channel"
            )
        self.write(
            f"SENS:TOT:TYPE {'RRES' if reset_on_read else 'READ'},{format_channel_list((parsed,))}"
        )
        self.write(
            f"SENS:TOT:SLOPE {'NEG' if falling_edge else 'POS'},{format_channel_list((parsed,))}"
        )

    def read_totalizer(self, channel: ChannelLike) -> int:
        parsed = Channel.from_value(channel)
        spec = self._module_for_channel(parsed)
        if (
            spec.model != ModuleType.MULTIFUNCTION_34907A
            or parsed.number not in spec.totalizer_channels
        ):
            raise UnsupportedOperationError(
                f"{parsed} is not a 34907A totalizer channel"
            )
        return int(
            float(
                parse_csv_response(
                    self.query(f"SENS:TOT:DATA? {format_channel_list((parsed,))}")
                )[0]
            )
        )

    def clear_totalizer(self, channel: ChannelLike) -> None:
        parsed = Channel.from_value(channel)
        spec = self._module_for_channel(parsed)
        if (
            spec.model != ModuleType.MULTIFUNCTION_34907A
            or parsed.number not in spec.totalizer_channels
        ):
            raise UnsupportedOperationError(
                f"{parsed} is not a 34907A totalizer channel"
            )
        self.write(f"SENS:TOT:CLEAR:IMM {format_channel_list((parsed,))}")

    def set_dac(self, channel: ChannelLike, voltage: float) -> None:
        parsed = Channel.from_value(channel)
        spec = self._module_for_channel(parsed)
        if (
            spec.model != ModuleType.MULTIFUNCTION_34907A
            or parsed.number not in spec.dac_channels
        ):
            raise UnsupportedOperationError(f"{parsed} is not a 34907A DAC channel")
        if not -spec.max_dac_voltage <= voltage <= spec.max_dac_voltage:
            raise ValueError("34907A DAC voltage must be between -12 and +12 volts")
        self.write(f"SOUR:VOLT {voltage},{format_channel_list((parsed,))}")

    def dac_voltage(self, channel: ChannelLike) -> float:
        parsed = Channel.from_value(channel)
        spec = self._module_for_channel(parsed)
        if (
            spec.model != ModuleType.MULTIFUNCTION_34907A
            or parsed.number not in spec.dac_channels
        ):
            raise UnsupportedOperationError(f"{parsed} is not a 34907A DAC channel")
        return float(
            parse_csv_response(
                self.query(f"SOUR:VOLT? {format_channel_list((parsed,))}")
            )[0]
        )


# Names commonly used by OpenHTF stations and older HP/Agilent code.
Keysight34970A = Keysight34970APlug
Agilent34970APlug = Keysight34970APlug
HP34970APlug = Keysight34970APlug

__all__ = [
    "Agilent34970APlug",
    "HP34970APlug",
    "Keysight34970A",
    "Keysight34970APlug",
]
