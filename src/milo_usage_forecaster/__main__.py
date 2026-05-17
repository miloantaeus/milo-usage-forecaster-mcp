"""Module entry: `python -m milo_usage_forecaster`."""

import sys

from milo_usage_forecaster.server import main


def _main_entry() -> int:
    return main()


# Re-export for the console script in pyproject.toml.
def main_entry() -> int:  # pragma: no cover - thin wrapper
    return _main_entry()


# Console script in pyproject points at this name.
main = main


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main_entry())
