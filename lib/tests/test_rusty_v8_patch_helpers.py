"""Tests for the repo-local rusty_v8 patch helper scripts."""

from __future__ import annotations

import runpy
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest


def _scripts_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "rusty-v8"


def _load_script(script_name: str) -> dict[str, Any]:
    return cast("dict[str, Any]", runpy.run_path(str(_scripts_dir() / script_name)))


def _write_with_extra_anchor(target: Path, namespace: dict[str, Any]) -> None:
    extra_anchor = namespace.get(
        "_EXTRA_REPLACE_NEEDLE",
        "    if (!rustc_nightly_capability) {\n"
        '      rustflags += [ "--cfg=rust_allocator_no_nightly_capability" ]\n'
        "    }\n",
    )
    target.write_text(
        f"prefix\n{namespace['_NEEDLE']}{extra_anchor}suffix\n", encoding="utf-8"
    )


def _main(namespace: dict[str, Any]) -> Callable[[], int]:
    return cast("Callable[[], int]", namespace["main"])


def test_patch_allocator_build_script_success_and_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patch allocator BUILD files and reject invalid CLI inputs."""
    namespace = _load_script("patch_allocator_build.py")
    target = tmp_path / "BUILD.gn"
    _write_with_extra_anchor(target, namespace)

    monkeypatch.setattr(sys, "argv", ["patch_allocator_build.py", str(target)])
    assert _main(namespace)() == 0
    patched = target.read_text(encoding="utf-8")
    assert 'config("allocator_alwayslink") {' in patched
    assert "legacy shim names" in patched

    monkeypatch.setattr(sys, "argv", ["patch_allocator_build.py"])
    with pytest.raises(SystemExit, match="usage: patch_allocator_build.py"):
        _main(namespace)()

    target.write_text("no anchor\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["patch_allocator_build.py", str(target)])
    with pytest.raises(SystemExit, match="allocator BUILD.gn anchor not found"):
        _main(namespace)()


def test_patch_allocator_build_main_guard_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Execute the allocator patch helper through its CLI entrypoint."""
    script_path = _scripts_dir() / "patch_allocator_build.py"
    namespace = _load_script(script_path.name)
    target = tmp_path / "BUILD.gn"
    _write_with_extra_anchor(target, namespace)

    monkeypatch.setattr(sys, "argv", [str(script_path), str(target)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(script_path), run_name="__main__")
    assert exc.value.code == 0


def test_patch_apple_toolchain_script_success_and_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patch Apple toolchain settings and reject invalid inputs."""
    namespace = _load_script("patch_apple_toolchain_host_build_tools.py")
    target = tmp_path / "toolchain.gni"
    target.write_text(f"prefix\n{namespace['_NEEDLE']}suffix\n", encoding="utf-8")

    monkeypatch.setattr(
        sys, "argv", ["patch_apple_toolchain_host_build_tools.py", str(target)]
    )
    assert _main(namespace)() == 0
    patched = target.read_text(encoding="utf-8")
    assert "use_lld = false" in patched
    assert "fatal_linker_warnings = false" in patched

    monkeypatch.setattr(sys, "argv", ["patch_apple_toolchain_host_build_tools.py"])
    with pytest.raises(
        SystemExit,
        match="usage: patch_apple_toolchain_host_build_tools.py",
    ):
        _main(namespace)()

    target.write_text("no anchor\n", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["patch_apple_toolchain_host_build_tools.py", str(target)]
    )
    with pytest.raises(
        SystemExit, match="apple toolchain host-build-tools anchor not found"
    ):
        _main(namespace)()


def test_patch_apple_toolchain_main_guard_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute the Apple toolchain patch helper through its CLI entrypoint."""
    script_path = _scripts_dir() / "patch_apple_toolchain_host_build_tools.py"
    namespace = _load_script(script_path.name)
    target = tmp_path / "toolchain.gni"
    target.write_text(f"prefix\n{namespace['_NEEDLE']}suffix\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [str(script_path), str(target)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(script_path), run_name="__main__")
    assert exc.value.code == 0


def test_patch_whole_archive_script_success_and_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patch Chromium whole-archive handling and reject invalid inputs."""
    namespace = _load_script("patch_whole_archive.py")
    target = tmp_path / "whole_archive.py"
    target.write_text(f"prefix\n{namespace['_NEEDLE']}suffix\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["patch_whole_archive.py", str(target)])
    assert _main(namespace)() == 0
    patched = target.read_text(encoding="utf-8")
    assert "def is_allocator_rlib" in patched
    assert "is_allocator_rlib(x)" in patched

    monkeypatch.setattr(sys, "argv", ["patch_whole_archive.py"])
    with pytest.raises(SystemExit, match="usage: patch_whole_archive.py"):
        _main(namespace)()

    target.write_text("no anchor\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["patch_whole_archive.py", str(target)])
    with pytest.raises(SystemExit, match="whole_archive.py anchor not found"):
        _main(namespace)()


def test_patch_whole_archive_main_guard_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute the whole-archive patch helper through its CLI entrypoint."""
    script_path = _scripts_dir() / "patch_whole_archive.py"
    namespace = _load_script(script_path.name)
    target = tmp_path / "whole_archive.py"
    target.write_text(f"prefix\n{namespace['_NEEDLE']}suffix\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [str(script_path), str(target)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(script_path), run_name="__main__")
    assert exc.value.code == 0
