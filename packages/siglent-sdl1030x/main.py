"""Compatibility entry point and public re-exports for the package."""

from siglent_sdl1030x import LoadMeasurement, LoadMode, SiglentSDL1030XPlug

__all__ = ["LoadMeasurement", "LoadMode", "SiglentSDL1030XPlug"]


def main() -> None:
    print("Import SiglentSDL1030XPlug from siglent_sdl1030x to control an SDL1030X.")


if __name__ == "__main__":
    main()
