"""OpenHTF plug for the Heraeus Voetsch VRSU interface.

The VRSU is the computer interface used by Heraeus Voetsch thermal chambers.
It provides three analog setpoint outputs and three analog actual-value inputs
(0 ... 3000 mV, where 10 mV = 1 degC or 1 %RH), eight relay outputs, eight
digital inputs, and a calendar clock.

The same command set is available over IEEE-488 (GPIB) and RS-232.  This plug
uses PyVISA for both, so a GPIB resource such as ``GPIB0::8::INSTR`` and a
serial resource such as ``ASRL/dev/ttyUSB0::INSTR`` are both supported.

Protocol notes
--------------
* Commands are ASCII strings terminated with CR+LF.
* On RS-232, auto-echo is enabled by default (``H1``), so every command is
  echoed before any reply.  The plug consumes and verifies that echo.
* On RS-232, a talk string is only produced after sending ``ST``.  On GPIB the
  talk string is read directly.
* The default delimiter is ``D0`` (CR+LF).  ``D1`` uses CR only and ``D2``
  uses EOI, which is only meaningful on GPIB.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import time
from collections.abc import Mapping, Sequence
from typing import Any

import pyvisa
from openhtf.plugs import BasePlug
from pyvisa import constants as visa_constants
from pyvisa import errors as visa_errors

from .exceptions import (
    VrsuCommunicationError,
    VrsuDeviceError,
    VrsuTimeoutError,
    VrsuUnsupportedOperationError,
)
from .models import (
    VrsuChannel,
    VrsuClock,
    VrsuErrorCode,
    VrsuLoop,
    VrsuPowerFailure,
    VrsuProgramLine,
    VrsuProgramStatus,
    VrsuSetpoints,
    VrsuStatus,
    parse_loop_table,
    parse_power_failure,
    parse_program_lines,
    parse_program_status,
    parse_setpoints,
    parse_status,
)

_PARITY_MAP = {
    "none": visa_constants.Parity.none,
    "even": visa_constants.Parity.even,
    "odd": visa_constants.Parity.odd,
}

_STOPBITS_MAP = {
    1: visa_constants.StopBits.one,
    1.5: visa_constants.StopBits.one_and_a_half,
    2: visa_constants.StopBits.two,
}

_READ_TERMINATIONS = {0: "\n", 1: "\r", 2: None}


def _coerce_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{name} must be an integer")
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TypeError(f"{name} must be an integer") from exc


def _coerce_float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TypeError(f"{name} must be a number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _relay_bits(states: int | Sequence[bool | int | str] | str) -> str:
    """Return the 8-character relay field used by the ``R`` command.

    An integer is interpreted as a bit mask (bit 0 = relay 1).  A sequence is
    interpreted in relay 1 ... relay 8 order.  A string is used verbatim and
    must already be in the instrument's left-to-right (relay 8 ... relay 1)
    order.
    """

    if isinstance(states, str):
        if len(states) != 8 or any(character not in "01" for character in states):
            raise ValueError("relay string must be 8 characters of 0/1")
        return states
    if isinstance(states, bool):
        raise TypeError("relays must be an int, an 8-character string, or a sequence")
    if isinstance(states, int):
        if not 0 <= states <= 0xFF:
            raise ValueError("relay state must be 0..255")
        return f"{states:08b}"
    try:
        values = list(states)
    except TypeError as exc:
        raise TypeError(
            "relays must be an int, an 8-character string, or a sequence"
        ) from exc
    if len(values) != 8:
        raise ValueError("relay sequence must contain 8 values")

    def bit(value: Any) -> str:
        if isinstance(value, str):
            if value not in {"0", "1"}:
                raise ValueError("relay sequence strings must be '0' or '1'")
            return value
        return "1" if bool(value) else "0"

    return "".join(bit(value) for value in reversed(values))


def _resolve_interface(
    interface: str | None, resource_name: str | None, resource: Any
) -> str:
    if interface is not None:
        normalized = str(interface).strip().lower()
        if normalized not in {"serial", "gpib"}:
            raise ValueError("interface must be 'serial' or 'gpib'")
        return normalized
    name = (resource_name or "").upper()
    if name.startswith("ASRL"):
        return "serial"
    if name.startswith("GPIB"):
        return "gpib"
    if hasattr(resource, "baud_rate"):
        return "serial"
    return "gpib"


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, visa_errors.VisaIOError):
        return exc.error_code == visa_constants.StatusCode.error_timeout
    return False


class HeraeusVoetschVRSUPlug(BasePlug):
    """Control a Heraeus Voetsch VRSU over IEEE-488 or RS-232.

    ``resource_name`` is a PyVISA resource string.  For RS-232 you can either
    pass a full ``ASRL...::INSTR`` resource or just ``serial_port``; the plug
    builds the ASRL resource for you.  A resource object can be injected for
    testing.

    The plug never changes chamber state during teardown.  A thermal chamber
    may be left at a dangerous temperature, so shutting it down is left to the
    test author.
    """

    def __init__(
        self,
        resource_name: str | None = None,
        *,
        serial_port: str | None = None,
        interface: str | None = None,
        visa_library: str = "",
        resource_manager: Any | None = None,
        resource: Any | None = None,
        resource_kwargs: Mapping[str, Any] | None = None,
        timeout_ms: int = 5000,
        echo_timeout_ms: int = 300,
        baud_rate: int = 9600,
        bytesize: int = 8,
        parity: str = "none",
        stopbits: float = 1,
        delimiter: int = 0,
        auto_echo: bool | None = None,
    ) -> None:
        super().__init__()

        timeout_ms = _coerce_int(timeout_ms, "timeout_ms")
        echo_timeout_ms = _coerce_int(echo_timeout_ms, "echo_timeout_ms")
        baud_rate = _coerce_int(baud_rate, "baud_rate")
        bytesize = _coerce_int(bytesize, "bytesize")
        delimiter = _coerce_int(delimiter, "delimiter")
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        if echo_timeout_ms <= 0:
            raise ValueError("echo_timeout_ms must be positive")
        if baud_rate <= 0:
            raise ValueError("baud_rate must be positive")
        if bytesize not in (7, 8):
            raise ValueError("bytesize must be 7 or 8")
        parity = str(parity).strip().lower()
        if parity not in _PARITY_MAP:
            raise ValueError("parity must be 'none', 'even', or 'odd'")
        stopbits = float(stopbits)
        if stopbits not in _STOPBITS_MAP:
            raise ValueError("stopbits must be 1, 1.5, or 2")
        if delimiter not in _READ_TERMINATIONS:
            raise ValueError("delimiter must be 0, 1, or 2")

        if resource is not None:
            if resource_name is not None or serial_port is not None:
                raise ValueError(
                    "pass either resource or resource_name/serial_port, not both"
                )
        elif serial_port is not None:
            if resource_name is not None:
                raise ValueError("pass either resource_name or serial_port, not both")
            resource_name = f"ASRL{serial_port}::INSTR"
        else:
            resource_name = (
                resource_name or os.environ.get("HERAEUS_VOETSCH_VRSU_RESOURCE") or None
            )
            if resource_name is None:
                raise ValueError(
                    "resource_name or serial_port is required "
                    "(or set HERAEUS_VOETSCH_VRSU_RESOURCE)"
                )

        self._resource_manager: Any = resource_manager
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
        self._interface = _resolve_interface(
            interface, self.resource_name, self._resource
        )
        self._serial = self._interface == "serial"
        self._baud_rate = baud_rate
        self._echo_timeout_ms = echo_timeout_ms
        self._delimiter = delimiter
        self._auto_echo = (
            bool(auto_echo) if auto_echo is not None else self._serial
        ) and self._serial
        self._test_mode = False
        self._pending_lines: list[str] = []

        self._configure_resource(
            timeout_ms=timeout_ms,
            baud_rate=baud_rate,
            bytesize=bytesize,
            parity=parity,
            stopbits=stopbits,
        )

    # ------------------------------------------------------------------
    # Connection and transport
    # ------------------------------------------------------------------

    @property
    def resource(self) -> Any:
        """The underlying PyVISA resource, exposed for advanced use."""

        return self._resource

    @property
    def interface(self) -> str:
        """``"serial"`` or ``"gpib"``."""

        return self._interface

    @property
    def serial(self) -> bool:
        """Whether the active interface is RS-232."""

        return self._serial

    @property
    def auto_echo(self) -> bool:
        """Whether the instrument is expected to echo RS-232 commands.

        The transport tolerates either state, because it always attempts to
        consume an echo with a short timeout.  This property tracks the state
        last set with :meth:`set_auto_echo` (or the constructor).
        """

        return self._auto_echo

    @property
    def delimiter(self) -> int:
        """The active talk-string delimiter (0, 1, or 2)."""

        return self._delimiter

    @property
    def test_mode(self) -> bool:
        """Whether the plug believes the VRSU is in test mode (up to 4095 mV)."""

        return self._test_mode

    def _configure_resource(
        self,
        *,
        timeout_ms: int,
        baud_rate: int,
        bytesize: int,
        parity: str,
        stopbits: float,
    ) -> None:
        read_termination = _READ_TERMINATIONS[self._delimiter]
        write_termination = "\r\n" if self._serial else None
        for attribute, value in (
            ("timeout", timeout_ms),
            ("read_termination", read_termination),
            ("write_termination", write_termination),
        ):
            try:
                setattr(self._resource, attribute, value)
            except (AttributeError, TypeError, ValueError):
                pass
        if not self._serial:
            return
        for attribute, value in (
            ("baud_rate", baud_rate),
            ("data_bits", bytesize),
            ("parity", _PARITY_MAP[parity]),
            ("stop_bits", _STOPBITS_MAP[stopbits]),
            ("flow_control", visa_constants.ControlFlow.none),
            ("xonxoff", False),
            ("rtscts", False),
            ("dsrdtr", False),
        ):
            try:
                setattr(self._resource, attribute, value)
            except (AttributeError, TypeError, ValueError):
                pass

    def _echo_timeout_for(self, command: str) -> int:
        if self._baud_rate <= 0:
            return self._echo_timeout_ms
        # 10 bits per character (start + 8 data + stop), doubled for the echo.
        transmission_ms = len(command) * 10 * 2 * 1000 / self._baud_rate
        return max(self._echo_timeout_ms, int(transmission_ms) + 200)

    def _read_line(self, timeout_ms: int | None = None) -> str:
        if self._pending_lines:
            return self._pending_lines.pop(0)

        old_timeout = getattr(self._resource, "timeout", None)
        if timeout_ms is not None:
            try:
                self._resource.timeout = timeout_ms
            except (AttributeError, TypeError, ValueError):
                pass
        try:
            text = str(self._resource.read())
        except (visa_errors.VisaIOError, TimeoutError, OSError) as exc:
            if _is_timeout(exc):
                raise VrsuTimeoutError(
                    f"no reply from the VRSU within "
                    f"{timeout_ms if timeout_ms is not None else old_timeout} ms"
                ) from exc
            raise VrsuCommunicationError(f"VRSU read failed: {exc}") from exc
        finally:
            if timeout_ms is not None and old_timeout is not None:
                try:
                    self._resource.timeout = old_timeout
                except (AttributeError, TypeError, ValueError):
                    pass

        lines = [
            line.strip()
            for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        ]
        lines = [line for line in lines if line]
        if not lines:
            return ""
        self._pending_lines.extend(lines[1:])
        return lines[0]

    def _write(self, command: str) -> None:
        self._resource.write(command)
        if not self._serial:
            return
        # Auto-echo is on by default, but it can be disabled with H0.  Always
        # attempt to consume an echo with a short timeout so the plug works in
        # either state.  If the line that arrives is not the echo, it is a
        # reply that arrived early; keep it for the next read.
        try:
            echo = self._read_line(timeout_ms=self._echo_timeout_for(command))
        except VrsuTimeoutError:
            return
        if echo.strip() != command.strip():
            self._pending_lines.insert(0, echo)

    def _talk(self, command: str | None = None) -> str:
        if command is not None:
            self._write(command)
        if self._serial:
            self._write("ST")
        return self._read_line()

    def write(self, command: str) -> None:
        """Send a raw VRSU command, consuming the RS-232 auto-echo if present."""

        self._write(command)

    def read(self) -> str:
        """Read the next talk string from the instrument."""

        return self._read_line()

    # ------------------------------------------------------------------
    # Talk calls
    # ------------------------------------------------------------------

    def status(self) -> VrsuStatus:
        """Read actual values, digital inputs, clock, and error number."""

        return parse_status(self._talk())

    def check_error(self) -> VrsuStatus:
        """Read the status and raise :class:`VrsuDeviceError` if ``ER.x`` != 0."""

        status = self.status()
        if status.error is not VrsuErrorCode.NO_ERROR:
            raise VrsuDeviceError(status.error, status.error.message)
        return status

    def setpoints(self) -> VrsuSetpoints:
        """Read the current setpoints, relay states, and clock (``LL``)."""

        return parse_setpoints(self._talk("LL"))

    def power_failure(self) -> VrsuPowerFailure:
        """Read the values stored at the last power failure (``PF``)."""

        return parse_power_failure(self._talk("PF"))

    def program_lines(
        self, start: int = 1, end: int = 99
    ) -> tuple[VrsuProgramLine, ...]:
        """Read program lines *start* through *end* (``LP``)."""

        start = _coerce_int(start, "start")
        end = _coerce_int(end, "end")
        if not 1 <= start <= 99 or not 1 <= end <= 99:
            raise ValueError("program line numbers must be 1..99")
        if start > end:
            raise ValueError("start must be <= end")
        return parse_program_lines(self._talk(f"LP {start:02d} {end:02d}"))

    def loop_table(self) -> tuple[VrsuLoop, ...]:
        """Read the loop table (``LC``)."""

        return parse_loop_table(self._talk("LC"))

    def program_status(self) -> VrsuProgramStatus:
        """Read the current program execution status (``LS``)."""

        return parse_program_status(self._talk("LS"))

    # ------------------------------------------------------------------
    # Direct mode: setpoints and actual values
    # ------------------------------------------------------------------

    def set_voltage(self, channel: VrsuChannel | int, millivolts: int) -> None:
        """Set an analog output channel in millivolts.

        The direct-mode limit is 3000 mV.  In test mode (``T1``) the VRSU
        accepts up to 4095 mV.
        """

        channel = VrsuChannel.coerce(channel)
        millivolts = _coerce_int(millivolts, "millivolts")
        maximum = 4095 if self._test_mode else 3000
        if not 0 <= millivolts <= maximum:
            raise ValueError(f"millivolts must be 0..{maximum}, got {millivolts}")
        self._write(f"U{int(channel)}{millivolts:04d}")

    def voltage_setpoint(self, channel: VrsuChannel | int) -> int:
        """Read a channel's setpoint in millivolts (``LL``)."""

        return self.setpoints().channel_mv(channel)

    def set_temperature(
        self,
        celsius: float,
        channel: VrsuChannel | int = VrsuChannel.CHANNEL_1,
    ) -> None:
        """Set a channel from a temperature in degrees Celsius (10 mV/degC)."""

        celsius = _coerce_float(celsius, "celsius")
        self.set_voltage(channel, round(celsius * 10))

    def set_humidity(
        self,
        percent: float,
        channel: VrsuChannel | int = VrsuChannel.CHANNEL_2,
    ) -> None:
        """Set a channel from a relative humidity in percent (10 mV/%RH)."""

        percent = _coerce_float(percent, "percent")
        if not 0 <= percent <= 100:
            raise ValueError("percent must be 0..100")
        self.set_voltage(channel, round(percent * 10))

    def read_channel(self, channel: VrsuChannel | int = VrsuChannel.CHANNEL_1) -> float:
        """Read one actual-value channel in engineering units (mV / 10)."""

        return self.status().channel_value(channel)

    def read_temperature(
        self, channel: VrsuChannel | int = VrsuChannel.CHANNEL_1
    ) -> float:
        """Read a channel as a temperature in degrees Celsius."""

        return self.read_channel(channel)

    def read_humidity(
        self, channel: VrsuChannel | int = VrsuChannel.CHANNEL_2
    ) -> float:
        """Read a channel as a relative humidity in percent."""

        return self.read_channel(channel)

    def read_all(self) -> VrsuStatus:
        """Read the complete status in one talk call."""

        return self.status()

    def wait_for_temperature(
        self,
        target: float,
        *,
        tolerance: float = 0.5,
        timeout: float = 3600.0,
        poll_interval: float = 1.0,
        channel: VrsuChannel | int = VrsuChannel.CHANNEL_1,
    ) -> float:
        """Poll until a channel is within *tolerance* of *target*.

        Returns the final reading.  Raises :class:`VrsuTimeoutError` if the
        target is not reached within *timeout* seconds.
        """

        target = _coerce_float(target, "target")
        tolerance = _coerce_float(tolerance, "tolerance")
        timeout = _coerce_float(timeout, "timeout")
        poll_interval = _coerce_float(poll_interval, "poll_interval")
        if tolerance < 0:
            raise ValueError("tolerance must be non-negative")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")

        deadline = time.monotonic() + timeout
        while True:
            value = self.read_channel(channel)
            if abs(value - target) <= tolerance:
                return value
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VrsuTimeoutError(
                    f"channel {int(VrsuChannel.coerce(channel))} did not reach "
                    f"{target} +/- {tolerance} within {timeout}s "
                    f"(last reading {value})"
                )
            time.sleep(min(poll_interval, remaining))

    # ------------------------------------------------------------------
    # Relays
    # ------------------------------------------------------------------

    def set_relays(self, states: int | Sequence[bool | int | str] | str) -> None:
        """Set all eight relay outputs (``RXXXXXXXX``).

        An integer is a bit mask (bit 0 = relay 1).  A sequence is in relay
        1 ... relay 8 order.  A string is used verbatim in the instrument's
        left-to-right (relay 8 ... relay 1) order.
        """

        self._write(f"R{_relay_bits(states)}")

    def relays(self) -> int:
        """Read the relay bit mask (bit 0 = relay 1)."""

        return self.setpoints().relays

    def relay_states(self) -> tuple[bool, ...]:
        """Read relay states in relay 1 ... relay 8 order."""

        return self.setpoints().relay_states()

    def relay(self, relay: int) -> bool:
        """Read one relay state (1 ... 8)."""

        return self.setpoints().relay(relay)

    def set_relay(self, relay: int, enabled: bool) -> None:
        """Set one relay while preserving the others."""

        relay = _coerce_int(relay, "relay")
        if not 1 <= relay <= 8:
            raise ValueError("relay must be 1..8")
        current = self.setpoints().relays
        mask = 1 << (relay - 1)
        new_state = (current | mask) if enabled else (current & ~mask)
        self.set_relays(new_state)

    # ------------------------------------------------------------------
    # Clock and configuration commands
    # ------------------------------------------------------------------

    def set_clock(self, when: dt.datetime | dt.date | dt.time | None = None) -> None:
        """Set the calendar clock (``KhhmmssTTMMJJ``).

        ``None`` uses the host's current local date and time.  A ``date`` is
        combined with midnight; a ``time`` is combined with today's date.
        """

        if when is None:
            when = dt.datetime.now()
        if isinstance(when, dt.datetime):
            date_part, time_part = when.date(), when.time()
        elif isinstance(when, dt.date):
            date_part, time_part = when, dt.time(0, 0, 0)
        elif isinstance(when, dt.time):
            date_part, time_part = dt.date.today(), when
        else:
            raise TypeError("when must be a datetime, date, or time")
        self._write(
            f"K{time_part.hour:02d}{time_part.minute:02d}{time_part.second:02d}"
            f"{date_part.day:02d}{date_part.month:02d}{date_part.year % 100:02d}"
        )

    def clock(self) -> VrsuClock:
        """Read the calendar clock."""

        return self.status().clock

    def set_averaging(self, enabled: bool) -> None:
        """Enable (``AV``) or disable (``NAV``) averaging of actual values."""

        self._write("AV" if enabled else "NAV")

    def set_test_mode(self, enabled: bool) -> None:
        """Enable (``T1``) or disable (``T0``) test mode.

        Test mode raises the setpoint limit from 3000 mV to 4095 mV.
        """

        self._write(f"T{1 if enabled else 0}")
        self._test_mode = bool(enabled)

    def set_delta(self, millivolts: int) -> None:
        """Set the setpoint-deviation window used by ``W`` program lines."""

        millivolts = _coerce_int(millivolts, "millivolts")
        if not 0 <= millivolts <= 4094:
            raise ValueError("delta must be 0..4094 mV")
        self._write(f"U4{millivolts:04d}")

    def set_power_failure_window(self, minutes: int) -> None:
        """Set the power-failure restart window (``NZMMMM``)."""

        minutes = _coerce_int(minutes, "minutes")
        if not 1 <= minutes <= 1439:
            raise ValueError("minutes must be 1..1439")
        self._write(f"NZ{minutes:04d}")

    def set_accelerated_test(self, enabled: bool) -> None:
        """Run program times at 1/8 real time (``W1``) or real time (``W0``)."""

        self._write(f"W{1 if enabled else 0}")

    def set_auto_echo(self, enabled: bool) -> None:
        """Enable (``H1``) or disable (``H0``) the RS-232 auto-echo."""

        if not self._serial:
            raise VrsuUnsupportedOperationError(
                "auto-echo is only available on the RS-232 interface"
            )
        self._write(f"H{1 if enabled else 0}")
        self._auto_echo = bool(enabled)

    def set_delimiter(self, delimiter: int) -> None:
        """Select the talk-string delimiter: 0 = CR+LF, 1 = CR, 2 = EOI."""

        delimiter = _coerce_int(delimiter, "delimiter")
        if delimiter not in _READ_TERMINATIONS:
            raise ValueError("delimiter must be 0, 1, or 2")
        self._write(f"D{delimiter}")
        self._delimiter = delimiter
        try:
            self._resource.read_termination = _READ_TERMINATIONS[delimiter]
        except (AttributeError, TypeError, ValueError):
            pass

    # ------------------------------------------------------------------
    # Program set-station mode
    # ------------------------------------------------------------------

    def initialize_program(self) -> None:
        """Delete the program and loop tables (``CI``)."""

        self._write("CI")

    def write_program_line(
        self,
        number: int,
        time_minutes: int,
        channel_1_mv: int,
        channel_2_mv: int,
        channel_3_mv: int,
        relays: int | Sequence[bool | int | str] | str = 0,
        *,
        wait_for_actual: bool = False,
    ) -> None:
        """Write one program line (``CP``).

        *time_minutes* is the time since program start.  When
        *wait_for_actual* is true, the VRSU waits for channel 1 to reach the
        setpoint (within the ``U4`` delta) before continuing.
        """

        number = _coerce_int(number, "number")
        if not 1 <= number <= 99:
            raise ValueError("program line number must be 1..99")
        time_minutes = _coerce_int(time_minutes, "time_minutes")
        if not 0 <= time_minutes <= 9999:
            raise ValueError("time_minutes must be 0..9999")
        setpoints = []
        for name, value in (
            ("channel_1_mv", channel_1_mv),
            ("channel_2_mv", channel_2_mv),
            ("channel_3_mv", channel_3_mv),
        ):
            value = _coerce_int(value, name)
            if not 0 <= value <= 3000:
                raise ValueError(f"{name} must be 0..3000")
            setpoints.append(value)
        wait_field = "W" if wait_for_actual else " "
        self._write(
            f"CP {number:02d} {wait_field} {time_minutes:04d} "
            f"{setpoints[0]:04d} {setpoints[1]:04d} {setpoints[2]:04d} "
            f"{_relay_bits(relays)}"
        )

    def set_loop(self, loop: int, start_line: int, end_line: int, count: int) -> None:
        """Define a program loop (``CC``).

        The manual's command table labels the first line number as the start
        and the second as the end, but its example and the ``LC`` reply show
        that the instrument actually expects the end line first.  This method
        takes the intuitive ``start_line``/``end_line`` order and emits the
        instrument's order.
        """

        loop = _coerce_int(loop, "loop")
        if not 1 <= loop <= 3:
            raise ValueError("loop must be 1..3")
        start_line = _coerce_int(start_line, "start_line")
        end_line = _coerce_int(end_line, "end_line")
        if not 1 <= start_line <= 99 or not 1 <= end_line <= 99:
            raise ValueError("program line numbers must be 1..99")
        if start_line > end_line:
            raise ValueError("start_line must be <= end_line")
        count = _coerce_int(count, "count")
        if not 1 <= count <= 99:
            raise ValueError("count must be 1..99")
        self._write(f"CC {loop} {end_line:02d} {start_line:02d} {count:02d}")

    def start_program(self, delay_minutes: int = 0) -> None:
        """Start the program after *delay_minutes* (``CS``)."""

        delay_minutes = _coerce_int(delay_minutes, "delay_minutes")
        if not 0 <= delay_minutes <= 9999:
            raise ValueError("delay_minutes must be 0..9999")
        self._write(f"CS {delay_minutes:04d}")

    def stop_program(self) -> None:
        """Abort the running program immediately (``B``)."""

        self._write("B")

    def pause_program(self) -> None:
        """Pause the running program (``E``)."""

        self._write("E")

    def continue_program(self) -> None:
        """Continue a paused program (``I``)."""

        self._write("I")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the VISA resource and any resource manager owned by this plug."""

        resource, self._resource = self._resource, None
        manager, self._resource_manager = self._resource_manager, None
        try:
            if resource is not None:
                resource.close()
        finally:
            if self._owns_resource_manager and manager is not None:
                manager.close()

    def tearDown(self) -> None:
        """Release the connection without changing chamber state."""

        self.close()
