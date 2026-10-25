"""Exceptions raised by the Heraeus Voetsch VRSU plug."""


class VrsuError(Exception):
    """Base class for errors reported by the VRSU driver."""


class VrsuCommunicationError(VrsuError):
    """The transport failed while talking to the VRSU."""


class VrsuTimeoutError(VrsuCommunicationError):
    """The VRSU did not answer within the configured timeout."""


class VrsuProtocolError(VrsuError, ValueError):
    """A reply from the VRSU did not match the documented format."""


class VrsuDeviceError(VrsuError, RuntimeError):
    """The VRSU reported an error in its talk string (``ER.x``)."""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"VRSU error ER.{int(code)}: {message}")


class VrsuUnsupportedOperationError(VrsuError, ValueError):
    """The requested operation is not available on the active interface."""
