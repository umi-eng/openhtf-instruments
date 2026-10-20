"""OpenHTF/PyVISA support for the ADCMT AD7461A digital multimeter."""

from __future__ import annotations

import csv
import io
import math
import os
import re
import threading
from collections.abc import Mapping
from typing import Any

import pyvisa
from openhtf.plugs import BasePlug

from .exceptions import ScpiError
from .models import (
    ACFilter,
    ErrorEntry,
    EventRegister,
    InputTerminal,
    MeasurementFunction,
    SamplingRate,
    TriggerSlope,
    TriggerSource,
)

_NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?")
_RANGE_FUNCTIONS = frozenset(
    {
        MeasurementFunction.DC_VOLTAGE,
        MeasurementFunction.AC_VOLTAGE,
        MeasurementFunction.RESISTANCE,
        MeasurementFunction.FOUR_WIRE_RESISTANCE,
        MeasurementFunction.DC_CURRENT,
        MeasurementFunction.AC_CURRENT,
        MeasurementFunction.AC_DC_VOLTAGE,
        MeasurementFunction.AC_DC_CURRENT,
        MeasurementFunction.LOW_POWER_RESISTANCE,
        MeasurementFunction.LOW_POWER_FOUR_WIRE_RESISTANCE,
        MeasurementFunction.FREQUENCY,
    }
)
_AC_FUNCTIONS = frozenset(
    {
        MeasurementFunction.AC_VOLTAGE,
        MeasurementFunction.AC_CURRENT,
        MeasurementFunction.AC_DC_VOLTAGE,
        MeasurementFunction.AC_DC_CURRENT,
    }
)


def parse_measurements(response: str) -> tuple[float, ...]:
    """Parse numeric readings from the instrument's optional-header output.

    The AD7461A can prefix each value with a measurement and calculation
    header. Taking the final numeric token in each comma-delimited field avoids
    interpreting digits in headers such as ``R2W`` as measurement values.
    """

    normalized = response.strip().replace("\r", "").replace("\n", ",")
    if not normalized:
        raise ValueError("the instrument returned an empty measurement response")
    fields = next(csv.reader(io.StringIO(normalized)), [])
    values: list[float] = []
    for field in fields:
        matches = _NUMBER_RE.findall(field)
        if not matches:
            raise ValueError(f"could not parse measurement value from {field!r}")
        values.append(float(matches[-1]))
    if not values:
        raise ValueError(f"could not parse measurement response: {response!r}")
    return tuple(values)


def _format_number(value: float, name: str, *, minimum: float = 0.0) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < minimum:
        raise ValueError(f"{name} must be finite and at least {minimum}")
    return format(number, ".12g")


_MAX_CALCULATION_CONSTANT = 9_999_999 * 10**6
_MIN_DB_REFERENCE = 0.000001e-9


def _format_bounded_number(
    value: float, name: str, *, minimum: float, maximum: float
) -> str:
    formatted = _format_number(value, name, minimum=minimum)
    if float(formatted) > maximum:
        raise ValueError(f"{name} must not exceed {maximum}")
    return formatted


def _format_calculation_constant(value: float | str) -> str:
    if isinstance(value, str):
        if value.strip().upper() in {"MEAS", "MEASUREMENT"}:
            return "MEASurement"
        raise ValueError("calculation constants must be numeric or 'MEASurement'")
    return _format_bounded_number(
        value,
        "calculation constant",
        minimum=-_MAX_CALCULATION_CONSTANT,
        maximum=_MAX_CALCULATION_CONSTANT,
    )


class ADCMTAD7461APlug(BasePlug):
    """Control an ADCMT/Advantest AD7461A using its documented SCPI commands.

    ``resource_name`` is passed directly to PyVISA. Typical examples are
    ``GPIB0::1::INSTR`` and ``ASRL/dev/cu.usbserial-0001::INSTR``. The USB
    implementation in the instrument manual requires ADCMT's USB driver; this
    plug only provides the PyVISA transport boundary and does not implement
    that proprietary driver. A resource or resource manager can be injected
    for tests and custom transports.
    """

    ENVIRONMENT_VARIABLE = "ADCMT_AD7461A_RESOURCE"

    def __init__(
        self,
        resource_name: str | None = None,
        *,
        visa_library: str = "",
        resource_manager: Any | None = None,
        resource: Any | None = None,
        resource_kwargs: Mapping[str, Any] | None = None,
        timeout_ms: int = 5000,
        read_termination: str | None = "\n",
        write_termination: str | None = "\n",
        baud_rate: int | None = None,
        verify_writes: bool = False,
    ) -> None:
        super().__init__()
        resource_name = resource_name or os.environ.get(self.ENVIRONMENT_VARIABLE)
        if resource is None and resource_name is None:
            raise ValueError(
                "resource_name is required when resource is not supplied "
                f"(or set {self.ENVIRONMENT_VARIABLE})"
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
        self.verify_writes = verify_writes
        self._io_lock = threading.RLock()
        self._configure_resource(
            timeout_ms=timeout_ms,
            read_termination=read_termination,
            write_termination=write_termination,
            baud_rate=baud_rate,
        )

    @property
    def resource(self) -> Any:
        """Return the underlying PyVISA resource for advanced use."""

        return self._resource

    def _configure_resource(
        self,
        *,
        timeout_ms: int,
        read_termination: str | None,
        write_termination: str | None,
        baud_rate: int | None,
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
        if baud_rate is not None:
            try:
                self._resource.baud_rate = baud_rate
            except (AttributeError, TypeError, ValueError):
                pass

    def write(self, command: str) -> None:
        """Send a raw SCPI command without reading a response."""

        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        with self._io_lock:
            self._resource.write(command)

    def query(self, command: str) -> str:
        """Send a raw SCPI query and return its stripped textual response."""

        if not isinstance(command, str) or not command.strip():
            raise ValueError("query must be a non-empty string")
        with self._io_lock:
            query_method = getattr(self._resource, "query", None)
            if callable(query_method):
                response = query_method(command)
            else:
                self._resource.write(command)
                response = self._resource.read()
        if isinstance(response, bytes):
            return response.decode(errors="replace").strip()
        return str(response).strip()

    def _write_setting(self, command: str) -> None:
        """Write a setting and optionally synchronize/check the SCPI queue."""

        with self._io_lock:
            self.write(command)
            if self.verify_writes:
                completed = self.query("*OPC?")
                if completed not in {"1", "+1"}:
                    raise RuntimeError(
                        f"instrument did not confirm command completion: {completed!r}"
                    )
                self.check_error()

    def identify(self) -> str:
        """Return the instrument identity from ``*IDN?``."""

        return self.query("*IDN?")

    def reset(self) -> None:
        """Reset the instrument to its SCPI defaults (``*RST``)."""

        self.write("*RST")

    def clear_status(self) -> None:
        """Clear the instrument status registers (``*CLS``)."""

        self.write("*CLS")

    def wait_until_complete(self) -> None:
        """Block until pending instrument operations complete using ``*OPC?``."""

        response = self.query("*OPC?")
        if response not in {"1", "+1"}:
            raise RuntimeError(f"unexpected *OPC? response: {response!r}")

    def self_test(self) -> int:
        """Run the instrument self-test and return its result (0 pass, 1 fail)."""

        result = int(self.query("*TST?").strip())
        if result not in {0, 1}:
            raise ValueError(f"unexpected *TST? response: {result}")
        return result

    def check_error(self) -> ErrorEntry:
        """Read the oldest error and raise :class:`ScpiError` if it is nonzero."""

        entry = ErrorEntry.parse(self.query(":SYSTem:ERRor?"))
        if entry.code:
            raise ScpiError(entry.code, entry.message)
        return entry

    def read_errors(self, *, limit: int = 20) -> tuple[ErrorEntry, ...]:
        """Drain the FIFO error queue, stopping at ``0, No error``."""

        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        errors: list[ErrorEntry] = []
        for _ in range(limit):
            entry = ErrorEntry.parse(self.query(":SYSTem:ERRor?"))
            if entry.code == 0:
                break
            errors.append(entry)
        return tuple(errors)

    @staticmethod
    def _function(function: MeasurementFunction | str) -> MeasurementFunction:
        return MeasurementFunction.coerce(function)

    @staticmethod
    def _function_command(
        function: MeasurementFunction, suffix: str, *, sense: bool = True
    ) -> str:
        root = ":SENSe:" if sense else ":MEASure:"
        return f"{root}{function.value}:{suffix}"

    def set_measurement_function(self, function: MeasurementFunction | str) -> None:
        """Select the measurement function without starting a reading."""

        measurement = self._function(function)
        self._write_setting(f':SENSe:FUNCtion "{measurement.value}"')

    def measurement_function(self) -> MeasurementFunction:
        """Read and return the currently selected measurement function."""

        value = self.query(":SENSe:FUNCtion?").strip().strip("\"'")
        return self._function(value)

    def configure_preset(self, function: MeasurementFunction | str) -> None:
        """Apply the instrument's preset configuration for one function."""

        measurement = self._function(function)
        self._write_setting(f":CONFigure:{measurement.value}")

    def configuration(self) -> tuple[MeasurementFunction, float]:
        """Read the active function and range using ``:CONFigure?``."""

        fields = next(csv.reader([self.query(":CONFigure?")]))
        if len(fields) < 2:
            raise ValueError(f"invalid configuration response: {fields!r}")
        return self._function(fields[0]), float(fields[1])

    def configure_measurement(
        self,
        function: MeasurementFunction | str,
        *,
        range: float | None = None,
        auto_range: bool | None = None,
        sampling_rate: SamplingRate | str | None = None,
        digits: int | None = None,
        nplc: float | None = None,
        aperture: float | None = None,
        ac_filter: ACFilter | str | None = None,
    ) -> None:
        """Select a function and apply its optional range/sampling settings.

        ``range`` and ``auto_range`` are mutually exclusive. The AD7461A
        disables range commands for continuity and diode measurements; NPLC
        and aperture are disabled for frequency. AC filtering is only valid for
        AC voltage/current measurements.
        """

        measurement = self._function(function)
        if range is not None and auto_range is not None:
            raise ValueError("range and auto_range cannot both be specified")
        if (
            range is not None or auto_range is not None
        ) and measurement not in _RANGE_FUNCTIONS:
            raise ValueError(f"range is not supported for {measurement.name}")
        if range is not None:
            _format_number(range, "range")
        if auto_range is not None and not isinstance(auto_range, bool):
            raise TypeError("auto_range must be a bool")
        if sampling_rate is not None:
            SamplingRate.coerce(sampling_rate)
        if digits is not None and (
            isinstance(digits, bool)
            or not isinstance(digits, int)
            or not 3 <= digits <= 6
        ):
            raise ValueError("digits must be an integer from 3 through 6")
        if nplc is not None and aperture is not None:
            raise ValueError("nplc and aperture are mutually exclusive")
        if (nplc is not None or aperture is not None) and (
            measurement == MeasurementFunction.FREQUENCY
        ):
            raise ValueError("NPLC and aperture are disabled for frequency")
        if nplc is not None:
            value = _format_number(nplc, "nplc", minimum=0.02)
            if float(value) > 100:
                raise ValueError("nplc must be between 0.02 and 100")
        if aperture is not None:
            value = _format_number(aperture, "aperture", minimum=0.00001)
            if float(value) > 10:
                raise ValueError("aperture must be between 0.00001 and 10 seconds")
        if ac_filter is not None:
            if measurement not in _AC_FUNCTIONS:
                raise ValueError(
                    f"AC filtering is not supported for {measurement.name}"
                )
            ACFilter.coerce(ac_filter)

        with self._io_lock:
            self.set_measurement_function(measurement)
            if range is not None:
                self.set_range(measurement, range)
            elif auto_range is not None:
                self.set_auto_range(measurement, auto_range)
            if sampling_rate is not None:
                self.set_sampling_rate(measurement, sampling_rate)
            if digits is not None:
                self.set_digits(measurement, digits)
            if nplc is not None:
                self.set_integration_time(measurement, nplc=nplc)
            if aperture is not None:
                self.set_integration_time(measurement, aperture=aperture)
            if ac_filter is not None:
                self.set_ac_filter(measurement, ac_filter)

    def set_range(self, function: MeasurementFunction | str, value: float) -> None:
        """Set a manual measurement range (zero is accepted by the SCPI spec)."""

        measurement = self._function(function)
        if measurement not in _RANGE_FUNCTIONS:
            raise ValueError(f"range is not supported for {measurement.name}")
        number = _format_number(value, "range")
        self._write_setting(self._function_command(measurement, "RANGe") + f" {number}")

    def range(self, function: MeasurementFunction | str) -> float:
        """Read the selected manual range for ``function``."""

        measurement = self._function(function)
        return float(self.query(self._function_command(measurement, "RANGe") + "?"))

    def set_auto_range(
        self, function: MeasurementFunction | str, enabled: bool
    ) -> None:
        """Enable or disable auto-ranging for a range-capable function."""

        measurement = self._function(function)
        if measurement not in _RANGE_FUNCTIONS:
            raise ValueError(f"auto-ranging is not supported for {measurement.name}")
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        value = "ON" if enabled else "OFF"
        self._write_setting(
            self._function_command(measurement, "RANGe:AUTO") + f" {value}"
        )

    def auto_range(self, function: MeasurementFunction | str) -> bool:
        """Return whether auto-ranging is enabled for ``function``."""

        measurement = self._function(function)
        response = self.query(self._function_command(measurement, "RANGe:AUTO") + "?")
        return response.strip().upper() in {"1", "ON"}

    def set_sampling_rate(
        self, function: MeasurementFunction | str, rate: SamplingRate | str
    ) -> None:
        """Set a sampling rate preset for a measurement function."""

        measurement = self._function(function)
        sampling_rate = SamplingRate.coerce(rate)
        self._write_setting(
            self._function_command(measurement, "SRATe") + f" {sampling_rate.value}"
        )

    def sampling_rate(self, function: MeasurementFunction | str) -> SamplingRate:
        """Read a function's sampling-rate preset."""

        measurement = self._function(function)
        response = self.query(self._function_command(measurement, "SRATe") + "?")
        return SamplingRate.coerce(response)

    def set_digits(self, function: MeasurementFunction | str, digits: int) -> None:
        """Set displayed digits; valid AD7461A values are 3 through 6."""

        measurement = self._function(function)
        if (
            isinstance(digits, bool)
            or not isinstance(digits, int)
            or not 3 <= digits <= 6
        ):
            raise ValueError("digits must be an integer from 3 through 6")
        self._write_setting(
            self._function_command(measurement, "DIGits") + f" {digits}"
        )

    def set_integration_time(
        self,
        function: MeasurementFunction | str,
        *,
        nplc: float | None = None,
        aperture: float | None = None,
    ) -> None:
        """Set integration time in power-line cycles or seconds."""

        measurement = self._function(function)
        if (nplc is None) == (aperture is None):
            raise ValueError("specify exactly one of nplc or aperture")
        if measurement == MeasurementFunction.FREQUENCY:
            raise ValueError("integration time is disabled for frequency")
        if nplc is not None:
            value = _format_number(nplc, "nplc", minimum=0.02)
            if float(value) > 100:
                raise ValueError("nplc must be between 0.02 and 100")
            command = self._function_command(measurement, "NPLCycles")
        else:
            assert aperture is not None
            value = _format_number(aperture, "aperture", minimum=0.00001)
            if float(value) > 10:
                raise ValueError("aperture must be between 0.00001 and 10 seconds")
            command = self._function_command(measurement, "APERture")
        self._write_setting(f"{command} {value}")

    def set_ac_filter(
        self, function: MeasurementFunction | str, value: ACFilter | str
    ) -> None:
        """Set the AC filter for ACV, ACI, ACV(AC+DC), or ACI(AC+DC)."""

        measurement = self._function(function)
        if measurement not in _AC_FUNCTIONS:
            raise ValueError(f"AC filtering is not supported for {measurement.name}")
        ac_filter = ACFilter.coerce(value)
        self._write_setting(
            self._function_command(measurement, "FILTer") + f" {ac_filter.value}"
        )

    def set_auto_zero(self, mode: bool | str) -> None:
        """Set auto-zero ON/OFF or request a one-shot ``ONCE`` auto-zero."""

        if isinstance(mode, bool):
            value = "ON" if mode else "OFF"
        else:
            value = mode.strip().upper()
            if value not in {"ON", "OFF", "ONCE"}:
                raise ValueError("auto-zero mode must be ON, OFF, or ONCE")
        self._write_setting(f":SENSe:ZERO:AUTO {value}")

    def auto_zero(self) -> bool:
        """Return the current auto-zero state."""

        return self.query(":SENSe:ZERO:AUTO?").upper() in {"1", "ON"}

    def set_continuity_threshold(self, ohms: int) -> None:
        """Set the continuity threshold to 1, 10, or 1000 ohms."""

        if (
            isinstance(ohms, bool)
            or not isinstance(ohms, int)
            or ohms not in {1, 10, 1000}
        ):
            raise ValueError("continuity threshold must be 1, 10, or 1000 ohms")
        self._write_setting(f":SENSe:CONTinuity:THReshold {ohms}")

    def continuity_threshold(self) -> int:
        """Read the continuity threshold in ohms."""

        return int(self.query(":SENSe:CONTinuity:THReshold?"))

    def set_input_impedance_auto(self, enabled: bool) -> None:
        """Select automatic/high input impedance for DC voltage measurement."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        value = "ON" if enabled else "OFF"
        self._write_setting(f":INPut:IMPedance:AUTO {value}")

    def input_impedance_auto(self) -> bool:
        """Return the DC input impedance auto-selection state."""

        return self.query(":INPut:IMPedance:AUTO?").upper() in {"1", "ON"}

    def measure(
        self, function: MeasurementFunction | str, *, range: float | None = None
    ) -> float:
        """Take and return one measurement using the requested function.

        With no explicit range the instrument's one-shot ``MEASure`` query is
        used. Supplying a range selects that function/range and then performs a
        ``READ?``. Use :meth:`read_measurements` or :meth:`fetch_measurements`
        for multi-sample results.
        """

        measurement = self._function(function)
        if range is None:
            response = self.query(f":MEASure:{measurement.value}?")
        else:
            self.configure_measurement(measurement, range=range)
            response = self.query(":READ?")
        values = parse_measurements(response)
        if len(values) != 1:
            raise ValueError(f"expected one measurement, received {len(values)}")
        return values[0]

    def read_measurements(self) -> tuple[float, ...]:
        """Start a measurement sequence and return the values from ``READ?``."""

        return parse_measurements(self.query(":READ?"))

    def read(self) -> float:
        """Read one value with ``READ?``; raise if the response has many values."""

        values = self.read_measurements()
        if len(values) != 1:
            raise ValueError(f"expected one measurement, received {len(values)}")
        return values[0]

    def fetch_measurements(self) -> tuple[float, ...]:
        """Return available measurement results using ``FETCh?``."""

        return parse_measurements(self.query(":FETCh?"))

    def fetch(self) -> float:
        """Fetch one result; use :meth:`fetch_measurements` for a result series."""

        values = self.fetch_measurements()
        if len(values) != 1:
            raise ValueError(f"expected one measurement, received {len(values)}")
        return values[0]

    def initiate(self) -> None:
        """Start the trigger/measurement system (``:INITiate``)."""

        self.write(":INITiate")

    def set_continuous(self, enabled: bool) -> None:
        """Enable or disable continuous trigger-system operation."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        value = "ON" if enabled else "OFF"
        self._write_setting(f":INITiate:CONTinuous {value}")

    def continuous(self) -> bool:
        """Return the continuous trigger-system state."""

        return self.query(":INITiate:CONTinuous?").upper() in {"1", "ON"}

    def abort(self) -> None:
        """Abort the trigger system (``:ABORt``)."""

        self.write(":ABORt")

    def trigger(self) -> None:
        """Send a bus trigger (``*TRG``)."""

        self.write("*TRG")

    def set_sample_count(self, count: int) -> None:
        """Set samples acquired per trigger (1 through 16000)."""

        self._check_integer(count, "sample count", 1, 16000)
        self._write_setting(f":SAMPle:COUNt {count}")

    def sample_count(self) -> int:
        """Read the number of samples acquired per trigger."""

        return int(self.query(":SAMPle:COUNt?"))

    def set_trigger_source(self, source: TriggerSource | str) -> None:
        """Select the trigger source (immediate, manual, external, bus, level, delta)."""

        trigger_source = TriggerSource.coerce(source)
        self._write_setting(f":TRIGger:SOURce {trigger_source.value}")

    def trigger_source(self) -> TriggerSource:
        """Read the selected trigger source."""

        return TriggerSource.coerce(self.query(":TRIGger:SOURce?"))

    def set_trigger_count(self, count: int) -> None:
        """Set trigger count (1 through 50000)."""

        self._check_integer(count, "trigger count", 1, 50000)
        self._write_setting(f":TRIGger:COUNt {count}")

    def trigger_count(self) -> int:
        """Read the number of triggers in the trigger sequence."""

        return int(self.query(":TRIGger:COUNt?"))

    def set_trigger_delay(self, seconds: float) -> None:
        """Set trigger delay in seconds (0 through 3600)."""

        value = _format_number(seconds, "trigger delay")
        if float(value) > 3600:
            raise ValueError("trigger delay must be between 0 and 3600 seconds")
        self._write_setting(f":TRIGger:DELay {value}")

    def trigger_delay(self) -> float:
        """Read trigger delay in seconds."""

        return float(self.query(":TRIGger:DELay?"))

    def set_sampling_interval(self, seconds: float) -> None:
        """Set the timer trigger interval in seconds (50 microseconds to 3600)."""

        value = _format_number(seconds, "sampling interval", minimum=0.00005)
        if float(value) > 3600:
            raise ValueError("sampling interval must not exceed 3600 seconds")
        self._write_setting(f":TRIGger:TIMer {value}")

    def sampling_interval(self) -> float:
        """Read the timer trigger interval in seconds."""

        return float(self.query(":TRIGger:TIMer?"))

    def set_level_trigger(self, count: int, slope: TriggerSlope | str) -> None:
        """Set level-trigger count threshold and edge direction."""

        self._check_integer(count, "level trigger count", -1199999, 1199999)
        if TriggerSlope.coerce(slope) == TriggerSlope.BOTH:
            raise ValueError("level trigger slope must be POSitive or NEGative")
        selected_slope = TriggerSlope.coerce(slope)
        with self._io_lock:
            self._write_setting(f":TSYStem:LEVel {count}")
            self._write_setting(f":TSYStem:LEVel:SLOPe {selected_slope.value}")

    def set_delta_trigger(
        self,
        count: int,
        slope: TriggerSlope | str,
        *,
        direction: str = "CONVergence",
    ) -> None:
        """Set delta-trigger count, slope, and convergence/divergence direction."""

        self._check_integer(count, "delta trigger count", 1, 600000)
        selected_slope = TriggerSlope.coerce(slope)
        direction_key = direction.strip().upper()
        directions = {
            "CONV": "CONVergence",
            "CONVERGENCE": "CONVergence",
            "EMIS": "EMISsion",
            "EMISSION": "EMISsion",
            "DIVERGENCE": "EMISsion",
        }
        try:
            selected_direction = directions[direction_key]
        except KeyError as exc:
            raise ValueError("direction must be convergence or emission") from exc
        with self._io_lock:
            self._write_setting(f":TSYStem:DELTa {count}")
            self._write_setting(f":TSYStem:DELTa:SLOPe {selected_slope.value}")
            self._write_setting(f":TSYStem:DELTa:DIRection {selected_direction}")

    def set_pretrigger(self, percent: int) -> None:
        """Set pre-trigger history percentage (0, 25, 50, or 75)."""

        if (
            isinstance(percent, bool)
            or not isinstance(percent, int)
            or percent not in {0, 25, 50, 75}
        ):
            raise ValueError("pretrigger must be 0, 25, 50, or 75 percent")
        self._write_setting(f":TSYStem:PRETrigger {percent}")

    def set_reference_calculation(
        self,
        function: MeasurementFunction | str,
        enabled: bool,
        *,
        reference: float | None = None,
    ) -> None:
        """Configure NULL/reference subtraction for a measurement function."""

        measurement = self._function(function)
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        reference_value = (
            _format_bounded_number(
                reference,
                "reference",
                minimum=-_MAX_CALCULATION_CONSTANT,
                maximum=_MAX_CALCULATION_CONSTANT,
            )
            if reference is not None
            else None
        )
        with self._io_lock:
            self._write_setting(
                self._function_command(measurement, "REFerence:STATe")
                + (" ON" if enabled else " OFF")
            )
            if reference_value is not None:
                self._write_setting(
                    self._function_command(measurement, "REFerence")
                    + f" {reference_value}"
                )

    def reference_calculation_enabled(
        self, function: MeasurementFunction | str
    ) -> bool:
        """Return the NULL/reference-calculation state."""

        measurement = self._function(function)
        response = self.query(
            self._function_command(measurement, "REFerence:STATe") + "?"
        )
        return response.upper() in {"1", "ON"}

    def reference_value(self, function: MeasurementFunction | str) -> float:
        """Read the NULL/reference constant for ``function``."""

        measurement = self._function(function)
        return float(self.query(self._function_command(measurement, "REFerence") + "?"))

    def set_smoothing(self, enabled: bool, *, points: int | None = None) -> None:
        """Enable/disable moving-average smoothing and optionally set 2–100 points."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        if points is not None:
            self._check_integer(points, "smoothing points", 2, 100)
        with self._io_lock:
            self._write_setting(f":CALCulate:SMOothing {'ON' if enabled else 'OFF'}")
            if points is not None:
                self._write_setting(f":CALCulate:SMOothing:POINts {points}")

    def set_scaling(
        self,
        enabled: bool,
        *,
        a: float | str | None = None,
        b: float | str | None = None,
        c: float | str | None = None,
    ) -> None:
        """Configure polynomial scaling and optional constants A, B, and C."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        constants = tuple(
            (name, _format_calculation_constant(value))
            for name, value in (("A", a), ("B", b), ("C", c))
            if value is not None
        )
        with self._io_lock:
            self._write_setting(f":CALCulate:SCALing {'ON' if enabled else 'OFF'}")
            for name, formatted in constants:
                self._write_setting(f":CALCulate:SCALing:{name} {formatted}")

    def set_max_min(self, enabled: bool) -> None:
        """Enable or disable MAX/MIN/average accumulation."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self._write_setting(f":CALCulate:AVERage {'ON' if enabled else 'OFF'}")

    def max_min_enabled(self) -> bool:
        """Return whether MAX/MIN/average accumulation is enabled."""

        return self.query(":CALCulate:AVERage?").upper() in {"1", "ON"}

    def max_min_results(self) -> dict[str, float]:
        """Read MAX, MIN, average, and sample count calculation results."""

        return {
            "maximum": parse_measurements(self.query(":CALCulate:AVERage:MAXimum?"))[0],
            "minimum": parse_measurements(self.query(":CALCulate:AVERage:MINimum?"))[0],
            "average": parse_measurements(self.query(":CALCulate:AVERage:AVERage?"))[0],
            "count": float(self.query(":CALCulate:AVERage:COUNt?")),
        }

    def set_comparator(
        self,
        enabled: bool,
        *,
        upper: float | str | None = None,
        lower: float | str | None = None,
        pass_low: bool | None = None,
        pass_go: bool | None = None,
        pass_high: bool | None = None,
        beeper: str | None = None,
    ) -> None:
        """Configure limit comparison bounds, PASS rules, and buzzer behavior."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        bounds = tuple(
            (name, _format_calculation_constant(value))
            for name, value in (("UPPer", upper), ("LOWer", lower))
            if value is not None
        )
        pass_options = (
            ("LOWer", pass_low),
            ("MED", pass_go),
            ("UPPer", pass_high),
        )
        if any(
            value is not None and not isinstance(value, bool)
            for _, value in pass_options
        ):
            raise TypeError("PASS options must be bool values")
        selected_beeper = None
        if beeper is not None:
            selected_beeper = beeper.strip().upper()
            if selected_beeper not in {"OFF", "FAIL", "PASS"}:
                raise ValueError("beeper must be OFF, FAIL, or PASS")
        with self._io_lock:
            self._write_setting(f":CALCulate:LIMit {'ON' if enabled else 'OFF'}")
            for name, formatted in bounds:
                self._write_setting(f":CALCulate:LIMit:{name} {formatted}")
            for command, value in pass_options:
                if value is not None:
                    self._write_setting(
                        f":CALCulate:LIMit:PASS:{command} {'ON' if value else 'OFF'}"
                    )
            if selected_beeper is not None:
                self._write_setting(f":CALCulate:LIMit:BEEPer {selected_beeper}")

    def set_statistics_range(self, start: int, stop: int) -> None:
        """Select trace-memory indices for statistical calculations."""

        self._check_integer(start, "statistics start", 0, 9999)
        self._check_integer(stop, "statistics stop", -1, 9999)
        self._write_setting(f":CALCulate:STATistics {start},{stop}")

    def statistics_results(self) -> dict[str, float]:
        """Read count, extrema, mean, standard deviation, and peak-to-peak."""

        return {
            "count": float(self.query(":CALCulate:STATistics:COUNt?")),
            "maximum": parse_measurements(self.query(":CALCulate:STATistics:MAXimum?"))[
                0
            ],
            "minimum": parse_measurements(self.query(":CALCulate:STATistics:MINimum?"))[
                0
            ],
            "average": parse_measurements(self.query(":CALCulate:STATistics:AVERage?"))[
                0
            ],
            "standard_deviation": parse_measurements(
                self.query(":CALCulate:STATistics:DEViation?")
            )[0],
            "peak_to_peak": parse_measurements(
                self.query(":CALCulate:STATistics:MAXMin?")
            )[0],
        }

    def set_db_mode(self, mode: str) -> None:
        """Set dB/dBm calculation mode to DB, DBM, or OFF."""

        selected = mode.strip().upper()
        if selected not in {"DB", "DBM", "OFF"}:
            raise ValueError("dB mode must be DB, DBM, or OFF")
        self._write_setting(f":CALCulate:DB {selected}")

    def db_mode(self) -> str:
        """Read the dB/dBm calculation mode."""

        return self.query(":CALCulate:DB?").upper()

    def set_db_reference(self, value: float | str) -> None:
        """Set a positive dB reference or use the current measurement."""

        if isinstance(value, str):
            if value.strip().upper() not in {"MEAS", "MEASUREMENT"}:
                raise ValueError("dB reference must be positive or 'MEASurement'")
            formatted = "MEASurement"
        else:
            formatted = _format_bounded_number(
                value,
                "dB reference",
                minimum=_MIN_DB_REFERENCE,
                maximum=_MAX_CALCULATION_CONSTANT,
            )
        self._write_setting(f":CALCulate:DB:D {formatted}")

    def set_display(self, enabled: bool) -> None:
        """Enable or disable measurement data on the front-panel display."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self._write_setting(f":DISPlay {'ON' if enabled else 'OFF'}")

    def display_enabled(self) -> bool:
        """Return whether measurement data display is enabled."""

        return self.query(":DISPlay?").upper() in {"1", "ON"}

    def set_buzzer(self, enabled: bool) -> None:
        """Enable or disable the instrument's general buzzer."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self._write_setting(f":SYSTem:BEEPer:STATe {'ON' if enabled else 'OFF'}")

    def buzzer_enabled(self) -> bool:
        """Return whether the general buzzer is enabled."""

        return self.query(":SYSTem:BEEPer:STATe?").upper() in {"1", "ON"}

    def input_terminal(self) -> InputTerminal:
        """Read whether the front or rear input terminals are selected."""

        return InputTerminal.coerce(self.query(":ROUTe:TERMinals?"))

    def set_input_terminal(self, terminal: InputTerminal | str) -> None:
        """Select the front or rear measurement input terminals."""

        selected = InputTerminal.coerce(terminal)
        self._write_setting(f":ROUTe:TERMinals {selected.value}")

    def line_frequency(self) -> int:
        """Read the detected mains frequency (50 or 60 Hz)."""

        value = int(self.query(":SENSe:LFRQency?"))
        if value not in {50, 60}:
            raise ValueError(f"unexpected line frequency: {value}")
        return value

    def scpi_version(self) -> str:
        """Read the SCPI version implemented by the instrument."""

        return self.query(":SYSTem:VERSion?")

    def inhibited_function(self, function: MeasurementFunction | str) -> bool:
        """Return whether a measurement function is currently unavailable."""

        measurement = self._function(function)
        response = self.query(f":INHibit:FUNCtion:{measurement.value}?")
        if response not in {"0", "1"}:
            raise ValueError(f"unexpected inhibited-function response: {response!r}")
        return response == "1"

    def set_gpib_delimiter(self, delimiter: str) -> None:
        """Set the GPIB block delimiter (CRLF, LF, or EOI)."""

        selected = delimiter.strip().upper()
        if selected not in {"CRLF", "LF", "EOI"}:
            raise ValueError("GPIB delimiter must be CRLF, LF, or EOI")
        self._write_setting(f":SYSTem:GPIB:DELimiter:BLOCk {selected}")

    def gpib_delimiter(self) -> str:
        """Read the configured GPIB block delimiter."""

        return self.query(":SYSTem:GPIB:DELimiter:BLOCk?").upper()

    def set_legacy_identity(self, enabled: bool) -> None:
        """Select the legacy ``AD7461A`` model string for ``*IDN?`` (C00+)."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self._write_setting(f":OIDentity {'ON' if enabled else 'OFF'}")

    def legacy_identity(self) -> bool:
        """Return whether ``*IDN?`` uses the old AD7461A model name."""

        return self.query(":OIDentity?").upper() in {"1", "ON"}

    def set_trace_enabled(self, enabled: bool) -> None:
        """Enable or disable storage in measurement data memory."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self._write_setting(f":TRACe:STATe {'ON' if enabled else 'OFF'}")

    def trace_enabled(self) -> bool:
        """Return whether measurement data memory is enabled."""

        return self.query(":TRACe:STATe?").upper() in {"1", "ON"}

    def clear_trace(self) -> None:
        """Clear the measurement data memory."""

        self.write(":TRACe:CLEar")

    def set_trace_range(self, start: int, stop: int) -> None:
        """Select the inclusive memory index range to read (0 through 9999)."""

        self._check_integer(start, "trace start", 0, 9999)
        self._check_integer(stop, "trace stop", 0, 9999)
        self._write_setting(f":TRACe:NUMBer {start},{stop}")

    def trace_range(self) -> tuple[int, int]:
        """Read the selected trace-memory range."""

        fields = next(csv.reader([self.query(":TRACe:DATA:NUMBer?")]))
        if len(fields) != 2:
            raise ValueError(f"invalid trace range response: {fields!r}")
        return int(fields[0]), int(fields[1])

    def trace_points(self) -> int:
        """Read the number of stored measurement results."""

        return int(self.query(":TRACe:DATA:POINts?"))

    def trace_data(self) -> tuple[float, ...]:
        """Read the selected measurement data memory range."""

        return parse_measurements(self.query(":TRACe:DATA?"))

    def set_completion_output(
        self, mode: str, *, pulse_width: float | None = None
    ) -> None:
        """Configure COMPLETE OUT pulse mode and optional width."""

        normalized_mode = mode.strip().upper()
        if normalized_mode not in {"SINGLE", "SING", "MULTI", "MULT"}:
            raise ValueError("completion mode must be SINGLE or MULTI")
        scpi_mode = "SINGle" if normalized_mode in {"SINGLE", "SING"} else "MULTi"
        width_value = None
        if pulse_width is not None:
            width_value = _format_number(pulse_width, "pulse width", minimum=0.000005)
            if float(width_value) > 0.0001:
                raise ValueError("pulse width must be between 5 and 100 microseconds")
        with self._io_lock:
            self._write_setting(f":TRIGger:COMPlete {scpi_mode}")
            if width_value is not None:
                self._write_setting(f":TRIGger:COMPlete:WIDTh {width_value}")

    def set_event_enable(self, register: EventRegister, mask: int) -> None:
        """Set the enable mask for an operation, questionable, or measurement event register."""

        if not isinstance(register, EventRegister):
            raise TypeError("register must be an EventRegister")
        self._check_integer(mask, "event enable mask", 0, 65535)
        self._write_setting(f":STATus:{register.value}:ENABle {mask}")

    def event_enable(self, register: EventRegister) -> int:
        """Read an operation, questionable, or measurement event enable mask."""

        if not isinstance(register, EventRegister):
            raise TypeError("register must be an EventRegister")
        return int(self.query(f":STATus:{register.value}:ENABle?"))

    def read_event_register(self, register: EventRegister) -> int:
        """Read and clear an operation, questionable, or measurement event register."""

        if not isinstance(register, EventRegister):
            raise TypeError("register must be an EventRegister")
        return int(self.query(f":STATus:{register.value}:EVENt?"))

    def set_standard_event_enable(self, mask: int) -> None:
        """Set the standard event status enable mask (0 through 127)."""

        self._check_integer(mask, "standard event enable mask", 0, 127)
        self._write_setting(f"*ESE {mask}")

    def standard_event_enable(self) -> int:
        """Read the standard event status enable mask."""

        return int(self.query("*ESE?"))

    def read_standard_event_status(self) -> int:
        """Read and clear the standard event status register."""

        return int(self.query("*ESR?"))

    def set_service_request_enable(self, mask: int) -> None:
        """Set the GPIB service-request mask; bit 6 is always enabled."""

        self._check_integer(mask, "service request enable mask", 64, 127)
        if not mask & 0x40:
            raise ValueError("service request enable mask must include bit 6")
        self._write_setting(f"*SRE {mask}")

    def service_request_enable(self) -> int:
        """Read the service-request enable mask."""

        return int(self.query("*SRE?"))

    def read_status_byte(self) -> int:
        """Read the IEEE-488.2 status byte register using ``*STB?``."""

        return int(self.query("*STB?"))

    @staticmethod
    def _check_integer(value: int, name: str, minimum: int, maximum: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer")
        if not minimum <= value <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")

    def close(self) -> None:
        """Close the VISA resource and any resource manager owned by this plug."""

        with self._io_lock:
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
        """Close owned VISA resources at OpenHTF plug teardown."""

        self.close()


AD7461APlug = ADCMTAD7461APlug

__all__ = ["AD7461APlug", "ADCMTAD7461APlug", "parse_measurements"]
