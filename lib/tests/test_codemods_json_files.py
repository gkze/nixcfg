"""Tests for structured JSON codemod helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.codemods.errors import CodemodError
from lib.codemods.json_files import (
    read_json_object,
    render_json_object,
    required_object,
    required_string,
    update_json_object,
    write_json_object,
)


def test_read_json_object_requires_object_payload(tmp_path: Path) -> None:
    """JSON codemods should reject non-object roots."""
    path = tmp_path / "package.json"
    path.write_text("[]\n", encoding="utf-8")

    with pytest.raises(CodemodError, match="Expected JSON object"):
        read_json_object(path, context="package")


def test_read_json_object_reports_invalid_json(tmp_path: Path) -> None:
    """Invalid JSON should include the caller context."""
    path = tmp_path / "package.json"
    path.write_text("{\n", encoding="utf-8")

    with pytest.raises(CodemodError, match="invalid JSON in package"):
        read_json_object(path, context="package")


def test_render_and_write_json_object_use_stable_format(tmp_path: Path) -> None:
    """Rendered JSON should be indented and newline-terminated."""
    payload = {"z": 1, "a": {"nested": True}}
    assert render_json_object(payload, sort_keys=True).startswith('{\n  "a"')

    path = tmp_path / "package.json"
    write_json_object(path, payload, sort_keys=True)
    assert json.loads(path.read_text(encoding="utf-8")) == payload
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_update_json_object_writes_mutated_payload(tmp_path: Path) -> None:
    """Updater callbacks should be able to mutate the JSON object in place."""
    path = tmp_path / "package.json"
    path.write_text('{"name":"demo"}\n', encoding="utf-8")

    def update(payload):
        payload["version"] = "1.0.0"
        return True

    assert update_json_object(path, update) is True
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "name": "demo",
        "version": "1.0.0",
    }


def test_update_json_object_can_skip_write_when_callback_reports_no_change(
    tmp_path: Path,
) -> None:
    """A false callback result should suppress formatting-only writes."""
    path = tmp_path / "package.json"
    original = '{"name":"demo"}\n'
    path.write_text(original, encoding="utf-8")

    assert update_json_object(path, lambda _payload: False) is False
    assert path.read_text(encoding="utf-8") == original


def test_required_json_fields_validate_shape() -> None:
    """Nested object and string accessors should fail with codemod errors."""
    payload = {"dependencies": {"demo": "1.0.0"}, "name": "demo"}

    assert required_object(payload, "dependencies", context="package") == {
        "demo": "1.0.0",
    }
    assert required_string(payload, "name", context="package") == "demo"

    with pytest.raises(CodemodError, match="Expected JSON object"):
        required_object(payload, "name", context="package")

    with pytest.raises(CodemodError, match="Expected string field"):
        required_string(payload, "dependencies", context="package")
