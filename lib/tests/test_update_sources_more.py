"""Additional tests for sources discovery and validation helpers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import lib.update.sources as update_sources_module
from lib.nix.models.sources import SourceEntry
from lib.update.sources import (
    nix_source_names,
    python_source_names,
    save_source_entry,
    validate_source_discovery_consistency,
)


def test_python_source_names_and_nix_missing_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read python source names and fail when nix executable is missing."""
    monkeypatch.setattr(
        "lib.update.sources._source_file_map",
        lambda: {"a": Path("/tmp/a"), "b": Path("/tmp/b")},
    )
    assert python_source_names() == {"a", "b"}

    monkeypatch.setattr("lib.update.sources.shutil.which", lambda _tool: None)
    with pytest.raises(RuntimeError, match="nix executable not found"):
        nix_source_names()


def test_nix_source_names_error_and_payload_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handle nix eval failures and invalid payload types."""
    monkeypatch.setattr("lib.update.sources.shutil.which", lambda _tool: "/bin/nix")

    monkeypatch.setattr(
        "lib.update.sources._run_nix_eval",
        lambda _expr: (1, "", "boom"),
    )
    with pytest.raises(RuntimeError, match="Failed to evaluate nix source names: boom"):
        nix_source_names()

    monkeypatch.setattr(
        "lib.update.sources._run_nix_eval",
        lambda _expr: (0, json.dumps(["ok", 1]), ""),
    )
    with pytest.raises(RuntimeError, match="Unexpected nix source name payload"):
        nix_source_names()


def test_validate_source_discovery_consistency_and_save_source_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detect source discovery mismatches and write single source entries."""
    monkeypatch.setattr("lib.update.sources.python_source_names", lambda: {"a", "b"})
    monkeypatch.setattr("lib.update.sources.nix_source_names", lambda: {"b", "c"})
    with pytest.raises(RuntimeError, match=r"Missing in nix outputs\.lib\.sources: a"):
        validate_source_discovery_consistency()

    monkeypatch.setattr("lib.update.sources.python_source_names", lambda: {"a", "b"})
    monkeypatch.setattr("lib.update.sources.nix_source_names", lambda: {"b"})
    with pytest.raises(RuntimeError, match="Missing in nix outputs.lib.sources: a"):
        validate_source_discovery_consistency()

    monkeypatch.setattr("lib.update.sources.python_source_names", lambda: {"a"})
    monkeypatch.setattr("lib.update.sources.nix_source_names", lambda: {"a"})
    validate_source_discovery_consistency()

    target = tmp_path / "sources.json"
    save_source_entry(target, SourceEntry(hashes={"x86_64-linux": "sha256-demo"}))
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["hashes"]["x86_64-linux"] == "sha256-demo"


def test_run_nix_eval_uses_thread_pool_when_loop_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run nix eval via a helper thread when already inside an event loop."""
    seen: dict[str, object] = {}

    async def _fake_run_nix(command: list[str], *, check: bool) -> object:
        seen["command"] = command
        seen["check"] = check
        return SimpleNamespace(returncode=0, stdout='["demo"]', stderr="")

    class _Future:
        def __init__(self, value: object) -> None:
            self._value = value

        def result(self) -> object:
            return self._value

    class _Executor:
        def __enter__(self) -> _Executor:
            seen["entered"] = True
            return self

        def __exit__(self, *_args: object) -> None:
            seen["exited"] = True

        def submit(self, func):
            seen["submitted"] = True
            return _Future(func())

    monkeypatch.setattr(update_sources_module, "run_nix", _fake_run_nix)
    monkeypatch.setattr(
        update_sources_module.asyncio, "get_running_loop", lambda: object()
    )
    monkeypatch.setattr(
        update_sources_module,
        "ThreadPoolExecutor",
        lambda **_kwargs: _Executor(),
    )

    returncode, stdout, stderr = update_sources_module._run_nix_eval(
        "builtins.attrNames {}"
    )

    assert (returncode, stdout, stderr) == (0, '["demo"]', "")
    assert seen["entered"] is True
    assert seen["submitted"] is True
    assert seen["exited"] is True
    assert seen["command"] == [
        "nix",
        "eval",
        "--impure",
        "--json",
        "--expr",
        "builtins.attrNames {}",
    ]
    assert seen["check"] is False
