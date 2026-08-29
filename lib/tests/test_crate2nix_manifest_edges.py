"""Focused orchestration edges for crate2nix manifest refreshes."""

import asyncio
import errno
import json
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lib.update import crate2nix
from lib.update.events import UpdateEvent, UpdateEventKind

if TYPE_CHECKING:
    from lib.update.artifacts import GeneratedArtifact


def _target(*, crate_sources: Path | None) -> crate2nix.Crate2NixTarget:
    return crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo-crate2nix-src",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("test-system",),
        source_input="demo" if crate_sources is not None else None,
        crate_sources=crate_sources,
    )


def test_manifest_propagates_cancellation_and_progress_to_both_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Manifest root resolution and slice hashing share caller coordination."""
    target = _target(crate_sources=Path("demo/crate-sources.json"))
    cargo_nix = tmp_path / "Cargo.nix"
    cargo_nix.write_text("generated Cargo.nix\n", encoding="utf-8")
    root = tmp_path / "source"
    cancel_event = threading.Event()
    progress_messages: list[str] = []
    progress = progress_messages.append
    calls: list[str] = []

    def resolve(
        actual_target: crate2nix.Crate2NixTarget,
        *,
        cancel_event: threading.Event | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> tuple[Path, str]:
        assert actual_target is target
        assert cancel_event is expected_cancel_event
        assert progress is expected_progress
        calls.append("resolve")
        return root, "sha256-root="

    def materialize(
        actual_root: Path,
        source_paths: tuple[str, ...],
        actual_cargo_nix: Path,
        *,
        root_source_name: str,
        cancel_event: threading.Event | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, crate2nix.CrateSourceSlice]:
        assert actual_root == root
        assert source_paths == ("crates/demo",)
        assert actual_cargo_nix == cargo_nix
        assert root_source_name == "source"
        assert cancel_event is expected_cancel_event
        assert progress is expected_progress
        calls.append("materialize")
        return {"crates/demo": {"hash": "sha256-slice=", "name": "demo"}}

    expected_cancel_event = cancel_event
    expected_progress = progress
    monkeypatch.setattr(crate2nix, "_resolve_production_root", resolve)
    monkeypatch.setattr(crate2nix, "_materialize_source_slices", materialize)

    rendered = crate2nix._render_crate_source_manifest(
        target,
        ("crates/demo",),
        cargo_nix=cargo_nix,
        cancel_event=cancel_event,
        progress=progress,
    )

    assert calls == ["resolve", "materialize"]
    assert json.loads(rendered)["slices"] == {
        "crates/demo": {"hash": "sha256-slice=", "name": "demo"}
    }


def test_refresh_propagates_coordination_through_generation_and_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A coordinated refresh must keep one cancellation/progress channel."""
    target = _target(crate_sources=Path("demo/crate-sources.json"))
    patched_src = tmp_path / "patched-src"
    patched_src.mkdir()
    cancel_event = threading.Event()
    progress_messages: list[str] = []
    progress = progress_messages.append
    calls: list[str] = []

    def build(
        actual_target: crate2nix.Crate2NixTarget,
        **kwargs: object,
    ) -> Path:
        assert actual_target is target
        assert kwargs == {
            "source_overrides": None,
            "cancel_event": cancel_event,
            "progress": progress,
        }
        calls.append("build")
        return patched_src

    def generate(
        args: list[str],
        *,
        env: dict[str, str],
        generated_outputs: tuple[Path, ...],
        seeded_outputs: dict[Path, bytes],
        cancel_event: threading.Event | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del env
        assert seeded_outputs == {}
        assert cancel_event is expected_cancel_event
        assert progress is expected_progress
        generated_cargo, generated_hashes = generated_outputs
        generated_cargo.write_text("{}\n", encoding="utf-8")
        generated_hashes.write_text("{}\n", encoding="utf-8")
        calls.append("generate")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    def render(
        actual_target: crate2nix.Crate2NixTarget,
        source_paths: tuple[str, ...],
        *,
        cargo_nix: Path,
        cancel_event: threading.Event | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> str:
        assert actual_target is target
        assert source_paths == ()
        assert cargo_nix.read_text(encoding="utf-8") == "{}\n"
        assert cancel_event is expected_cancel_event
        assert progress is expected_progress
        calls.append("manifest")
        return '{"source": {}, "slices": {}}\n'

    expected_cancel_event = cancel_event
    expected_progress = progress
    monkeypatch.setattr(crate2nix, "_build_patched_src", build)
    monkeypatch.setattr(
        crate2nix,
        "_crate2nix_cargo_home",
        lambda: tmp_path / "cargo-home",
    )
    monkeypatch.setattr(crate2nix, "_filtered_crate_hash_seed", lambda *_args: None)
    monkeypatch.setattr(
        crate2nix,
        "load_normalizer",
        lambda _path: lambda text: (text, 0, False),
    )
    monkeypatch.setattr(crate2nix, "_run_crate2nix_generate", generate)
    monkeypatch.setattr(crate2nix, "_render_crate_source_manifest", render)

    refreshed = crate2nix._refresh_target_impl(
        target,
        cancel_event=cancel_event,
        progress=progress,
    )

    assert calls == ["build", "generate", "manifest"]
    assert refreshed == crate2nix.RefreshResult(
        cargo_nix="{}\n",
        crate_hashes="{}\n",
        crate_sources='{"source": {}, "slices": {}}\n',
    )


def test_refresh_rejects_local_sources_without_a_manifest_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A generated local source contract requires a declared source manifest."""
    target = _target(crate_sources=None)
    patched_src = tmp_path / "patched-src"
    patched_src.mkdir()

    def generate(
        args: list[str],
        *,
        generated_outputs: tuple[Path, ...],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        generated_cargo, generated_hashes = generated_outputs
        generated_cargo.write_text("{}\n", encoding="utf-8")
        generated_hashes.write_text("{}\n", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(crate2nix, "_build_patched_src", lambda _target: patched_src)
    monkeypatch.setattr(
        crate2nix,
        "_crate2nix_cargo_home",
        lambda: tmp_path / "cargo-home",
    )
    monkeypatch.setattr(crate2nix, "_filtered_crate_hash_seed", lambda *_args: None)
    monkeypatch.setattr(
        crate2nix,
        "load_normalizer",
        lambda _path: lambda text: (text, 0, False),
    )
    monkeypatch.setattr(crate2nix, "_run_crate2nix_generate", generate)
    monkeypatch.setattr(
        crate2nix,
        "_apply_crate_source_contract",
        lambda text: (text, ("crates/demo",)),
    )

    with pytest.raises(
        RuntimeError,
        match="Missing crate source artifact path for demo",
    ):
        crate2nix._refresh_target_impl(target)


def test_refresh_reraises_non_storage_os_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only ENOSPC is reclassified; unrelated operating-system errors survive."""
    target = _target(crate_sources=None)
    failure = OSError(errno.EIO, "input/output error")

    def fail(*_args: object, **_kwargs: object) -> crate2nix.RefreshResult:
        raise failure

    monkeypatch.setattr(crate2nix, "_refresh_target_impl", fail)

    with pytest.raises(OSError, match="input/output error") as raised:
        crate2nix._refresh_target(target)

    assert raised.value is failure


@pytest.mark.parametrize(
    "failure",
    [
        OSError(errno.EIO, "input/output error"),
        RuntimeError("runtime failure"),
        TypeError("type failure"),
        ValueError("value failure"),
    ],
)
def test_cancel_worker_swallows_expected_teardown_failures(failure: Exception) -> None:
    """Stream teardown retrieves known worker failures after signalling cancel."""

    async def cancel() -> None:
        future: asyncio.Future[tuple[GeneratedArtifact, ...]] = (
            asyncio.get_running_loop().create_future()
        )
        future.set_exception(failure)
        cancel_event = threading.Event()

        await crate2nix._cancel_artifact_worker(future, cancel_event)

        assert cancel_event.is_set()
        assert future.done()

    asyncio.run(cancel())


def test_stream_drains_progress_queued_by_an_already_completed_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Progress queued immediately before worker completion remains observable."""
    target = _target(crate_sources=None)
    monkeypatch.setitem(crate2nix.TARGETS, target.name, target)
    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "test-system")

    def artifacts(
        _name: str,
        *,
        cancel_event: threading.Event,
        progress: Callable[[str], None],
    ) -> tuple[GeneratedArtifact, ...]:
        assert not cancel_event.is_set()
        progress("queued at completion")
        return ()

    monkeypatch.setattr(crate2nix, "crate2nix_artifact_updates", artifacts)

    async def collect() -> list[UpdateEvent]:
        loop = asyncio.get_running_loop()

        def run_immediately(
            _executor: None,
            worker: Callable[[], tuple[GeneratedArtifact, ...]],
        ) -> asyncio.Future[tuple[GeneratedArtifact, ...]]:
            future: asyncio.Future[tuple[GeneratedArtifact, ...]] = loop.create_future()
            future.set_result(worker())
            return future

        monkeypatch.setattr(loop, "run_in_executor", run_immediately)
        return [
            event
            async for event in crate2nix.stream_crate2nix_artifact_updates(target.name)
        ]

    events = asyncio.run(collect())

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.LINE,
        UpdateEventKind.STATUS,
    ]
    assert events[1].message == "queued at completion"
    assert events[1].stream == "crate2nix"
