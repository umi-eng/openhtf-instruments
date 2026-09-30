from __future__ import annotations

import unittest
from typing import cast

from siglent_sdg100x_plus import (
    Channel,
    OutputPolarity,
    OutputSettings,
    SiglentSDG1000XPlusPlug,
    Waveform,
    WaveformSettings,
)


class FakeResource:
    def __init__(self, responses=None):
        self.commands = []
        self.responses = responses or {}
        self.closed = False
        self.timeout = None
        self.read_termination = None
        self.write_termination = None
        self.tracking = False
        self.outputs = {"C1": False, "C2": False}
        self.loads = {"C1": "HZ", "C2": "50"}
        self.polarities = {"C1": "NOR", "C2": "NOR"}
        self.waveforms = {
            "C1": {
                "WVTP": "SINE",
                "FRQ": "1000HZ",
                "PERI": "1MS",
                "AMP": "2V",
                "OFST": "0V",
                "PHSE": "0",
            },
            "C2": {"WVTP": "SINE", "FRQ": "100HZ", "AMP": "1V"},
        }

    def write(self, command):
        self.commands.append(command)
        if command.startswith("COUP TRACE,"):
            self.tracking = command.endswith("ON")
        elif command == "OUT_BOTHCH ON":
            self.outputs = {"C1": True, "C2": True}
        elif command == "OUT_BOTHCH OFF":
            self.outputs = {"C1": False, "C2": False}
        elif command.startswith(("C1:BSWV ", "C2:BSWV ")):
            channel, payload = command.split(":BSWV ", 1)
            fields = payload.split(",")
            self.waveforms[channel][fields[0]] = fields[1]
        elif command.startswith(("C1:OUTP ", "C2:OUTP ")):
            channel, payload = command.split(":OUTP ", 1)
            fields = payload.split(",")
            if fields[0] in {"ON", "OFF"}:
                self.outputs[channel] = fields[0] == "ON"
            elif fields[0] == "LOAD":
                self.loads[channel] = fields[1]
            elif fields[0] == "PLRT":
                self.polarities[channel] = fields[1]

    def query(self, command):
        self.commands.append(command)
        if command == "COUPling?":
            state = "ON" if self.tracking else "OFF"
            return f"COUP TRACE,{state},FCOUP,OFF,PCOUP,OFF,ACOUP,OFF"
        if command in {"C1:BSWV?", "C2:BSWV?"}:
            channel = command[:2]
            fields = self.waveforms[channel]
            payload = ",".join(token for pair in fields.items() for token in pair)
            return f"{channel}:BSWV {payload}"
        if command in {"C1:OUTP?", "C2:OUTP?"}:
            channel = command[:2]
            state = "ON" if self.outputs[channel] else "OFF"
            return (
                f"{channel}:OUTP {state},LOAD,{self.loads[channel]},"
                f"PLRT,{self.polarities[channel]}"
            )
        return self.responses.get(command, "0")

    def read(self):
        return "Siglent,SDG1062X Plus,123,1.0\n"

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


class SiglentSDG1000XPlusPlugTest(unittest.TestCase):
    def test_identify_and_resource_setup(self):
        resource = FakeResource({"*IDN?": "Siglent,SDG1062X Plus,123,1.0"})
        manager = FakeResourceManager(resource)
        plug = SiglentSDG1000XPlusPlug(
            "USB0::1::INSTR",
            resource_manager=manager,
            resource_kwargs={"open_timeout": 1000},
            timeout_ms=2000,
        )

        self.assertEqual(plug.identify(), "Siglent,SDG1062X Plus,123,1.0")
        self.assertEqual(manager.opened, ("USB0::1::INSTR", {"open_timeout": 1000}))
        self.assertEqual(resource.timeout, 2000)
        self.assertEqual(resource.read_termination, "\n")
        plug.close()
        self.assertTrue(resource.closed)
        self.assertFalse(manager.closed)

    def test_configure_waveform_and_readback(self):
        resource = FakeResource()
        plug = SiglentSDG1000XPlusPlug(resource=resource)

        plug.configure_waveform(
            "sine",
            channel="CH1",
            frequency_hz=1000,
            amplitude_vpp=2,
            offset_v=0,
            phase_deg=90,
        )
        settings = plug.waveform_settings(Channel.C1)
        self.assertEqual(
            settings,
            WaveformSettings(
                waveform=Waveform.SINE,
                frequency_hz=1000,
                period_s=0.001,
                amplitude_vpp=2,
                offset_v=0,
                phase_deg=90,
            ),
        )
        self.assertEqual(
            resource.commands,
            [
                "C1:BSWV WVTP,SINE",
                "C1:BSWV FRQ,1000.0",
                "C1:BSWV AMP,2.0",
                "C1:BSWV OFST,0.0",
                "C1:BSWV PHSE,90.0",
                "C1:BSWV?",
            ],
        )
        plug.close()

    def test_readback_converts_engineering_units(self):
        resource = FakeResource()
        resource.waveforms["C1"]["FRQ"] = "1KHZ"
        plug = SiglentSDG1000XPlusPlug(resource=resource)

        self.assertEqual(plug.frequency(), 1000)
        self.assertEqual(plug.period(), 0.001)
        self.assertEqual(resource.commands, ["C1:BSWV?", "C1:BSWV?"])
        plug.close()

    def test_pulse_and_ramp_specific_parameters(self):
        resource = FakeResource()
        plug = SiglentSDG1000XPlusPlug(resource=resource)

        plug.configure_waveform(
            Waveform.PULSE,
            channel=Channel.C2,
            frequency_hz=1000,
            duty_cycle_pct=25,
            pulse_width_s=0.00025,
            rise_time_s=1e-8,
            fall_time_s=1e-8,
        )
        self.assertEqual(
            resource.commands,
            [
                "COUPling?",
                "C2:BSWV WVTP,PULSE",
                "C2:BSWV FRQ,1000.0",
                "C2:BSWV DUTY,25.0",
                "C2:BSWV WIDTH,0.00025",
                "C2:BSWV RISE,1e-08",
                "C2:BSWV FALL,1e-08",
            ],
        )
        with self.assertRaisesRegex(ValueError, "SYM is not valid"):
            plug.configure_waveform(Waveform.SQUARE, symmetry_pct=50)
        plug.close()

    def test_waveform_parameter_validation_precedes_writes(self):
        resource = FakeResource()
        plug = SiglentSDG1000XPlusPlug(resource=resource)

        invalid_calls = (
            lambda: plug.configure_waveform(Waveform.NOISE, frequency_hz=10),
            lambda: plug.configure_waveform(Waveform.SINE, frequency_hz=0),
            lambda: plug.configure_waveform(Waveform.SINE, phase_deg=361),
            lambda: plug.configure_waveform(
                Waveform.SINE, frequency_hz=10, period_s=0.1
            ),
        )
        for call in invalid_calls:
            with self.assertRaises(ValueError):
                call()
        self.assertEqual(resource.commands, [])
        with self.assertRaises(TypeError):
            plug.set_frequency(True)
        plug.close()

    def test_tracking_blocks_c2_edits_until_disabled(self):
        resource = FakeResource()
        plug = SiglentSDG1000XPlusPlug(resource=resource)

        plug.set_tracking(True)
        self.assertTrue(plug.tracking_enabled)
        with self.assertRaisesRegex(ValueError, "C1 controls C2"):
            plug.set_waveform(Waveform.SQUARE, Channel.C2)
        with self.assertRaisesRegex(ValueError, "C1 controls C2"):
            plug.set_output(False, Channel.C2)
        plug.set_waveform(Waveform.SQUARE, Channel.C1)
        plug.set_tracking(False)
        plug.set_waveform(Waveform.RAMP, Channel.C2)
        self.assertEqual(
            resource.commands,
            [
                "COUP TRACE,ON",
                "COUPling?",
                "COUPling?",
                "COUPling?",
                "C1:BSWV WVTP,SQUARE",
                "COUP TRACE,OFF",
                "COUPling?",
                "C2:BSWV WVTP,RAMP",
            ],
        )
        plug.close()

    def test_output_state_load_and_polarity(self):
        resource = FakeResource()
        plug = SiglentSDG1000XPlusPlug(resource=resource)

        self.assertEqual(
            plug.output_settings(Channel.C1),
            OutputSettings(False, None, OutputPolarity.NORMAL),
        )
        plug.set_load(75, Channel.C1)
        plug.set_polarity("inverted", Channel.C1)
        plug.set_output(True, Channel.C1)
        self.assertTrue(plug.is_output_enabled(Channel.C1))
        self.assertEqual(
            plug.output_settings(Channel.C1),
            OutputSettings(True, 75.0, OutputPolarity.INVERTED),
        )
        plug.set_load(None, Channel.C1)
        self.assertIsNone(plug.output_settings(Channel.C1).load_ohms)
        self.assertEqual(
            resource.commands,
            [
                "C1:OUTP?",
                "C1:OUTP LOAD,75.0",
                "C1:OUTP PLRT,INVT",
                "C1:OUTP ON",
                "C1:OUTP?",
                "C1:OUTP?",
                "C1:OUTP LOAD,HZ",
                "C1:OUTP?",
            ],
        )
        plug.close()

    def test_invalid_load_polarity_and_channel(self):
        resource = FakeResource()
        plug = SiglentSDG1000XPlusPlug(resource=resource)

        for load in (49, 100001, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                plug.set_load(load)
        with self.assertRaises(ValueError):
            plug.set_polarity("reverse")
        with self.assertRaises(ValueError):
            plug.set_output(True, "C3")
        with self.assertRaises(TypeError):
            plug.set_outputs(cast(bool, 1))
        self.assertEqual(resource.commands, [])
        plug.close()

    def test_both_outputs_and_teardown(self):
        resource = FakeResource()
        plug = SiglentSDG1000XPlusPlug(resource=resource)

        plug.set_outputs(True)
        self.assertTrue(plug.is_output_enabled(Channel.C1))
        self.assertTrue(plug.is_output_enabled(Channel.C2))
        plug.tearDown()
        self.assertFalse(resource.outputs["C1"])
        self.assertFalse(resource.outputs["C2"])
        self.assertTrue(resource.closed)
        self.assertEqual(resource.commands[-1], "OUT_BOTHCH OFF")

    def test_query_falls_back_to_write_and_read(self):
        class WriteReadResource:
            def __init__(self):
                self.commands = []

            def write(self, command):
                self.commands.append(command)

            def read(self):
                return "Siglent,SDG1062X Plus,123,1.0\n"

            def close(self):
                pass

        resource = WriteReadResource()
        plug = SiglentSDG1000XPlusPlug(resource=resource)

        self.assertEqual(plug.identify(), "Siglent,SDG1062X Plus,123,1.0")
        self.assertEqual(resource.commands, ["*IDN?"])
        plug.close()

    def test_resource_is_required_without_environment_or_injection(self):
        with self.assertRaisesRegex(ValueError, "resource_name is required"):
            SiglentSDG1000XPlusPlug()


if __name__ == "__main__":
    unittest.main()
