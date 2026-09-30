# Siglent SDG1000X Plus OpenHTF plug

This package controls SDG1000X Plus waveform generators over PyVISA using the
programming guide's SCPI commands. It supports both channels, basic waveform
selection/configuration and readback, channel outputs, load/polarity settings,
and the C1-to-C2 tracking function.

## Install

```sh
uv add openhtf-plug-siglent-sdg100x-plus
```

Install a VISA backend appropriate for the connection, such as NI-VISA or
`pyvisa-py`. Set `SIGLENT_SDG1000X_PLUS_RESOURCE`, or pass `resource_name`
directly. A common LAN resource is `TCPIP0::192.0.2.10::inst0::INSTR`.

## OpenHTF example

```python
from openhtf import Test, plugs
from siglent_sdg100x_plus import Channel, SiglentSDG1000XPlusPlug, Waveform


@plugs.plug(generator=SiglentSDG1000XPlusPlug)
def generate_tone(test_api, generator):
    generator.configure_waveform(
        Waveform.SINE,
        channel=Channel.C1,
        frequency_hz=1000,
        amplitude_vpp=2,
        offset_v=0,
        phase_deg=0,
    )
    generator.set_output(True, Channel.C1)
    settings = generator.waveform_settings(Channel.C1)
    test_api.attachments.attach("generator_settings", repr(settings).encode())


Test(generate_tone).execute()
```

`configure_waveform()` supports sine, square, ramp, pulse, noise, arbitrary, DC,
PRBS, multi-pulse, and sequence types. It validates applicable parameters and
leaves device-specific frequency/amplitude limits to the instrument. When C1-to-
C2 tracking is enabled, C2 modifications are rejected because C2 follows C1.
`set_outputs()` uses the instrument's simultaneous two-channel command. Plug
teardown attempts to disable both outputs.
