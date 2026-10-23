from __future__ import annotations

import unittest

from adcmt_624x import (
    ADCMT624XPlug,
    CommandError,
    CurrentSourceRange,
    IntegrationTime,
    MeasurementFunction,
    SourceFunction,
    VoltageSourceRange,
    parse_measurement,
    parse_measurements,
)


class FakeTransport:
    def __init__(self, responses=None, reads=None):
        self.commands = []
        self.responses = responses or {}
        self.reads = list(reads or [])
        self.closed = False

    def write(self, command):
        self.commands.append(("write", command))

    def read(self, size=None):
        self.commands.append(("read", size))
        if self.reads:
            return self.reads.pop(0)
        return b"DI +1.00000E-03\r\n"

    def query(self, command, *, size=None):
        self.commands.append(("query", command, size))
        response = self.responses.get(command, "0")
        if callable(response):
            response = response()
        if isinstance(response, str):
            response = response.encode("ascii")
        return response

    def close(self):
        self.closed = True


class ADCMT624XPlugTest(unittest.TestCase):
    def create_plug(self, **kwargs):
        transport = kwargs.pop("transport", None) or FakeTransport()
        return ADCMT624XPlug(
            transport=transport,
            command_delay_s=0,
            **kwargs,
        ), transport

    def test_identify_and_model(self):
        plug, transport = self.create_plug(
            transport=FakeTransport({"*IDN?": "ADC Corp.,6241A,123456789,C00"})
        )

        self.assertEqual(plug.identify(), "ADC Corp.,6241A,123456789,C00")
        self.assertEqual(plug.model, "6241A")
        self.assertEqual(transport.commands, [("query", "*IDN?", 50)])

    def test_measure_configures_source_and_returns_value(self):
        plug, transport = self.create_plug(
            transport=FakeTransport(reads=[b"DI +1.00000E-03\r\n"])
        )

        value = plug.measure(
            source_function=SourceFunction.VOLTAGE,
            source_value=1.0,
            limiter=0.003,
            function=MeasurementFunction.DC_CURRENT,
            integration_time=IntegrationTime.T1PLC,
            auto_zero=True,
            standby=True,
        )

        self.assertEqual(value, 0.001)
        self.assertEqual(
            transport.commands,
            [
                ("write", "VF"),
                ("write", "SOV1"),
                ("write", "LMI0.003"),
                ("write", "F2"),
                ("write", "IT5"),
                ("write", "AZ1"),
                ("write", "M1"),
                ("write", "OPR"),
                ("write", "*TRG"),
                ("read", 50),
                ("write", "SBY"),
            ],
        )

    def test_source_and_measurement_configuration(self):
        plug, transport = self.create_plug()

        plug.set_source_function("voltage")
        plug.set_source_value(2.5)
        plug.set_limiter(0.03, low=-0.02)
        plug.set_source_range(VoltageSourceRange.RANGE_3V)
        plug.set_measurement_function("DCI")
        plug.set_measurement_range("AUTO")
        plug.set_integration_time("1PLC")
        plug.set_auto_zero(True)
        plug.set_remote_sensing("4W")
        plug.set_display_digits("5.5")

        self.assertEqual(
            transport.commands,
            [
                ("write", "VF"),
                ("write", "SOV2.5"),
                ("write", "LMI0.03,-0.02"),
                ("write", "SVR4"),
                ("write", "F2"),
                ("write", "R0"),
                ("write", "IT5"),
                ("write", "AZ1"),
                ("write", "RS1"),
                ("write", "RE5"),
            ],
        )

    def test_source_value_requires_source_function(self):
        plug, _ = self.create_plug()

        with self.assertRaisesRegex(RuntimeError, "source function"):
            plug.set_source_value(1.0)

    def test_current_source_uses_current_commands(self):
        plug, transport = self.create_plug()

        plug.set_source_function(SourceFunction.CURRENT)
        plug.set_source_value(0.002)
        plug.set_limiter(3.0)
        plug.set_pulse_base(0.001)

        self.assertEqual(
            transport.commands,
            [
                ("write", "IF"),
                ("write", "SOI0.002"),
                ("write", "LMV3"),
                ("write", "DBI0.001"),
            ],
        )

    def test_source_range_accepts_query_response_aliases(self):
        plug, transport = self.create_plug()

        plug.set_source_function(SourceFunction.VOLTAGE)
        plug.set_source_range("SVRX3")
        plug.set_source_range("0.3V")
        plug.set_source_function(SourceFunction.CURRENT)
        plug.set_source_range("SIRX-1")

        self.assertEqual(
            transport.commands,
            [
                ("write", "VF"),
                ("write", "SVRX"),
                ("write", "SVR3"),
                ("write", "IF"),
                ("write", "SIRX"),
            ],
        )

    def test_current_range_distinguishes_sir_minus_one_from_sir_one(self):
        plug, transport = self.create_plug()
        plug.set_source_function(SourceFunction.CURRENT)

        plug.set_source_range("SIR-1")
        plug.set_source_range("SIR1")

        self.assertEqual(
            transport.commands,
            [
                ("write", "IF"),
                ("write", "SIR-1"),
                ("write", "SIR1"),
            ],
        )

    def test_current_limiter_rejects_same_polarity(self):
        plug, _ = self.create_plug()
        plug.set_source_function(SourceFunction.VOLTAGE)

        with self.assertRaisesRegex(ValueError, "opposite polarity"):
            plug.set_limiter(0.03, low=0.02)

    def test_sweep_type_uses_large_response_size(self):
        plug, transport = self.create_plug(
            transport=FakeTransport({"SX?": "SN+0.0E+00,+5.0E+00,1.0E-01"})
        )

        self.assertEqual(plug.sweep_type(), "SN+0.0E+00,+5.0E+00,1.0E-01")
        self.assertEqual(transport.commands, [("query", "SX?", 256)])

    def test_6241a_rejects_five_amp_range(self):
        plug, _ = self.create_plug(
            transport=FakeTransport({"*IDN?": "ADC Corp.,6241A,123456789,C00"})
        )
        plug.identify()
        plug.set_source_function(SourceFunction.CURRENT)

        with self.assertRaisesRegex(ValueError, "5 A"):
            plug.set_source_range(CurrentSourceRange.RANGE_5A)

    def test_read_measurement_parses_flags(self):
        plug, _ = self.create_plug(
            transport=FakeTransport(reads=[b"DIU+3.00000E-03\r\n"])
        )

        measurement = plug.read_measurement()

        self.assertEqual(measurement.value, 0.003)
        self.assertEqual(measurement.header, "DI")
        self.assertEqual(measurement.flags, frozenset({"U"}))
        self.assertTrue(measurement.is_high_limit)
        self.assertFalse(measurement.is_valid)

    def test_buffer_recall_stops_at_empty_slot(self):
        plug, transport = self.create_plug(
            transport=FakeTransport(
                responses={"SZ?": "3"},
                reads=[
                    b"DI +1.00000E-03\r\n",
                    b"DI +2.00000E-03\r\n",
                    b"EE +8.88888E+30\r\n",
                ],
            )
        )

        measurements = plug.read_buffer()

        self.assertEqual([item.value for item in measurements], [0.001, 0.002])
        self.assertEqual(
            transport.commands,
            [
                ("query", "SZ?", 50),
                ("write", "RN1,0"),
                ("read", 50),
                ("read", 50),
                ("read", 50),
                ("write", "RN0,0"),
            ],
        )

    def test_error_log_raises_command_error(self):
        plug, _ = self.create_plug(
            transport=FakeTransport(
                {
                    "ERC?": "2",
                    "ERL?": "-102, 000, -222, 000, 000",
                }
            )
        )

        with self.assertRaises(CommandError) as context:
            plug.check_error()

        self.assertEqual(context.exception.count, 2)
        self.assertEqual(context.exception.codes, (-102, -222))

    def test_statistics_and_self_test_register(self):
        plug, _ = self.create_plug(
            transport=FakeTransport(
                {
                    "AVE?": "AVE +1.00000E+00",
                    "MAX?": "MAX +2.00000E+00",
                    "MIN?": "MIN +0.50000E+00",
                    "TOT?": "TOT +3.50000E+00",
                    "AVN?": "AVN 3",
                    "TER?": "00000, 00000, 00016, 00000, 00000",
                }
            )
        )

        statistics = plug.statistics()
        self.assertEqual(statistics.average, 1.0)
        self.assertEqual(statistics.maximum, 2.0)
        self.assertEqual(statistics.minimum, 0.5)
        self.assertEqual(statistics.total, 3.5)
        self.assertEqual(statistics.count, 3)
        self.assertEqual(plug.self_test_register(), (0, 0, 16, 0, 0))

    def test_close_and_teardown_close_transport(self):
        plug, transport = self.create_plug()

        plug.close()
        self.assertTrue(transport.closed)

        second_plug, second_transport = self.create_plug()
        second_plug.tearDown()
        self.assertTrue(second_transport.closed)


class MeasurementParsingTest(unittest.TestCase):
    def test_headers_flags_and_multiple_values(self):
        measurements = parse_measurements(
            "DV +1.00000E+00,DIU+3.00000E-03,EE +8.88888E+30"
        )

        self.assertEqual(measurements[0].value, 1.0)
        self.assertEqual(measurements[0].header, "DV")
        self.assertEqual(measurements[1].flags, frozenset({"U"}))
        self.assertTrue(measurements[2].is_empty)

    def test_header_off_and_newline_separated_values(self):
        measurements = parse_measurements("+1.0E+00\n+2.0E-03")

        self.assertEqual([item.value for item in measurements], [1.0, 0.002])
        self.assertIsNone(measurements[0].header)

    def test_single_measurement_rejects_multiple_values(self):
        with self.assertRaisesRegex(ValueError, "expected one"):
            parse_measurement("+1.0E+00,+2.0E+00")

    def test_empty_response_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            parse_measurements("  \r\n\x00")


if __name__ == "__main__":
    unittest.main()
