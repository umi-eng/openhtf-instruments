from __future__ import annotations

import unittest

from adcmt_74x1 import (
    AD74X1USBPlug,
    AD74X1USBTransport,
    USBTransportError,
)


def response_frame(payload: bytes, *, sequence: int = 2) -> bytes:
    header = bytearray(12)
    header[0] = 2
    header[1] = sequence
    header[2] = sequence ^ 0xFF
    header[4:8] = len(payload).to_bytes(4, "little")
    return bytes(header) + payload


class FakeEndpoint:
    def __init__(self, address, response=b"", *, attributes=2):
        self.bEndpointAddress = address
        self.bmAttributes = attributes
        self.wMaxPacketSize = 64
        self.response = bytearray(response)
        self.writes = []
        self.reads = []

    def write(self, payload, *, timeout):
        self.writes.append((bytes(payload), timeout))
        return len(payload)

    def read(self, size, *, timeout):
        self.reads.append((size, timeout))
        chunk = bytes(self.response[:size])
        del self.response[: len(chunk)]
        return chunk


class FakeInterface:
    def __init__(self, number, endpoints, alternate=0):
        self.bInterfaceNumber = number
        self.bAlternateSetting = alternate
        self.endpoints = endpoints

    def __iter__(self):
        return iter(self.endpoints)


class FakeConfiguration:
    def __init__(self, interfaces):
        self.interfaces = interfaces

    def __iter__(self):
        return iter(self.interfaces)


class FakeDevice:
    def __init__(
        self,
        usb_id=1,
        *,
        response=b"ADC,AD7461A,123456789,C00\r\n",
    ):
        self.bulk_in = FakeEndpoint(0x81, response_frame(response))
        self.bulk_out = FakeEndpoint(0x02)
        self.configuration = FakeConfiguration(
            [
                FakeInterface(
                    0,
                    [
                        FakeEndpoint(0x83, attributes=1),
                        self.bulk_in,
                        self.bulk_out,
                    ],
                )
            ]
        )
        self.usb_id = usb_id
        self.control_transfers = []
        self.active_configuration = self.configuration
        self.set_configuration_called = False

    def ctrl_transfer(self, request_type, request, value, index, size, *, timeout):
        self.control_transfers.append(
            (request_type, request, value, index, size, timeout)
        )
        return bytes([self.usb_id])

    def get_active_configuration(self):
        return self.active_configuration

    def set_configuration(self):
        self.set_configuration_called = True
        self.active_configuration = self.configuration


class FakeUSBUtil:
    def __init__(self):
        self.claimed = []
        self.released = []
        self.disposed = []

    def claim_interface(self, device, number):
        self.claimed.append((device, number))

    def release_interface(self, device, number):
        self.released.append((device, number))

    def dispose_resources(self, device):
        self.disposed.append(device)


class FakeUSBCore:
    def __init__(self, devices):
        self.devices = devices
        self.find_kwargs = None

    def find(self, **kwargs):
        self.find_kwargs = kwargs
        return iter(self.devices)


class AD74X1USBTransportTest(unittest.TestCase):
    def setUp(self):
        self.device = FakeDevice()
        self.usb_util = FakeUSBUtil()
        self.usb_core = FakeUSBCore([self.device])

    def create_transport(self, **kwargs):
        return AD74X1USBTransport(
            usb_core=self.usb_core,
            usb_util=self.usb_util,
            timeout_ms=2500,
            **kwargs,
        )

    def test_discovers_74x1_by_usb_id_and_claims_bulk_interface(self):
        transport = self.create_transport(usb_id=1)

        self.assertEqual(
            self.usb_core.find_kwargs,
            {"find_all": True, "idVendor": 0x1334, "idProduct": 0x0201},
        )
        self.assertEqual(
            self.device.control_transfers,
            [
                (0xC1, 0xF5, 0, 0, 1, 2500),
                (0xA1, 0xA0, 1, 0, 1, 2500),
            ],
        )
        self.assertEqual(self.usb_util.claimed, [(self.device, 0)])
        self.assertEqual(transport.interface_number, 0)
        transport.close()

    def test_usb_id_mismatch_is_reported(self):
        self.device.usb_id = 2

        with self.assertRaisesRegex(USBTransportError, "USB ID 1"):
            self.create_transport(usb_id=1)

    def test_bulk_io_uses_adcmt_packets_and_parses_response_frame(self):
        self.device.bulk_in.response = bytearray(
            response_frame(b"+1.234E+00\r\n", sequence=2)
        )
        transport = self.create_transport(usb_id=None)

        self.assertEqual(transport.write("F1,R0,PR1,AZ0"), 13)
        self.assertEqual(transport.read(), b"+1.234E+00\r\n")
        self.assertEqual(
            self.device.bulk_out.writes,
            [
                (
                    (
                        b"\x01\x01\xfe\x00\x10\x00\x00\x00"
                        b"\x01\x00\x00\x00F1,R0,PR1,AZ0\n\x00\x00"
                    ),
                    2500,
                ),
                (
                    b"\x02\x02\xfd\x00\x00\x00\x00\x00\x00\x00\x00\x00",
                    2500,
                ),
            ],
        )
        self.assertEqual(
            self.device.bulk_in.reads,
            [(64, 2500)],
        )
        self.assertEqual(
            self.device.control_transfers,
            [(0xA1, 0xA0, 1, 0, 1, 2500)],
        )
        transport.close()

    def test_frame_bytes_match_the_windows_capture(self):
        self.assertEqual(
            AD74X1USBTransport._frame_packet(1, 0x17, 1, b"H0"),
            bytes.fromhex("01 17 e8 00 04 00 00 00 01 00 00 00 48 30 0a 00"),
        )
        self.assertEqual(
            AD74X1USBTransport._frame_packet(2, 0x19, 0, b""),
            bytes.fromhex("02 19 e6 00 00 00 00 00 00 00 00 00"),
        )

    def test_openhtf_plug_query_strips_only_crlf(self):
        transport = self.create_transport(usb_id=None)
        plug = AD74X1USBPlug(transport=transport)

        self.assertEqual(plug.query("*IDN?"), "ADC,AD7461A,123456789,C00")
        self.assertEqual(
            self.device.bulk_out.writes,
            [
                (
                    b"\x01\x01\xfe\x00\x08\x00\x00\x00\x01\x00\x00\x00*IDN?\n\x00\x00",
                    2500,
                ),
                (
                    b"\x02\x02\xfd\x00\x00\x00\x00\x00\x00\x00\x00\x00",
                    2500,
                ),
            ],
        )
        self.assertEqual(self.device.bulk_in.reads, [(64, 2500)])
        plug.close()
        self.assertEqual(self.usb_util.released, [(self.device, 0)])
        self.assertEqual(self.usb_util.disposed, [self.device])
        self.assertEqual(
            self.device.control_transfers,
            [
                (0xA1, 0xA0, 1, 0, 1, 2500),
                (0xA1, 0xA0, 0, 0, 1, 2500),
            ],
        )

    def test_openhtf_teardown_closes_transport(self):
        transport = self.create_transport(usb_id=None)
        plug = AD74X1USBPlug(transport=transport)

        plug.tearDown()

        self.assertEqual(self.usb_util.released, [(self.device, 0)])
        self.assertEqual(self.usb_util.disposed, [self.device])

    def test_closed_transport_rejects_io(self):
        transport = self.create_transport(usb_id=None)
        transport.close()

        with self.assertRaisesRegex(USBTransportError, "closed"):
            transport.write("*IDN?")

    def test_invalid_commands_and_read_sizes_are_rejected(self):
        transport = self.create_transport(usb_id=None)

        with self.assertRaises(ValueError):
            transport.write("µ")
        with self.assertRaises(ValueError):
            transport.read(0)
        transport.close()


if __name__ == "__main__":
    unittest.main()
