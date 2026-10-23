"""Generic OpenHTF plug for raw ADCMT USB command I/O."""

from __future__ import annotations

from typing import Any

from openhtf.plugs import BasePlug

from .transport import ADCMTUSBTransport


class ADCMTUSBPlug(BasePlug):
    """Expose an ADCMT USB instrument as raw command I/O.

    Subclasses set ``TRANSPORT_CLASS`` to a transport configured for their
    instrument family. This plug intentionally provides only ``write()``,
    ``read()``, ``query()``, and ``identify()``; model-specific command helpers
    belong in the instrument plug.
    """

    TRANSPORT_CLASS: type[ADCMTUSBTransport] = ADCMTUSBTransport

    def __init__(
        self,
        *,
        usb_id: int | None = 1,
        timeout_ms: int = 10_000,
        response_size: int = ADCMTUSBTransport.DEFAULT_READ_SIZE,
        interface_number: int | None = None,
        transport: ADCMTUSBTransport | Any | None = None,
        device: Any | None = None,
        usb_core: Any | None = None,
        usb_util: Any | None = None,
        **transport_kwargs: Any,
    ) -> None:
        super().__init__()
        if (
            isinstance(response_size, bool)
            or not isinstance(response_size, int)
            or response_size <= 0
        ):
            raise ValueError("response_size must be a positive integer")
        self._response_size = response_size
        self._transport = transport or self.TRANSPORT_CLASS(
            usb_id=usb_id,
            timeout_ms=timeout_ms,
            read_size=response_size,
            interface_number=interface_number,
            device=device,
            usb_core=usb_core,
            usb_util=usb_util,
            **transport_kwargs,
        )

    @property
    def transport(self) -> Any:
        """Return the underlying transport for advanced operations."""

        return self._transport

    def write(self, command: str | bytes) -> int:
        """Send an ADCMT command without appending CR, LF, or NUL."""

        return self._transport.write(command)

    def read(self, size: int | None = None) -> str:
        """Read once from USB and strip only trailing CR/LF characters."""

        response = self._transport.read(self._response_size if size is None else size)
        return response.decode("ascii", errors="replace").rstrip("\r\n")

    def query(self, command: str | bytes, *, size: int | None = None) -> str:
        """Send a command and return one ASCII response read."""

        response = self._transport.query(
            command,
            size=self._response_size if size is None else size,
        )
        return response.decode("ascii", errors="replace").rstrip("\r\n")

    def identify(self) -> str:
        """Return the response to ``*IDN?``."""

        return self.query("*IDN?")

    def close(self) -> None:
        """Close the underlying USB transport."""

        self._transport.close()

    def tearDown(self) -> None:
        """Release the USB device when OpenHTF tears down the plug."""

        self.close()
