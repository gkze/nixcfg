"""Reject a mutable vendor download that differs from the selected release."""

import argparse
import plistlib
from pathlib import Path


def validate(info_plist: Path, version: str) -> None:
    """Check the installed bundle identity without launching vendor software."""
    with info_plist.open("rb") as stream:
        info = plistlib.load(stream)
    expected = {
        "CFBundleIdentifier": "so.ara.desktop",
        "CFBundleExecutable": "Ara",
        "CFBundleShortVersionString": version,
    }
    for key, value in expected.items():
        if info.get(key) != value:
            msg = f"Reason {key} does not match selected release {version}"
            raise ValueError(msg)


def main() -> None:
    """Validate the realized package during installation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("info_plist", type=Path)
    parser.add_argument("version")
    args = parser.parse_args()
    validate(args.info_plist, args.version)


if __name__ == "__main__":  # pragma: no cover -- package-build entry point
    main()
