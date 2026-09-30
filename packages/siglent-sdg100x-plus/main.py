"""Compatibility entry point and public re-exports for the package."""

from siglent_sdg100x_plus import (
    Channel,
    OutputPolarity,
    OutputSettings,
    SiglentSDG1000XPlusPlug,
    Waveform,
    WaveformSettings,
)

__all__ = [
    "Channel",
    "OutputPolarity",
    "OutputSettings",
    "SiglentSDG1000XPlusPlug",
    "Waveform",
    "WaveformSettings",
]


def main() -> None:
    print(
        "Import SiglentSDG1000XPlusPlug from siglent_sdg100x_plus "
        "to control an SDG1000X Plus."
    )


if __name__ == "__main__":
    main()
