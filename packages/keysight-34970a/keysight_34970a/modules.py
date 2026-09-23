"""Capability definitions for the 34970A plug-in modules."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from .models import MeasurementFunction


class ModuleType(str, Enum):
    """Supported 34970A plug-in cards."""

    MUX_34901A = "34901A"
    MUX_34902A = "34902A"
    ACTUATOR_34903A = "34903A"
    MATRIX_34904A = "34904A"
    RF_MUX_34905A = "34905A"
    RF_MUX_34906A = "34906A"
    MULTIFUNCTION_34907A = "34907A"
    MUX_34908A = "34908A"

    @classmethod
    def coerce(cls, value: ModuleType | str) -> ModuleType:
        if isinstance(value, cls):
            return value
        match = re.search(r"3490[1-8]A", str(value).upper())
        if not match:
            raise ValueError(f"Unknown 34970A module: {value!r}")
        return cls(match.group(0))


@dataclass(frozen=True)
class ModuleSpec:
    model: ModuleType
    name: str
    valid_channels: frozenset[int]
    measurement_functions: frozenset[MeasurementFunction] = frozenset()
    scan_channels: frozenset[int] = frozenset()
    switch_channels: frozenset[int] = frozenset()
    digital_channels: frozenset[int] = frozenset()
    totalizer_channels: frozenset[int] = frozenset()
    dac_channels: frozenset[int] = frozenset()
    max_dac_voltage: float = 12.0

    def has_channel(self, number: int) -> bool:
        return number in self.valid_channels

    def supports_measurement(self, number: int, function: MeasurementFunction) -> bool:
        if (
            function not in self.measurement_functions
            or number not in self.scan_channels
        ):
            return False
        if (
            function == MeasurementFunction.DC_CURRENT
            or function == MeasurementFunction.AC_CURRENT
        ):
            return self.model == ModuleType.MUX_34901A and number in (21, 22)
        if self.model == ModuleType.MUX_34901A and number in (21, 22):
            return False
        if function in {
            MeasurementFunction.FRTD,
            MeasurementFunction.FOUR_WIRE_RESISTANCE,
        }:
            return (
                self.model == ModuleType.MUX_34901A
                and 1 <= number <= 10
                or self.model == ModuleType.MUX_34902A
                and 1 <= number <= 8
            )
        return True


_MUX_FUNCTIONS = frozenset(
    {
        MeasurementFunction.TEMPERATURE,
        MeasurementFunction.THERMOCOUPLE,
        MeasurementFunction.RTD,
        MeasurementFunction.FRTD,
        MeasurementFunction.THERMISTOR,
        MeasurementFunction.DC_VOLTAGE,
        MeasurementFunction.AC_VOLTAGE,
        MeasurementFunction.RESISTANCE,
        MeasurementFunction.FOUR_WIRE_RESISTANCE,
        MeasurementFunction.FREQUENCY,
        MeasurementFunction.PERIOD,
    }
)


MODULE_SPECS: dict[ModuleType, ModuleSpec] = {
    ModuleType.MUX_34901A: ModuleSpec(
        ModuleType.MUX_34901A,
        "20-channel armature multiplexer",
        frozenset(range(1, 23)),
        _MUX_FUNCTIONS
        | {MeasurementFunction.DC_CURRENT, MeasurementFunction.AC_CURRENT},
        frozenset(range(1, 23)),
        frozenset(range(1, 23)),
    ),
    ModuleType.MUX_34902A: ModuleSpec(
        ModuleType.MUX_34902A,
        "16-channel reed multiplexer",
        frozenset(range(1, 17)),
        _MUX_FUNCTIONS,
        frozenset(range(1, 17)),
        frozenset(range(1, 17)),
    ),
    ModuleType.ACTUATOR_34903A: ModuleSpec(
        ModuleType.ACTUATOR_34903A,
        "20-channel Form C actuator",
        frozenset(range(1, 21)),
        switch_channels=frozenset(range(1, 21)),
    ),
    ModuleType.MATRIX_34904A: ModuleSpec(
        ModuleType.MATRIX_34904A,
        "4x8 two-wire matrix",
        frozenset(row * 10 + column for row in range(1, 5) for column in range(1, 9)),
        switch_channels=frozenset(
            row * 10 + column for row in range(1, 5) for column in range(1, 9)
        ),
    ),
    ModuleType.RF_MUX_34905A: ModuleSpec(
        ModuleType.RF_MUX_34905A,
        "dual 4-channel 50 ohm RF multiplexer",
        frozenset((11, 12, 13, 14, 21, 22, 23, 24)),
        switch_channels=frozenset((11, 12, 13, 14, 21, 22, 23, 24)),
    ),
    ModuleType.RF_MUX_34906A: ModuleSpec(
        ModuleType.RF_MUX_34906A,
        "dual 4-channel 75 ohm RF multiplexer",
        frozenset((11, 12, 13, 14, 21, 22, 23, 24)),
        switch_channels=frozenset((11, 12, 13, 14, 21, 22, 23, 24)),
    ),
    ModuleType.MULTIFUNCTION_34907A: ModuleSpec(
        ModuleType.MULTIFUNCTION_34907A,
        "multifunction digital I/O, totalizer, and DAC",
        frozenset(range(1, 6)),
        frozenset({MeasurementFunction.DIGITAL_BYTE, MeasurementFunction.TOTALIZER}),
        frozenset((1, 2, 3)),
        digital_channels=frozenset((1, 2)),
        totalizer_channels=frozenset((3,)),
        dac_channels=frozenset((4, 5)),
    ),
    ModuleType.MUX_34908A: ModuleSpec(
        ModuleType.MUX_34908A,
        "40-channel single-ended multiplexer",
        frozenset(range(1, 41)),
        _MUX_FUNCTIONS
        - {MeasurementFunction.FRTD, MeasurementFunction.FOUR_WIRE_RESISTANCE},
        frozenset(range(1, 41)),
        frozenset(range(1, 41)),
    ),
}


def module_spec(module: ModuleType | str) -> ModuleSpec:
    return MODULE_SPECS[ModuleType.coerce(module)]


def supported_module_models() -> tuple[ModuleType, ...]:
    return tuple(MODULE_SPECS)


def format_module_map(
    modules: Iterable[tuple[int, ModuleType | str]],
) -> dict[int, ModuleSpec]:
    return {slot: module_spec(model) for slot, model in modules}
