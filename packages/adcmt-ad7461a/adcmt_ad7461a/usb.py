"""Raw USB support for the ADCMT AD7461A.

The transport itself lives in the shared :mod:`adcmt_usb` package so that other
ADCMT instrument plugs can use the same ADC USB driver protocol. This module
keeps the AD7461A-specific names for backward compatibility.
"""

from __future__ import annotations

from adcmt_usb import ADCMTUSBPlug, ADCMTUSBTransport


class AD7461AUSBTransport(ADCMTUSBTransport):
    """ADCMT USB transport fixed to the AD7461A product ID."""

    PRODUCT_ID = 0x0201


class AD7461AUSBPlug(ADCMTUSBPlug):
    """OpenHTF plug exposing the AD7461A USB interface as raw command I/O.

    USB uses ADCMT's ADC-style command set; this plug intentionally does not
    inherit the SCPI subsystem convenience methods from ``AD7461APlug``.
    """

    TRANSPORT_CLASS = AD7461AUSBTransport


ADCMTAD7461AUSBPlug = AD7461AUSBPlug

__all__ = [
    "AD7461AUSBPlug",
    "AD7461AUSBTransport",
    "ADCMTAD7461AUSBPlug",
]
