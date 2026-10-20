# ADCMT AD7461A OpenHTF plug

This package provides an OpenHTF plug for the ADCMT/Advantest AD7461A
(7461A) digital multimeter. It exposes documented SCPI measurement,
configuration, calculation, trigger, memory, system, and status-register
operations through PyVISA. Raw `write()` and `query()` methods are also
available for commands not wrapped by a convenience method.

## Interface and driver note

The manual describes SCPI mode as available over GPIB. Its USB interface uses
ADCMT's proprietary USB driver, which this package does not implement. The
plug accepts any PyVISA resource, including GPIB and serial resources, so a
USB-to-serial transport or suitable VISA driver can be added without changing
the instrument API. The USB driver/transport itself remains a follow-up.

Examples of resource names:

```text
GPIB0::1::INSTR
ASRL/dev/cu.usbserial-0001::INSTR
```

Set a serial baud rate and any interface-specific PyVISA options using
`baud_rate` or `resource_kwargs`. The manual recommends issuing a query and
reading its response after each setting command over USB. Enable
`verify_writes=True` to synchronize with `*OPC?` and check the SCPI error queue
after every high-level setting command.

## Installation

```sh
uv add openhtf-plug-adcmt-ad7461a
```

Install a VISA backend suitable for your GPIB or serial interface separately,
for example NI-VISA or `pyvisa-py` where supported.

## Example

```python
import os

from openhtf import Test, plugs
from adcmt_ad7461a import ADCMTAD7461APlug, MeasurementFunction

os.environ["ADCMT_AD7461A_RESOURCE"] = "GPIB0::1::INSTR"


@plugs.plug(multimeter=ADCMTAD7461APlug)
def measure_supply(test_api, multimeter):
    volts = multimeter.measure(MeasurementFunction.DC_VOLTAGE)
    test_api.attachments.attach("supply_voltage", str(volts).encode())


Test(measure_supply).execute()
```

For an explicit range, configure the function and range before using `READ?`:

```python
multimeter.configure_measurement(
    MeasurementFunction.DC_VOLTAGE,
    range=10,
    sampling_rate="SLOW",
)
volts = multimeter.read()
```

`measure()` uses the instrument's `MEASure:<function>?` one-shot query. For
instrument-timed sampling, use `set_sample_count()`, configure the trigger
source, then call `initiate()` and read results with `fetch_measurements()` or
`read_measurements()`. Calculation and data-memory helpers are documented in
the method docstrings; use raw SCPI passthrough for advanced or model-specific
commands.

The AD7461A API covers 7461A features. Features identified by the manual as
7461P-only (temperature measurement, second DC voltage channel, and digital
output) are not exposed as typed helpers.
