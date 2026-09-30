# Siglent SPD3303X OpenHTF plug

This package controls the Siglent SPD3303X/SPD3303X-E with PyVISA and SCPI. It
supports independent, series, and parallel CH1/CH2 operation; voltage and
current setpoints; output switching; and voltage, current, and power readback.

## Install

```sh
uv add openhtf-plug-siglent-spd3303x
```

Install a VISA backend for your connection, such as NI-VISA or `pyvisa-py`.
Set `SIGLENT_SPD3303X_RESOURCE` to your VISA resource name, or pass
`resource_name` directly. Resource names can be listed with
`pyvisa.ResourceManager().list_resources()`.

## OpenHTF example

```python
from openhtf import Test, plugs
from siglent_spd3303x import OutputMode, SiglentSPD3303XPlug


@plugs.plug(supply=SiglentSPD3303XPlug)
def supply_test(test_api, supply):
    supply.set_output_mode(OutputMode.INDEPENDENT)
    supply.set_voltage(5.0, channel="CH1")
    supply.set_current(1.0, channel="CH1")
    supply.enable_output("CH1")
    readings = supply.measure("CH1")
    test_api.attachments.attach("supply_readings", repr(readings).encode())


Test(supply_test).execute()
```

`output_mode` reads the mode from the system status register. In independent
mode, CH1 and CH2 have separate 0–32 V, 0–3.2 A setpoints. Series mode exposes
the combined CH1-controlled output (up to 60 V, 3.2 A); parallel mode exposes
the CH1-controlled output (up to 32 V, 6.4 A). CH2 setpoint and output-control
calls are rejected while the channels are coupled. CH3 output can be switched
through SCPI, but its 2.5/3.3/5 V selector is physical and its measurements are
not exposed by the documented SCPI commands.

Setpoints are checked against the published operating limits. Plug teardown
attempts to switch all three outputs off before closing the VISA connection.
