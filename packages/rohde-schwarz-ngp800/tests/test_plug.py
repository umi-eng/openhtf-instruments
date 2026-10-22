from __future__ import annotations

import unittest
from typing import cast
from unittest import mock

from rohde_schwarz_ngp800 import (
    Channel,
    Readback,
    RegulationMode,
    RohdeSchwarzNGP800Plug,
    ScpiError,
    StatisticsMeasurement,
    SupplyMeasurement,
)
from rohde_schwarz_ngp800.plug import _SocketResource


class FakeResource:
    """Stateful fake of the NGP800 SCPI command set."""

    def __init__(self, model="NGP824", responses=None):
        self.commands = []
        self.responses = responses or {}
        self.closed = False
        self.timeout = None
        self.read_termination = None
        self.write_termination = None
        self.model = model
        self.selected_channel = 1
        self.voltages = {number: 0.0 for number in range(1, 5)}
        self.currents = {number: 0.001 for number in range(1, 5)}
        self.outputs = {number: False for number in range(1, 5)}
        self.measurements = {
            1: (1.0, 0.1, 0.1),
            2: (2.0, 0.2, 0.4),
            3: (3.0, 0.3, 0.9),
            4: (4.0, 0.4, 1.6),
        }
        self.statistics = {
            number: {
                "volt_min": 0.9,
                "volt_max": 1.1,
                "volt_avg": 1.0,
                "curr_min": 0.09,
                "curr_max": 0.11,
                "curr_avg": 0.1,
                "pow_min": 0.09,
                "pow_max": 0.11,
                "pow_avg": 0.1,
                "energy": 5.0,
                "count": 10,
            }
            for number in range(1, 5)
        }
        self.ocp = {number: False for number in range(1, 5)}
        self.ocp_tripped = {number: False for number in range(1, 5)}
        self.ocp_blowing = {number: 0.0 for number in range(1, 5)}
        self.ocp_initial = {number: 0.0 for number in range(1, 5)}
        self.ovp = {number: False for number in range(1, 5)}
        self.ovp_tripped = {number: False for number in range(1, 5)}
        self.ovp_levels = {number: 64.05 for number in range(1, 5)}
        self.opp = {number: False for number in range(1, 5)}
        self.opp_tripped = {number: False for number in range(1, 5)}
        self.opp_levels = {number: 200.0 for number in range(1, 5)}
        self.safety_enabled = {number: False for number in range(1, 5)}
        self.voltage_limits = {number: (0.0, 64.05) for number in range(1, 5)}
        self.current_limits = {number: (0.001, 10.01) for number in range(1, 5)}
        self.tracking = False
        self.channel_tracking = {number: False for number in range(1, 5)}
        self.remote_sense = {number: False for number in range(1, 5)}
        self.remote_sense_tripped = {number: False for number in range(1, 5)}
        self.output_delay_enabled = {number: False for number in range(1, 5)}
        self.output_delays = {number: 0.0 for number in range(1, 5)}
        self.high_impedance = {number: False for number in range(1, 5)}
        self.slew_rate = {number: False for number in range(1, 5)}
        self.ramp_enabled = {number: False for number in range(1, 5)}
        self.ramp_durations = {number: 0.0 for number in range(1, 5)}
        self.regulation = {number: "CV" for number in range(1, 5)}
        self.errors = []

    def _channels(self, command):
        if "(@" in command:
            text = command.split("(@", 1)[1].rstrip(")")
            return [int(part) for part in text.split(",")]
        return [self.selected_channel]

    def _split(self, payload):
        if ",(@" in payload:
            value, _, channel_text = payload.partition(",(@")
            channels = [int(part) for part in channel_text.rstrip(")").split(",")]
            return value.strip(), channels
        return payload.strip(), [self.selected_channel]

    def write(self, command):
        self.commands.append(command)
        if command == "*RST":
            self.voltages = {number: 0.0 for number in self.voltages}
            self.currents = {number: 0.001 for number in self.currents}
            self.outputs = {number: False for number in self.outputs}
        elif command == "*CLS":
            self.errors.clear()
        elif command.startswith("INST:NSEL "):
            self.selected_channel = int(command.split(" ", 1)[1])
        elif command.startswith("APPL "):
            voltage, current = command.split(" ", 1)[1].split(",")
            self.voltages[self.selected_channel] = float(voltage)
            self.currents[self.selected_channel] = float(current)
        elif command.startswith("VOLT:ALIM:LOW "):
            value, channels = self._split(command[len("VOLT:ALIM:LOW ") :])
            for channel in channels:
                lower, upper = self.voltage_limits[channel]
                self.voltage_limits[channel] = (float(value), upper)
        elif command.startswith("VOLT:ALIM:UPP "):
            value, channels = self._split(command[len("VOLT:ALIM:UPP ") :])
            for channel in channels:
                lower, _ = self.voltage_limits[channel]
                self.voltage_limits[channel] = (lower, float(value))
        elif command.startswith("CURR:ALIM:LOW "):
            value, channels = self._split(command[len("CURR:ALIM:LOW ") :])
            for channel in channels:
                lower, upper = self.current_limits[channel]
                self.current_limits[channel] = (float(value), upper)
        elif command.startswith("CURR:ALIM:UPP "):
            value, channels = self._split(command[len("CURR:ALIM:UPP ") :])
            for channel in channels:
                lower, _ = self.current_limits[channel]
                self.current_limits[channel] = (lower, float(value))
        elif command.startswith("ALIM "):
            value, channels = self._split(command[len("ALIM ") :])
            for channel in channels:
                self.safety_enabled[channel] = value == "1"
        elif command.startswith("VOLT:PROT:LEV "):
            value, channels = self._split(command[len("VOLT:PROT:LEV ") :])
            for channel in channels:
                self.ovp_levels[channel] = float(value)
        elif command.startswith("VOLT:PROT:CLE"):
            for channel in self._channels(command):
                self.ovp_tripped[channel] = False
        elif command.startswith("VOLT:PROT "):
            value, channels = self._split(command[len("VOLT:PROT ") :])
            for channel in channels:
                self.ovp[channel] = value == "1"
        elif command.startswith("POW:PROT:LEV "):
            value, channels = self._split(command[len("POW:PROT:LEV ") :])
            for channel in channels:
                self.opp_levels[channel] = float(value)
        elif command.startswith("POW:PROT:CLE"):
            for channel in self._channels(command):
                self.opp_tripped[channel] = False
        elif command.startswith("POW:PROT "):
            value, channels = self._split(command[len("POW:PROT ") :])
            for channel in channels:
                self.opp[channel] = value == "1"
        elif command.startswith("FUSE:DEL "):
            value, channels = self._split(command[len("FUSE:DEL ") :])
            for channel in channels:
                self.ocp_blowing[channel] = float(value)
        elif command.startswith("FUSE:DEL:INIT "):
            value, channels = self._split(command[len("FUSE:DEL:INIT ") :])
            for channel in channels:
                self.ocp_initial[channel] = float(value)
        elif command.startswith("FUSE:TRIP:CLE"):
            for channel in self._channels(command):
                self.ocp_tripped[channel] = False
        elif command.startswith("FUSE "):
            value, channels = self._split(command[len("FUSE ") :])
            for channel in channels:
                self.ocp[channel] = value == "1"
        elif command.startswith("PROT:CLE"):
            for channel in self._channels(command):
                self.ocp_tripped[channel] = False
                self.ovp_tripped[channel] = False
                self.opp_tripped[channel] = False
        elif command.startswith("TRAC:ENAB:GEN "):
            self.tracking = command.endswith("1")
        elif command.startswith("TRAC:ENAB:CH"):
            channel = int(command[len("TRAC:ENAB:CH")])
            self.channel_tracking[channel] = command.endswith("1")
        elif command.startswith("VOLT:SENS:TRIP:CLE"):
            for channel in self._channels(command):
                self.remote_sense_tripped[channel] = False
        elif command.startswith("VOLT:SENS "):
            value, channels = self._split(command[len("VOLT:SENS ") :])
            for channel in channels:
                self.remote_sense[channel] = value == "EXT"
        elif command.startswith("OUTP:DEL:DUR "):
            value, channels = self._split(command[len("OUTP:DEL:DUR ") :])
            for channel in channels:
                self.output_delays[channel] = float(value)
        elif command.startswith("OUTP:DEL "):
            value, channels = self._split(command[len("OUTP:DEL ") :])
            for channel in channels:
                self.output_delay_enabled[channel] = value == "1"
        elif command.startswith("OUTP:IMP "):
            value, channels = self._split(command[len("OUTP:IMP ") :])
            for channel in channels:
                self.high_impedance[channel] = value == "1"
        elif command.startswith("OUTP:SRAT "):
            value, channels = self._split(command[len("OUTP:SRAT ") :])
            for channel in channels:
                self.slew_rate[channel] = value == "1"
        elif command.startswith("OUTP "):
            value, channels = self._split(command[len("OUTP ") :])
            for channel in channels:
                self.outputs[channel] = value == "1"
        elif command.startswith("VOLT:RAMP:DUR "):
            value, channels = self._split(command[len("VOLT:RAMP:DUR ") :])
            for channel in channels:
                self.ramp_durations[channel] = float(value)
        elif command.startswith("VOLT:RAMP "):
            value, channels = self._split(command[len("VOLT:RAMP ") :])
            for channel in channels:
                self.ramp_enabled[channel] = value == "1"
        elif command.startswith("MEAS:STAT:RES"):
            for channel in self._channels(command):
                self.statistics[channel] = {
                    "volt_min": 0.0,
                    "volt_max": 0.0,
                    "volt_avg": 0.0,
                    "curr_min": 0.0,
                    "curr_max": 0.0,
                    "curr_avg": 0.0,
                    "pow_min": 0.0,
                    "pow_max": 0.0,
                    "pow_avg": 0.0,
                    "energy": 0.0,
                    "count": 0,
                }
        elif command.startswith("VOLT "):
            value, channels = self._split(command[len("VOLT ") :])
            for channel in channels:
                self.voltages[channel] = float(value)
        elif command.startswith("CURR "):
            value, channels = self._split(command[len("CURR ") :])
            for channel in channels:
                self.currents[channel] = float(value)

    def query(self, command):
        self.commands.append(command)
        if command == "*IDN?":
            return f"Rohde&Schwarz,{self.model},5601.4007k03/123456,02.025"
        if command == "INST:NSEL?":
            return str(self.selected_channel)
        if command == "READ?":
            voltage, current, _ = self.measurements[self.selected_channel]
            return f"{voltage:.6E},{current:.6E}"
        if command.startswith("VOLT:ALIM:LOW?"):
            channel = self._channels(command)[0]
            return f"{self.voltage_limits[channel][0]:.3f}"
        if command.startswith("VOLT:ALIM:UPP?"):
            channel = self._channels(command)[0]
            return f"{self.voltage_limits[channel][1]:.3f}"
        if command.startswith("CURR:ALIM:LOW?"):
            channel = self._channels(command)[0]
            return f"{self.current_limits[channel][0]:.3f}"
        if command.startswith("CURR:ALIM:UPP?"):
            channel = self._channels(command)[0]
            return f"{self.current_limits[channel][1]:.3f}"
        if command.startswith("ALIM?"):
            channel = self._channels(command)[0]
            return "1" if self.safety_enabled[channel] else "0"
        if command.startswith("VOLT:PROT:LEV?"):
            channel = self._channels(command)[0]
            return f"{self.ovp_levels[channel]:.3f}"
        if command.startswith("VOLT:PROT:TRIP?"):
            channel = self._channels(command)[0]
            return "1" if self.ovp_tripped[channel] else "0"
        if command.startswith("VOLT:PROT?"):
            channel = self._channels(command)[0]
            return "1" if self.ovp[channel] else "0"
        if command.startswith("POW:PROT:LEV?"):
            channel = self._channels(command)[0]
            return f"{self.opp_levels[channel]:.3f}"
        if command.startswith("POW:PROT:TRIP?"):
            channel = self._channels(command)[0]
            return "1" if self.opp_tripped[channel] else "0"
        if command.startswith("POW:PROT?"):
            channel = self._channels(command)[0]
            return "1" if self.opp[channel] else "0"
        if command.startswith("FUSE:DEL?"):
            channel = self._channels(command)[0]
            return f"{self.ocp_blowing[channel]:.3f}"
        if command.startswith("FUSE:DEL:INIT?"):
            channel = self._channels(command)[0]
            return f"{self.ocp_initial[channel]:.3f}"
        if command.startswith("FUSE:TRIP?"):
            channel = self._channels(command)[0]
            return "1" if self.ocp_tripped[channel] else "0"
        if command.startswith("FUSE?"):
            channel = self._channels(command)[0]
            return "1" if self.ocp[channel] else "0"
        if command == "TRAC:ENAB:GEN?":
            return "1" if self.tracking else "0"
        if command.startswith("TRAC:ENAB:CH"):
            channel = int(command[len("TRAC:ENAB:CH")])
            return "1" if self.channel_tracking[channel] else "0"
        if command.startswith("VOLT:SENS:TRIP?"):
            channel = self._channels(command)[0]
            return "1" if self.remote_sense_tripped[channel] else "0"
        if command.startswith("VOLT:SENS?"):
            channel = self._channels(command)[0]
            return "EXT" if self.remote_sense[channel] else "INT"
        if command.startswith("OUTP:DEL:DUR?"):
            channel = self._channels(command)[0]
            return f"{self.output_delays[channel]:.3f}"
        if command.startswith("OUTP:DEL?"):
            channel = self._channels(command)[0]
            return "1" if self.output_delay_enabled[channel] else "0"
        if command.startswith("OUTP:IMP?"):
            channel = self._channels(command)[0]
            return "1" if self.high_impedance[channel] else "0"
        if command.startswith("OUTP:SRAT?"):
            channel = self._channels(command)[0]
            return "1" if self.slew_rate[channel] else "0"
        if command.startswith("OUTP?"):
            channel = self._channels(command)[0]
            return "1" if self.outputs[channel] else "0"
        if command.startswith("VOLT:RAMP:DUR?"):
            channel = self._channels(command)[0]
            return f"{self.ramp_durations[channel]:.3f}"
        if command.startswith("VOLT:RAMP?"):
            channel = self._channels(command)[0]
            return "1" if self.ramp_enabled[channel] else "0"
        if command.startswith("MEAS:VOLT:MIN?"):
            channel = self._channels(command)[0]
            return f"{self.statistics[channel]['volt_min']:.3f}"
        if command.startswith("MEAS:VOLT:MAX?"):
            channel = self._channels(command)[0]
            return f"{self.statistics[channel]['volt_max']:.3f}"
        if command.startswith("MEAS:VOLT:AVG?"):
            channel = self._channels(command)[0]
            return f"{self.statistics[channel]['volt_avg']:.3f}"
        if command.startswith("MEAS:CURR:MIN?"):
            channel = self._channels(command)[0]
            return f"{self.statistics[channel]['curr_min']:.3f}"
        if command.startswith("MEAS:CURR:MAX?"):
            channel = self._channels(command)[0]
            return f"{self.statistics[channel]['curr_max']:.3f}"
        if command.startswith("MEAS:CURR:AVG?"):
            channel = self._channels(command)[0]
            return f"{self.statistics[channel]['curr_avg']:.3f}"
        if command.startswith("MEAS:POW:MIN?"):
            channel = self._channels(command)[0]
            return f"{self.statistics[channel]['pow_min']:.3f}"
        if command.startswith("MEAS:POW:MAX?"):
            channel = self._channels(command)[0]
            return f"{self.statistics[channel]['pow_max']:.3f}"
        if command.startswith("MEAS:POW:AVG?"):
            channel = self._channels(command)[0]
            return f"{self.statistics[channel]['pow_avg']:.3f}"
        if command.startswith("MEAS:ENER?"):
            channel = self._channels(command)[0]
            return f"{self.statistics[channel]['energy']:.3f}"
        if command.startswith("MEAS:STAT:COUN?"):
            channel = self._channels(command)[0]
            return str(self.statistics[channel]["count"])
        if command.startswith("MEAS:VOLT?"):
            channel = self._channels(command)[0]
            return f"{self.measurements[channel][0]:.3f}"
        if command.startswith("MEAS:CURR?"):
            channel = self._channels(command)[0]
            return f"{self.measurements[channel][1]:.3f}"
        if command.startswith("MEAS:POW?"):
            channel = self._channels(command)[0]
            return f"{self.measurements[channel][2]:.3f}"
        if command.startswith("STAT:QUES:INST:ISUM"):
            number = int(command[len("STAT:QUES:INST:ISUM")])
            mode = self.regulation[number]
            return {"CC": "1", "CV": "2", "OFF": "0"}[mode]
        if command == "SYST:ERR?":
            if self.errors:
                code, message = self.errors.pop(0)
                return f'{code},"{message}"'
            return '0,"No error"'
        if command.startswith("VOLT?"):
            channel = self._channels(command)[0]
            return f"{self.voltages[channel]:.3f}"
        if command.startswith("CURR?"):
            channel = self._channels(command)[0]
            return f"{self.currents[channel]:.3f}"
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


class RohdeSchwarzNGP800PlugTest(unittest.TestCase):
    def test_identify_and_model_detection(self):
        resource = FakeResource(model="NGP824")
        plug = RohdeSchwarzNGP800Plug(resource=resource)

        self.assertEqual(
            plug.identify(),
            "Rohde&Schwarz,NGP824,5601.4007k03/123456,02.025",
        )
        self.assertEqual(plug.model, "NGP824")
        self.assertEqual(plug.channel_count, 4)
        self.assertEqual(
            plug.channels,
            (Channel.CH1, Channel.CH2, Channel.CH3, Channel.CH4),
        )
        self.assertEqual(plug.model_info.name, "NGP824")
        plug.close()

    def test_model_is_queried_once(self):
        resource = FakeResource(model="NGP824")
        plug = RohdeSchwarzNGP800Plug(resource=resource)

        self.assertEqual(plug.model, "NGP824")
        self.assertEqual(plug.model, "NGP824")
        self.assertEqual(resource.commands.count("*IDN?"), 1)
        plug.close()

    def test_model_query_failure_falls_back(self):
        class FailingResource(FakeResource):
            def query(self, command):
                if command == "*IDN?":
                    raise OSError("disconnected")
                return super().query(command)

        plug = RohdeSchwarzNGP800Plug(resource=FailingResource())

        self.assertIsNone(plug.model)
        self.assertIsNone(plug.model_info)
        self.assertEqual(plug.channel_count, 4)
        plug.close()

    def test_select_and_query_channel(self):
        resource = FakeResource()
        plug = RohdeSchwarzNGP800Plug(resource=resource, model="NGP824")

        plug.select_channel(Channel.CH3)
        self.assertEqual(plug.selected_channel(), Channel.CH3)
        self.assertEqual(resource.commands, ["INST:NSEL 3", "INST:NSEL?"])
        plug.close()

    def test_two_channel_model_rejects_ch3(self):
        resource = FakeResource(model="NGP802")
        plug = RohdeSchwarzNGP800Plug(resource=resource)

        self.assertEqual(plug.channel_count, 2)
        with self.assertRaisesRegex(ValueError, "CH3 is not available"):
            plug.set_voltage(1.0, Channel.CH3)
        plug.close()

    def test_set_all_outputs_uses_model_channel_count(self):
        resource = FakeResource(model="NGP802")
        plug = RohdeSchwarzNGP800Plug(resource=resource)

        plug.set_all_outputs(True)

        self.assertEqual(
            resource.commands,
            ["*IDN?", "OUTP 1,(@1)", "OUTP 1,(@2)"],
        )
        plug.close()

    def test_model_specific_setpoint_limits(self):
        resource = FakeResource(model="NGP804")
        plug = RohdeSchwarzNGP800Plug(resource=resource)

        plug.set_voltage(32.05, Channel.CH1)
        plug.set_current(20.01, Channel.CH1)
        with self.assertRaises(ValueError):
            plug.set_voltage(32.06, Channel.CH1)
        with self.assertRaises(ValueError):
            plug.set_current(20.02, Channel.CH1)
        plug.close()

        resource = FakeResource(model="NGP824")
        plug = RohdeSchwarzNGP800Plug(resource=resource)
        plug.set_voltage(64.05, Channel.CH4)
        plug.set_current(10.01, Channel.CH4)
        with self.assertRaises(ValueError):
            plug.set_voltage(64.06, Channel.CH4)
        with self.assertRaises(ValueError):
            plug.set_current(10.02, Channel.CH4)
        plug.close()

    def test_ngp814_mixed_channel_limits(self):
        resource = FakeResource(model="NGP814")
        plug = RohdeSchwarzNGP800Plug(resource=resource)

        plug.set_voltage(32.05, Channel.CH1)
        plug.set_current(20.01, Channel.CH2)
        plug.set_voltage(64.05, Channel.CH3)
        plug.set_current(10.01, Channel.CH4)
        with self.assertRaises(ValueError):
            plug.set_voltage(64.05, Channel.CH1)
        with self.assertRaises(ValueError):
            plug.set_current(20.02, Channel.CH1)
        with self.assertRaises(ValueError):
            plug.set_voltage(64.06, Channel.CH3)
        with self.assertRaises(ValueError):
            plug.set_current(20.01, Channel.CH3)
        plug.close()

    def test_setpoints_and_readback(self):
        resource = FakeResource()
        plug = RohdeSchwarzNGP800Plug(resource=resource, model="NGP824")

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
                "VOLT 5,(@1)",
                "CURR 1,(@1)",
                "VOLT 12,(@2)",
                "CURR 2,(@2)",
                "VOLT? (@1)",
                "CURR? (@1)",
                "VOLT? (@2)",
                "CURR? (@2)",
            ],
        )
        plug.close()

    def test_apply_changes_selected_channel(self):
        resource = FakeResource()
        plug = RohdeSchwarzNGP800Plug(resource=resource, model="NGP824")

        plug.apply(5, 1, Channel.CH2)

        self.assertEqual(resource.commands, ["INST:NSEL 2", "APPL 5,1"])
        self.assertEqual(resource.voltages[2], 5.0)
        self.assertEqual(resource.currents[2], 1.0)
        plug.close()

    def test_output_control_and_readback(self):
        resource = FakeResource()
        plug = RohdeSchwarzNGP800Plug(resource=resource, model="NGP824")

        plug.enable_output("CH1")
        plug.enable_output("CH2")
        self.assertTrue(plug.output_enabled("CH1"))
        self.assertTrue(plug.output_enabled("CH2"))
        plug.disable_output("CH1")
        self.assertFalse(plug.output_enabled("CH1"))
        plug.set_all_outputs(True)
        self.assertTrue(all(resource.outputs.values()))
        plug.set_all_outputs(False)
        self.assertFalse(any(resource.outputs.values()))
        self.assertEqual(
            [command for command in resource.commands if command.startswith("OUTP ")],
            [
                "OUTP 1,(@1)",
                "OUTP 1,(@2)",
                "OUTP 0,(@1)",
                "OUTP 1,(@1)",
                "OUTP 1,(@2)",
                "OUTP 1,(@3)",
                "OUTP 1,(@4)",
                "OUTP 0,(@1)",
                "OUTP 0,(@2)",
                "OUTP 0,(@3)",
                "OUTP 0,(@4)",
            ],
        )
        plug.close()

    def test_measurements(self):
        resource = FakeResource()
        plug = RohdeSchwarzNGP800Plug(resource=resource, model="NGP824")

        self.assertEqual(
            plug.measure("CH2"),
            SupplyMeasurement(voltage=2.0, current=0.2, power=0.4),
        )
        self.assertEqual(
            resource.commands,
            [
                "MEAS:VOLT? (@2)",
                "MEAS:CURR? (@2)",
                "MEAS:POW? (@2)",
            ],
        )
        resource.commands.clear()
        self.assertEqual(
            plug.measure_all(),
            {
                Channel.CH1: SupplyMeasurement(1.0, 0.1, 0.1),
                Channel.CH2: SupplyMeasurement(2.0, 0.2, 0.4),
                Channel.CH3: SupplyMeasurement(3.0, 0.3, 0.9),
                Channel.CH4: SupplyMeasurement(4.0, 0.4, 1.6),
            },
        )
        plug.close()

    def test_read_uses_selected_channel(self):
        resource = FakeResource()
        plug = RohdeSchwarzNGP800Plug(resource=resource, model="NGP824")

        self.assertEqual(plug.read(Channel.CH3), Readback(voltage=3.0, current=0.3))
        self.assertEqual(resource.commands, ["INST:NSEL 3", "READ?"])
        plug.close()

    def test_regulation_mode(self):
        resource = FakeResource()
        resource.regulation = {1: "CV", 2: "CC", 3: "OFF", 4: "CV"}
        plug = RohdeSchwarzNGP800Plug(resource=resource, model="NGP824")

        self.assertEqual(plug.regulation_mode("CH1"), RegulationMode.CV)
        self.assertEqual(plug.regulation_mode("CH2"), RegulationMode.CC)
        self.assertEqual(plug.regulation_mode("CH3"), RegulationMode.OFF)
        plug.close()

    def test_ocp_control(self):
        resource = FakeResource()
        plug = RohdeSchwarzNGP800Plug(resource=resource, model="NGP824")

        plug.set_ocp(True, "CH1")
        self.assertTrue(plug.ocp_enabled("CH1"))
        self.assertFalse(plug.ocp_tripped("CH1"))
        resource.ocp_tripped[1] = True
        self.assertTrue(plug.ocp_tripped("CH1"))
        plug.clear_ocp("CH1")
        self.assertFalse(plug.ocp_tripped("CH1"))
        plug.set_ocp_blowing_delay(0.05, "CH1")
        plug.set_ocp_initial_delay(0.1, "CH1")
        self.assertEqual(plug.ocp_blowing_delay("CH1"), 0.05)
        self.assertEqual(plug.ocp_initial_delay("CH1"), 0.1)
        self.assertEqual(
            resource.commands,
            [
                "FUSE 1,(@1)",
                "FUSE? (@1)",
                "FUSE:TRIP? (@1)",
                "FUSE:TRIP? (@1)",
                "FUSE:TRIP:CLE (@1)",
                "FUSE:TRIP? (@1)",
                "FUSE:DEL 0.05,(@1)",
                "FUSE:DEL:INIT 0.1,(@1)",
                "FUSE:DEL? (@1)",
                "FUSE:DEL:INIT? (@1)",
            ],
        )
        plug.close()

    def test_ovp_and_opp_control(self):
        resource = FakeResource()
        plug = RohdeSchwarzNGP800Plug(resource=resource, model="NGP824")

        plug.set_ovp(True, "CH1")
        plug.set_ovp_level(30.0, "CH1")
        self.assertTrue(plug.ovp_enabled("CH1"))
        self.assertEqual(plug.ovp_level("CH1"), 30.0)
        resource.ovp_tripped[1] = True
        self.assertTrue(plug.ovp_tripped("CH1"))
        plug.clear_ovp("CH1")
        self.assertFalse(plug.ovp_tripped("CH1"))

        plug.set_opp(True, "CH1")
        plug.set_opp_level(150.0, "CH1")
        self.assertTrue(plug.opp_enabled("CH1"))
        self.assertEqual(plug.opp_level("CH1"), 150.0)
        resource.opp_tripped[1] = True
        self.assertTrue(plug.opp_tripped("CH1"))
        plug.clear_opp("CH1")
        self.assertFalse(plug.opp_tripped("CH1"))

        resource.ocp_tripped[1] = True
        resource.ovp_tripped[1] = True
        resource.opp_tripped[1] = True
        plug.clear_protection("CH1")
        self.assertFalse(resource.ocp_tripped[1])
        self.assertFalse(resource.ovp_tripped[1])
        self.assertFalse(resource.opp_tripped[1])
        plug.close()

    def test_safety_limits(self):
        resource = FakeResource()
        plug = RohdeSchwarzNGP800Plug(resource=resource, model="NGP824")

        plug.set_safety_limits_enabled(True, "CH1")
        self.assertTrue(plug.safety_limits_enabled("CH1"))
        plug.set_voltage_safety_limits(1.0, 12.0, "CH1")
        plug.set_current_safety_limits(0.1, 2.0, "CH1")
        self.assertEqual(plug.voltage_safety_limits("CH1"), (1.0, 12.0))
        self.assertEqual(plug.current_safety_limits("CH1"), (0.1, 2.0))
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            plug.set_voltage_safety_limits(12.0, 1.0, "CH1")
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            plug.set_current_safety_limits(2.0, 0.1, "CH1")
        plug.close()

    def test_safety_limit_ordering_sets_upper_first(self):
        resource = FakeResource()
        plug = RohdeSchwarzNGP800Plug(resource=resource, model="NGP824")

        plug.set_voltage_safety_limits(1.0, 10.0, "CH1")
        plug.set_current_safety_limits(0.1, 2.0, "CH1")

        self.assertEqual(
            resource.commands,
            [
                "VOLT:ALIM:UPP 64.05,(@1)",
                "VOLT:ALIM:LOW 1,(@1)",
                "VOLT:ALIM:UPP 10,(@1)",
                "CURR:ALIM:UPP 10.01,(@1)",
                "CURR:ALIM:LOW 0.1,(@1)",
                "CURR:ALIM:UPP 2,(@1)",
            ],
        )
        plug.close()

    def test_tracking(self):
        resource = FakeResource()
        plug = RohdeSchwarzNGP800Plug(resource=resource, model="NGP824")

        self.assertFalse(plug.tracking_enabled())
        plug.set_tracking(True)
        self.assertTrue(plug.tracking_enabled())
        plug.set_channel_tracking("CH2", True)
        self.assertTrue(plug.channel_tracking("CH2"))
        plug.set_channel_tracking("CH2", False)
        self.assertFalse(plug.channel_tracking("CH2"))
        self.assertEqual(
            resource.commands,
            [
                "TRAC:ENAB:GEN?",
                "TRAC:ENAB:GEN 1",
                "TRAC:ENAB:GEN?",
                "TRAC:ENAB:CH2 1",
                "TRAC:ENAB:CH2?",
                "TRAC:ENAB:CH2 0",
                "TRAC:ENAB:CH2?",
            ],
        )
        plug.close()

    def test_output_settings(self):
        resource = FakeResource()
        plug = RohdeSchwarzNGP800Plug(resource=resource, model="NGP824")

        plug.set_remote_sense(True, "CH1")
        self.assertTrue(plug.remote_sense("CH1"))
        resource.remote_sense_tripped[1] = True
        self.assertTrue(plug.remote_sense_tripped("CH1"))
        plug.clear_remote_sense_trip("CH1")
        self.assertFalse(plug.remote_sense_tripped("CH1"))

        plug.set_output_delay_enabled(True, "CH1")
        plug.set_output_delay(0.5, "CH1")
        self.assertTrue(plug.output_delay_enabled("CH1"))
        self.assertEqual(plug.output_delay("CH1"), 0.5)

        plug.set_high_impedance(True, "CH1")
        self.assertTrue(plug.high_impedance("CH1"))
        plug.set_slew_rate_reduction(True, "CH1")
        self.assertTrue(plug.slew_rate_reduction("CH1"))

        plug.set_ramp(True, 2.5, "CH1")
        self.assertTrue(plug.ramp_enabled("CH1"))
        self.assertEqual(plug.ramp_duration("CH1"), 2.5)
        plug.close()

    def test_statistics(self):
        resource = FakeResource()
        plug = RohdeSchwarzNGP800Plug(resource=resource, model="NGP824")

        self.assertEqual(
            plug.measure_statistics("CH1"),
            StatisticsMeasurement(
                voltage_min=0.9,
                voltage_max=1.1,
                voltage_avg=1.0,
                current_min=0.09,
                current_max=0.11,
                current_avg=0.1,
                power_min=0.09,
                power_max=0.11,
                power_avg=0.1,
                energy=5.0,
                sample_count=10,
            ),
        )
        self.assertEqual(plug.energy("CH1"), 5.0)
        self.assertEqual(plug.sample_count("CH1"), 10)
        plug.reset_statistics("CH1")
        self.assertEqual(plug.energy("CH1"), 0.0)
        self.assertEqual(plug.sample_count("CH1"), 0)
        plug.close()

    def test_error_queue(self):
        resource = FakeResource()
        resource.errors = [(-113, "Undefined header"), (-222, "Data out of range")]
        plug = RohdeSchwarzNGP800Plug(resource=resource, model="NGP824")

        self.assertEqual(
            plug.next_error(),
            ScpiError(code=-113, message="Undefined header"),
        )
        self.assertEqual(
            plug.check_errors(),
            [ScpiError(code=-222, message="Data out of range")],
        )
        self.assertIsNone(plug.next_error())
        plug.close()

    def test_reset_and_clear_status(self):
        resource = FakeResource()
        plug = RohdeSchwarzNGP800Plug(resource=resource, model="NGP824")

        plug.set_voltage(5, "CH1")
        plug.reset()
        self.assertEqual(resource.voltages[1], 0.0)
        resource.errors = [(1, "error")]
        plug.clear_status()
        self.assertEqual(resource.errors, [])
        self.assertEqual(resource.commands[-2:], ["*RST", "*CLS"])
        plug.close()

    def test_setpoint_and_channel_validation(self):
        resource = FakeResource()
        plug = RohdeSchwarzNGP800Plug(resource=resource, model="NGP824")

        for value in (-0.1, 64.06, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                plug.set_voltage(value)
        for value in (0.0, 10.02, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                plug.set_current(value)
        with self.assertRaises(TypeError):
            plug.set_voltage(True)
        with self.assertRaises(TypeError):
            plug.set_current(True)
        with self.assertRaises(ValueError):
            plug.set_output(True, "CH5")
        with self.assertRaises(TypeError):
            plug.set_output(cast(bool, 1))
        with self.assertRaises(ValueError):
            plug.set_ovp_level(64.06)
        with self.assertRaises(ValueError):
            plug.set_opp_level(0.5)
        with self.assertRaises(ValueError):
            plug.set_output_delay(10.01)
        with self.assertRaises(ValueError):
            plug.set_ramp_duration(60.01)
        self.assertFalse(
            any(
                command.startswith(("VOLT ", "CURR ", "OUTP "))
                for command in resource.commands
            )
        )
        plug.close()

    def test_resource_manager_configuration(self):
        resource = FakeResource()
        manager = FakeResourceManager(resource)
        plug = RohdeSchwarzNGP800Plug(
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
                return "Rohde&Schwarz,NGP824,123,1.0\n"

            def close(self):
                pass

        resource = WriteReadResource()
        plug = RohdeSchwarzNGP800Plug(resource=resource)

        self.assertEqual(plug.identify(), "Rohde&Schwarz,NGP824,123,1.0")
        self.assertEqual(resource.commands, ["*IDN?"])
        plug.close()

    def test_query_decodes_bytes_responses(self):
        class BytesResource:
            def write(self, command):
                pass

            def read(self):
                return b"Rohde&Schwarz,NGP824,123,1.0\n"

            def close(self):
                pass

        plug = RohdeSchwarzNGP800Plug(resource=BytesResource())

        self.assertEqual(plug.identify(), "Rohde&Schwarz,NGP824,123,1.0")
        plug.close()

    def test_teardown_switches_off_outputs_and_closes(self):
        resource = FakeResource()
        plug = RohdeSchwarzNGP800Plug(resource=resource, model="NGP824")

        plug.tearDown()

        self.assertEqual(
            resource.commands,
            [
                "OUTP 0,(@1)",
                "OUTP 0,(@2)",
                "OUTP 0,(@3)",
                "OUTP 0,(@4)",
            ],
        )
        self.assertTrue(resource.closed)

    def test_resource_is_required_without_environment_or_injection(self):
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            self.assertRaisesRegex(ValueError, "resource_name or host"),
        ):
            RohdeSchwarzNGP800Plug()

    def test_host_and_resource_name_are_mutually_exclusive(self):
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            RohdeSchwarzNGP800Plug("TCPIP::192.0.2.10::INSTR", host="192.0.2.10")

    def test_unsupported_model_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported model"):
            RohdeSchwarzNGP800Plug(resource=FakeResource(), model="NGP999")

    def test_invalid_responses_raise(self):
        class InvalidResource(FakeResource):
            def query(self, command):
                self.commands.append(command)
                if command == "VOLT? (@1)":
                    return "not-a-number"
                if command == "OUTP? (@1)":
                    return "maybe"
                if command == "SYST:ERR?":
                    return "oops"
                return super().query(command)

        plug = RohdeSchwarzNGP800Plug(resource=InvalidResource(), model="NGP824")
        with self.assertRaisesRegex(ValueError, "invalid voltage"):
            plug.voltage("CH1")
        with self.assertRaisesRegex(ValueError, "invalid output state"):
            plug.output_enabled("CH1")
        with self.assertRaisesRegex(ValueError, "invalid error queue"):
            plug.next_error()
        plug.close()


class SocketResourceTest(unittest.TestCase):
    class FakeSocket:
        def __init__(self, chunks):
            self.chunks = list(chunks)
            self.sent = []
            self.timeouts = []
            self.closed = False

        def sendall(self, payload):
            self.sent.append(payload)

        def recv(self, size):
            if self.chunks:
                return self.chunks.pop(0)
            return b""

        def settimeout(self, timeout):
            self.timeouts.append(timeout)

        def close(self):
            self.closed = True

    def test_write_query_and_partial_reads(self):
        fake_socket = self.FakeSocket([b"Rohde&Schwarz,NGP", b"824,123,1.0\n"])
        with mock.patch(
            "rohde_schwarz_ngp800.plug.socket.create_connection",
            return_value=fake_socket,
        ) as create_connection:
            resource = _SocketResource("192.0.2.10", 5025, 1000)

        create_connection.assert_called_once_with(("192.0.2.10", 5025), timeout=1.0)
        self.assertEqual(resource.resource_name, "TCPIP::192.0.2.10::5025::SOCKET")
        self.assertEqual(resource.query("*IDN?"), "Rohde&Schwarz,NGP824,123,1.0\n")
        self.assertEqual(fake_socket.sent, [b"*IDN?\n"])
        resource.close()
        self.assertTrue(fake_socket.closed)

    def test_host_constructor_uses_socket_resource(self):
        fake_socket = self.FakeSocket([b"1\n"])
        with mock.patch(
            "rohde_schwarz_ngp800.plug.socket.create_connection",
            return_value=fake_socket,
        ):
            plug = RohdeSchwarzNGP800Plug(host="192.0.2.10", model="NGP824")

        self.assertEqual(plug.resource_name, "TCPIP::192.0.2.10::5025::SOCKET")
        self.assertEqual(plug.query("INST:NSEL?"), "1")
        plug.close()
        self.assertTrue(fake_socket.closed)

    def test_closed_connection_raises(self):
        fake_socket = self.FakeSocket([])
        with mock.patch(
            "rohde_schwarz_ngp800.plug.socket.create_connection",
            return_value=fake_socket,
        ):
            resource = _SocketResource("192.0.2.10")

        with self.assertRaisesRegex(ConnectionError, "closed"):
            resource.read()
        resource.close()


if __name__ == "__main__":
    unittest.main()
