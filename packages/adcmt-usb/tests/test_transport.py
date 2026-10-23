from __future__ import annotations

import unittest

from adcmt_usb import ADCMTUSBPlug, ADCMTUSBTransport, USBTransportError


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
    def __init__(self, usb_id=1, product_id=0x0301, response=b"ADC,6241A,1,C00\r\n"):
        self.idProduct = product_id
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

    def ctrl_transfer(self, request_type, request, value, index, size, *, timeout):
        self.control_transfers.append(
            (request_type, request, value, index, size, timeout)
        )
        return bytes([self.usb_id])

    def get_active_configuration(self):
        return self.active_configuration

    def set_configuration(self):
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


class ADCMTUSBTransportTest(unittest.TestCase):
    def setUp(self):
        self.device = FakeDevice(product_id=0x0302)
        self.usb_util = FakeUSBUtil()
        self.usb_core = FakeUSBCore([self.device])

    def create_transport(self, **kwargs):
        return ADCMTUSBTransport(
            usb_core=self.usb_core,
            usb_util=self.usb_util,
            timeout_ms=2500,
            **kwargs,
        )

    def test_multiple_product_ids_filter_by_id_product(self):
        transport = self.create_transport(usb_id=None, product_ids=(0x0301, 0x0302))

        self.assertEqual(
            self.usb_core.find_kwargs,
            {"find_all": True, "idVendor": 0x1334},
        )
        self.assertEqual(transport.product_id, 0x0302)
        self.assertEqual(transport.product_ids, (0x0301, 0x0302))
        transport.close()

    def test_single_product_id_uses_id_product_in_find(self):
        transport = self.create_transport(usb_id=None, product_ids=0x0301)

        self.assertEqual(
            self.usb_core.find_kwargs,
            {"find_all": True, "idVendor": 0x1334, "idProduct": 0x0301},
        )
        transport.close()

    def test_usb_id_mismatch_is_reported(self):
        self.device.usb_id = 2

        with self.assertRaisesRegex(USBTransportError, "USB ID 1"):
            self.create_transport(usb_id=1, product_ids=0x0301)

    def test_raw_plug_query_strips_crlf(self):
        transport = self.create_transport(usb_id=None, product_ids=0x0301)
        plug = ADCMTUSBPlug(transport=transport)

        self.assertEqual(plug.query("*IDN?"), "ADC,6241A,1,C00")
        plug.close()
        self.assertEqual(self.usb_util.released, [(self.device, 0)])
        self.assertEqual(self.usb_util.disposed, [self.device])

    def test_closed_transport_rejects_io(self):
        transport = self.create_transport(usb_id=None, product_ids=0x0301)
        transport.close()

        with self.assertRaisesRegex(USBTransportError, "closed"):
            transport.write("*IDN?")

    def test_invalid_product_id_is_rejected(self):
        with self.assertRaises(ValueError):
            self.create_transport(product_ids=(0x0301, 0x10000))


if __name__ == "__main__":
    unittest.main()
