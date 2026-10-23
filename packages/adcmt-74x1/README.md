# ADCMT 7461A/7461P/7451A OpenHTF plug

This package provides an OpenHTF plug for the ADCMT/Advantest 74x1 family of
digital multimeters: the 7461A, 7461P, and 7451A. It exposes documented SCPI
measurement, configuration, calculation, trigger, memory, system, and
status-register operations through PyVISA. Raw `write()` and `query()` methods
are also available for commands not wrapped by a convenience method.

## Interfaces and USB command mode

`ADCMT74X1Plug` uses the documented SCPI subsystem commands through PyVISA,
for example over GPIB. ADCMT's public 7461A USB sample instead uses its ADC-style
command set over USB; it does not use the colon-prefixed SCPI subsystem
commands wrapped by `ADCMT74X1Plug`. `AD74X1USBPlug` is a separate raw
USB-command plug and intentionally provides only `write()`, `read()`,
`query()`, and `identify()` until model-specific USB behavior is validated on
hardware.

The USB transport is provided by the shared
[`openhtf-adcmt-usb`](../adcmt-usb) package, which is also used by the ADCMT
6241A/6242 plug. It uses PyUSB to discover the instrument (VID `0x1334`, PID
`0x0201` from the included ADCMT INF) and select the USB ID (default `1`). It
uses the first bulk IN/OUT endpoints, reads the USB ID with ADCMT's vendor
request, and sends the captured USB488 remote-enable request. It frames command
bytes with ADCMT's 12-byte packet header, includes the padded body length,
appends LF, and zero-pads to a 4-byte boundary. Each read sends a separate
12-byte request frame and parses the response frame. Before connecting, select USB as the instrument
interface; ADCMT's [USB-driver instructions](https://www.adcmt.com/download/usbdriver)
state that 7461A/P instruments default to GPIB. The sample program and driver
downloads are listed on ADCMT's [sample-program page](https://www.adcmt.com/download/sampleprogram).

Install the optional PyUSB dependency and a libusb backend:

```sh
uv add "openhtf-plug-adcmt-74x1[usb]"
brew install libusb  # macOS
```

The SCPI plug still accepts PyVISA resources such as `GPIB0::1::INSTR`; its
`baud_rate` and `resource_kwargs` options are for serial/VISA resources.

### Raw USB example

```python
from openhtf import Test, plugs
from adcmt_74x1 import AD74X1USBPlug, parse_measurements


@plugs.plug(multimeter=AD74X1USBPlug)
def measure_supply(test_api, multimeter):
    identity = multimeter.identify()  # sends *IDN?; USB ID defaults to 1
    multimeter.write("F1,R0,PR1,AZ0")  # DCV, auto range, sample rate 1, auto-zero off
    multimeter.write("TRS0")  # immediate acquisition
    volts = parse_measurements(multimeter.read())[0]
    test_api.attachments.attach("identity", identity.encode())
    test_api.attachments.attach("supply_voltage", str(volts).encode())


Test(measure_supply).execute()
```

Use ADCMT's USB/ADC-style commands for this plug. The SCPI convenience methods
such as `measure()` and `configure_measurement()` are available on
`ADCMT74X1Plug` and are not implemented by `AD74X1USBPlug`.

## Installation

```sh
uv add openhtf-plug-adcmt-74x1
```

Install a VISA backend suitable for your GPIB or serial interface separately,
for example NI-VISA or `pyvisa-py` where supported.

## Example

```python
import os

from openhtf import Test, plugs
from adcmt_74x1 import ADCMT74X1Plug, MeasurementFunction

os.environ["ADCMT_74X1_RESOURCE"] = "GPIB0::1::INSTR"


@plugs.plug(multimeter=ADCMT74X1Plug)
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

The typed API covers the SCPI features shared by the 74x1 family. Features that
are only available on some models — for example the 7461P temperature
measurement, second DC voltage channel, and digital output — are not exposed as
typed helpers; use raw SCPI passthrough for those.
