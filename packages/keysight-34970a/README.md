# HP/Agilent/Keysight 34970A OpenHTF plug

This package controls the 34970A Data Acquisition / Switch Unit through
PyVISA using the instrument's SCPI interface. It supports RS-232 resources
and serial-to-Ethernet adapters exposed by VISA as TCP socket resources.
All three mainframe slots are discovered and validated against the supported
34901A, 34902A, 34903A, 34904A, 34905A, 34906A, 34907A, and 34908A cards.

## Installation

Install the package with uv or pip. A VISA implementation is also required;
for example, install NI-VISA or `pyvisa-py` separately for the interfaces you
use.

```sh
uv add openhtf-plug-keysight-34970a
```

## Instrument-timed scan

`ScanProfile` configures the 34970A's scan list, trigger source, interval, and
count. The measurements run in the instrument, rather than in a host-side
loop, which preserves the 34970A's deterministic scan timing.

```python
import os

from openhtf import Test, plugs
from keysight_34970a import (
    Keysight34970APlug,
    MeasurementConfig,
    MeasurementFunction,
    ScanProfile,
    TriggerSource,
)


os.environ["KEYSIGHT_34970A_RESOURCE"] = "ASRL/dev/cu.usbserial-0001::INSTR"


@plugs.plug(daq=Keysight34970APlug)
def collect(test_api, daq):
    profile = ScanProfile(
        measurements=(
            MeasurementConfig(101, MeasurementFunction.DC_VOLTAGE, range=10),
            MeasurementConfig.thermocouple(102, kind="K"),
        ),
        trigger_source=TriggerSource.TIMER,
        interval=1.0,
        count=10,
    )
    readings = daq.read_scan(profile)
    test_api.attachments.attach("daq_readings", repr(readings).encode())


Test(collect).execute()
```

For an Ethernet serial adapter, use its VISA TCP socket resource instead:

```python
os.environ["KEYSIGHT_34970A_RESOURCE"] = "TCPIP::192.0.2.10::5025::SOCKET"
```

The `measurements` field is optional. Supplying only `channels` creates a
profile for channels already configured on the instrument, which is useful
when a scan setup has been prepared on the front panel or by another tool.
Use `initiate()`, `trigger()`, `fetch_scan()`, and `abort()` when the scan
lifecycle needs to be controlled explicitly.

Switching and 34907A control operations are available through
`close_channels()`, `open_channels()`, `read_digital()`, `write_digital()`,
`read_totalizer()`, and `set_dac()`. Call `discover_modules()` or pass a
known model with `set_module()` before using card-specific operations when
module discovery is not available on a VISA simulator.
