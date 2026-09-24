from __future__ import annotations

import unittest

from siglent_sdl1000x import LoadMeasurement, LoadMode, SiglentSDL1000XPlug


class FakeResource:
    def __init__(self, responses=None):
        self.commands = []
        self.responses = responses or {}
        self.closed = False

    def write(self, command):
        self.commands.append(command)

    def query(self, command):
        self.commands.append(command)
        return self.responses.get(command, "0")

    def close(self):
        self.closed = True


class SiglentSDL1000XPlugTest(unittest.TestCase):
    def test_mode_and_level_commands(self):
        resource = FakeResource({"SOUR:FUNC?": "CURRENT", "SOUR:CURR?": "1.250"})
        plug = SiglentSDL1000XPlug(resource=resource)

        plug.set_mode("CC")
        plug.set_level(1.25)
        self.assertEqual(plug.mode, LoadMode.CC)
        self.assertEqual(plug.level("CC"), 1.25)
        self.assertEqual(
            resource.commands,
            [
                "SOUR:FUNC CURRent",
                "SOUR:FUNC?",
                "SOUR:CURR 1.25",
                "SOUR:FUNC?",
                "SOUR:CURR?",
            ],
        )
        plug.close()

    def test_mode_aliases_cover_all_static_modes(self):
        resource = FakeResource()
        plug = SiglentSDL1000XPlug(resource=resource)
        for alias, mode in (
            ("CV", LoadMode.CV),
            ("CP", LoadMode.CP),
            ("CR", LoadMode.CR),
            ("LED", LoadMode.LED),
        ):
            plug.set_mode(alias)
            self.assertEqual(resource.commands[-1], f"SOUR:FUNC {mode.value}")
        plug.close()

    def test_setpoint_commands_cover_cc_cv_cp_and_cr(self):
        resource = FakeResource(
            {
                "SOUR:CURR?": "0.5",
                "SOUR:VOLT?": "2.0",
                "SOUR:POW?": "3.0",
                "SOUR:RES?": "4.0",
            }
        )
        plug = SiglentSDL1000XPlug(resource=resource)

        for mode, command, expected in (
            (LoadMode.CC, "SOUR:CURR", 0.5),
            (LoadMode.CV, "SOUR:VOLT", 2.0),
            (LoadMode.CP, "SOUR:POW", 3.0),
            (LoadMode.CR, "SOUR:RES", 4.0),
        ):
            plug.set_level(expected, mode)
            self.assertEqual(plug.level(mode), expected)
            self.assertEqual(
                resource.commands[-2:], [f"{command} {expected}", f"{command}?"]
            )
        precise_value = 0.12345678912345678
        plug.set_level(precise_value, LoadMode.CC)
        self.assertEqual(resource.commands[-1], f"SOUR:CURR {precise_value}")
        plug.close()

    def test_input_state_and_measurements(self):
        resource = FakeResource(
            {
                "SOUR:INP?": "1",
                "MEAS:VOLT?": "12.5",
                "MEAS:CURR?": "0.5",
                "MEAS:POW?": "6.25",
                "MEAS:RES?": "25",
            }
        )
        plug = SiglentSDL1000XPlug(resource=resource)

        self.assertTrue(plug.input_enabled)
        plug.enable_input()
        self.assertEqual(
            plug.measure(),
            LoadMeasurement(voltage=12.5, current=0.5, power=6.25, resistance=25.0),
        )
        plug.disable_input()
        self.assertEqual(
            resource.commands,
            [
                "SOUR:INP?",
                "SOUR:INP ON",
                "MEAS:VOLT?",
                "MEAS:CURR?",
                "MEAS:POW?",
                "MEAS:RES?",
                "SOUR:INP OFF",
            ],
        )
        plug.close()

    def test_setpoint_validation_and_led_rejection(self):
        plug = SiglentSDL1000XPlug(resource=FakeResource())
        for value in (-1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                plug.set_level(value, LoadMode.CC)
        with self.assertRaises(TypeError):
            plug.set_level(True, LoadMode.CC)
        with self.assertRaises(ValueError):
            plug.set_level(1, LoadMode.LED)
        plug.close()

    def test_teardown_disables_input_and_closes_resource(self):
        resource = FakeResource()
        plug = SiglentSDL1000XPlug(resource=resource)

        plug.tearDown()

        self.assertEqual(resource.commands, ["SOUR:INP OFF"])
        self.assertTrue(resource.closed)

    def test_resource_is_required_without_environment_or_injection(self):
        with self.assertRaisesRegex(ValueError, "resource_name is required"):
            SiglentSDL1000XPlug()


if __name__ == "__main__":
    unittest.main()
