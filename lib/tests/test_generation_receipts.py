"""Behavioral contracts for optional deterministic generation receipts."""

import asyncio
import dataclasses
import os
import subprocess
from pathlib import Path

import pytest

from lib.update import crate2nix, generation_receipts
from lib.update.config import resolve_config
from lib.update.runtime import runtime_scope


@pytest.fixture
def generation_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> crate2nix.Crate2NixTarget:
    """Give generation a fully inspectable source without Nix or network I/O."""
    target = crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("test-system",),
        cache_generation=True,
    )
    for name in (
        "demo/normalize.py",
        "lib/generator.py",
        "lib/source-slice.nix",
        "flake.nix",
        "flake.lock",
        "uv.lock",
        "bindings/parser.py",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original\n", encoding="utf-8")
    monkeypatch.setattr(crate2nix, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(crate2nix, "_xdg_cache_home", lambda: tmp_path / "cache")
    monkeypatch.setattr(crate2nix, "_crate2nix_cargo_home", lambda: tmp_path / "cargo")
    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "test-system")
    monkeypatch.setattr(
        crate2nix.shutil, "which", lambda name: f"/nix/store/tool/bin/{name}"
    )
    monkeypatch.setattr(
        crate2nix.nix_manipulator, "__file__", str(tmp_path / "bindings/parser.py")
    )
    monkeypatch.setattr(
        crate2nix.tree_sitter, "__file__", str(tmp_path / "bindings/parser.py")
    )
    return target


def test_receipt_requires_exact_inputs_and_complete_output_set(tmp_path: Path) -> None:
    """Missing, corrupted, incomplete, or changed outputs cannot authorize reuse."""
    path = tmp_path / "receipt.json"
    outputs = {"Cargo.nix": "cargo", "crate-hashes.json": "{}"}
    assert not generation_receipts.matches(path, identity="one", outputs=outputs)
    generation_receipts.save(path, identity="one", outputs=outputs)
    assert generation_receipts.matches(path, identity="one", outputs=outputs)
    assert not generation_receipts.matches(path, identity="two", outputs=outputs)
    assert not generation_receipts.matches(
        path, identity="one", outputs={"Cargo.nix": "cargo"}
    )
    assert not generation_receipts.matches(
        path, identity="one", outputs=outputs | {"Cargo.nix": "changed"}
    )
    path.write_text("{broken", encoding="utf-8")
    assert not generation_receipts.matches(path, identity="one", outputs=outputs)
    assert generation_receipts.identity_digest({
        "b": 2,
        "a": 1,
    }) == generation_receipts.identity_digest({"a": 1, "b": 2})


def test_optional_receipt_write_failure_keeps_generation_successful(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An unusable cache directory cannot turn successful generation into failure."""
    parent = tmp_path / "not-a-directory"
    parent.write_text("occupied", encoding="utf-8")
    generation_receipts.save(
        parent / "receipt", identity="one", outputs={"file": "content"}
    )
    assert "optional generated-artifact receipt" in caplog.text


@pytest.mark.parametrize(
    "changed",
    [
        "demo/normalize.py",
        "lib/generator.py",
        "lib/source-slice.nix",
        "flake.lock",
        "uv.lock",
        "bindings/parser.py",
    ],
)
def test_generation_identity_tracks_code_toolchain_and_locked_inputs(
    tmp_path: Path, generation_target: crate2nix.Crate2NixTarget, changed: str
) -> None:
    """Same-source reuse is invalidated by every independently varying producer input."""
    source = Path("/nix/store/source")
    before = crate2nix._generation_identity(generation_target, source)
    assert before is not None
    (tmp_path / changed).write_text("changed", encoding="utf-8")
    assert crate2nix._generation_identity(generation_target, source) != before


def test_generation_identity_tracks_options_config_and_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    generation_target: crate2nix.Crate2NixTarget,
) -> None:
    """Generator options and Cargo configuration cannot be hidden by source equality."""
    source = Path("/nix/store/source")
    before = crate2nix._generation_identity(generation_target, source)
    assert before is not None
    assert (
        crate2nix._generation_identity(generation_target, Path("/nix/store/other"))
        != before
    )
    changed = dataclasses.replace(
        generation_target, cargo_manifest_relpath=Path("nested/Cargo.toml")
    )
    assert crate2nix._generation_identity(changed, source) != before
    (tmp_path / "cargo").mkdir()
    (tmp_path / "cargo/config.toml").write_text(
        "[net]\noffline=true\n", encoding="utf-8"
    )
    configured = crate2nix._generation_identity(generation_target, source)
    assert configured != before
    monkeypatch.setenv("CARGO_BUILD_TARGET", "different-platform")
    assert crate2nix._generation_identity(generation_target, source) != configured


def test_generation_identity_normalizes_nix_user_conf_files_by_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    generation_target: crate2nix.Crate2NixTarget,
) -> None:
    """Runner-local nix config paths should only matter through their file contents."""
    source = Path("/nix/store/source")
    first = tmp_path / "runner-a" / "nix.conf"
    second = tmp_path / "runner-b" / "nix.conf"
    for path in (first, second):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("substituters = https://cache.nixos.org\n", encoding="utf-8")
    monkeypatch.setenv("NIX_USER_CONF_FILES", f"{first}{os.pathsep}{second}")
    baseline = crate2nix._generation_identity(generation_target, source)
    assert baseline is not None
    moved_first = tmp_path / "runner-c" / "nix.conf"
    moved_second = tmp_path / "runner-d" / "nix.conf"
    for path in (moved_first, moved_second):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("substituters = https://cache.nixos.org\n", encoding="utf-8")
    monkeypatch.setenv(
        "NIX_USER_CONF_FILES", f"{moved_first}{os.pathsep}{moved_second}"
    )
    assert crate2nix._generation_identity(generation_target, source) == baseline
    moved_second.write_text(
        "substituters = https://cache.nixos.org https://gkze.cachix.org\n",
        encoding="utf-8",
    )
    assert crate2nix._generation_identity(generation_target, source) != baseline


def test_generation_identity_fails_closed_for_unreadable_nix_user_conf(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    generation_target: crate2nix.Crate2NixTarget,
) -> None:
    """Unreadable nix user config files must not authorize receipt reuse."""
    source = Path("/nix/store/source")
    conf = tmp_path / "nix.conf"
    conf.write_text("substituters = https://cache.nixos.org\n", encoding="utf-8")
    monkeypatch.setenv("NIX_USER_CONF_FILES", str(conf))
    original = Path.read_bytes

    def boom(self: Path) -> bytes:
        if self == conf:
            raise PermissionError("denied")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", boom)
    assert crate2nix._generation_identity(generation_target, source) is None


def test_generation_identity_ignores_empty_nix_user_conf_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    generation_target: crate2nix.Crate2NixTarget,
) -> None:
    """Trailing path separators must not invent missing-path identity noise."""
    source = Path("/nix/store/source")
    conf = tmp_path / "nix.conf"
    conf.write_text("substituters = https://cache.nixos.org\n", encoding="utf-8")
    monkeypatch.setenv("NIX_USER_CONF_FILES", f"{conf}{os.pathsep}")
    baseline = crate2nix._generation_identity(generation_target, source)
    monkeypatch.setenv("NIX_USER_CONF_FILES", str(conf))
    assert crate2nix._generation_identity(generation_target, source) == baseline


def test_generation_identity_fails_closed_for_uninspectable_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    generation_target: crate2nix.Crate2NixTarget,
) -> None:
    """Unknown generators, mutable sources, missing inputs, and absent tools regenerate."""
    source = Path("/nix/store/source")
    assert (
        crate2nix._generation_identity(
            dataclasses.replace(generation_target, cache_generation=False), source
        )
        is None
    )
    assert crate2nix._generation_identity(generation_target, tmp_path) is None
    (tmp_path / "uv.lock").unlink()
    assert crate2nix._generation_identity(generation_target, source) is None
    (tmp_path / "uv.lock").write_text("restored", encoding="utf-8")
    monkeypatch.setattr(crate2nix.shutil, "which", lambda _name: None)
    assert crate2nix._generation_identity(generation_target, source) is None


def test_generation_identity_checks_mutable_tool_contents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    generation_target: crate2nix.Crate2NixTarget,
) -> None:
    """A tool replaced in place must invalidate a receipt even with unchanged PATH."""
    executable = tmp_path / "tool"
    executable.write_text("tool one", encoding="utf-8")
    monkeypatch.setattr(crate2nix.shutil, "which", lambda _name: str(executable))
    before = crate2nix._generation_identity(
        generation_target, Path("/nix/store/source")
    )
    executable.write_text("tool two", encoding="utf-8")
    assert (
        crate2nix._generation_identity(generation_target, Path("/nix/store/source"))
        != before
    )


def test_crate_generation_reuses_verified_outputs_and_repairs_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    generation_target: crate2nix.Crate2NixTarget,
) -> None:
    """A warm no-op performs no generation; damaged outputs and changed code do."""
    calls = 0
    monkeypatch.setattr(
        crate2nix,
        "_build_patched_src",
        lambda *_args, **_kwargs: Path("/nix/store/source"),
    )
    monkeypatch.setattr(
        crate2nix, "load_normalizer", lambda _path: lambda text: (text, 0, False)
    )

    def generate(
        args: list[str], *, generated_outputs: tuple[Path, ...], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        generated_outputs[0].write_text("{}\n", encoding="utf-8")
        generated_outputs[1].write_text("{}\n", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(crate2nix, "_run_crate2nix_generate", generate)
    refreshed = crate2nix._refresh_target(generation_target)
    crate2nix._write_target(generation_target, refreshed)
    progress: list[str] = []
    assert (
        crate2nix._refresh_target(generation_target, progress=progress.append)
        == refreshed
    )
    assert calls == 1
    assert progress == ["Reusing verified crate2nix artifacts"]

    async def cached_refresh() -> crate2nix.RefreshResult:
        async with runtime_scope(resolve_config()) as runtime:
            result = await asyncio.to_thread(
                crate2nix._refresh_target, generation_target
            )
            assert (
                runtime.timing(
                    generation_target.name, "crate2nix_generation"
                ).cache_hits
                == 1
            )
            return result

    assert asyncio.run(cached_refresh()) == refreshed
    assert calls == 1
    (tmp_path / generation_target.cargo_nix).write_text("drift\n", encoding="utf-8")
    assert crate2nix._refresh_target(generation_target) == refreshed
    assert calls == 2
    crate2nix._write_target(generation_target, refreshed)
    (tmp_path / "demo/normalize.py").write_text("updated\n", encoding="utf-8")
    assert crate2nix._refresh_target(generation_target) == refreshed
    assert calls == 3


def test_manifest_receipt_requires_the_manifest_and_valid_utf8(
    tmp_path: Path, generation_target: crate2nix.Crate2NixTarget
) -> None:
    """Every declared artifact, including source slices, belongs to the receipt."""
    target = dataclasses.replace(
        generation_target, crate_sources=Path("demo/crate-sources.json")
    )
    outputs = {str(path): "{}\n" for path in target.artifact_paths}
    for name, content in outputs.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    generation_receipts.save(
        crate2nix._generation_receipt_path(target), identity="key", outputs=outputs
    )
    cached = crate2nix._cached_refresh(target, "key")
    assert cached is not None
    assert cached.crate_sources == "{}\n"
    (tmp_path / target.crate_hashes).write_bytes(b"\xff")
    assert crate2nix._cached_refresh(target, "key") is None
    assert crate2nix._cached_refresh(target, None) is None
