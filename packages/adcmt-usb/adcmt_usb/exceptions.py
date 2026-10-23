"""Exceptions raised by the shared ADCMT USB transport."""


class ADCMTError(Exception):
    """Base class for ADCMT driver errors."""


class USBTransportError(ADCMTError, RuntimeError):
    """An error opening or using an ADCMT USB transport."""
