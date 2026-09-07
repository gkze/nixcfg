"""Validate Buzz desktop entitlements contracts."""

import plistlib
import sys
from pathlib import Path


def validate(path: Path, label: str) -> None:
    """Require the reviewed entitlements on the signed app or executable."""
    expected = {
        "com.apple.security.cs.disable-library-validation": True,
        "com.apple.security.device.audio-input": True,
        "com.apple.security.device.camera": True,
    }
    try:
        with path.open("rb") as plist_file:
            entitlements = plistlib.load(plist_file)
    except (OSError, plistlib.InvalidFileException) as error:
        message = f"{label} entitlement plist is invalid: {error}"
        raise SystemExit(message) from error
    if entitlements != expected:
        message = f"{label} entitlement contract differs"
        raise SystemExit(message)


if __name__ == "__main__":
    validate(Path(sys.argv[1]), sys.argv[2])
