"""Exceptions raised by the ADCMT AD7461A plug."""

from adcmt_usb.exceptions import ADCMTError, USBTransportError

# Keep the AD7461A-specific name as an alias of the shared base class so that
# USBTransportError remains an AD7461AError for existing callers.
AD7461AError = ADCMTError


class ScpiError(AD7461AError, RuntimeError):
    """An error reported in the instrument's SCPI error queue."""

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"SCPI error {code}: {message}")


__all__ = ["AD7461AError", "ScpiError", "USBTransportError"]
