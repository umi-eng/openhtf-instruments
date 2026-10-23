# ADCMT shared USB transport

This package contains the raw USB transport shared by the ADCMT instrument
plugs in this repository. It implements the wire protocol used by ADCMT's
`ausb.dll` / ADC Instruments USB Driver over WinUSB:

- discovers instruments by ADCMT's vendor ID (`0x1334`) and the product IDs
  configured by the instrument plug;
- selects a unit with ADCMT's USB-ID vendor control request;
- claims the vendor-specific interface and its bulk IN/OUT endpoints;
- sends the USB488 remote-enable request on open and remote-disable on close;
- frames commands with ADCMT's 12-byte packet header, LF terminator, and
  4-byte padding;
- sends a separate read-request frame and parses the response frame.

The transport is intentionally model-agnostic. Instrument packages subclass
`ADCMTUSBTransport` and set `PRODUCT_IDS` for their family. For example:

```python
from adcmt_usb import ADCMTUSBTransport


class MyInstrumentTransport(ADCMTUSBTransport):
    PRODUCT_IDS = (0x0301, 0x0302)
```

`ADCMTUSBPlug` is a small OpenHTF plug that exposes raw `write()`, `read()`,
`query()`, and `identify()` methods. Instrument plugs can subclass it and add
typed command helpers.

## Installation

```sh
uv add "openhtf-adcmt-usb[usb]"
```

PyUSB also needs a libusb backend. On macOS:

```sh
brew install libusb
```

## Notes

- Select USB as the instrument interface before connecting. ADCMT instruments
  cannot use GPIB and USB at the same time.
- The USB ID is set on the instrument's front panel and defaults to `1`.
- The transport uses PyUSB directly, so it does not require a VISA backend.
