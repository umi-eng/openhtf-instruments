# Rohde & Schwarz NGP800 OpenHTF plug

This package controls the R&S NGP800 power supply series through its SCPI
interface. It supports the NGP802, NGP822, NGP804, NGP814, and NGP824, with
model-aware channel counts and voltage/current limits.

Connections can use any PyVISA resource (USB TMC, VXI-11, HiSLIP, or a raw TCP
socket), or the built-in raw TCP transport on port 5025, which does not require
a VISA backend.

## Install

```sh
uv add openhtf-plug-rohde-schwarz-ngp800
```

For USB or VXI-11 connections, install a VISA backend such as NI-VISA or
`pyvisa-py`. Raw TCP connections through `host=` need no VISA backend.

## Connecting

Set `ROHDE_SCHWARZ_NGP800_RESOURCE` to a PyVISA resource name, or pass
`resource_name` directly:

```python
# USB TMC
os.environ["ROHDE_SCHWARZ_NGP800_RESOURCE"] = "USB0::0x0AAD::0x0197::...::INSTR"

# LAN, VXI-11
os.environ["ROHDE_SCHWARZ_NGP800_RESOURCE"] = "TCPIP::192.0.2.10::INSTR"

# LAN, raw socket through PyVISA
os.environ["ROHDE_SCHWARZ_NGP800_RESOURCE"] = "TCPIP::192.0.2.10::5025::SOCKET"
```

For a raw TCP connection without VISA, pass the instrument's hostname or IP
address. The default SCPI raw port is 5025:

```python
from rohde_schwarz_ngp800 import RohdeSchwarzNGP800Plug

supply = RohdeSchwarzNGP800Plug(host="192.0.2.10")
```

## OpenHTF example

```python
from openhtf import Test, plugs
from rohde_schwarz_ngp800 import Channel, RohdeSchwarzNGP800Plug


@plugs.plug(supply=RohdeSchwarzNGP800Plug)
def supply_test(test_api, supply):
    supply.set_current(1.0, Channel.CH1)
    supply.set_voltage(5.0, Channel.CH1)
    supply.enable_output(Channel.CH1)

    readings = supply.measure(Channel.CH1)
    test_api.attachments.attach("supply_readings", repr(readings).encode())

    supply.disable_output(Channel.CH1)


Test(supply_test).execute()
```

The plug detects the model from `*IDN?` and validates setpoints against the
published limits:

| Model  | Channels | Voltage per channel                  | Current per channel                  |
| ------ | -------- | ------------------------------------ | ------------------------------------ |
| NGP802 | 2        | 32.05 V                              | 20.01 A                              |
| NGP822 | 2        | 64.05 V                              | 10.01 A                              |
| NGP804 | 4        | 32.05 V                              | 20.01 A                              |
| NGP814 | 4        | 32.05 V (CH1/CH2), 64.05 V (CH3/CH4) | 20.01 A (CH1/CH2), 10.01 A (CH3/CH4) |
| NGP824 | 4        | 64.05 V                              | 10.01 A                              |

Setpoints are validated against these limits. Current setpoints have a
0.001 A minimum, matching the instrument's published range.

Pass `model="NGP824"` to skip the identification query, or inject a `resource`
for unit tests.

## API overview

- Setpoints: `set_voltage()`, `voltage()`, `set_current()`, `current()`,
  `apply()`.
- Outputs: `set_output()`, `enable_output()`, `disable_output()`,
  `output_enabled()`, `set_all_outputs()`.
- Measurements: `measure_voltage()`, `measure_current()`, `measure_power()`,
  `measure()`, `measure_all()`, `read()`, `regulation_mode()`.
- Statistics: `measure_statistics()`, `energy()`, `sample_count()`,
  `reset_statistics()`.
- Protection: `set_ocp()`, `ocp_enabled()`, `ocp_tripped()`, `clear_ocp()`,
  `set_ovp()`, `set_ovp_level()`, `ovp_tripped()`, `clear_ovp()`, `set_opp()`,
  `set_opp_level()`, `opp_tripped()`, `clear_opp()`, `clear_protection()`.
- Safety limits: `set_safety_limits_enabled()`,
  `set_voltage_safety_limits()`, `set_current_safety_limits()`, and their
  readback methods.
- Output settings: `set_remote_sense()`, `set_output_delay()`,
  `set_high_impedance()`, `set_slew_rate_reduction()`, `set_ramp()`.
- Tracking: `set_tracking()`, `set_channel_tracking()`.
- Instrument state: `identify()`, `reset()`, `clear_status()`, `next_error()`,
  `check_errors()`.

`apply()` and `read()` use the instrument's selected channel, so they change
the channel selection. All other channel-specific methods use SCPI channel
lists and do not disturb the selection.

Plug teardown switches every output off before closing the connection.

Advanced QuickArb sequences, data logging, digital I/O triggers, analog input,
and calibration are not exposed yet.
