"""Reject a mutable vendor download that differs from the selected release."""

import argparse
import plistlib
from pathlib import Path

# Reason kept `so.ara.desktop` through the early rename, then shipped 0.1.64+
# as `com.reasonmachines.desktop`. Accept both so a cached older pin still
# installs while Update can promote the rebranded feed.
_BUNDLE_IDENTIFIERS = frozenset({"so.ara.desktop", "com.reasonmachines.desktop"})


def validate(info_plist: Path, version: str) -> None:
    """Check the installed bundle identity without launching vendor software."""
    with info_plist.open("rb") as stream:
        info = plistlib.load(stream)
    identifier = info.get("CFBundleIdentifier")
    if identifier not in _BUNDLE_IDENTIFIERS:
        msg = f"Reason CFBundleIdentifier does not match selected release {version}"
        raise ValueError(msg)
    expected = {
        "CFBundleExecutable": "Reason",
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
