"""OpenHTF plug for the ADCMT 6241A/6242 DC voltage current source/monitor.

The 6241A/6242 uses ADCMT's ADC command set, not SCPI. Commands are sent over
the instrument's USB interface with the shared :mod:`adcmt_usb` transport.
"""

from __future__ import annotations

import math
import re
import threading
import time
from typing import Any

from adcmt_usb import ADCMTUSBTransport
from openhtf.plugs import BasePlug

from .exceptions import CommandError, MeasurementParseError
from .models import (
    BlockDelimiter,
    BufferMemory,
    BuzzerMode,
    CurrentSourceRange,
    DisplayDigits,
    FilterMode,
    IntegrationTime,
    InterlockMode,
    Measurement,
    MeasurementFunction,
    MeasurementRange,
    OutputState,
    RemoteSensing,
    SourceFunction,
    SourceMode,
    Statistics,
    SuspendMode,
    SweepRange,
    SyncOutputMode,
    SyncWidth,
    TriggerMode,
    UnitDisplay,
    VoltageSourceRange,
    check_integer,
    format_number,
    parse_integer,
    parse_measurement,
    parse_number,
)

_ERROR_CODE_RE = re.compile(r"[-+]?\d{3}")


class ADCMT624XUSBTransport(ADCMTUSBTransport):
    """ADCMT USB transport for the 6241A and 6242 source/monitors."""

    PRODUCT_IDS = (0x0301, 0x0302)


class ADCMT624XPlug(BasePlug):
    """Control an ADCMT 6241A/6242 over its USB interface.

    ``usb_id`` selects the unit when multiple instruments are connected and
    defaults to ``1``. The instrument's USB ID is set on its front panel. A
    transport can be injected for tests or custom hardware access.

    The plug tracks the selected source function because the source value,
    limiter, and pulse-base commands differ between voltage and current source
    modes. Call :meth:`set_source_function` before those commands, or pass
    ``function=`` to the individual methods.
    """

    def __init__(
        self,
        *,
        usb_id: int | None = 1,
        timeout_ms: int = 10_000,
        response_size: int = ADCMTUSBTransport.DEFAULT_READ_SIZE,
        command_delay_s: float = 0.02,
        interface_number: int | None = None,
        remote_enable: bool = True,
        transport: Any | None = None,
        device: Any | None = None,
        usb_core: Any | None = None,
        usb_util: Any | None = None,
    ) -> None:
        super().__init__()
        if (
            isinstance(response_size, bool)
            or not isinstance(response_size, int)
            or response_size <= 0
        ):
            raise ValueError("response_size must be a positive integer")
        if (
            isinstance(command_delay_s, bool)
            or not isinstance(command_delay_s, (int, float))
            or not math.isfinite(float(command_delay_s))
            or command_delay_s < 0
        ):
            raise ValueError("command_delay_s must be a non-negative finite number")

        self._response_size = response_size
        self._command_delay_s = float(command_delay_s)
        self._io_lock = threading.RLock()
        self._model: str | None = None
        self._source_function: SourceFunction | None = None
        self._transport = transport or ADCMT624XUSBTransport(
            usb_id=usb_id,
            timeout_ms=timeout_ms,
            read_size=response_size,
            interface_number=interface_number,
            device=device,
            usb_core=usb_core,
            usb_util=usb_util,
            remote_enable=remote_enable,
            command_delay_s=command_delay_s,
        )

    @property
    def transport(self) -> Any:
        """Return the underlying transport for advanced operations."""

        return self._transport

    @property
    def model(self) -> str:
        """Return the instrument model from ``*IDN?`` (``6241A`` or ``6242``)."""

        if self._model is None:
            self.identify()
        if self._model is None:
            raise ValueError("could not determine the instrument model from *IDN?")
        return self._model

    @staticmethod
    def _decode_response(response: bytes) -> str:
        return response.decode("ascii", errors="replace").strip("\x00\r\n ")

    def write(self, command: str) -> None:
        """Send one ADC command without reading a response."""

        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        with self._io_lock:
            self._transport.write(command)

    def read(self, size: int | None = None) -> str:
        """Request and return one response from the instrument."""

        with self._io_lock:
            response = self._transport.read(
                self._response_size if size is None else size
            )
        return self._decode_response(response)

    def query(self, command: str, *, size: int | None = None) -> str:
        """Send a query and return its response."""

        if not isinstance(command, str) or not command.strip():
            raise ValueError("query must be a non-empty string")
        with self._io_lock:
            response = self._transport.query(
                command,
                size=self._response_size if size is None else size,
            )
        return self._decode_response(response)

    def _read_after_trigger(self) -> str:
        with self._io_lock:
            if self._command_delay_s:
                time.sleep(self._command_delay_s)
            return self.read()

    def identify(self) -> str:
        """Return the instrument identity from ``*IDN?``."""

        response = self.query("*IDN?")
        fields = [field.strip() for field in response.split(",")]
        if len(fields) >= 2:
            self._model = fields[1]
        return response

    # ------------------------------------------------------------------
    # System and status
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Initialize parameters with ``*RST``."""

        self.write("*RST")

    def initialize_parameters(self) -> None:
        """Load the default parameter values with ``RINI``."""

        self.write("RINI")

    def clear_status(self) -> None:
        """Clear the status registers with ``*CLS``."""

        self.write("*CLS")

    def clear_device(self) -> None:
        """Clear the device with the ``C`` command."""

        self.write("C")

    def save_parameters(self, slot: int) -> None:
        """Save the current parameters to non-volatile area 0 through 3."""

        check_integer(slot, "parameter slot", 0, 3)
        self.write(f"STP{slot}")

    def load_parameters(self, slot: int) -> None:
        """Load parameters from non-volatile area 0 through 3."""

        check_integer(slot, "parameter slot", 0, 3)
        self.write(f"RCLP{slot}")

    def initialize_saved_parameters(self) -> None:
        """Set default values in all non-volatile parameter areas."""

        self.write("SINI")

    def wait_until_complete(self) -> None:
        """Block until pending operations complete using ``*OPC?``."""

        response = self.query("*OPC?")
        if response not in {"1", "+1"}:
            raise RuntimeError(f"unexpected *OPC? response: {response!r}")

    def self_test(self) -> int:
        """Run the self-test and return 0 (pass) or 1 (fail)."""

        result = parse_integer(self.query("*TST?"), "*TST? response")
        if result not in {0, 1}:
            raise ValueError(f"unexpected *TST? response: {result}")
        return result

    def self_test_register(self) -> tuple[int, int, int, int, int]:
        """Return the five self-test register values from ``TER?``."""

        values = tuple(int(value) for value in re.findall(r"\d+", self.query("TER?")))
        if len(values) != 5:
            raise ValueError(f"unexpected TER? response: {values!r}")
        return values  # type: ignore[return-value]

    def error_count(self) -> int:
        """Return the number of errors in the instrument error log."""

        return parse_integer(self.query("ERC?"), "error count")

    def read_errors(self) -> tuple[int, ...]:
        """Read and clear the error log, returning nonzero error codes."""

        response = self.query("ERL?")
        codes = tuple(int(match) for match in _ERROR_CODE_RE.findall(response))
        return tuple(code for code in codes if code != 0)

    def check_error(self) -> None:
        """Raise :class:`CommandError` if the instrument error log is not empty."""

        count = self.error_count()
        if count:
            raise CommandError(count, self.read_errors())

    def read_error_register(self) -> int:
        """Return the error register value from ``ERR?``."""

        return parse_integer(self.query("ERR?"), "error register")

    def read_status_byte(self) -> int:
        """Return the status byte register value from ``*STB?``."""

        return parse_integer(self.query("*STB?"), "status byte")

    def read_standard_event_status(self) -> int:
        """Return the standard event status register value from ``*ESR?``."""

        return parse_integer(self.query("*ESR?"), "standard event status")

    def read_device_event_status(self) -> int:
        """Return the device event status register value from ``DSR?``."""

        return parse_integer(self.query("DSR?"), "device event status")

    def set_service_request_enable(self, mask: int) -> None:
        """Set the service request enable register (0 through 255)."""

        check_integer(mask, "service request enable mask", 0, 255)
        self.write(f"*SRE{mask}")

    def service_request_enable(self) -> int:
        """Return the service request enable register value."""

        return parse_integer(self.query("*SRE?"), "service request enable mask")

    def set_standard_event_enable(self, mask: int) -> None:
        """Set the standard event status enable register (0 through 255)."""

        check_integer(mask, "standard event enable mask", 0, 255)
        self.write(f"*ESE{mask}")

    def standard_event_enable(self) -> int:
        """Return the standard event status enable register value."""

        return parse_integer(self.query("*ESE?"), "standard event enable mask")

    def set_device_event_enable(self, mask: int) -> None:
        """Set the device event status enable register (0 through 65535)."""

        check_integer(mask, "device event enable mask", 0, 65535)
        self.write(f"DSE{mask}")

    def device_event_enable(self) -> int:
        """Return the device event status enable register value."""

        return parse_integer(self.query("DSE?"), "device event enable mask")

    def set_srq(self, enabled: bool) -> None:
        """Enable or disable SRQ transmission (``S1``/``S0``)."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self.write("S1" if enabled else "S0")

    def srq_enabled(self) -> bool:
        """Return whether SRQ transmission is enabled."""

        return self.query("S?").upper() == "S1"

    def line_frequency(self) -> int:
        """Return the detected line frequency in Hz (50 or 60)."""

        response = self.query("LF?").upper()
        if response == "LF0":
            return 50
        if response == "LF1":
            return 60
        raise ValueError(f"unexpected LF? response: {response!r}")

    def relay_count(self) -> int:
        """Return the relay operation counter from ``RLY?``."""

        return parse_integer(self.query("RLY?"), "relay count")

    def set_header(self, enabled: bool) -> None:
        """Enable or disable talker headers (``OH1``/``OH0``)."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self.write("OH1" if enabled else "OH0")

    def header_enabled(self) -> bool:
        """Return whether talker headers are enabled."""

        return self.query("OH?").upper() == "OH1"

    def set_block_delimiter(self, delimiter: BlockDelimiter | str) -> None:
        """Set the talker block delimiter (``DL0`` through ``DL3``)."""

        self.write(BlockDelimiter.coerce(delimiter).value)

    def block_delimiter(self) -> BlockDelimiter:
        """Return the talker block delimiter."""

        return BlockDelimiter.coerce(self.query("DL?"))

    # ------------------------------------------------------------------
    # Source configuration
    # ------------------------------------------------------------------

    def set_source_function(self, function: SourceFunction | str) -> None:
        """Select voltage or current source mode (``VF``/``IF``)."""

        selected = SourceFunction.coerce(function)
        self.write(selected.value)
        self._source_function = selected

    def source_function(self) -> SourceFunction:
        """Return the source function last selected by this plug."""

        return self._require_source_function()

    def _require_source_function(self) -> SourceFunction:
        if self._source_function is None:
            raise RuntimeError(
                "source function is unknown; call set_source_function() first"
            )
        return self._source_function

    def set_source_mode(self, mode: SourceMode | str) -> None:
        """Select DC, pulse, DC sweep, or pulse sweep mode."""

        self.write(SourceMode.coerce(mode).value)

    def source_mode(self) -> SourceMode:
        """Return the current source mode."""

        return SourceMode.coerce(self.query("MD?"))

    def set_source_range(
        self,
        range: VoltageSourceRange | CurrentSourceRange | str,
        *,
        function: SourceFunction | str | None = None,
    ) -> None:
        """Select a source range for the current source function.

        ``range`` may be a :class:`VoltageSourceRange` or
        :class:`CurrentSourceRange`, or a string such as ``"3V"``, ``"30mA"``,
        or ``"AUTO"``. The range is validated against the selected source
        function.
        """

        if function is not None:
            self.set_source_function(function)
        selected_function = self._require_source_function()
        if selected_function is SourceFunction.VOLTAGE:
            selected_range = VoltageSourceRange.coerce(range)
        else:
            selected_range = CurrentSourceRange.coerce(range)
            if selected_range is CurrentSourceRange.RANGE_5A and self._model == "6241A":
                raise ValueError("the 6241A does not support the 5 A range")
        self.write(selected_range.value)

    def source_range(self) -> str:
        """Return the source range response (for example ``SVRX3`` or ``SIR1``)."""

        command = (
            "SVR?"
            if self._require_source_function() is SourceFunction.VOLTAGE
            else "SIR?"
        )
        return self.query(command)

    def set_source_value(
        self,
        value: float,
        *,
        function: SourceFunction | str | None = None,
    ) -> None:
        """Set the DC source value in volts or amps (``SOV``/``SOI``)."""

        if function is not None:
            self.set_source_function(function)
        selected_function = self._require_source_function()
        command = "SOV" if selected_function is SourceFunction.VOLTAGE else "SOI"
        self.write(f"{command}{format_number(value, 'source value')}")

    def source_value(self) -> float:
        """Return the configured source value in volts or amps."""

        command = (
            "SOV?"
            if self._require_source_function() is SourceFunction.VOLTAGE
            else "SOI?"
        )
        return parse_number(self.query(command), "source value")

    def set_limiter(
        self,
        value: float,
        *,
        low: float | None = None,
        function: SourceFunction | str | None = None,
    ) -> None:
        """Set the source limiter (``LMI`` for voltage source, ``LMV`` for current).

        The limiter is the opposite quantity of the source: a voltage source
        uses a current limiter, and a current source uses a voltage limiter.
        With only ``value``, the instrument uses ``+value`` and ``-value`` as
        the high and low limits. Supplying ``low`` sets asymmetric limits.
        """

        if function is not None:
            self.set_source_function(function)
        selected_function = self._require_source_function()
        command = "LMI" if selected_function is SourceFunction.VOLTAGE else "LMV"
        high_text = format_number(value, "limiter value")
        if low is None:
            payload = high_text
        else:
            low_text = format_number(low, "low limiter value")
            high_value = float(high_text)
            low_value = float(low_text)
            if high_value <= low_value:
                raise ValueError("high limiter must be greater than low limiter")
            if selected_function is SourceFunction.VOLTAGE and (
                (high_value > 0 and low_value > 0) or (high_value < 0 and low_value < 0)
            ):
                raise ValueError(
                    "current limiter high and low values must have opposite polarity"
                )
            payload = f"{high_text},{low_text}"
        self.write(f"{command}{payload}")

    def limiter(self) -> tuple[float, float]:
        """Return the high and low limiter values."""

        command = (
            "LMI?"
            if self._require_source_function() is SourceFunction.VOLTAGE
            else "LMV?"
        )
        response = self.query(command)
        numbers = [
            float(match)
            for match in re.findall(
                r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?", response
            )
        ]
        if len(numbers) < 2:
            raise ValueError(f"unexpected limiter response: {response!r}")
        return numbers[0], numbers[1]

    def set_suspend_voltage(self, value: float) -> None:
        """Set the suspend voltage (``SUV``)."""

        self.write(f"SUV{format_number(value, 'suspend voltage')}")

    def suspend_voltage(self) -> float:
        """Return the suspend voltage."""

        return parse_number(self.query("SUV?"), "suspend voltage")

    def set_suspend_mode(self, mode: SuspendMode | str) -> None:
        """Select high-impedance or low-impedance suspend output."""

        self.write(SuspendMode.coerce(mode).value)

    def suspend_mode(self) -> SuspendMode:
        """Return the suspend output mode."""

        return SuspendMode.coerce(self.query("SUZ?"))

    def set_pulse_base(
        self,
        value: float,
        *,
        function: SourceFunction | str | None = None,
    ) -> None:
        """Set the pulse base value (``DBV``/``DBI``)."""

        if function is not None:
            self.set_source_function(function)
        command = (
            "DBV"
            if self._require_source_function() is SourceFunction.VOLTAGE
            else "DBI"
        )
        self.write(f"{command}{format_number(value, 'pulse base value')}")

    def pulse_base(self) -> float:
        """Return the pulse base value."""

        command = (
            "DBV?"
            if self._require_source_function() is SourceFunction.VOLTAGE
            else "DBI?"
        )
        return parse_number(self.query(command), "pulse base value")

    def set_timing(
        self,
        hold_ms: float,
        delay_ms: float,
        period_ms: float,
        pulse_width_ms: float | None = None,
    ) -> None:
        """Set pulse/sweep timing in milliseconds (``SP``)."""

        values = [
            format_number(hold_ms, "hold time", minimum=0),
            format_number(delay_ms, "measurement delay", minimum=0),
            format_number(period_ms, "period", minimum=0),
        ]
        if pulse_width_ms is not None:
            values.append(format_number(pulse_width_ms, "pulse width", minimum=0))
        self.write(f"SP{','.join(values)}")

    def timing(self) -> tuple[float, float, float, float]:
        """Return hold, delay, period, and pulse-width times in milliseconds."""

        response = self.query("SP?")
        numbers = [
            float(match)
            for match in re.findall(
                r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?", response
            )
        ]
        if len(numbers) < 4:
            raise ValueError(f"unexpected SP? response: {response!r}")
        return numbers[0], numbers[1], numbers[2], numbers[3]

    def set_source_delay(self, delay_ms: float) -> None:
        """Set the source delay time in milliseconds (``SD``)."""

        self.write(f"SD{format_number(delay_ms, 'source delay', minimum=0)}")

    def source_delay(self) -> float:
        """Return the source delay time in milliseconds."""

        return parse_number(self.query("SD?"), "source delay")

    def set_filter(self, mode: FilterMode | str) -> None:
        """Select the slow or fast source filter (``FL0``/``FL1``)."""

        self.write(FilterMode.coerce(mode).value)

    def filter_mode(self) -> FilterMode:
        """Return the source filter mode."""

        return FilterMode.coerce(self.query("FL?"))

    # ------------------------------------------------------------------
    # Measurement configuration
    # ------------------------------------------------------------------

    def set_measurement_function(self, function: MeasurementFunction | str) -> None:
        """Select the measurement function (``F0`` through ``F3``)."""

        self.write(MeasurementFunction.coerce(function).value)

    def measurement_function(self) -> MeasurementFunction:
        """Return the selected measurement function."""

        return MeasurementFunction.coerce(self.query("F?"))

    def set_measurement_range(self, range: MeasurementRange | str) -> None:
        """Select auto or fixed measurement ranging (``R0``/``R1``)."""

        self.write(MeasurementRange.coerce(range).value)

    def measurement_range(self) -> MeasurementRange:
        """Return the measurement range mode."""

        return MeasurementRange.coerce(self.query("R?"))

    def set_function_link(self, enabled: bool) -> None:
        """Enable or disable measurement/source function linking (``FX``)."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self.write("FX1" if enabled else "FX0")

    def function_link(self) -> bool:
        """Return whether measurement/source function linking is enabled."""

        return self.query("FX?").upper() == "FX1"

    def set_integration_time(self, integration_time: IntegrationTime | str) -> None:
        """Select the integration time (``IT0`` through ``IT8``)."""

        self.write(IntegrationTime.coerce(integration_time).value)

    def integration_time(self) -> IntegrationTime:
        """Return the selected integration time."""

        return IntegrationTime.coerce(self.query("IT?"))

    def set_auto_zero(self, enabled: bool) -> None:
        """Enable or disable auto-zero (``AZ1``/``AZ0``)."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self.write("AZ1" if enabled else "AZ0")

    def auto_zero(self) -> bool:
        """Return whether auto-zero is enabled."""

        return self.query("AZ?").upper() == "AZ1"

    def set_display_digits(self, digits: DisplayDigits | str) -> None:
        """Select 3.5, 4.5, or 5.5 displayed digits (``RE3`` through ``RE5``)."""

        self.write(DisplayDigits.coerce(digits).value)

    def display_digits(self) -> DisplayDigits:
        """Return the displayed digit setting."""

        return DisplayDigits.coerce(self.query("RE?"))

    def set_unit_display(self, display: UnitDisplay | str) -> None:
        """Select decimal or exponent unit display (``DM0``/``DM1``)."""

        self.write(UnitDisplay.coerce(display).value)

    def unit_display(self) -> UnitDisplay:
        """Return the unit display mode."""

        return UnitDisplay.coerce(self.query("DM?"))

    def set_remote_sensing(self, sensing: RemoteSensing | str) -> None:
        """Select 2-wire or 4-wire remote sensing (``RS0``/``RS1``)."""

        self.write(RemoteSensing.coerce(sensing).value)

    def remote_sensing(self) -> RemoteSensing:
        """Return the remote sensing mode."""

        return RemoteSensing.coerce(self.query("RS?"))

    def set_auto_range_delay(self, delay_ms: float) -> None:
        """Set the measurement auto-range delay in milliseconds (``RD``)."""

        self.write(f"RD{format_number(delay_ms, 'auto range delay', minimum=0)}")

    def auto_range_delay(self) -> float:
        """Return the measurement auto-range delay in milliseconds."""

        return parse_number(self.query("RD?"), "auto range delay")

    # ------------------------------------------------------------------
    # Output and triggering
    # ------------------------------------------------------------------

    def set_trigger_mode(self, mode: TriggerMode | str) -> None:
        """Select auto or hold trigger mode (``M0``/``M1``)."""

        self.write(TriggerMode.coerce(mode).value)

    def trigger_mode(self) -> TriggerMode:
        """Return the trigger mode."""

        return TriggerMode.coerce(self.query("M?"))

    def operate(self) -> None:
        """Turn the output on (``OPR``)."""

        self.write("OPR")

    def standby(self) -> None:
        """Turn the output off (``SBY``)."""

        self.write("SBY")

    def suspend(self) -> None:
        """Suspend the output (``SUS``)."""

        self.write("SUS")

    def output_state(self) -> OutputState:
        """Return the current output state."""

        response = self.query("OPR?").upper()
        try:
            return OutputState(response)
        except ValueError as exc:
            raise ValueError(f"unexpected output state response: {response!r}") from exc

    def trigger(self) -> None:
        """Send a measurement or sweep trigger (``*TRG``)."""

        self.write("*TRG")

    def spot(self, value: float) -> None:
        """Set the source value and trigger a measurement (``G``)."""

        self.write(f"G{format_number(value, 'spot value')}")

    @staticmethod
    def _parse_measurement_response(response: str) -> Measurement:
        try:
            return parse_measurement(response)
        except ValueError as exc:
            raise MeasurementParseError(str(exc)) from exc

    def read_measurement(self) -> Measurement:
        """Trigger a read and parse one measurement response."""

        return self._parse_measurement_response(self._read_after_trigger())

    def measure(
        self,
        *,
        function: MeasurementFunction | str | None = None,
        source_function: SourceFunction | str | None = None,
        source_value: float | None = None,
        limiter: float | None = None,
        limiter_low: float | None = None,
        range: MeasurementRange | str | None = None,
        integration_time: IntegrationTime | str | None = None,
        auto_zero: bool | None = None,
        trigger_mode: TriggerMode | str | None = TriggerMode.HOLD,
        operate: bool = True,
        standby: bool = False,
    ) -> float:
        """Configure the source, take one reading, and return its value.

        The source function is applied before the source value and limiter so
        the correct ``SOV``/``SOI`` and ``LMV``/``LMI`` commands are used.
        ``standby=True`` turns the output off after the reading, including when
        the read fails.
        """

        try:
            if source_function is not None:
                self.set_source_function(source_function)
            if source_value is not None:
                self.set_source_value(source_value)
            if limiter is not None:
                self.set_limiter(limiter, low=limiter_low)
            if function is not None:
                self.set_measurement_function(function)
            if range is not None:
                self.set_measurement_range(range)
            if integration_time is not None:
                self.set_integration_time(integration_time)
            if auto_zero is not None:
                self.set_auto_zero(auto_zero)
            if trigger_mode is not None:
                self.set_trigger_mode(trigger_mode)
            if operate:
                self.operate()
            self.trigger()
            return self.read_measurement().value
        finally:
            if standby:
                self.standby()

    # ------------------------------------------------------------------
    # Sweep
    # ------------------------------------------------------------------

    def set_sweep_linear(self, start: float, stop: float, step: float) -> None:
        """Configure a linear sweep (``SN``)."""

        self.write(
            "SN"
            + ",".join(
                (
                    format_number(start, "sweep start"),
                    format_number(stop, "sweep stop"),
                    format_number(step, "sweep step"),
                )
            )
        )

    def set_sweep_fixed(self, level: float, count: int) -> None:
        """Configure a fixed-level sweep (``SF``)."""

        check_integer(count, "sweep sample count", 1, 8000)
        self.write(f"SF{format_number(level, 'sweep level')},{count}")

    def set_sweep_random(self, start_address: int, stop_address: int) -> None:
        """Configure a random sweep address range (``SC``)."""

        check_integer(start_address, "sweep start address", 0, 7999)
        check_integer(stop_address, "sweep stop address", 0, 7999)
        if stop_address < start_address:
            raise ValueError("sweep stop address must not be before start address")
        self.write(f"SC{start_address},{stop_address}")

    def set_sweep_two_slope(
        self,
        first: float,
        middle: float,
        last: float,
        step1: float,
        step2: float,
    ) -> None:
        """Configure a two-slope linear sweep (``SM``)."""

        self.write(
            "SM"
            + ",".join(
                (
                    format_number(first, "first sweep value"),
                    format_number(middle, "middle sweep value"),
                    format_number(last, "last sweep value"),
                    format_number(step1, "first sweep step"),
                    format_number(step2, "second sweep step"),
                )
            )
        )

    def sweep_type(self) -> str:
        """Return the current sweep settings from ``SX?``."""

        return self.query("SX?", size=256)

    def set_sweep_range(self, range: SweepRange | str) -> None:
        """Select auto or fixed sweep range (``SR0``/``SR1``)."""

        self.write(SweepRange.coerce(range).value)

    def sweep_range(self) -> SweepRange:
        """Return the sweep range mode."""

        return SweepRange.coerce(self.query("SR?"))

    def set_sweep_count(self, count: int) -> None:
        """Set the sweep repeat count (0 through 1000; 0 is infinite)."""

        check_integer(count, "sweep count", 0, 1000)
        self.write(f"SS{count}")

    def sweep_count(self) -> int:
        """Return the sweep repeat count."""

        return parse_integer(self.query("SS?"), "sweep count")

    def set_sweep_bias(self, value: float) -> None:
        """Set the sweep bias value (``SB``)."""

        self.write(f"SB{format_number(value, 'sweep bias')}")

    def sweep_bias(self) -> float:
        """Return the sweep bias value."""

        return parse_number(self.query("SB?"), "sweep bias")

    def set_sweep_base(self, value: float) -> None:
        """Set the pulse sweep base value (``BS``)."""

        self.write(f"BS{format_number(value, 'sweep base')}")

    def sweep_base(self) -> float:
        """Return the pulse sweep base value."""

        return parse_number(self.query("BS?"), "sweep base")

    def set_reverse(self, enabled: bool) -> None:
        """Enable or disable reverse sweep mode (``SV1``/``SV0``)."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self.write("SV1" if enabled else "SV0")

    def reverse_enabled(self) -> bool:
        """Return whether reverse sweep mode is enabled."""

        return self.query("SV?").upper() == "SV1"

    def set_return_to_bias(self, enabled: bool) -> None:
        """Enable or disable return-to-bias after a sweep (``RB1``/``RB0``)."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self.write("RB1" if enabled else "RB0")

    def return_to_bias_enabled(self) -> bool:
        """Return whether return-to-bias is enabled."""

        return self.query("RB?").upper() == "RB1"

    def stop_sweep(self) -> None:
        """Stop a running sweep (``SWSP``)."""

        self.write("SWSP")

    def save_random_sweep(self) -> None:
        """Save random sweep data (``RSAV``)."""

        self.write("RSAV")

    def load_random_sweep(self) -> None:
        """Load random sweep data (``RLOD``)."""

        self.write("RLOD")

    def clear_random_sweep(self) -> None:
        """Initialize random sweep data (``RCLR``)."""

        self.write("RCLR")

    def random_sweep_status(self) -> int:
        """Return 0 when random sweep memory setting is complete, else 1."""

        return parse_integer(self.query("NP?"), "random sweep status")

    # ------------------------------------------------------------------
    # Measurement buffer memory
    # ------------------------------------------------------------------

    def set_buffer_memory(self, mode: BufferMemory | str) -> None:
        """Select measurement buffer memory mode (``ST0`` through ``ST2``)."""

        self.write(BufferMemory.coerce(mode).value)

    def buffer_memory(self) -> BufferMemory:
        """Return the measurement buffer memory mode."""

        return BufferMemory.coerce(self.query("ST?"))

    def clear_buffer(self) -> None:
        """Clear stored measurement data (``RL``)."""

        self.write("RL")

    def set_store_target(self, count: int) -> None:
        """Set the number of measurements that sets the memory-full event."""

        check_integer(count, "store target", 0, 8000)
        self.write(f"RNM{count}")

    def store_target(self) -> int:
        """Return the configured store target."""

        return parse_integer(self.query("RNM?"), "store target")

    def stored_count(self) -> int:
        """Return the number of measurements stored in buffer memory."""

        return parse_integer(self.query("SZ?"), "stored count")

    def set_recall(self, enabled: bool, address: int | None = None) -> None:
        """Enter or leave buffer recall mode (``RN``)."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        command = "RN1" if enabled else "RN0"
        if address is not None:
            check_integer(address, "recall address", 0, 7999)
            command += f",{address}"
        self.write(command)

    def recall_status(self) -> tuple[bool, int]:
        """Return the recall mode and current recall address."""

        response = self.query("RN?")
        numbers = [int(value) for value in re.findall(r"\d+", response)]
        if len(numbers) < 2:
            raise ValueError(f"unexpected RN? response: {response!r}")
        return bool(numbers[0]), numbers[1]

    def read_buffer(self, count: int | None = None) -> tuple[Measurement, ...]:
        """Read stored measurements from buffer memory.

        When ``count`` is omitted, the instrument's stored count is used. The
        recall mode is always released before returning.
        """

        if count is None:
            count = self.stored_count()
        check_integer(count, "buffer read count", 0, 8000)
        if count == 0:
            return ()

        self.set_recall(True, 0)
        measurements: list[Measurement] = []
        try:
            for _ in range(count):
                measurement = self._parse_measurement_response(self.read())
                if measurement.is_empty:
                    break
                measurements.append(measurement)
        finally:
            self.set_recall(False, 0)
        return tuple(measurements)

    # ------------------------------------------------------------------
    # Calculation functions
    # ------------------------------------------------------------------

    def set_null(self, enabled: bool) -> None:
        """Enable or disable NULL calculation (``NL1``/``NL0``)."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self.write("NL1" if enabled else "NL0")

    def null_enabled(self) -> bool:
        """Return whether NULL calculation is enabled."""

        return self.query("NL?").upper() == "NL1"

    def set_null_value(self, value: float) -> None:
        """Set the NULL constant (``KNL``); enable NULL calculation first."""

        self.write(f"KNL{format_number(value, 'null value')}")

    def null_value(self) -> float:
        """Return the NULL constant."""

        return parse_number(self.query("KNL?"), "null value")

    def set_comparator(self, enabled: bool) -> None:
        """Enable or disable comparator calculation (``CO1``/``CO0``)."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self.write("CO1" if enabled else "CO0")

    def comparator_enabled(self) -> bool:
        """Return whether comparator calculation is enabled."""

        return self.query("CO?").upper() == "CO1"

    def set_comparator_limits(self, high: float, low: float) -> None:
        """Set the comparator high and low limits (``KHI``/``KLO``).

        Enable comparator calculation with :meth:`set_comparator` first.
        """

        high_text = format_number(high, "comparator high limit")
        low_text = format_number(low, "comparator low limit")
        if float(high_text) <= float(low_text):
            raise ValueError("comparator high limit must be greater than low limit")
        self.write(f"KHI{high_text}")
        self.write(f"KLO{low_text}")

    def comparator_limits(self) -> tuple[float, float]:
        """Return the comparator high and low limits."""

        high = parse_number(self.query("KHI?"), "comparator high limit")
        low = parse_number(self.query("KLO?"), "comparator low limit")
        return high, low

    def set_scaling(self, enabled: bool) -> None:
        """Enable or disable scaling calculation (``SCL1``/``SCL0``)."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self.write("SCL1" if enabled else "SCL0")

    def scaling_enabled(self) -> bool:
        """Return whether scaling calculation is enabled."""

        return self.query("SCL?").upper() == "SCL1"

    def set_scaling_constants(self, a: float, b: float, c: float) -> None:
        """Set scaling constants A, B, and C (``KA``/``KB``/``KC``).

        Enable scaling calculation with :meth:`set_scaling` first.
        """

        if float(format_number(a, "scaling constant A")) == 0:
            raise ValueError("scaling constant A must not be zero")
        self.write(f"KA{format_number(a, 'scaling constant A')}")
        self.write(f"KB{format_number(b, 'scaling constant B')}")
        self.write(f"KC{format_number(c, 'scaling constant C')}")

    def scaling_constants(self) -> tuple[float, float, float]:
        """Return scaling constants A, B, and C."""

        return (
            parse_number(self.query("KA?"), "scaling constant A"),
            parse_number(self.query("KB?"), "scaling constant B"),
            parse_number(self.query("KC?"), "scaling constant C"),
        )

    def set_max_min(self, enabled: bool) -> None:
        """Enable or disable MAX/MIN calculation (``MN1``/``MN0``)."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self.write("MN1" if enabled else "MN0")

    def max_min_enabled(self) -> bool:
        """Return whether MAX/MIN calculation is enabled."""

        return self.query("MN?").upper() == "MN1"

    def statistics(self) -> Statistics:
        """Return average, maximum, minimum, total, and count.

        Enable MAX/MIN calculation with :meth:`set_max_min` first.
        """

        return Statistics(
            average=parse_number(self.query("AVE?"), "average"),
            maximum=parse_number(self.query("MAX?"), "maximum"),
            minimum=parse_number(self.query("MIN?"), "minimum"),
            total=parse_number(self.query("TOT?"), "total"),
            count=parse_integer(self.query("AVN?"), "measurement count"),
        )

    # ------------------------------------------------------------------
    # Buzzer and synchronous control
    # ------------------------------------------------------------------

    def set_buzzer(self, mode: BuzzerMode | str) -> None:
        """Select the comparator/limit buzzer mode (``BZ0`` through ``BZ4``)."""

        self.write(BuzzerMode.coerce(mode).value)

    def buzzer(self) -> BuzzerMode:
        """Return the buzzer mode."""

        return BuzzerMode.coerce(self.query("BZ?"))

    def set_limit_buzzer(self, enabled: bool) -> None:
        """Enable or disable the limit-detection buzzer (``UZ1``/``UZ0``)."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self.write("UZ1" if enabled else "UZ0")

    def limit_buzzer(self) -> bool:
        """Return whether the limit-detection buzzer is enabled."""

        return self.query("UZ?").upper() == "UZ1"

    def set_notice_buzzer(self, enabled: bool) -> None:
        """Enable or disable the notice buzzer (``NZ1``/``NZ0``)."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self.write("NZ1" if enabled else "NZ0")

    def notice_buzzer(self) -> bool:
        """Return whether the notice buzzer is enabled."""

        return self.query("NZ?").upper() == "NZ1"

    def set_interlock(self, mode: InterlockMode | str) -> None:
        """Select the interlock input/output mode (``OP0`` through ``OP4``)."""

        self.write(InterlockMode.coerce(mode).value)

    def interlock(self) -> InterlockMode:
        """Return the interlock mode."""

        return InterlockMode.coerce(self.query("OP?"))

    def set_sync_output(self, mode: SyncOutputMode | str) -> None:
        """Select the synchronous control output (``CP0`` through ``CP6``)."""

        self.write(SyncOutputMode.coerce(mode).value)

    def sync_output(self) -> SyncOutputMode:
        """Return the synchronous control output mode."""

        return SyncOutputMode.coerce(self.query("CP?"))

    def set_sync_width(self, width: SyncWidth | str) -> None:
        """Select the synchronous control output width (``CW0``/``CW1``)."""

        self.write(SyncWidth.coerce(width).value)

    def sync_width(self) -> SyncWidth:
        """Return the synchronous control output width."""

        return SyncWidth.coerce(self.query("CW?"))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying USB transport."""

        with self._io_lock:
            self._transport.close()

    def tearDown(self) -> None:
        """Release the USB device when OpenHTF tears down the plug."""

        self.close()


ADCMT6241APlug = ADCMT624XPlug
ADCMT6242APlug = ADCMT624XPlug

__all__ = [
    "ADCMT624XPlug",
    "ADCMT624XUSBTransport",
    "ADCMT6241APlug",
    "ADCMT6242APlug",
]
