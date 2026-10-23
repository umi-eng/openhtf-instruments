"""Exceptions raised by the ADCMT 6241A/6242 plug."""


class ADCMT624XError(Exception):
    """Base class for 6241A/6242 driver errors."""


class CommandError(ADCMT624XError, RuntimeError):
    """The instrument reported one or more command errors."""

    def __init__(self, count: int, codes: tuple[int, ...] = ()) -> None:
        self.count = count
        self.codes = codes
        detail = f" ({', '.join(str(code) for code in codes)})" if codes else ""
        super().__init__(f"instrument reported {count} error(s){detail}")


class MeasurementParseError(ADCMT624XError, ValueError):
    """A measurement response could not be parsed."""
