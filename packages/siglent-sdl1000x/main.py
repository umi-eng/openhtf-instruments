"""Compatibility entry point and public re-exports for the package."""

from siglent_sdl1000x import LoadMeasurement, LoadMode, SiglentSDL1000XPlug

__all__ = ["LoadMeasurement", "LoadMode", "SiglentSDL1000XPlug"]


def main() -> None:
    print("Import SiglentSDL1000XPlug from siglent_sdl1000x to control an SDL1030X.")


if __name__ == "__main__":
    main()
