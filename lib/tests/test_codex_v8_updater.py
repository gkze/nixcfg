"""Tests for the codex-v8 updater."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.nix.models.sources import HashEntry
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import collect_events as _collect_events
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.nix import _build_fetchgit_call
from lib.update.updaters.base import VersionInfo


def _load_module(module_name: str):
    return load_repo_module("overlays/codex-v8/updater.py", module_name)


def test_codex_v8_updater_computes_recursive_src_hash(monkeypatch) -> None:
    """Compute source and Linux prebuilt hashes from the selected rusty_v8 tag."""
    module = _load_module("codex_v8_updater_test")
    updater = module.CodexV8Updater()

    calls: list[str] = []
    url_batches: list[list[str]] = []

    async def _hash_stream(_name: str, expr: str, *, config=None):
        _ = config
        calls.append(expr)
        yield module.UpdateEvent.value(
            "codex-v8",
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        )

    async def _url_hashes(_name: str, urls):
        url_batches.append(list(urls))
        yield module.UpdateEvent.value(
            "codex-v8",
            {
                url_batches[0][
                    0
                ]: "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                url_batches[0][
                    1
                ]: "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
            },
        )

    monkeypatch.setattr(module, "compute_fixed_output_hash", _hash_stream)
    monkeypatch.setattr(module, "compute_url_hashes", _url_hashes)

    events = _run(
        _collect_events(
            updater.fetch_hashes(
                VersionInfo(version="v999.0.0"),
                object(),
            )
        )
    )

    assert_nix_ast_equal(
        calls[0],
        _build_fetchgit_call(
            "https://github.com/denoland/rusty_v8.git",
            "v999.0.0",
            fetch_submodules=True,
        ),
    )
    assert events[-1].payload == [
        HashEntry.create(
            "srcHash",
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        ),
        HashEntry.create(
            "rustyV8ArchiveHash",
            "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
            platform="x86_64-linux",
            url="https://github.com/denoland/rusty_v8/releases/download/v999.0.0/librusty_v8_release_x86_64-unknown-linux-gnu.a.gz",
        ),
        HashEntry.create(
            "rustyV8BindingHash",
            "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
            platform="x86_64-linux",
            url="https://github.com/denoland/rusty_v8/releases/download/v999.0.0/src_binding_release_x86_64-unknown-linux-gnu.rs",
        ),
    ]


def test_codex_v8_fetch_latest_reads_version_from_codex_cargo_nix(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Resolve the target rusty_v8 version from Codex's generated Cargo.nix."""
    module = _load_module("codex_v8_updater_fetch_latest_test")
    updater = module.CodexV8Updater()
    repo_root = tmp_path / "repo"
    cargo_nix = repo_root / "packages" / "codex" / "Cargo.nix"
    cargo_nix.parent.mkdir(parents=True)
    cargo_nix.write_text(
        '{\n  "v8" = rec {\n    version = "147.4.0";\n  };\n}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "REPO_ROOT", repo_root)

    latest = _run(updater.fetch_latest(object()))

    assert latest.version == "v147.4.0"


def test_codex_v8_fetch_latest_prefers_generated_cargo_nix_artifact() -> None:
    """Use earlier in-run Cargo.nix artifacts before falling back to the repo copy."""
    module = _load_module("codex_v8_updater_fetch_latest_artifact_test")
    updater = module.CodexV8Updater()
    context = module.UpdateContext(
        current=None,
        generated_artifacts={
            Path("packages/codex/Cargo.nix"): (
                '{\n  "v8" = rec {\n    version = "148.1.2";\n  };\n}\n'
            )
        },
    )

    latest = _run(updater.fetch_latest(object(), context=context))

    assert latest.version == "v148.1.2"


def test_codex_v8_version_requires_cargo_nix_v8_entry() -> None:
    """Fail clearly when Codex's generated Cargo.nix no longer exposes v8."""
    module = _load_module("codex_v8_updater_missing_version_test")

    with pytest.raises(RuntimeError, match="Could not resolve Codex v8 version"):
        module.CodexV8Updater._codex_v8_version("{ }\n")


def test_codex_v8_is_latest_requires_all_expected_hash_entries() -> None:
    """The updater should only accept current entries with all required hashes present."""
    module = _load_module("codex_v8_updater_latest_test")
    updater = module.CodexV8Updater()
    latest = VersionInfo(version="v999.0.0")

    incomplete = SimpleNamespace(
        version="v999.0.0",
        hashes=SimpleNamespace(
            entries=[
                HashEntry.create(
                    "srcHash",
                    "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                )
            ]
        ),
    )
    complete = SimpleNamespace(
        version="v999.0.0",
        hashes=SimpleNamespace(
            entries=[
                HashEntry.create(
                    "srcHash",
                    "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                ),
                HashEntry.create(
                    "rustyV8ArchiveHash",
                    "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                    platform="x86_64-linux",
                ),
                HashEntry.create(
                    "rustyV8BindingHash",
                    "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
                    platform="x86_64-linux",
                ),
            ]
        ),
    )

    assert _run(updater._is_latest(None, latest)) is False
    assert (
        _run(
            updater._is_latest(
                SimpleNamespace(
                    version="v999.0.0",
                    hashes=SimpleNamespace(entries=None),
                ),
                latest,
            )
        )
        is False
    )
    assert _run(updater._is_latest(incomplete, latest)) is False
    assert _run(updater._is_latest(complete, latest)) is True


def test_codex_v8_fetch_hashes_forwards_non_value_events(monkeypatch) -> None:
    """Non-value events from both hash streams should be preserved."""
    module = _load_module("codex_v8_updater_forwarding_test")
    updater = module.CodexV8Updater()

    async def _hash_stream(_name: str, _expr: str, *, config=None):
        _ = config
        yield module.UpdateEvent.status("codex-v8", "computing src")
        yield module.UpdateEvent.value(
            "codex-v8",
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        )

    async def _url_hashes(_name: str, urls):
        urls = list(urls)
        yield module.UpdateEvent.status("codex-v8", "computing assets")
        yield module.UpdateEvent.value(
            "codex-v8",
            {
                urls[0]: "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                urls[1]: "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
            },
        )

    monkeypatch.setattr(module, "compute_fixed_output_hash", _hash_stream)
    monkeypatch.setattr(module, "compute_url_hashes", _url_hashes)

    events = _run(
        _collect_events(
            updater.fetch_hashes(
                VersionInfo(version="v999.0.0"),
                object(),
            )
        )
    )

    assert [event.kind.value for event in events] == ["status", "status", "value"]
    assert [event.message for event in events[:-1]] == [
        "computing src",
        "computing assets",
    ]
