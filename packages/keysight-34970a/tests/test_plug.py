from __future__ import annotations

import unittest

from keysight_34970a import (
    Channel,
    InvalidChannelError,
    MeasurementConfig,
    MeasurementFunction,
    ModuleType,
    ReadingFormat,
    ScanProfile,
    TriggerSource,
    UnsupportedOperationError,
)
from keysight_34970a.plug import Keysight34970APlug


class FakeResource:
    def __init__(self, responses=None):
        self.commands = []
        self.responses = responses or {}

    def write(self, command):
        self.commands.append(command)

    def query(self, command):
        self.commands.append(command)
        response = self.responses.get(command, "0")
        return response() if callable(response) else response

    def close(self):
        self.closed = True


class Keysight34970ATest(unittest.TestCase):
    def test_channel_numbering_and_card_capabilities(self):
        self.assertEqual(int(Channel(100, 11)), 111)
        self.assertEqual(Channel.from_value("211"), Channel(200, 11))
        plug = Keysight34970APlug(resource=FakeResource())
        for slot, model in zip((100, 200, 300), list(ModuleType)[:3], strict=True):
            plug.set_module(slot, model)
        self.assertEqual(
            plug.modules,
            {
                100: ModuleType.MUX_34901A,
                200: ModuleType.MUX_34902A,
                300: ModuleType.ACTUATOR_34903A,
            },
        )
        plug.close()

    def test_scan_profile_reapplies_full_scan_list_after_configuration(self):
        resource = FakeResource(
            {"SYST:CTYP? 100": "34901A", "FETC?": "1.25,101,2.5,102"}
        )
        plug = Keysight34970APlug(resource=resource)
        profile = ScanProfile(
            measurements=(
                MeasurementConfig(101, MeasurementFunction.DC_VOLTAGE, range=10),
                MeasurementConfig.thermocouple(102, kind="K"),
            ),
            trigger_source=TriggerSource.TIMER,
            interval=0.5,
            count=2,
            reading_format=ReadingFormat(channel=True),
        )
        readings = plug.read_scan(profile)

        self.assertEqual(resource.commands[-1], "FETC?")
        self.assertIn("ROUT:SCAN (@101,102)", resource.commands)
        self.assertIn("TRIG:SOUR TIM", resource.commands)
        self.assertIn("TRIG:TIMER 0.5", resource.commands)
        self.assertIn("TRIG:COUNT 2", resource.commands)
        self.assertEqual([reading.channel for reading in readings], [101, 102])

    def test_34907a_control_operations(self):
        resource = FakeResource(
            {
                "SENS:DIG:DATA:BYTE? (@101)": "17",
                "SENS:TOT:DATA? (@103)": "42",
                "SOUR:VOLT? (@104)": "1.5",
            }
        )
        plug = Keysight34970APlug(resource=resource)
        plug.set_module(100, ModuleType.MULTIFUNCTION_34907A)

        plug.write_digital(101, 17)
        self.assertEqual(plug.read_digital(101), 17)
        plug.configure_totalizer(103, reset_on_read=True, falling_edge=True)
        self.assertEqual(plug.read_totalizer(103), 42)
        plug.set_dac(104, 1.5)
        self.assertEqual(plug.dac_voltage(104), 1.5)

        self.assertIn("SOUR:DIG:DATA:BYTE 17,(@101)", resource.commands)
        self.assertIn("SENS:TOT:TYPE RRES,(@103)", resource.commands)
        self.assertIn("SENS:TOT:SLOPE NEG,(@103)", resource.commands)
        plug.close()

    def test_switch_card_restrictions_are_enforced(self):
        plug = Keysight34970APlug(resource=FakeResource())
        plug.set_module(100, ModuleType.RF_MUX_34905A)
        plug.close_channels([111])
        with self.assertRaises(UnsupportedOperationError):
            plug.open_channels([111])
        with self.assertRaises(InvalidChannelError):
            plug.close_channels([115])
        plug.close()


if __name__ == "__main__":
    unittest.main()
