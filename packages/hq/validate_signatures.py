"""Validate Nix-owned HQ code-signing evidence."""

import argparse
import os
import plistlib
import re
from pathlib import Path

EXPECTED_ENTITLEMENTS = {
    "com.apple.security.cs.allow-jit": True,
    "com.apple.security.cs.allow-unsigned-executable-memory": True,
    "com.apple.security.cs.disable-library-validation": True,
    "com.apple.security.device.audio-input": True,
}


def validate_signature_evidence(
    *,
    label: str,
    entitlements_payload: bytes,
    details: str,
    require_entitlements: bool,
) -> None:
    """Validate one codesign report under macOS executable/library semantics."""
    if entitlements_payload:
        try:
            entitlements = plistlib.loads(entitlements_payload)
        except plistlib.InvalidFileException as error:
            msg = f"invalid HQ entitlements for {label}"
            raise ValueError(msg) from error
        if entitlements != EXPECTED_ENTITLEMENTS:
            msg = f"unexpected HQ entitlements for {label}: {entitlements!r}"
            raise ValueError(msg)
    elif require_entitlements:
        msg = f"HQ executable {label} lacks required entitlements"
        raise ValueError(msg)

    code_directories = [
        line for line in details.splitlines() if line.startswith("CodeDirectory ")
    ]
    if len(code_directories) != 1:
        msg = (
            f"HQ signature for {label} has {len(code_directories)} CodeDirectory lines"
        )
        raise ValueError(msg)
    flags_match = re.search(r"flags=[^(]+\(([^)]*)\)", code_directories[0])
    flags = set(flags_match.group(1).split(",")) if flags_match else set()
    if "runtime" not in flags:
        msg = f"HQ signature for {label} lacks hardened runtime"
        raise ValueError(msg)


def validate_signature_inventory(
    *,
    audit_root: Path,
    inventory_path: Path,
    expected_count: int,
    app_label: str,
) -> None:
    """Validate the ordered codesign evidence for every HQ Mach-O and app."""
    paths = [
        os.fsdecode(path) for path in inventory_path.read_bytes().split(b"\0") if path
    ]
    if len(paths) != expected_count:
        msg = f"HQ signature inventory expected {expected_count}, got {len(paths)}"
        raise ValueError(msg)

    for index, path in enumerate(paths, start=1):
        validate_signature_evidence(
            label=path,
            entitlements_payload=(
                audit_root / f"{index}.entitlements.plist"
            ).read_bytes(),
            details=(audit_root / f"{index}.details").read_text(encoding="utf-8"),
            require_entitlements=(
                audit_root / f"{index}.requires-entitlements"
            ).exists(),
        )
    validate_signature_evidence(
        label=app_label,
        entitlements_payload=(audit_root / "app.entitlements.plist").read_bytes(),
        details=(audit_root / "app.details").read_text(encoding="utf-8"),
        require_entitlements=True,
    )


def main() -> None:
    """Validate one realized HQ signature inventory from its install check."""
    parser = argparse.ArgumentParser()
    parser.add_argument("audit_root", type=Path)
    parser.add_argument("inventory_path", type=Path)
    parser.add_argument("expected_count", type=int)
    parser.add_argument("app_label")
    arguments = parser.parse_args()
    validate_signature_inventory(
        audit_root=arguments.audit_root,
        inventory_path=arguments.inventory_path,
        expected_count=arguments.expected_count,
        app_label=arguments.app_label,
    )


if __name__ == "__main__":  # pragma: no cover -- package-build entry point
    main()
