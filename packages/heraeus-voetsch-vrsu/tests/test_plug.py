from __future__ import annotations

import datetime as dt
import os
import unittest
from unittest import mock

from heraeus_voetsch_vrsu import (
    HeraeusVoetschVRSUPlug,
    VrsuChannel,
    VrsuDeviceError,
    VrsuErrorCode,
    VrsuProtocolError,
    VrsuTimeoutError,
    VrsuUnsupportedOperationError,
    parse_loop_table,
    parse_power_failure,
    parse_program_lines,
    parse_program_status,
    parse_setpoints,
    parse_status,
)

STATUS = "U1 = 1111 mV U2 = 2222 mV E = 11111011 U3 = 3000 mV T20.05.15 D11.03.87 ER.0"
STATUS_ERROR = (
    "U1 = 1111 mV U2 = 2222 mV E = 11111011 U3 = 3000 mV T20.05.15 D11.03.87 ER.5"
)
SETPOINTS = "U1 = 1234 mV U2 = 2345 mV R = 10001001 U3 = 3000 mV T20.05.15 D11.03.87"
POWER_FAILURE = (
    "U1 = 1111 mV U2 = 2222 mV E = 11111111 U3 = 3000 mV T20.05.15 D11.03.87 ER.0"
)
PROGRAM_LINES = "01 W 0004 1000 2000 3000 00000011  02  0004 2000 2000 3000 00000011  "
LOOP_TABLE = "1 02 01 02 2 04 01 02 3 00 00 00 "
PROGRAM_STATUS = "00 00 00 05 0012 0 1 "


class FakeVrsuResource:
    """A small stateful fake of the VRSU transport.

    ``responses`` are returned in order by ``read()``.  When ``echo`` is true,
    every command is echoed before the next response, matching the VRSU's
    default RS-232 behaviour.
    """

    def __init__(self, *, echo=False, responses=None):
        self.commands = []
        self.responses = list(responses or [])
        self.echo = echo
        self.closed = False
        self.timeout = None
        self.read_termination = None
        self.write_termination = None
        self.baud_rate = None
        self.data_bits = None
        self.parity = None
        self.stop_bits = None
        self.flow_control = None
        self.xonxoff = None
        self.rtscts = None
        self.dsrdtr = None
        self._buffer = []

    def write(self, command):
        self.commands.append(command)
        if self.echo:
            self._buffer.append(command)
        if command == "H0":
            self.echo = False
        elif command == "H1":
            self.echo = True

    def read(self):
        if self._buffer:
            return self._buffer.pop(0)
        if self.responses:
            return self.responses.pop(0)
        raise TimeoutError("no response")

    def close(self):
        self.closed = True


def make_plug(resource, *, interface="gpib", **kwargs):
    return HeraeusVoetschVRSUPlug(resource=resource, interface=interface, **kwargs)


class ParserTest(unittest.TestCase):
    def test_channel_coercion(self):
        self.assertEqual(VrsuChannel.coerce("2"), VrsuChannel.CHANNEL_2)
        self.assertEqual(VrsuChannel.coerce(3), VrsuChannel.CHANNEL_3)
        for value in (0, 4, 1.5, "x"):
            with self.assertRaises(ValueError):
                VrsuChannel.coerce(value)
        with self.assertRaises(TypeError):
            VrsuChannel.coerce(True)

    def test_parse_status(self):
        status = parse_status(STATUS)
        self.assertEqual(status.channel_1_mv, 1111)
        self.assertEqual(status.channel_2_mv, 2222)
        self.assertEqual(status.channel_3_mv, 3000)
        self.assertEqual(status.digital_input_bits, "11111011")
        self.assertEqual(status.digital_inputs, 0b11111011)
        self.assertEqual(status.clock.time, dt.time(20, 5, 15))
        self.assertEqual(status.clock.date(1900), dt.date(1987, 3, 11))
        self.assertEqual(status.clock.date(), dt.date(2087, 3, 11))
        self.assertEqual(status.error, VrsuErrorCode.NO_ERROR)
        self.assertAlmostEqual(status.temperature, 111.1)
        self.assertAlmostEqual(status.humidity, 222.2)
        self.assertFalse(status.digital_input(2))
        self.assertTrue(status.digital_input(3))

    def test_parse_setpoints(self):
        setpoints = parse_setpoints(SETPOINTS)
        self.assertEqual(setpoints.channel_mv(1), 1234)
        self.assertEqual(setpoints.channel_mv(VrsuChannel.CHANNEL_2), 2345)
        self.assertEqual(setpoints.channel_mv(3), 3000)
        self.assertEqual(setpoints.relays, 0b10001001)
        self.assertEqual(
            setpoints.relay_states(),
            (True, False, False, True, False, False, False, True),
        )
        self.assertTrue(setpoints.relay(1))
        self.assertFalse(setpoints.relay(2))
        self.assertTrue(setpoints.relay(8))

    def test_parse_power_failure(self):
        failure = parse_power_failure(POWER_FAILURE)
        self.assertEqual(failure.channel_1_mv, 1111)
        self.assertEqual(failure.relays, 0xFF)
        self.assertEqual(failure.error, VrsuErrorCode.NO_ERROR)
        self.assertTrue(all(failure.relay_states()))

    def test_parse_program_lines(self):
        lines = parse_program_lines(PROGRAM_LINES)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0].number, 1)
        self.assertTrue(lines[0].wait_for_actual)
        self.assertEqual(lines[0].time_minutes, 4)
        self.assertEqual(lines[0].channel_1_mv, 1000)
        self.assertEqual(lines[0].channel_2_mv, 2000)
        self.assertEqual(lines[0].channel_3_mv, 3000)
        self.assertEqual(lines[0].relays, 0b11)
        self.assertEqual(lines[1].number, 2)
        self.assertFalse(lines[1].wait_for_actual)
        self.assertEqual(lines[1].channel_1_mv, 2000)

    def test_parse_loop_table(self):
        loops = parse_loop_table(LOOP_TABLE)
        self.assertEqual(len(loops), 3)
        self.assertEqual(loops[0].number, 1)
        self.assertEqual(loops[0].start_line, 1)
        self.assertEqual(loops[0].end_line, 2)
        self.assertEqual(loops[0].count, 2)
        self.assertEqual(loops[1].start_line, 1)
        self.assertEqual(loops[1].end_line, 4)

    def test_parse_program_status(self):
        status = parse_program_status(PROGRAM_STATUS)
        self.assertEqual(status.loop_remaining, (0, 0, 0))
        self.assertEqual(status.current_line, 5)
        self.assertEqual(status.remaining_minutes, 12)
        self.assertFalse(status.waiting)
        self.assertTrue(status.running)

    def test_invalid_reply_raises_protocol_error(self):
        with self.assertRaises(VrsuProtocolError):
            parse_status("not a VRSU reply")


class PlugTest(unittest.TestCase):
    def test_serial_status_consumes_auto_echo(self):
        resource = FakeVrsuResource(echo=True, responses=[STATUS])
        plug = make_plug(resource, interface="serial")

        status = plug.status()

        self.assertEqual(resource.commands, ["ST"])
        self.assertEqual(status.channel_1_mv, 1111)
        self.assertEqual(resource._buffer, [])
        plug.close()

    def test_serial_status_with_auto_echo_disabled(self):
        resource = FakeVrsuResource(echo=False, responses=[STATUS])
        plug = make_plug(resource, interface="serial", auto_echo=False)

        status = plug.status()

        self.assertEqual(resource.commands, ["ST"])
        self.assertEqual(status.channel_1_mv, 1111)
        plug.close()

    def test_gpib_status_does_not_send_st(self):
        resource = FakeVrsuResource(echo=False, responses=[STATUS])
        plug = make_plug(resource, interface="gpib")

        status = plug.status()

        self.assertEqual(resource.commands, [])
        self.assertEqual(status.channel_1_mv, 1111)
        plug.close()

    def test_set_voltage_and_scaled_values(self):
        resource = FakeVrsuResource()
        plug = make_plug(resource)

        plug.set_voltage(1, 1234)
        plug.set_voltage(VrsuChannel.CHANNEL_2, 0)
        plug.set_temperature(85.0)
        plug.set_humidity(50.0)

        self.assertEqual(
            resource.commands,
            ["U11234", "U20000", "U10850", "U20500"],
        )
        plug.close()

    def test_set_voltage_limits_and_test_mode(self):
        resource = FakeVrsuResource()
        plug = make_plug(resource)

        with self.assertRaises(ValueError):
            plug.set_voltage(1, 3001)
        plug.set_test_mode(True)
        plug.set_voltage(1, 4095)
        self.assertEqual(resource.commands, ["T1", "U14095"])
        plug.close()

    def test_humidity_range(self):
        plug = make_plug(FakeVrsuResource())
        with self.assertRaises(ValueError):
            plug.set_humidity(101)
        plug.close()

    def test_relay_commands(self):
        resource = FakeVrsuResource()
        plug = make_plug(resource)

        plug.set_relays(0b10001001)
        plug.set_relays([True, False, False, True, False, False, False, True])
        plug.set_relays(["1", "0", "0", "1", "0", "0", "0", "1"])
        plug.set_relays("10001001")

        self.assertEqual(
            resource.commands,
            ["R10001001", "R10001001", "R10001001", "R10001001"],
        )
        plug.close()

    def test_set_relay_reads_and_preserves_other_relays(self):
        resource = FakeVrsuResource(responses=[SETPOINTS, SETPOINTS])
        plug = make_plug(resource)

        plug.set_relay(2, True)
        plug.set_relay(1, False)

        self.assertEqual(resource.commands, ["LL", "R10001011", "LL", "R10001000"])
        plug.close()

    def test_clock_command(self):
        resource = FakeVrsuResource()
        plug = make_plug(resource)

        plug.set_clock(dt.datetime(2026, 3, 11, 20, 5, 15))

        self.assertEqual(resource.commands, ["K200515110326"])
        plug.close()

    def test_configuration_commands(self):
        resource = FakeVrsuResource()
        plug = make_plug(resource)

        plug.set_averaging(False)
        plug.set_delta(100)
        plug.set_power_failure_window(3)
        plug.set_accelerated_test(True)
        plug.set_test_mode(True)

        self.assertEqual(
            resource.commands,
            ["NAV", "U40100", "NZ0003", "W1", "T1"],
        )
        plug.close()

    def test_program_commands(self):
        resource = FakeVrsuResource()
        plug = make_plug(resource)

        plug.initialize_program()
        plug.write_program_line(1, 0, 0, 0, 0, 1)
        plug.write_program_line(2, 4, 1000, 2000, 3000, 0b11, wait_for_actual=True)
        plug.set_loop(1, 1, 2, 2)
        plug.start_program(60)
        plug.pause_program()
        plug.continue_program()
        plug.stop_program()

        self.assertEqual(
            resource.commands,
            [
                "CI",
                "CP 01   0000 0000 0000 0000 00000001",
                "CP 02 W 0004 1000 2000 3000 00000011",
                "CC 1 02 01 02",
                "CS 0060",
                "E",
                "I",
                "B",
            ],
        )
        plug.close()

    def test_talk_calls_use_st_in_serial_mode(self):
        resource = FakeVrsuResource(
            echo=True,
            responses=[
                SETPOINTS,
                POWER_FAILURE,
                PROGRAM_LINES,
                LOOP_TABLE,
                PROGRAM_STATUS,
            ],
        )
        plug = make_plug(resource, interface="serial")

        plug.setpoints()
        plug.power_failure()
        plug.program_lines(1, 2)
        plug.loop_table()
        plug.program_status()

        self.assertEqual(
            resource.commands,
            ["LL", "ST", "PF", "ST", "LP 01 02", "ST", "LC", "ST", "LS", "ST"],
        )
        plug.close()

    def test_check_error_raises_device_error(self):
        resource = FakeVrsuResource(responses=[STATUS_ERROR])
        plug = make_plug(resource)

        with self.assertRaises(VrsuDeviceError) as caught:
            plug.check_error()

        self.assertEqual(caught.exception.code, VrsuErrorCode.ILLEGAL_SETPOINT)
        plug.close()

    def test_set_auto_echo(self):
        resource = FakeVrsuResource(echo=True)
        plug = make_plug(resource, interface="serial")

        plug.set_auto_echo(False)
        self.assertFalse(plug.auto_echo)
        plug.set_auto_echo(True)
        self.assertTrue(plug.auto_echo)

        self.assertEqual(resource.commands, ["H0", "H1"])
        plug.close()

    def test_set_auto_echo_rejected_on_gpib(self):
        plug = make_plug(FakeVrsuResource(), interface="gpib")
        with self.assertRaises(VrsuUnsupportedOperationError):
            plug.set_auto_echo(False)
        plug.close()

    def test_set_delimiter_updates_read_termination(self):
        resource = FakeVrsuResource(echo=True)
        plug = make_plug(resource, interface="serial")

        plug.set_delimiter(1)

        self.assertEqual(resource.commands, ["D1"])
        self.assertEqual(plug.delimiter, 1)
        self.assertEqual(resource.read_termination, "\r")
        plug.close()

    def test_wait_for_temperature(self):
        cold = (
            "U1 = 0200 mV U2 = 0500 mV E = 00000000 "
            "U3 = 0000 mV T20.05.15 D11.03.87 ER.0"
        )
        hot = (
            "U1 = 0850 mV U2 = 0500 mV E = 00000000 "
            "U3 = 0000 mV T20.05.15 D11.03.87 ER.0"
        )
        resource = FakeVrsuResource(responses=[cold, hot])
        plug = make_plug(resource, interface="serial", auto_echo=False)

        with mock.patch("heraeus_voetsch_vrsu.plug.time.sleep"):
            value = plug.wait_for_temperature(85.0, tolerance=0.5, timeout=10)

        self.assertEqual(value, 85.0)
        self.assertEqual(resource.commands, ["ST", "ST"])
        plug.close()

    def test_wait_for_temperature_times_out(self):
        cold = (
            "U1 = 0200 mV U2 = 0500 mV E = 00000000 "
            "U3 = 0000 mV T20.05.15 D11.03.87 ER.0"
        )
        resource = FakeVrsuResource(responses=[cold, cold])
        plug = make_plug(resource)

        with (
            mock.patch("heraeus_voetsch_vrsu.plug.time.sleep"),
            self.assertRaises(VrsuTimeoutError),
        ):
            plug.wait_for_temperature(85.0, tolerance=0.5, timeout=0.001)
        plug.close()

    def test_teardown_closes_resource(self):
        resource = FakeVrsuResource()
        plug = make_plug(resource)

        plug.tearDown()

        self.assertTrue(resource.closed)

    def test_resource_is_required(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            self.assertRaisesRegex(ValueError, "resource_name or serial_port"),
        ):
            HeraeusVoetschVRSUPlug()

    def test_serial_port_builds_asrl_resource(self):
        resource = FakeVrsuResource()
        manager = mock.Mock()
        manager.open_resource.return_value = resource

        with mock.patch(
            "heraeus_voetsch_vrsu.plug.pyvisa.ResourceManager",
            return_value=manager,
        ):
            plug = HeraeusVoetschVRSUPlug(serial_port="/dev/ttyUSB0")

        manager.open_resource.assert_called_once_with("ASRL/dev/ttyUSB0::INSTR")
        self.assertEqual(plug.interface, "serial")
        self.assertEqual(resource.baud_rate, 9600)
        self.assertEqual(resource.data_bits, 8)
        plug.close()
        self.assertTrue(resource.closed)
        manager.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
