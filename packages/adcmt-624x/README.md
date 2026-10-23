# ADCMT 6241A/6242 OpenHTF plug

This package provides an OpenHTF plug for the ADCMT 6241A and 6242 DC voltage
current source/monitors. It uses the instrument's USB interface and ADCMT's ADC
command set, not SCPI. The raw USB transport is shared with the AD7461A plug in
[`openhtf-adcmt-usb`](../adcmt-usb).

The plug covers source and measurement configuration, output control, manual
triggering, buffer-memory recall, sweeps, calculation functions, and status
registers. Raw `write()` and `query()` methods are available for commands that
do not have a typed helper.

## USB setup

1. Select `USB` as the interface on the instrument: `MENU` → `L) I/F` →
   `1) I/F BUS` → `USB`.
2. Set the USB ID (default `1`): `MENU` → `L) I/F` → `2) USB Id`.
3. Connect the instrument's USB B port to the PC.
4. Install a libusb backend. On macOS:

   ```sh
   brew install libusb
   ```

The plug discovers the 6241A (`VID 0x1334`, `PID 0x0301`) and 6242
(`VID 0x1334`, `PID 0x0302`) and selects the unit with ADCMT's USB-ID control
request. GPIB and USB cannot be used at the same time.

## Installation

```sh
uv add openhtf-plug-adcmt-624x
```

## Example

The following example sources 1 V through a resistor and measures the resulting
current. Connect the resistor between the `HI` and `LO` output terminals and
make sure the current limiter is appropriate for the resistor. Start with a
1 kΩ resistor, which gives about 1 mA at 1 V.

```python
from openhtf import Test, plugs
from adcmt_624x import (
    ADCMT624XPlug,
    IntegrationTime,
    MeasurementFunction,
    SourceFunction,
)


@plugs.plug(source_monitor=ADCMT624XPlug)
def measure_resistor(test_api, source_monitor):
    identity = source_monitor.identify()
    source_monitor.reset()
    source_monitor.set_header(True)

    current = source_monitor.measure(
        source_function=SourceFunction.VOLTAGE,
        source_value=1.0,
        limiter=0.003,
        function=MeasurementFunction.DC_CURRENT,
        integration_time=IntegrationTime.T1PLC,
        auto_zero=True,
        standby=True,
    )

    test_api.attachments.attach("identity", identity.encode())
    test_api.attachments.attach("current_a", str(current).encode())


Test(measure_resistor).execute()
```

`measure()` returns the numeric value. Use `read_measurement()` when you need
the header and status flags:

```python
measurement = source_monitor.read_measurement()
if not measurement.is_valid:
    raise RuntimeError(f"invalid reading: {measurement!r}")
print(measurement.value, measurement.header, measurement.flags)
```

## Source and measurement API

The source function must be selected before source-value, limiter, and
pulse-base commands because the command names differ between voltage and
current source modes:

```python
source_monitor.set_source_function(SourceFunction.VOLTAGE)
source_monitor.set_source_value(2.5)
source_monitor.set_limiter(0.03)          # ±30 mA
source_monitor.set_measurement_function(MeasurementFunction.DC_CURRENT)
source_monitor.set_measurement_range("AUTO")
source_monitor.set_integration_time("1PLC")
source_monitor.set_auto_zero(True)
source_monitor.set_trigger_mode("HOLD")
source_monitor.operate()
source_monitor.trigger()
reading = source_monitor.read_measurement()
source_monitor.standby()
```

Use `set_source_range("3V")` or `set_source_range("30mA")` for a fixed source
range. The range is validated against the selected source function, and the
6241A rejects the 5 A range.

## Buffer memory

The 6241A/6242 can store measurements in its 8000-point buffer. Enable storage,
run the measurement or sweep, then recall the values:

```python
source_monitor.set_buffer_memory("NORMAL")
source_monitor.clear_buffer()
source_monitor.set_store_target(100)
# ... run a sweep or a series of triggered measurements ...
readings = source_monitor.read_buffer()   # stops at the first empty slot
```

`read_buffer()` enters recall mode, reads until the stored count or the first
`EE` empty-data response, and always releases recall mode.

## Sweeps

```python
source_monitor.set_source_function(SourceFunction.VOLTAGE)
source_monitor.set_measurement_function(MeasurementFunction.DC_CURRENT)
source_monitor.set_source_mode("DC_SWEEP")
source_monitor.set_sweep_linear(0.0, 5.0, 0.1)
source_monitor.set_sweep_bias(0.0)
source_monitor.set_timing(3, 4, 100)
source_monitor.set_limiter(0.03)
source_monitor.set_buffer_memory("NORMAL")
source_monitor.clear_buffer()
source_monitor.operate()
source_monitor.trigger()
# Wait for the sweep to finish, then:
source_monitor.standby()
readings = source_monitor.read_buffer()
```

For instrument-timed completion, enable the sweep-complete event in the device
event status register and poll `read_device_event_status()`, or use `*OPC?`
with `wait_until_complete()`.

## Safety

The 6241A can source up to 32 V / 500 mA and the 6242 up to 6 V / 5 A. Always
set an appropriate limiter, verify the wiring before calling `operate()`, and
call `standby()` when finished. The `measure(..., standby=True)` convenience
turns the output off even if the read fails.
