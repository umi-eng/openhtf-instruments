"""Exceptions raised by the ADCMT AD7461A plug."""


class AD7461AError(Exception):
    """Base class for AD7461A driver errors."""


class ScpiError(AD7461AError, RuntimeError):
    """An error reported in the instrument's SCPI error queue."""

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"SCPI error {code}: {message}")
