"""Raw USB support for the ADCMT 7461A/7461P/7451A.

The transport itself lives in the shared :mod:`adcmt_usb` package so that other
ADCMT instrument plugs can use the same ADC USB driver protocol. This module
keeps the 74x1-specific names for convenience.
"""

from __future__ import annotations

from adcmt_usb import ADCMTUSBPlug, ADCMTUSBTransport


class AD74X1USBTransport(ADCMTUSBTransport):
    """ADCMT USB transport fixed to the 74x1 family product ID."""

    PRODUCT_ID = 0x0201


class AD74X1USBPlug(ADCMTUSBPlug):
    """OpenHTF plug exposing the 74x1 USB interface as raw command I/O.

    USB uses ADCMT's ADC-style command set; this plug intentionally does not
    inherit the SCPI subsystem convenience methods from ``ADCMT74X1Plug``.
    """

    TRANSPORT_CLASS = AD74X1USBTransport


ADCMT74X1USBPlug = AD74X1USBPlug

__all__ = [
    "AD74X1USBPlug",
    "AD74X1USBTransport",
    "ADCMT74X1USBPlug",
]
