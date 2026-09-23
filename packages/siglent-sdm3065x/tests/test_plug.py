from __future__ import annotations

import unittest

from siglent_sdm3065x import MeasurementFunction, SiglentSDM3065XPlug


class FakeResource:
    def __init__(self, responses=None):
        self.commands = []
        self.responses = responses or {}
        self.closed = False
        self.timeout = None
        self.read_termination = None
        self.write_termination = None

    def write(self, command):
        self.commands.append(command)

    def query(self, command):
        self.commands.append(command)
        return self.responses.get(command, "0")

    def close(self):
        self.closed = True


class FakeResourceManager:
    def __init__(self, resource):
        self.resource = resource
        self.opened = None
        self.closed = False

    def open_resource(self, name, **kwargs):
        self.opened = (name, kwargs)
        return self.resource

    def close(self):
        self.closed = True


class SiglentSDM3065XPlugTest(unittest.TestCase):
    def test_identify(self):
        resource = FakeResource({"*IDN?": "SIGLENT,SDM3065X,12345,1.0"})
        plug = SiglentSDM3065XPlug(resource=resource)

        self.assertEqual(plug.identify(), "SIGLENT,SDM3065X,12345,1.0")
        plug.close()

    def test_measurement_query_and_result(self):
        resource = FakeResource({"MEASure:VOLTage:DC? 10": "1.2345\n"})
        plug = SiglentSDM3065XPlug(resource=resource)

        self.assertEqual(plug.measure(MeasurementFunction.DC_VOLTAGE, range=10), 1.2345)
        self.assertEqual(resource.commands, ["MEASure:VOLTage:DC? 10"])
        plug.close()
        self.assertTrue(resource.closed)

    def test_function_alias_resolution_and_range(self):
        resource = FakeResource({"MEASure:FRESistance? AUTO": "12.5"})
        plug = SiglentSDM3065XPlug(resource=resource)

        self.assertEqual(plug.measure("four_wire_resistance", range="AUTO"), 12.5)
        self.assertEqual(resource.commands, ["MEASure:FRESistance? AUTO"])
        plug.close()

    def test_temperature_probe_options(self):
        resource = FakeResource({"MEASure:TEMPerature? THER,K": "23.5"})
        plug = SiglentSDM3065XPlug(resource=resource)

        self.assertEqual(
            plug.measure(
                MeasurementFunction.TEMPERATURE,
                temperature_transducer="THER",
                temperature_type="K",
            ),
            23.5,
        )
        self.assertEqual(resource.commands, ["MEASure:TEMPerature? THER,K"])
        plug.close()

    def test_capacitance_range_uses_manual_units(self):
        resource = FakeResource({"MEASure:CAPacitance? 2uF": "1e-6"})
        plug = SiglentSDM3065XPlug(resource=resource)

        self.assertEqual(plug.measure("CAPacitance", range="2uF"), 1e-6)
        self.assertEqual(resource.commands, ["MEASure:CAPacitance? 2uF"])
        plug.close()

    def test_resource_manager_configuration_and_ownership(self):
        resource = FakeResource()
        manager = FakeResourceManager(resource)
        plug = SiglentSDM3065XPlug(
            "USB0::1::INSTR",
            resource_manager=manager,
            resource_kwargs={"open_timeout": 1000},
            timeout_ms=2000,
        )

        self.assertEqual(manager.opened, ("USB0::1::INSTR", {"open_timeout": 1000}))
        self.assertEqual(resource.timeout, 2000)
        self.assertEqual(resource.read_termination, "\n")
        plug.close()
        self.assertTrue(resource.closed)
        self.assertFalse(manager.closed)

    def test_invalid_function_and_range_are_rejected(self):
        resource = FakeResource()
        plug = SiglentSDM3065XPlug(resource=resource)

        with self.assertRaises(ValueError):
            plug.measure("not-a-measurement")
        with self.assertRaises(ValueError):
            plug.measure(MeasurementFunction.DC_VOLTAGE, range=-1)
        with self.assertRaises(ValueError):
            plug.measure(MeasurementFunction.DC_VOLTAGE, range="10;*RST")
        with self.assertRaises(ValueError):
            plug.measure(MeasurementFunction.DC_VOLTAGE, range="10V")
        with self.assertRaises(ValueError):
            plug.measure(MeasurementFunction.PERIOD, range=10)
        with self.assertRaises(ValueError):
            plug.measure(
                MeasurementFunction.TEMPERATURE,
                temperature_transducer="THER",
                temperature_type="K;*RST",
            )
        self.assertEqual(resource.commands, [])
        plug.close()

    def test_query_falls_back_to_write_and_read(self):
        class WriteReadResource:
            def __init__(self):
                self.commands = []

            def write(self, command):
                self.commands.append(command)

            def read(self):
                return "3.3\n"

            def close(self):
                pass

        resource = WriteReadResource()
        plug = SiglentSDM3065XPlug(resource=resource)

        self.assertEqual(plug.measure(MeasurementFunction.DC_VOLTAGE), 3.3)
        self.assertEqual(resource.commands, ["MEASure:VOLTage:DC?"])
        plug.close()


if __name__ == "__main__":
    unittest.main()
