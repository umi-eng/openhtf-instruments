# Siglent SDL1030X OpenHTF plug

This package controls the Siglent SDL1030X electronic load with PyVISA and the
SDL1000X SCPI command set. It supports CC, CV, CP, CR, and LED mode selection;
static setpoints for CC/CV/CP/CR; input on/off; and voltage, current, power, and
resistance readings.

## Install

```sh
uv add openhtf-plug-siglent-sdl1030x
```

A VISA backend is also required. Install NI-VISA, or install `pyvisa-py` for
supported USB, serial, or TCP/IP connections.

Set `SIGLENT_SDL1030X_RESOURCE` to the VISA resource name reported for your
instrument (for example, use `ResourceManager().list_resources()` to discover
it). Resource names vary by interface and VISA backend.

## OpenHTF example

```python
import os

from openhtf import Test, plugs
from siglent_sdl1030x import LoadMode, SiglentSDL1030XPlug

os.environ["SIGLENT_SDL1030X_RESOURCE"] = "USB0::...::INSTR"  # replace with your VISA resource


@plugs.plug(load=SiglentSDL1030XPlug)
def load_test(test_api, load):
    load.set_mode(LoadMode.CC)
    load.set_level(0.5)  # amps; uses the selected mode
    load.enable_input()
    readings = load.measure()
    test_api.attachments.attach("load_readings", repr(readings).encode())


Test(load_test).execute()
```

The load input is disabled during plug teardown. The SCPI API is also available
directly: use `set_mode()`, `set_level(value, mode=...)`, `level()`,
`input_enabled`, `set_input()`, the individual `measure_*()` methods, or
`measure()` for a `LoadMeasurement` containing all four readings. Setpoints
are in amperes (CC), volts (CV), watts (CP), or ohms (CR); instrument-specific
limits are enforced by the load itself.
