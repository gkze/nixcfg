"""Skip markers for tests that execute macOS-only system tools."""

import sys
from pathlib import Path

import pytest

PLISTBUDDY = Path("/usr/libexec/PlistBuddy")

# PlistBuddy ships only with macOS, so Linux dev shells cannot run these
# tests; on Darwin they must keep running against the real tool.
requires_plistbuddy = pytest.mark.skipif(
    sys.platform != "darwin" or not PLISTBUDDY.is_file(),
    reason="requires macOS /usr/libexec/PlistBuddy",
)
