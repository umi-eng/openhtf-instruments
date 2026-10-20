from __future__ import annotations

import unittest

from adcmt_ad7461a import (
    ACFilter,
    ADCMTAD7461APlug,
    ErrorEntry,
    EventRegister,
    MeasurementFunction,
    SamplingRate,
    ScpiError,
    TriggerSource,
    parse_measurements,
)


class FakeResource:
    def __init__(self, responses=None):
        self.commands = []
        self.responses = responses or {}
        self.closed = False
        self.timeout = None
        self.read_termination = None
        self.write_termination = None
        self.baud_rate = None

    def write(self, command):
        self.commands.append(("write", command))

    def query(self, command):
        self.commands.append(("query", command))
        response = self.responses.get(command, "0")
        return response() if callable(response) else response

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


class ADCMTAD7461APlugTest(unittest.TestCase):
    def test_identify_and_one_shot_measurement(self):
        resource = FakeResource(
            {
                "*IDN?": "ADC Corp.,7461A,123456789,C00",
                ":MEASure:VOLTage:DC?": "DCV_ +1.234560E+00",
            }
        )
        plug = ADCMTAD7461APlug(resource=resource)

        self.assertEqual(plug.identify(), "ADC Corp.,7461A,123456789,C00")
        self.assertEqual(plug.measure("DCV"), 1.23456)
        self.assertEqual(
            resource.commands,
            [
                ("query", "*IDN?"),
                ("query", ":MEASure:VOLTage:DC?"),
            ],
        )
        plug.close()
        self.assertTrue(resource.closed)

    def test_configure_measurement_and_headered_range_response(self):
        resource = FakeResource({":READ?": "R2W_ +1.250000E+02"})
        plug = ADCMTAD7461APlug(resource=resource)

        plug.configure_measurement(
            MeasurementFunction.RESISTANCE,
            range=1000,
            sampling_rate=SamplingRate.MEDIUM,
        )
        self.assertEqual(plug.read(), 125.0)
        self.assertEqual(
            resource.commands,
            [
                ("write", ':SENSe:FUNCtion "RESistance"'),
                ("write", ":SENSe:RESistance:RANGe 1000"),
                ("write", ":SENSe:RESistance:SRATe MED"),
                ("query", ":READ?"),
            ],
        )
        plug.close()

    def test_measurement_values_with_and_without_headers(self):
        self.assertEqual(
            parse_measurements("DCV_ +1.0E+00,DCV_ -2.5E-01"), (1.0, -0.25)
        )
        self.assertEqual(parse_measurements("R2W_ +1.0E+00"), (1.0,))
        self.assertEqual(parse_measurements("+1.0E+00\n+2.0E+00"), (1.0, 2.0))

    def test_function_configuration_restrictions_and_coercion(self):
        resource = FakeResource()
        plug = ADCMTAD7461APlug(resource=resource)

        plug.set_sampling_rate("DCV", "SSLOW")
        plug.set_ac_filter(MeasurementFunction.AC_DC_VOLTAGE, ACFilter.MEDIUM)
        self.assertEqual(
            resource.commands,
            [
                ("write", ":SENSe:VOLTage:DC:SRATe SSLow"),
                ("write", ":SENSe:VOLTage:ACDC:FILTer MED"),
            ],
        )
        with self.assertRaises(ValueError):
            plug.configure_measurement(MeasurementFunction.DIODE, range=1)
        with self.assertRaises(ValueError):
            plug.set_ac_filter(MeasurementFunction.DC_VOLTAGE, "FAST")
        plug.close()

    def test_measurement_series_and_fetch(self):
        resource = FakeResource(
            {
                ":READ?": "DCV_ +1.0E+00,DCV_ +2.0E+00",
                ":FETCh?": "+3.0E+00,+4.0E+00",
            }
        )
        plug = ADCMTAD7461APlug(resource=resource)

        self.assertEqual(plug.read_measurements(), (1.0, 2.0))
        self.assertEqual(plug.fetch_measurements(), (3.0, 4.0))
        with self.assertRaises(ValueError):
            plug.read()
        plug.close()

    def test_trigger_and_calculation_commands(self):
        resource = FakeResource(
            {
                ":TRIGger:SOURce?": "BUS",
                ":STATus:MEASurement:EVENt?": "256",
            }
        )
        plug = ADCMTAD7461APlug(resource=resource)

        plug.set_trigger_source(TriggerSource.BUS)
        plug.set_trigger_count(4)
        plug.set_sample_count(10)
        plug.set_sampling_interval(0.0005)
        plug.set_smoothing(True, points=5)
        plug.set_reference_calculation("DCV", True, reference=1.25)
        self.assertEqual(plug.trigger_source(), TriggerSource.BUS)
        self.assertEqual(plug.read_event_register(EventRegister.MEASUREMENT), 256)
        self.assertEqual(
            resource.commands,
            [
                ("write", ":TRIGger:SOURce BUS"),
                ("write", ":TRIGger:COUNt 4"),
                ("write", ":SAMPle:COUNt 10"),
                ("write", ":TRIGger:TIMer 0.0005"),
                ("write", ":CALCulate:SMOothing ON"),
                ("write", ":CALCulate:SMOothing:POINts 5"),
                ("write", ":SENSe:VOLTage:DC:REFerence:STATe ON"),
                ("write", ":SENSe:VOLTage:DC:REFerence 1.25"),
                ("query", ":TRIGger:SOURce?"),
                ("query", ":STATus:MEASurement:EVENt?"),
            ],
        )
        plug.close()

    def test_trace_system_and_status_commands(self):
        resource = FakeResource(
            {
                ":TRACe:DATA:NUMBer?": "0,2",
                ":TRACe:DATA:POINts?": "3",
                ":TRACe:DATA?": "DCV_ +1.0E+00,DCV_ +2.0E+00",
                "*STB?": "16",
                "*ESR?": "1",
            }
        )
        plug = ADCMTAD7461APlug(resource=resource)

        plug.set_trace_enabled(True)
        plug.set_trace_range(0, 2)
        self.assertEqual(plug.trace_range(), (0, 2))
        self.assertEqual(plug.trace_points(), 3)
        self.assertEqual(plug.trace_data(), (1.0, 2.0))
        self.assertEqual(plug.read_status_byte(), 16)
        self.assertEqual(plug.read_standard_event_status(), 1)
        self.assertIn(("write", ":TRACe:STATe ON"), resource.commands)
        plug.close()

    def test_error_queue_and_verified_writes(self):
        resource = FakeResource(
            {
                "*OPC?": "1",
                ":SYSTem:ERRor?": '0,"No error"',
                ":SYSTem:VERSion?": "1999.0",
            }
        )
        plug = ADCMTAD7461APlug(resource=resource, verify_writes=True)

        plug.set_display(False)
        self.assertEqual(plug.scpi_version(), "1999.0")
        self.assertEqual(
            resource.commands[:3],
            [
                ("write", ":DISPlay OFF"),
                ("query", "*OPC?"),
                ("query", ":SYSTem:ERRor?"),
            ],
        )
        self.assertEqual(ErrorEntry.parse('+000,"No error"').code, 0)
        plug.close()

    def test_scpi_error_raises(self):
        resource = FakeResource({":SYSTem:ERRor?": '-113,"Undefined header"'})
        plug = ADCMTAD7461APlug(resource=resource)

        with self.assertRaises(ScpiError) as error:
            plug.check_error()
        self.assertEqual(error.exception.code, -113)
        plug.close()

    def test_visa_resource_configuration_and_ownership(self):
        resource = FakeResource()
        manager = FakeResourceManager(resource)
        plug = ADCMTAD7461APlug(
            "ASRL/dev/cu.usbserial-0001::INSTR",
            resource_manager=manager,
            resource_kwargs={"open_timeout": 1000},
            timeout_ms=2500,
            baud_rate=9600,
        )

        self.assertEqual(
            manager.opened,
            ("ASRL/dev/cu.usbserial-0001::INSTR", {"open_timeout": 1000}),
        )
        self.assertEqual(resource.timeout, 2500)
        self.assertEqual(resource.baud_rate, 9600)
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
                return b"ADC Corp.,7461A,123,C00\n"

            def close(self):
                pass

        resource = WriteReadResource()
        plug = ADCMTAD7461APlug(resource=resource)

        self.assertEqual(plug.identify(), "ADC Corp.,7461A,123,C00")
        self.assertEqual(resource.commands, ["*IDN?"])
        plug.close()

    def test_calculation_values_follow_documented_bounds(self):
        resource = FakeResource()
        plug = ADCMTAD7461APlug(resource=resource)

        with self.assertRaises(ValueError):
            plug.set_reference_calculation("DCV", True, reference=1e14)
        with self.assertRaises(ValueError):
            plug.set_scaling(True, a=1e14)
        with self.assertRaises(ValueError):
            plug.set_comparator(True, upper=1e14)
        with self.assertRaises(ValueError):
            plug.set_db_reference(0)
        with self.assertRaises(ValueError):
            plug.set_db_reference(-1)
        with self.assertRaises(ValueError):
            plug.set_db_reference(1e14)
        self.assertEqual(resource.commands, [])

        plug.set_db_reference(1)
        plug.set_db_reference("MEASurement")
        self.assertEqual(
            resource.commands,
            [
                ("write", ":CALCulate:DB:D 1"),
                ("write", ":CALCulate:DB:D MEASurement"),
            ],
        )
        plug.close()

    def test_invalid_inputs_do_not_send_commands(self):
        resource = FakeResource()
        plug = ADCMTAD7461APlug(resource=resource)

        with self.assertRaises(ValueError):
            plug.measure("not-a-function")
        with self.assertRaises(ValueError):
            plug.set_range(MeasurementFunction.DC_VOLTAGE, -1)
        with self.assertRaises(ValueError):
            plug.set_sample_count(0)
        with self.assertRaises(ValueError):
            plug.set_trigger_source("not-a-trigger")
        self.assertEqual(resource.commands, [])
        plug.close()


if __name__ == "__main__":
    unittest.main()
