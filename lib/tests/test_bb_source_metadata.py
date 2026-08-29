"""Tests for bb's pristine source-manifest metadata validator."""

import json
from pathlib import Path
from types import ModuleType

import pytest

from lib.tests._updater_helpers import load_repo_module


def _load_module() -> ModuleType:
    return load_repo_module(
        "packages/bb/validate_source_metadata.py",
        "bb_source_metadata_test",
    )


def _write_manifests(
    source: Path,
    *,
    desktop_version: object = "0.38.0",
    bb_app_version: object = "0.38.0",
    electron_version: object = "41.7.0",
) -> None:
    desktop = source / "apps" / "desktop" / "package.json"
    bb_app = source / "packages" / "bb-app" / "package.json"
    desktop.parent.mkdir(parents=True)
    bb_app.parent.mkdir(parents=True)
    desktop.write_text(
        json.dumps({
            "version": desktop_version,
            "devDependencies": {"electron": electron_version},
        }),
        encoding="utf-8",
    )
    bb_app.write_text(
        json.dumps({"version": bb_app_version}),
        encoding="utf-8",
    )


def test_bb_source_metadata_accepts_exact_pinned_manifests(tmp_path: Path) -> None:
    """Accept source manifests that match both tracked release fields."""
    module = _load_module()
    _write_manifests(tmp_path)

    module.validate_source_metadata(
        tmp_path,
        expected_version="0.38.0",
        expected_electron_version="41.7.0",
    )
    assert (
        module.main([
            "--source",
            str(tmp_path),
            "--expected-version",
            "0.38.0",
            "--expected-electron-version",
            "41.7.0",
        ])
        == 0
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"desktop_version": "0.38.1"}, "desktop version"),
        ({"bb_app_version": "0.38.1"}, "bb-app version"),
        ({"electron_version": "42.0.0"}, "Electron version"),
    ],
)
def test_bb_source_metadata_rejects_manifest_drift(
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    """Reject each upstream field when it drifts from evaluator metadata."""
    module = _load_module()
    _write_manifests(tmp_path, **overrides)

    with pytest.raises(module.SourceMetadataValidationError, match=message):
        module.validate_source_metadata(
            tmp_path,
            expected_version="0.38.0",
            expected_electron_version="41.7.0",
        )


@pytest.mark.parametrize(
    ("relative_path", "payload", "message"),
    [
        ("apps/desktop/package.json", [], "JSON object"),
        ("apps/desktop/package.json", {"version": 1}, "version"),
        (
            "apps/desktop/package.json",
            {"version": "0.38.0", "devDependencies": []},
            "devDependencies",
        ),
        (
            "apps/desktop/package.json",
            {"version": "0.38.0", "devDependencies": {"electron": ""}},
            "electron",
        ),
        ("packages/bb-app/package.json", {"version": None}, "version"),
    ],
)
def test_bb_source_metadata_rejects_malformed_manifest_fields(
    tmp_path: Path,
    relative_path: str,
    payload: object,
    message: str,
) -> None:
    """Reject missing or malformed package metadata before patching the source."""
    module = _load_module()
    _write_manifests(tmp_path)
    (tmp_path / relative_path).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(module.SourceMetadataValidationError, match=message):
        module.validate_source_metadata(
            tmp_path,
            expected_version="0.38.0",
            expected_electron_version="41.7.0",
        )
