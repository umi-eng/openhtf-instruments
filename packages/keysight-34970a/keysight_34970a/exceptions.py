"""Exceptions raised by the Keysight 34970A plug."""


class Keysight34970AError(Exception):
    """Base class for errors reported by the 34970A driver."""


class InvalidChannelError(Keysight34970AError, ValueError):
    """A channel is not valid for the installed module or operation."""


class UnsupportedOperationError(Keysight34970AError, ValueError):
    """The installed module does not support the requested operation."""


class ScpiError(Keysight34970AError, RuntimeError):
    """The instrument returned an error from its SCPI error queue."""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"SCPI error {code}: {message}")
