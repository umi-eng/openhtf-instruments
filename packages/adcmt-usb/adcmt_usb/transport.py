"""Raw USB transport for ADCMT instruments using the ADC USB driver protocol.

ADCMT's Windows driver (``ausb.dll``) uses WinUSB bulk pipes for command and
response bytes. This module implements the same wire protocol with PyUSB. It
does not reproduce the vendor DLL; it only provides the transport boundary used
by the instrument-specific OpenHTF plugs.

The protocol is shared by the ADCMT instrument families that use the ADC USB
driver. Subclasses select the USB product IDs for their family by setting
``PRODUCT_IDS`` (or the older single ``PRODUCT_ID`` attribute).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from importlib import import_module
from types import TracebackType
from typing import Any, Self

from .exceptions import USBTransportError

_LOGGER = logging.getLogger(__name__)


class ADCMTUSBTransport:
    """Synchronous bulk-I/O transport matching ADCMT's public ``ausb`` API.

    ADCMT's DLL wraps each command in a 12-byte packet header, appends LF,
    zero-pads the command body to a 4-byte boundary, and sends a separate
    12-byte read-request packet before receiving a framed response. The
    device's first bulk IN and OUT endpoints are discovered from its active
    USB interface.

    PyUSB and a libusb backend are required when ``device`` is not injected.
    """

    VENDOR_ID = 0x1334
    PRODUCT_IDS: tuple[int, ...] = ()
    PRODUCT_ID: int | None = None
    USB_ID_REQUEST_TYPE = 0xC1
    USB_ID_REQUEST = 0xF5
    USB_ID_INDEX = 0
    USB488_REQUEST_TYPE = 0xA1
    USB488_REN_CONTROL = 0xA0
    DEFAULT_READ_SIZE = 50
    MAX_RESPONSE_SIZE = 1_048_576

    def __init__(
        self,
        *,
        usb_id: int | None = 1,
        timeout_ms: int = 10_000,
        read_size: int = DEFAULT_READ_SIZE,
        interface_number: int | None = None,
        device: Any | None = None,
        usb_core: Any | None = None,
        usb_util: Any | None = None,
        vendor_id: int | None = None,
        product_ids: int | tuple[int, ...] | None = None,
        remote_enable: bool = True,
        command_delay_s: float = 0.0,
    ) -> None:
        if usb_id is not None and (
            isinstance(usb_id, bool)
            or not isinstance(usb_id, int)
            or not 0 <= usb_id <= 255
        ):
            raise ValueError("usb_id must be an integer from 0 to 255 or None")
        if (
            isinstance(timeout_ms, bool)
            or not isinstance(timeout_ms, int)
            or timeout_ms <= 0
        ):
            raise ValueError("timeout_ms must be a positive integer")
        if (
            isinstance(read_size, bool)
            or not isinstance(read_size, int)
            or read_size <= 0
        ):
            raise ValueError("read_size must be a positive integer")
        if interface_number is not None and (
            isinstance(interface_number, bool)
            or not isinstance(interface_number, int)
            or not 0 <= interface_number <= 255
        ):
            raise ValueError(
                "interface_number must be an integer from 0 to 255 or None"
            )
        if not isinstance(remote_enable, bool):
            raise TypeError("remote_enable must be a bool")
        if (
            isinstance(command_delay_s, bool)
            or not isinstance(command_delay_s, (int, float))
            or not math.isfinite(float(command_delay_s))
            or command_delay_s < 0
        ):
            raise ValueError("command_delay_s must be a non-negative finite number")

        resolved_vendor_id = self.VENDOR_ID if vendor_id is None else vendor_id
        if (
            isinstance(resolved_vendor_id, bool)
            or not isinstance(resolved_vendor_id, int)
            or not 0 <= resolved_vendor_id <= 0xFFFF
        ):
            raise ValueError("vendor_id must be an integer from 0 to 0xFFFF")

        resolved_product_ids = self._resolve_product_ids(product_ids)
        if device is None and not resolved_product_ids:
            raise ValueError(
                "at least one product ID is required when device is not supplied"
            )

        if usb_core is None or usb_util is None:
            try:
                pyusb_core = import_module("usb.core")
                pyusb_util = import_module("usb.util")
            except ImportError as exc:
                raise USBTransportError(
                    "USB support requires PyUSB; install the package's 'usb' extra"
                ) from exc
            usb_core = usb_core or pyusb_core
            usb_util = usb_util or pyusb_util

        assert usb_core is not None and usb_util is not None
        self._usb_core: Any = usb_core
        self._usb_util: Any = usb_util
        self._usb_errors = self._usb_exception_types(usb_core)
        self._timeout_ms = timeout_ms
        self._read_size = read_size
        self._usb_id = usb_id
        self._vendor_id = resolved_vendor_id
        self._product_ids = resolved_product_ids
        self._remote_enable = remote_enable
        self._command_delay_s = float(command_delay_s)
        self._lock = threading.RLock()
        self._sequence = 0xFF
        self._device = (
            device
            if device is not None
            else self._find_device(usb_id, resolved_vendor_id, resolved_product_ids)
        )
        self.product_id = self._device_product_id(self._device)
        self._claimed_interface: int | None = None
        self._remote_enabled = False
        self._closed = False

        try:
            configuration = self._active_configuration()
            interface, endpoint_in, endpoint_out = self._find_bulk_endpoints(
                configuration, interface_number
            )
            interface_number = int(interface.bInterfaceNumber)
            self._usb_util.claim_interface(self._device, interface_number)
            self._claimed_interface = interface_number
            if self._remote_enable:
                self._set_remote(True, interface_number)
        except USBTransportError:
            self._dispose_device()
            raise
        except Exception as exc:
            self._dispose_device()
            raise USBTransportError(
                f"could not claim the ADCMT USB interface: {exc}"
            ) from exc

        self._endpoint_in = endpoint_in
        self._endpoint_out = endpoint_out
        self._read_packet_size = max(1, int(endpoint_in.wMaxPacketSize))
        self._read_buffer = bytearray()
        self.interface_number = interface_number

    @classmethod
    def _resolve_product_ids(
        cls, product_ids: int | tuple[int, ...] | None
    ) -> tuple[int, ...]:
        if product_ids is None:
            if cls.PRODUCT_IDS:
                resolved = tuple(cls.PRODUCT_IDS)
            elif cls.PRODUCT_ID is not None:
                resolved = (cls.PRODUCT_ID,)
            else:
                resolved = ()
        elif isinstance(product_ids, int):
            resolved = (product_ids,)
        else:
            resolved = tuple(product_ids)

        for product_id in resolved:
            if (
                isinstance(product_id, bool)
                or not isinstance(product_id, int)
                or not 0 <= product_id <= 0xFFFF
            ):
                raise ValueError("product IDs must be integers from 0 to 0xFFFF")
        return resolved

    @staticmethod
    def _device_product_id(device: Any) -> int | None:
        product_id = getattr(device, "idProduct", None)
        if isinstance(product_id, int) and not isinstance(product_id, bool):
            return product_id
        return None

    @staticmethod
    def _usb_exception_types(usb_core: Any) -> tuple[type[Exception], ...]:
        exceptions: list[type[Exception]] = [OSError, ValueError]
        usb_error = getattr(usb_core, "USBError", None)
        if isinstance(usb_error, type) and issubclass(usb_error, Exception):
            exceptions.append(usb_error)
        return tuple(exceptions)

    def _set_remote(self, enabled: bool, interface_number: int) -> None:
        try:
            self._device.ctrl_transfer(
                self.USB488_REQUEST_TYPE,
                self.USB488_REN_CONTROL,
                1 if enabled else 0,
                interface_number,
                1,
                timeout=self._timeout_ms,
            )
        except self._usb_errors as exc:
            state = "remote" if enabled else "local"
            raise USBTransportError(
                f"could not switch the ADCMT USB interface to {state}: {exc}"
            ) from exc
        self._remote_enabled = enabled

    @property
    def device(self) -> Any:
        """Return the underlying PyUSB device for advanced use."""

        return self._device

    @property
    def usb_id(self) -> int | None:
        """USB ID selected with ADCMT's vendor control request, if specified."""

        return self._usb_id

    @property
    def vendor_id(self) -> int:
        """USB vendor ID used to discover the device."""

        return self._vendor_id

    @property
    def product_ids(self) -> tuple[int, ...]:
        """USB product IDs accepted by this transport."""

        return self._product_ids

    def _find_device(
        self,
        usb_id: int | None,
        vendor_id: int,
        product_ids: tuple[int, ...],
    ) -> Any:
        try:
            if len(product_ids) == 1:
                devices = self._usb_core.find(
                    find_all=True,
                    idVendor=vendor_id,
                    idProduct=product_ids[0],
                )
                candidates = list(devices or ())
            else:
                devices = self._usb_core.find(
                    find_all=True,
                    idVendor=vendor_id,
                )
                candidates = [
                    candidate
                    for candidate in (devices or ())
                    if self._device_product_id(candidate) in product_ids
                ]
        except self._usb_errors as exc:
            raise USBTransportError(
                f"could not enumerate ADCMT USB devices: {exc}"
            ) from exc

        if not candidates:
            product_text = ", ".join(f"{pid:#06x}" for pid in product_ids)
            raise USBTransportError(
                f"no ADCMT USB device found (VID={vendor_id:#06x}, "
                f"PID={product_text}); select USB on the instrument"
            )
        if usb_id is None:
            return candidates[0]

        last_error: Exception | None = None
        for candidate in candidates:
            try:
                if self._read_usb_id(candidate) == usb_id:
                    return candidate
            except USBTransportError as exc:
                last_error = exc

        detail = f" Last USB-ID read failed: {last_error}" if last_error else ""
        raise USBTransportError(
            f"no ADCMT USB device responded with USB ID {usb_id}.{detail}"
        )

    def _read_usb_id(self, device: Any) -> int:
        """Read the byte used by ``ausb_open(hDev, dwMyid)`` to select a unit."""

        try:
            reply = device.ctrl_transfer(
                self.USB_ID_REQUEST_TYPE,
                self.USB_ID_REQUEST,
                0,
                self.USB_ID_INDEX,
                1,
                timeout=self._timeout_ms,
            )
            data = bytes(reply)
        except self._usb_errors as exc:
            raise USBTransportError(f"could not read the ADCMT USB ID: {exc}") from exc
        if not data:
            raise USBTransportError("ADCMT USB-ID control request returned no data")
        return data[0]

    def _active_configuration(self) -> Any:
        try:
            configuration = self._device.get_active_configuration()
        except self._usb_errors:
            configuration = None
        if configuration is not None:
            return configuration

        try:
            self._device.set_configuration()
            return self._device.get_active_configuration()
        except self._usb_errors as exc:
            raise USBTransportError(
                f"could not select an active USB configuration: {exc}"
            ) from exc

    @staticmethod
    def _find_bulk_endpoints(
        configuration: Any, interface_number: int | None
    ) -> tuple[Any, Any, Any]:
        for interface in configuration:
            number = int(interface.bInterfaceNumber)
            if interface_number is not None and number != interface_number:
                continue
            if int(interface.bAlternateSetting) != 0:
                continue

            endpoint_in = None
            endpoint_out = None
            for endpoint in interface:
                if (int(endpoint.bmAttributes) & 0x03) != 0x02:
                    continue
                if int(endpoint.bEndpointAddress) & 0x80:
                    endpoint_in = endpoint_in or endpoint
                else:
                    endpoint_out = endpoint_out or endpoint
            if endpoint_in is not None and endpoint_out is not None:
                return interface, endpoint_in, endpoint_out

        if interface_number is None:
            message = "no ADCMT USB interface found with bulk IN/OUT pipes"
        else:
            message = (
                f"no ADCMT USB interface {interface_number} found "
                "with bulk IN/OUT pipes"
            )
        raise USBTransportError(message)

    def _next_sequence(self) -> int:
        self._sequence = 1 if self._sequence == 0xFF else self._sequence + 1
        return self._sequence

    @staticmethod
    def _frame_packet(
        packet_type: int, sequence: int, mode: int, payload: bytes
    ) -> bytes:
        header = bytearray(12)
        header[0] = packet_type
        header[1] = sequence
        header[2] = sequence ^ 0xFF
        header[8] = mode
        if packet_type == 2 and not payload:
            body = b""
        else:
            body = payload
            if packet_type != 0x7E:
                body += b"\n"
                body += b"\x00" * (-len(body) % 4)
        header[4:8] = len(body).to_bytes(4, "little")
        return bytes(header) + body

    def _send_packet(self, packet_type: int, mode: int, payload: bytes) -> None:
        packet = self._frame_packet(packet_type, self._next_sequence(), mode, payload)
        try:
            written = int(self._endpoint_out.write(packet, timeout=self._timeout_ms))
        except self._usb_errors as exc:
            raise USBTransportError(f"USB bulk OUT write failed: {exc}") from exc
        if written != len(packet):
            raise USBTransportError(
                f"short USB write: sent {written} of {len(packet)} framed bytes"
            )

    def write(self, command: str | bytes | bytearray | memoryview) -> int:
        """Send a command in ADCMT's 12-byte USB packet envelope."""

        if isinstance(command, str):
            try:
                payload = command.encode("ascii")
            except UnicodeEncodeError as exc:
                raise ValueError(
                    "USB commands must contain ASCII characters only"
                ) from exc
        elif isinstance(command, (bytes, bytearray, memoryview)):
            payload = bytes(command)
        else:
            raise TypeError("command must be str or bytes-like")
        if not payload:
            raise ValueError("command must not be empty")

        with self._lock:
            self._ensure_open()
            self._send_packet(packet_type=1, mode=1, payload=payload)
            return len(payload)

    def _read_exact(self, size: int) -> bytes:
        while len(self._read_buffer) < size:
            needed = size - len(self._read_buffer)
            transfer_size = max(self._read_packet_size, needed)
            try:
                chunk = bytes(
                    self._endpoint_in.read(transfer_size, timeout=self._timeout_ms)
                )
            except self._usb_errors as exc:
                raise USBTransportError(
                    f"USB bulk IN read failed with {len(self._read_buffer)} buffered "
                    f"bytes while requesting {size}: {exc}"
                ) from exc
            if not chunk:
                raise USBTransportError(
                    f"short USB response: received {len(self._read_buffer)} of {size} bytes"
                )
            self._read_buffer.extend(chunk)

        result = bytes(self._read_buffer[:size])
        del self._read_buffer[:size]
        return result

    def read(self, size: int | None = None) -> bytes:
        """Request one ADCMT response and return up to ``size`` payload bytes."""

        if size is None:
            size = self._read_size
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise ValueError("size must be a positive integer")

        with self._lock:
            self._ensure_open()
            self._send_packet(packet_type=2, mode=0, payload=b"")
            header = self._read_exact(12)
            if header[0] != 2:
                raise USBTransportError(
                    f"unexpected ADCMT response packet type: {header[0]}"
                )
            payload_size = int.from_bytes(header[4:8], "little")
            if payload_size > self.MAX_RESPONSE_SIZE:
                raise USBTransportError(
                    f"ADCMT response frame is too large: {payload_size} bytes"
                )
            payload = self._read_exact(payload_size)
            return payload[:size]

    def query(
        self,
        command: str | bytes,
        *,
        size: int | None = None,
        delay_s: float | None = None,
    ) -> bytes:
        """Write a command and perform one bounded read for its response.

        ``delay_s`` overrides the transport's ``command_delay_s`` for this
        query. Some instruments require a short settling time between a
        command and the following read request.
        """

        delay = self._command_delay_s if delay_s is None else delay_s
        if (
            isinstance(delay, bool)
            or not isinstance(delay, (int, float))
            or not math.isfinite(float(delay))
            or delay < 0
        ):
            raise ValueError("delay_s must be a non-negative finite number")

        with self._lock:
            self.write(command)
            if delay:
                time.sleep(delay)
            return self.read(size)

    def _ensure_open(self) -> None:
        if self._closed:
            raise USBTransportError("USB transport is closed")

    def _dispose_device(self) -> None:
        if self._remote_enabled and self._claimed_interface is not None:
            try:
                self._set_remote(False, self._claimed_interface)
            except USBTransportError:
                _LOGGER.debug(
                    "Failed to switch ADCMT USB interface to local", exc_info=True
                )
        if self._claimed_interface is not None:
            try:
                self._usb_util.release_interface(self._device, self._claimed_interface)
            except self._usb_errors:
                _LOGGER.debug("Failed to release ADCMT USB interface", exc_info=True)
            self._claimed_interface = None
        try:
            self._usb_util.dispose_resources(self._device)
        except self._usb_errors:
            _LOGGER.debug("Failed to dispose ADCMT USB resources", exc_info=True)

    def close(self) -> None:
        """Release the USB interface and associated libusb resources."""

        with self._lock:
            if self._closed:
                return
            self._dispose_device()
            self._closed = True

    def __enter__(self) -> Self:
        self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
