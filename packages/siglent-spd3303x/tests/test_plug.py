from __future__ import annotations

import unittest
from typing import cast

from siglent_spd3303x import (
    Channel,
    OutputMode,
    SiglentSPD3303XPlug,
    SupplyMeasurement,
)


class FakeResource:
    def __init__(self, responses=None):
        self.commands = []
        self.responses = responses or {}
        self.closed = False
        self.timeout = None
        self.read_termination = None
        self.write_termination = None
        self.mode_value = 0
        self.outputs = {"CH1": False, "CH2": False, "CH3": False}

    def write(self, command):
        self.commands.append(command)
        if command.startswith("OUTPut:TRACK "):
            self.mode_value = int(command.rsplit(" ", 1)[1])
        elif command.startswith("OUTPut "):
            channel, state = command.removeprefix("OUTPut ").split(",")
            self.outputs[channel] = state == "ON"

    def query(self, command):
        self.commands.append(command)
        if command == "SYSTem:STATus?":
            mode_bits = {0: 1, 1: 0, 2: 2}[self.mode_value]
            status = mode_bits << 2
            status |= int(self.outputs["CH1"]) << 4
            status |= int(self.outputs["CH2"]) << 5
            return f"0x{status:04X}"
        return self.responses.get(command, "0")

    def read(self):
        return "3.3\n"

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


class SiglentSPD3303XPlugTest(unittest.TestCase):
    def test_identify_and_mode_readback(self):
        resource = FakeResource({"*IDN?": "Siglent Technologies,SPD3303X,123,1.0"})
        plug = SiglentSPD3303XPlug(resource=resource)

        self.assertEqual(plug.identify(), "Siglent Technologies,SPD3303X,123,1.0")
        for mode in OutputMode:
            plug.set_output_mode(mode)
            self.assertEqual(plug.output_mode, mode)
        self.assertEqual(
            resource.commands,
            [
                "*IDN?",
                "OUTPut:TRACK 0",
                "SYSTem:STATus?",
                "OUTPut:TRACK 1",
                "SYSTem:STATus?",
                "OUTPut:TRACK 2",
                "SYSTem:STATus?",
            ],
        )
        plug.close()

    def test_independent_setpoints_and_readback(self):
        resource = FakeResource(
            {
                "CH1:VOLT?": "5.000",
                "CH1:CURRent?": "1.000",
                "CH2:VOLT?": "12.0",
                "CH2:CURRent?": "2.0",
            }
        )
        plug = SiglentSPD3303XPlug(resource=resource)

        plug.set_voltage(5, Channel.CH1)
        plug.set_current(1, "CH1")
        plug.set_voltage(12, 2)
        plug.set_current(2, 2)
        self.assertEqual(plug.voltage("CH1"), 5.0)
        self.assertEqual(plug.current("CH1"), 1.0)
        self.assertEqual(plug.voltage("CH2"), 12.0)
        self.assertEqual(plug.current("CH2"), 2.0)
        self.assertEqual(
            resource.commands,
            [
                "SYSTem:STATus?",
                "CH1:VOLT 5.0",
                "SYSTem:STATus?",
                "CH1:CURRent 1.0",
                "SYSTem:STATus?",
                "CH2:VOLT 12.0",
                "SYSTem:STATus?",
                "CH2:CURRent 2.0",
                "CH1:VOLT?",
                "CH1:CURRent?",
                "SYSTem:STATus?",
                "CH2:VOLT?",
                "SYSTem:STATus?",
                "CH2:CURRent?",
            ],
        )
        plug.close()

    def test_series_and_parallel_limits_and_ch1_control(self):
        resource = FakeResource()
        plug = SiglentSPD3303XPlug(resource=resource)

        plug.set_output_mode(OutputMode.SERIES)
        plug.set_voltage(60)
        plug.set_current(3.2)
        with self.assertRaises(ValueError):
            plug.set_voltage(60.01)
        plug.set_output_mode("parallel")
        plug.set_voltage(32)
        plug.set_current(6.4)
        with self.assertRaises(ValueError):
            plug.set_current(6.401)

        self.assertEqual(
            [command for command in resource.commands if command.startswith("CH1:")],
            [
                "CH1:VOLT 60.0",
                "CH1:CURRent 3.2",
                "CH1:VOLT 32.0",
                "CH1:CURRent 6.4",
            ],
        )
        plug.close()

    def test_ch2_control_is_rejected_in_coupled_modes(self):
        resource = FakeResource()
        plug = SiglentSPD3303XPlug(resource=resource)
        plug.set_output_mode(OutputMode.SERIES)

        for operation in (
            lambda: plug.set_voltage(1, "CH2"),
            lambda: plug.set_current(1, "CH2"),
            lambda: plug.voltage("CH2"),
            lambda: plug.current("CH2"),
            lambda: plug.enable_output("CH2"),
        ):
            with self.assertRaisesRegex(ValueError, "CH1 controls"):
                operation()
        self.assertFalse(
            any(command.startswith("CH2:") for command in resource.commands)
        )
        plug.close()

    def test_output_controls_and_coupled_all_outputs(self):
        resource = FakeResource()
        plug = SiglentSPD3303XPlug(resource=resource)
        plug.enable_output("CH1")
        plug.enable_output("CH2")
        plug.enable_output("CH3")
        self.assertTrue(plug.is_output_enabled("CH1"))
        self.assertTrue(plug.is_output_enabled("CH2"))

        plug.set_output_mode(OutputMode.PARALLEL)
        plug.enable_output("CH1")
        self.assertTrue(plug.is_output_enabled("CH2"))
        plug.set_all_outputs(False)
        self.assertEqual(
            [command for command in resource.commands if command.startswith("OUTPut ")],
            [
                "OUTPut CH1,ON",
                "OUTPut CH2,ON",
                "OUTPut CH3,ON",
                "OUTPut CH1,ON",
                "OUTPut CH1,OFF",
                "OUTPut CH3,OFF",
            ],
        )
        self.assertFalse(plug.is_output_enabled("CH1"))
        with self.assertRaisesRegex(ValueError, "not reported"):
            plug.is_output_enabled("CH3")
        plug.close()

    def test_measurements(self):
        resource = FakeResource(
            {
                "MEASure:VOLTage? CH2": "3.30",
                "MEASure:CURRent? CH2": "0.25",
                "MEASure:POWEr? CH2": "0.825",
            }
        )
        plug = SiglentSPD3303XPlug(resource=resource)

        self.assertEqual(
            plug.measure("CH2"),
            SupplyMeasurement(voltage=3.3, current=0.25, power=0.825),
        )
        self.assertEqual(
            resource.commands,
            [
                "MEASure:VOLTage? CH2",
                "MEASure:CURRent? CH2",
                "MEASure:POWEr? CH2",
            ],
        )
        with self.assertRaisesRegex(ValueError, "CH3 measurements"):
            plug.measure("CH3")
        plug.close()

    def test_ch3_has_only_output_switching(self):
        resource = FakeResource()
        plug = SiglentSPD3303XPlug(resource=resource)

        plug.enable_output(Channel.CH3)
        with self.assertRaisesRegex(ValueError, "physical DIP switch"):
            plug.set_voltage(3.3, Channel.CH3)
        with self.assertRaisesRegex(ValueError, "physical DIP switch"):
            plug.set_current(1, Channel.CH3)
        self.assertEqual(resource.commands, ["OUTPut CH3,ON"])
        plug.close()

    def test_setpoint_and_mode_validation(self):
        resource = FakeResource()
        plug = SiglentSPD3303XPlug(resource=resource)

        for value in (-0.1, 32.1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                plug.set_voltage(value)
        with self.assertRaises(TypeError):
            plug.set_voltage(True)
        with self.assertRaises(ValueError):
            plug.set_output_mode("tracking")
        with self.assertRaises(ValueError):
            plug.set_output(True, "CH4")
        with self.assertRaises(TypeError):
            plug.set_output(cast(bool, 1))
        self.assertFalse(
            any(command.startswith("CH1:") for command in resource.commands)
        )
        plug.close()

    def test_status_errors(self):
        class InvalidStatusResource(FakeResource):
            def query(self, command):
                self.commands.append(command)
                if command == "SYSTem:STATus?":
                    return "not-hex"
                return "0"

        plug = SiglentSPD3303XPlug(resource=InvalidStatusResource())
        with self.assertRaisesRegex(ValueError, "invalid system status"):
            _ = plug.output_mode
        plug.close()

    def test_resource_manager_configuration(self):
        resource = FakeResource()
        manager = FakeResourceManager(resource)
        plug = SiglentSPD3303XPlug(
            "USB0::1::INSTR",
            resource_manager=manager,
            resource_kwargs={"open_timeout": 1000},
            timeout_ms=2000,
        )

        self.assertEqual(manager.opened, ("USB0::1::INSTR", {"open_timeout": 1000}))
        self.assertEqual(resource.timeout, 2000)
        self.assertEqual(resource.read_termination, "\n")
        self.assertEqual(resource.write_termination, "\n")
        plug.close()
        self.assertTrue(resource.closed)
        self.assertFalse(manager.closed)

    def test_query_falls_back_to_write_and_read(self):
        class WriteReadResource:
            def __init__(self):
                self.commands = []

            def write(self, command):
                self.commands.append(command)

            def read(self):
                return "Siglent,SPD3303X,123,1.0\n"

            def close(self):
                pass

        resource = WriteReadResource()
        plug = SiglentSPD3303XPlug(resource=resource)

        self.assertEqual(plug.identify(), "Siglent,SPD3303X,123,1.0")
        self.assertEqual(resource.commands, ["*IDN?"])
        plug.close()

    def test_teardown_switches_off_outputs_and_closes_resource(self):
        resource = FakeResource()
        plug = SiglentSPD3303XPlug(resource=resource)

        plug.tearDown()

        self.assertEqual(
            resource.commands,
            ["OUTPut CH1,OFF", "OUTPut CH2,OFF", "OUTPut CH3,OFF"],
        )
        self.assertTrue(resource.closed)

    def test_resource_is_required_without_environment_or_injection(self):
        with self.assertRaisesRegex(ValueError, "resource_name is required"):
            SiglentSPD3303XPlug()


if __name__ == "__main__":
    unittest.main()
