# Heraeus Voetsch VRSU OpenHTF plug

This package controls the Heraeus Voetsch VRSU interface used by thermal
chambers and other environmental test systems. The VRSU provides three analog
setpoint outputs and three analog actual-value inputs (0 ... 3000 mV, where
10 mV = 1 degC or 1 %RH), eight relay outputs, eight digital inputs, and a
calendar clock.

The same command set is available over IEEE-488 (GPIB) and RS-232. This plug
uses PyVISA for both interfaces, so a GPIB resource such as
`GPIB0::8::INSTR` and a serial resource such as
`ASRL/dev/ttyUSB0::INSTR` are both supported.

## Install

```sh
uv add openhtf-plug-heraeus-voetsch-vrsu
```

A VISA backend is also required. Install NI-VISA, or install `pyvisa-py` for
supported serial and GPIB connections.

## OpenHTF example

```python
import os

from openhtf import Test, plugs
from heraeus_voetsch_vrsu import HeraeusVoetschVRSUPlug

# IEEE-488 example. Set the device address with the VRSU DIL switches.
os.environ["HERAEUS_VOETSCH_VRSU_RESOURCE"] = "GPIB0::8::INSTR"

# RS-232 example:
# os.environ["HERAEUS_VOETSCH_VRSU_RESOURCE"] = "ASRL/dev/ttyUSB0::INSTR"


@plugs.plug(chamber=HeraeusVoetschVRSUPlug)
def soak(test_api, chamber):
    chamber.set_temperature(85.0)          # channel 1, 10 mV/degC
    chamber.set_humidity(85.0)             # channel 2, 10 mV/%RH
    chamber.set_relay(1, True)             # chamber ON
    chamber.wait_for_temperature(85.0, tolerance=0.5, timeout=1800)
    status = chamber.read_all()
    test_api.attachments.attach("vrsu_status", repr(status).encode())


Test(soak).execute()
```

The plug does not change chamber state during teardown. A thermal chamber may
be left at a dangerous temperature, so shutting it down is left to the test
author.

## Interfaces

### IEEE-488 (GPIB)

Pass a GPIB resource name. Talk strings are read directly; the plug does not
send `ST`, because that command is only used on RS-232.

```python
plug = HeraeusVoetschVRSUPlug("GPIB0::8::INSTR")
```

The VRSU device address is set with DIL switches 4 ... 8 (see the service
manual, Figure 3.3). Switch 1 must be ON for IEEE-488.

### RS-232

Pass either a full VISA ASRL resource or just the serial port. The plug builds
the ASRL resource and configures the baud rate, character format, parity, and
stop bits.

```python
plug = HeraeusVoetschVRSUPlug(
    serial_port="/dev/ttyUSB0",
    baud_rate=9600,
    bytesize=8,
    parity="none",
    stopbits=1,
)
```

The VRSU's DIL switches must match these settings, and switch 1 must be OFF
for RS-232. The switches are only read at power-on, so power-cycle the unit
after changing them.

The repository's `vrsu_check.py` script is a standalone, read-only RS-232
connectivity checker. It can scan the documented baud/format combinations and
print the parsed status string, which is useful when bringing up a new unit.

Auto-echo is enabled on the VRSU by default (`H1`). The plug consumes and
verifies the echo automatically. If auto-echo has been disabled on the
instrument, construct the plug with `auto_echo=False` (or call
`set_auto_echo(False)`).

## API overview

### Actual values and status

| Method                              | Description                                                                      |
| ----------------------------------- | -------------------------------------------------------------------------------- |
| `status()`                          | `VrsuStatus` with all three actual values, digital inputs, clock, and error code |
| `check_error()`                     | Like `status()`, but raises `VrsuDeviceError` if `ER.x` is not zero              |
| `read_channel(channel)`             | One actual value in engineering units (mV / 10)                                  |
| `read_temperature(channel=1)`       | Channel 1 (or another channel) in degC                                           |
| `read_humidity(channel=2)`          | Channel 2 (or another channel) in %RH                                            |
| `read_all()`                        | Alias for `status()`                                                             |
| `wait_for_temperature(target, ...)` | Poll until a channel is within tolerance of a target                             |

### Setpoints

| Method                                | Description                                                         |
| ------------------------------------- | ------------------------------------------------------------------- |
| `set_voltage(channel, millivolts)`    | Set an analog output in mV (0 ... 3000, or 0 ... 4095 in test mode) |
| `set_temperature(celsius, channel=1)` | Set a channel from degC (10 mV/degC)                                |
| `set_humidity(percent, channel=2)`    | Set a channel from %RH (10 mV/%RH)                                  |
| `voltage_setpoint(channel)`           | Read a setpoint in mV (`LL`)                                        |
| `setpoints()`                         | Read all setpoints, relay states, and the clock (`LL`)              |

### Relays

| Method                      | Description                                                                                       |
| --------------------------- | ------------------------------------------------------------------------------------------------- |
| `set_relays(states)`        | Set all eight relays; accepts an int bit mask, a relay 1 ... 8 sequence, or an 8-character string |
| `set_relay(relay, enabled)` | Set one relay while preserving the others                                                         |
| `relays()`                  | Read the relay bit mask                                                                           |
| `relay_states()`            | Read relay states in relay 1 ... 8 order                                                          |
| `relay(relay)`              | Read one relay                                                                                    |

### Clock and configuration

| Method                              | Description                                                          |
| ----------------------------------- | -------------------------------------------------------------------- |
| `set_clock(when=None)`              | Set the calendar clock (`KhhmmssTTMMJJ`); `None` uses the host clock |
| `clock()`                           | Read the calendar clock                                              |
| `set_averaging(enabled)`            | `AV` / `NAV`                                                         |
| `set_test_mode(enabled)`            | `T1` / `T0`; raises the setpoint limit to 4095 mV                    |
| `set_delta(millivolts)`             | `U4XXXX` setpoint-deviation window                                   |
| `set_power_failure_window(minutes)` | `NZMMMM` restart window                                              |
| `set_accelerated_test(enabled)`     | `W1` / `W0` (1/8 real time)                                          |
| `set_auto_echo(enabled)`            | `H1` / `H0` (RS-232 only)                                            |
| `set_delimiter(delimiter)`          | `D0` (CR+LF), `D1` (CR), or `D2` (EOI)                               |

### Program set-station mode

| Method                                        | Description                                            |
| --------------------------------------------- | ------------------------------------------------------ |
| `initialize_program()`                        | `CI`; delete the program and loop tables               |
| `write_program_line(...)`                     | `CP`; write one program line                           |
| `set_loop(loop, start_line, end_line, count)` | `CC`; define a loop                                    |
| `start_program(delay_minutes=0)`              | `CS`; start the program                                |
| `stop_program()`                              | `B`; abort immediately                                 |
| `pause_program()`                             | `E`; pause                                             |
| `continue_program()`                          | `I`; continue after a pause                            |
| `program_lines(start, end)`                   | `LP`; read program lines                               |
| `loop_table()`                                | `LC`; read the loop table                              |
| `program_status()`                            | `LS`; read program execution status                    |
| `power_failure()`                             | `PF`; read the values stored at the last power failure |

## Protocol notes

- **Scaling.** The VRSU's analog channels are 0 ... 3000 mV. The manual defines
  10 mV = 1 degC and 10 mV = 1 %RH. `set_temperature()` and
  `set_humidity()` apply that scaling; `set_voltage()` works in raw mV.
- **Serial talk calls.** On RS-232, the VRSU only produces a talk string after
  `ST`. The plug sends `ST` automatically for every talk call. On GPIB it reads
  the talk string directly.
- **Auto-echo.** On RS-232, every command is echoed before the reply. The plug
  consumes the echo with a short timeout, so it also works when auto-echo has
  been disabled with `H0`.
- **Delimiter.** The default is `D0` (CR+LF). `D1` uses CR only. `D2` uses EOI
  and is only meaningful on GPIB.
- **Two-digit year.** The VRSU clock stores only a two-digit year.
  `VrsuClock.date()` maps it into 2000 ... 2099 by default; pass a different
  `year_base` if your installation uses another century.
- **Loop command order.** The manual's `CC` table labels the first line number
  as the start and the second as the end, but its example and the `LC` reply
  show that the instrument expects the end line first. `set_loop()` takes the
  intuitive `start_line`/`end_line` order and emits the instrument's order.
- **Digital input bit order.** In the talk string, the leftmost bit is 2^7 and
  the rightmost bit is 2^0. `VrsuStatus.digital_input(bit)` uses that
  convention.
