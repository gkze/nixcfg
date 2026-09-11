"""Optional receipts for deterministic generators with verified current outputs."""

import hashlib
import json
import logging
from typing import TYPE_CHECKING

from lib.update.io import atomic_write_json

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

log = logging.getLogger(__name__)


def content_digest(content: bytes) -> str:
    """Return a stable digest without retaining possibly sensitive input data."""
    return hashlib.sha256(content).hexdigest()


def identity_digest(identity: object) -> str:
    """Hash a canonical JSON generator-input identity."""
    return content_digest(json.dumps(identity, sort_keys=True).encode())


def _receipt(identity: str, outputs: Mapping[str, str]) -> dict[str, object]:
    return {
        "version": 1,
        "identity": identity,
        "outputs": {
            name: content_digest(text.encode()) for name, text in outputs.items()
        },
    }


def matches(path: Path, *, identity: str, outputs: Mapping[str, str]) -> bool:
    """Accept only an exact complete output set from the same generator inputs."""
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        # Receipts are disposable accelerators, never authoritative artifacts.
        return False
    return saved == _receipt(identity, outputs)


def save(path: Path, *, identity: str, outputs: Mapping[str, str]) -> None:
    """Record successful generation atomically without making cache I/O required."""
    try:
        atomic_write_json(path, _receipt(identity, outputs), mkdir=True)
    except OSError:
        log.warning("Could not save optional generated-artifact receipt")
