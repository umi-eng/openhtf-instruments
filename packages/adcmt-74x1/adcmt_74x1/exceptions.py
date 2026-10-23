"""Exceptions raised by the ADCMT 74x1 plug."""

from adcmt_usb.exceptions import ADCMTError, USBTransportError

# Keep the 74x1-specific name as an alias of the shared base class so that
# USBTransportError remains an ADCMT74X1Error for existing callers.
ADCMT74X1Error = ADCMTError


class ScpiError(ADCMT74X1Error, RuntimeError):
    """An error reported in the instrument's SCPI error queue."""

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"SCPI error {code}: {message}")


__all__ = ["ADCMT74X1Error", "ScpiError", "USBTransportError"]
