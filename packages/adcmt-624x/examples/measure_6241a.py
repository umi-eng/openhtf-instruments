"""Smoke-test the ADCMT 6241A/6242 USB plug against real hardware.

Connect a resistor between the output HI and LO terminals. The script sources
a small voltage, measures the resulting current, and prints the reading. It
always turns the output off and closes the USB device.

Example (1 kOhm resistor, about 1 mA at 1 V):

    uv run --package openhtf-plug-adcmt-624x \
        python packages/adcmt-624x/examples/measure_6241a.py

Use ``--voltage`` and ``--limiter`` to change the source value and current
limit. The default limiter is 3 mA, which is safe for a 1 kOhm resistor at 1 V.
"""

from __future__ import annotations

import argparse
import sys

from adcmt_624x import (
    ADCMT624XPlug,
    CommandError,
    IntegrationTime,
    MeasurementFunction,
    MeasurementParseError,
    SourceFunction,
)
from adcmt_usb import USBTransportError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--usb-id",
        type=int,
        default=1,
        help="instrument USB ID set on the front panel (default: 1)",
    )
    parser.add_argument(
        "--voltage",
        type=float,
        default=1.0,
        help="source voltage in volts (default: 1.0)",
    )
    parser.add_argument(
        "--limiter",
        type=float,
        default=0.003,
        help="current limiter in amps (default: 0.003)",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=10_000,
        help="USB timeout in milliseconds (default: 10000)",
    )
    parser.add_argument(
        "--no-remote",
        action="store_true",
        help="skip the USB remote-enable request (troubleshooting only)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plug = ADCMT624XPlug(
        usb_id=args.usb_id,
        timeout_ms=args.timeout_ms,
        remote_enable=not args.no_remote,
    )
    try:
        identity = plug.identify()
        print(f"Identity: {identity}")
        print(f"Model:    {plug.model}")

        plug.reset()
        plug.set_header(True)
        plug.set_source_function(SourceFunction.VOLTAGE)
        plug.set_source_value(args.voltage)
        plug.set_limiter(args.limiter)
        plug.set_measurement_function(MeasurementFunction.DC_CURRENT)
        plug.set_measurement_range("AUTO")
        plug.set_integration_time(IntegrationTime.T1PLC)
        plug.set_auto_zero(True)
        plug.set_trigger_mode("HOLD")

        plug.operate()
        plug.trigger()
        measurement = plug.read_measurement()

        print(f"Header:   {measurement.header}")
        print(f"Flags:    {sorted(measurement.flags) or 'none'}")
        print(f"Current:  {measurement.value:.9g} A")
        if measurement.value:
            print(f"Resistance: {args.voltage / measurement.value:.9g} ohm")
        if not measurement.is_valid:
            print("WARNING: the instrument reported an invalid/flagged reading")

        plug.check_error()
        return 0
    except USBTransportError as exc:
        print(f"USB error: {exc}", file=sys.stderr)
        print(
            "Check that USB is selected on the instrument, the USB ID matches, "
            "and libusb is installed (for example: brew install libusb).",
            file=sys.stderr,
        )
        return 1
    except CommandError as exc:
        print(f"Instrument error: {exc}", file=sys.stderr)
        return 1
    except MeasurementParseError as exc:
        print(f"Could not parse the measurement: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            plug.standby()
        finally:
            plug.close()


if __name__ == "__main__":
    raise SystemExit(main())
