# Siglent SDM3000X OpenHTF plug

This package provides an OpenHTF plug for taking single measurements from the
Siglent SDM3000X series over PyVISA. It supports DC/AC voltage and current,
2-wire and
4-wire resistance, capacitance, frequency, period, continuity, diode, and
temperature measurements. Scan-card operations are not included.

## Installation

Add this package to the project and install a VISA backend suitable for the
connection (for example, NI-VISA or `pyvisa-py`):

```sh
uv add openhtf-plug-siglent-sdm3000x
uv add pyvisa-py
```

Configure a PyVISA resource string directly or through
`SIGLENT_SDM3000X_RESOURCE`. For LAN, a common resource form is
`TCPIP0::192.0.2.10::inst0::INSTR`; USB resource names can be discovered with
`pyvisa.ResourceManager().list_resources()`.

## Example

```python
import os

from openhtf import Test, plugs
from siglent_sdm3000x import MeasurementFunction, SiglentSDM3000XPlug

os.environ["SIGLENT_SDM3000X_RESOURCE"] = "TCPIP0::192.0.2.10::inst0::INSTR"


@plugs.plug(multimeter=SiglentSDM3000XPlug)
def measure_supply(test_api, multimeter):
    volts = multimeter.measure(MeasurementFunction.DC_VOLTAGE, range=10)
    test_api.attachments.attach("supply_voltage", str(volts).encode())


Test(measure_supply).execute()
```

`measure()` accepts a `MeasurementFunction` or its name as a string. An
optional `range` is supported for voltage, current, resistance, and capacitance. Temperature readings can specify `temperature_transducer` (`RTD`
or `THER`) and `temperature_type` (for example, `K` for a thermocouple). Other
modes use the instrument's default measurement settings.
