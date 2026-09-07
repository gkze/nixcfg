"""Tests for the codex-v8 updater."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.nix.models.sources import HashEntry
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import collect_events as _collect_events
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import EventSink, UpdateEvent, ignore_event
from lib.update.nix import _build_fetchgit_call
from lib.update.updaters import UpdateContext, VersionInfo

_COMMIT = "a" * 40


def _load_module(module_name: str):
    return load_repo_module("overlays/codex-v8/updater.py", module_name)


def test_codex_v8_updater_computes_recursive_src_hash(monkeypatch) -> None:
    """Compute source and Linux prebuilt hashes from the resolved commit."""
    module = _load_module("codex_v8_updater_test")
    updater = module.CodexV8Updater()

    calls: list[str] = []
    url_batches: list[list[str]] = []

    async def _hash_stream(
        _name: str, expr: str, *, config=None, emit: EventSink = ignore_event
    ) -> object:
        _ = config
        calls.append(expr)
        return "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

    async def _url_hashes(
        _name: str, urls, *, config=None, emit: EventSink = ignore_event
    ) -> object:
        assert config is updater.config
        url_batches.append(list(urls))
        return {
            url_batches[0][0]: "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
            url_batches[0][1]: "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
        }

    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _hash_stream)
    monkeypatch.setattr("lib.update.process.compute_url_hashes", _url_hashes)

    events = _run(
        _collect_events(
            lambda emit: updater.fetch_hashes(
                VersionInfo(
                    version="v999.0.0",
                    metadata={"commit": _COMMIT, "tag": "v999.0.0"},
                ),
                object(),
                emit=emit,
                context=UpdateContext(current=None),
            )
        )
    )

    assert_nix_ast_equal(
        calls[0],
        _build_fetchgit_call(
            "https://github.com/denoland/rusty_v8.git",
            _COMMIT,
            fetch_submodules=True,
        ),
    )
    assert events.result == [
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

    async def _resolve_commit(session: object, tag: str) -> str:
        assert tag == "v147.4.0"
        return _COMMIT

    monkeypatch.setattr(updater, "_resolve_release_tag_commit", _resolve_commit)

    latest = _run(updater.fetch_latest(object(), context=UpdateContext(current=None)))

    assert latest.version == "v147.4.0"
    assert latest.commit == _COMMIT


def test_codex_v8_fetch_latest_prefers_generated_cargo_nix_artifact(
    monkeypatch,
) -> None:
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

    async def _resolve_commit(session: object, tag: str) -> str:
        assert tag == "v148.1.2"
        return _COMMIT

    monkeypatch.setattr(updater, "_resolve_release_tag_commit", _resolve_commit)

    latest = _run(updater.fetch_latest(object(), context=context))

    assert latest.version == "v148.1.2"
    assert latest.commit == _COMMIT


def test_codex_v8_version_requires_cargo_nix_v8_entry() -> None:
    """Fail clearly when Codex's generated Cargo.nix no longer exposes v8."""
    module = _load_module("codex_v8_updater_missing_version_test")

    with pytest.raises(RuntimeError, match="Could not resolve Codex v8 version"):
        module.CodexV8Updater._codex_v8_version("{ }\n")


def test_codex_v8_is_latest_requires_all_expected_hash_entries() -> None:
    """The updater should only accept current entries with all required hashes present."""
    module = _load_module("codex_v8_updater_latest_test")
    updater = module.CodexV8Updater()
    latest = VersionInfo(
        version="v999.0.0",
        metadata={"commit": _COMMIT, "tag": "v999.0.0"},
    )

    incomplete = SimpleNamespace(
        version="v999.0.0",
        commit=_COMMIT,
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
        commit=_COMMIT,
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

    assert _run(updater._is_latest(UpdateContext(current=None), latest)) is False
    assert (
        _run(
            updater._is_latest(
                UpdateContext(
                    current=SimpleNamespace(
                        version="v999.0.0",
                        commit=_COMMIT,
                        hashes=SimpleNamespace(entries=None),
                    )
                ),
                latest,
            )
        )
        is False
    )
    assert _run(updater._is_latest(UpdateContext(current=incomplete), latest)) is False
    assert (
        _run(
            updater._is_latest(
                UpdateContext(
                    current=SimpleNamespace(
                        version="v999.0.0",
                        commit="b" * 40,
                        hashes=complete.hashes,
                    )
                ),
                latest,
            )
        )
        is False
    )
    assert _run(updater._is_latest(UpdateContext(current=complete), latest)) is True


def test_codex_v8_result_persists_the_resolved_commit() -> None:
    """Keep the release tag for assets while fetching source by immutable commit."""
    module = _load_module("codex_v8_updater_result_commit_test")
    updater = module.CodexV8Updater()
    info = VersionInfo(
        version="v999.0.0",
        metadata={"commit": _COMMIT, "tag": "v999.0.0"},
    )

    result = updater.build_result(
        info,
        [
            HashEntry.create(
                "srcHash",
                "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            )
        ],
    )

    assert result.version == "v999.0.0"
    assert result.commit == _COMMIT


def test_codex_v8_fetch_hashes_forwards_hash_progress(monkeypatch) -> None:
    """Progress from both hash operations is preserved."""
    module = _load_module("codex_v8_updater_forwarding_test")
    updater = module.CodexV8Updater()

    async def _hash_stream(
        _name: str, _expr: str, *, config=None, emit: EventSink = ignore_event
    ) -> object:
        _ = config
        await emit(UpdateEvent.status("codex-v8", "computing src"))
        return "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

    async def _url_hashes(
        _name: str, urls, *, config=None, emit: EventSink = ignore_event
    ) -> object:
        assert config is updater.config
        urls = list(urls)
        await emit(UpdateEvent.status("codex-v8", "computing assets"))
        return {
            urls[0]: "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
            urls[1]: "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
        }

    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _hash_stream)
    monkeypatch.setattr("lib.update.process.compute_url_hashes", _url_hashes)

    events = _run(
        _collect_events(
            lambda emit: updater.fetch_hashes(
                VersionInfo(
                    version="v999.0.0",
                    metadata={"commit": _COMMIT, "tag": "v999.0.0"},
                ),
                object(),
                emit=emit,
                context=UpdateContext(current=None),
            )
        )
    )

    assert [event.kind.value for event in events] == ["status", "status"]
    assert [event.message for event in events] == [
        "computing src",
        "computing assets",
    ]
