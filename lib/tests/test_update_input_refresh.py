"""Behavioral coverage for reusing ref refreshes across updater phases."""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.flake_lock import FlakeLock, FlakeLockNode, LockedRef
from lib.nix.models.sources import SourceEntry, SourcesFile
from lib.update import flake, refs, source_runner
from lib.update.config import resolve_config
from lib.update.events import EventSink, UpdateEvent, UpdateEventKind, ignore_event
from lib.update.refs import FlakeInputRef, RefUpdateResult
from lib.update.source_runner import SourcesPhaseContext, UpdatePhaseResult

if TYPE_CHECKING:
    import aiohttp

    from lib.update.config import UpdateConfig
    from lib.update.updaters import UpdateContext


class _InputUpdater:
    input_name = "input"

    def __init__(self, *, config: object) -> None:
        _ = config

    async def update_stream(
        self,
        current: SourceEntry | None,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext,
        emit: EventSink = ignore_event,
    ) -> None:
        _ = current, session, context
        await emit(UpdateEvent.result("demo"))


def _write_input_files(
    root: Path, *, input_ref: str = "v1", other_ref: str = "v1", shared_ref: str = "v1"
) -> None:
    versions = {"input": input_ref, "other": other_ref, "shared": shared_ref}
    declarations = AttributeSet.from_dict({
        "inputs": {
            name: {"url": f"github:owner/{name}/{version}"}
            for name, version in versions.items()
        }
    })
    lock = FlakeLock(
        version=7,
        nodes={
            "root": FlakeLockNode(inputs={name: name for name in versions}),
            **{
                name: FlakeLockNode(
                    locked=LockedRef(
                        type="github",
                        owner="owner",
                        repo=name,
                        rev=version,
                        narHash="sha256-test",
                    ),
                    inputs={"dependency": ["shared"]} if name == "input" else None,
                )
                for name, version in versions.items()
            },
        },
    )
    (root / "flake.nix").write_text(declarations.rebuild())
    (root / "flake.lock").write_text(lock.model_dump_json())


@pytest.mark.parametrize(
    ("ref_status", "changed_file", "expected_refreshes"),
    [
        ("updated", None, 0),
        ("no_change", None, 1),
        ("error", None, 1),
        ("update_error", None, 1),
        ("updated", "flake.nix", 1),
        ("updated", "flake.lock", 1),
    ],
)
def test_sources_reuse_only_successful_unchanged_ref_refreshes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ref_status: str,
    changed_file: str | None,
    expected_refreshes: int,
) -> None:
    """Keep ref and source execution real while substituting the network boundary."""
    _write_input_files(tmp_path)
    monkeypatch.setattr(flake, "get_repo_root", lambda: tmp_path)
    monkeypatch.setattr(source_runner, "UPDATERS", {"demo": _InputUpdater})
    source_refreshes: list[str] = []

    async def _check_ref(*_args: object, **_kwargs: object) -> RefUpdateResult:
        return RefUpdateResult(
            name="input",
            current_ref="v1",
            latest_ref="v1" if ref_status == "no_change" else "v2",
            error="upstream unavailable" if ref_status == "error" else None,
        )

    async def _update_ref(*_args: object, **_kwargs: object) -> None:
        if ref_status == "update_error":
            raise RuntimeError("lock refresh failed")
        _write_input_files(tmp_path, input_ref="v2")

    async def _update_input(
        input_name: str,
        *,
        source: str,
        emit: EventSink = ignore_event,
        config: UpdateConfig,
    ) -> None:
        assert config.default_subprocess_timeout == 17
        source_refreshes.append(input_name)
        await emit(UpdateEvent.status(source, "lock refreshed"))

    monkeypatch.setattr(refs, "check_flake_ref_update", _check_ref)
    monkeypatch.setattr(refs, "update_flake_ref", _update_ref)
    monkeypatch.setattr(flake, "update_flake_input", _update_input)

    async def _run() -> list[UpdateEvent | None]:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        config = resolve_config(subprocess_timeout=17)
        result = await source_runner.run_ref_phase(
            ref_inputs=[FlakeInputRef("input", "owner", "repo", "v1", "github")],
            queue=queue,
            config=config,
        )
        assert result.details == {
            "input": "error" if ref_status == "update_error" else ref_status
        }
        assert result.input_refreshes == (
            {"input": flake.read_flake_input_state("input")}
            if ref_status == "updated"
            else {}
        )
        if changed_file is not None:
            (tmp_path / changed_file).write_bytes(b"later modification")
        merged = UpdatePhaseResult().merged(result)
        source_result = await source_runner.run_sources_phase(
            SourcesPhaseContext(
                source_names=["demo"],
                sources=SourcesFile(entries={"demo": SourceEntry(hashes={})}),
                queue=queue,
                update_input=True,
                native_only=False,
                config=config,
                input_refreshes=merged.input_refreshes,
            )
        )
        assert source_result.details == {"demo": "no_change"}
        return [queue.get_nowait() for _ in range(queue.qsize())]

    events = asyncio.run(_run())
    assert source_refreshes == ["input"] * expected_refreshes
    reuse_messages = [
        event.message
        for event in events
        if event is not None
        and event.source == "demo"
        and "Reusing" in (event.message or "")
    ]
    assert reuse_messages == (
        ["Reusing flake input 'input' refresh..."] if expected_refreshes == 0 else []
    )


@pytest.mark.parametrize("changes_dependency", [False, True])
def test_later_ref_reuses_only_independent_earlier_refreshes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    changes_dependency: bool,
) -> None:
    """Updating B preserves A's receipt only when A's dependencies stay unchanged."""
    _write_input_files(tmp_path)
    monkeypatch.setattr(flake, "get_repo_root", lambda: tmp_path)
    monkeypatch.setattr(source_runner, "UPDATERS", {"demo": _InputUpdater})
    refreshes: list[str] = []

    async def _check(
        input_ref: FlakeInputRef, *_args: object, **_kwargs: object
    ) -> RefUpdateResult:
        return RefUpdateResult(input_ref.name, "v1", "v2")

    async def _update_ref(
        input_ref: FlakeInputRef, *_args: object, **_kwargs: object
    ) -> None:
        if input_ref.name == "input":
            _write_input_files(tmp_path, input_ref="v2")
        else:
            _write_input_files(
                tmp_path,
                input_ref="v2",
                other_ref="v2",
                shared_ref="v2" if changes_dependency else "v1",
            )

    async def _update_input(input_name: str, **_kwargs: object) -> None:
        refreshes.append(input_name)

    monkeypatch.setattr(refs, "check_flake_ref_update", _check)
    monkeypatch.setattr(refs, "update_flake_ref", _update_ref)
    monkeypatch.setattr(flake, "update_flake_input", _update_input)

    async def _run() -> None:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        config = resolve_config()
        result = await source_runner.run_ref_phase(
            ref_inputs=[
                FlakeInputRef(name, "owner", "repo", "v1", "github")
                for name in ("input", "other")
            ],
            queue=queue,
            config=config,
        )
        assert result.details == {"input": "updated", "other": "updated"}
        assert set(result.input_refreshes) == {"input", "other"}
        assert (
            result.input_refreshes["input"] == flake.read_flake_input_state("input")
        ) is not changes_dependency
        result = await source_runner.run_sources_phase(
            SourcesPhaseContext(
                source_names=["demo"],
                sources=SourcesFile(entries={}),
                queue=queue,
                update_input=True,
                native_only=False,
                config=config,
                input_refreshes=result.input_refreshes,
            )
        )
        assert result.details == {"demo": "no_change"}

    asyncio.run(_run())
    assert refreshes == (["input"] if changes_dependency else [])


@pytest.mark.parametrize("cancel", [False, True])
def test_ref_result_failure_never_publishes_a_refresh_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    cancel: bool,
) -> None:
    """A cancelled or failed ref task cannot seed a later successful source phase."""
    _write_input_files(tmp_path)
    monkeypatch.setattr(flake, "get_repo_root", lambda: tmp_path)
    monkeypatch.setattr(source_runner, "UPDATERS", {"demo": _InputUpdater})
    refreshes: list[str] = []

    class _RejectRefResult(asyncio.Queue[UpdateEvent | None]):
        async def put(self, event: UpdateEvent | None) -> None:
            if (
                event is not None
                and event.source == "input"
                and event.kind is UpdateEventKind.RESULT
            ):
                if cancel:
                    raise asyncio.CancelledError
                raise RuntimeError("ref event sink closed")
            await super().put(event)

    async def _check(*_args: object, **_kwargs: object) -> RefUpdateResult:
        return RefUpdateResult("input", "v1", "v2")

    async def _update_ref(*_args: object, **_kwargs: object) -> None:
        _write_input_files(tmp_path, input_ref="v2")

    async def _update_input(input_name: str, **_kwargs: object) -> None:
        refreshes.append(input_name)

    monkeypatch.setattr(refs, "check_flake_ref_update", _check)
    monkeypatch.setattr(refs, "update_flake_ref", _update_ref)
    monkeypatch.setattr(flake, "update_flake_input", _update_input)

    async def _run() -> None:
        queue = _RejectRefResult()
        config = resolve_config()
        phase = source_runner.run_ref_phase(
            ref_inputs=[FlakeInputRef("input", "owner", "repo", "v1", "github")],
            queue=queue,
            config=config,
        )
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await phase
            input_refreshes = {}
        else:
            result = await phase
            assert result.details == {"input": "error"}
            assert result.input_refreshes == {}
            input_refreshes = result.input_refreshes
        result = await source_runner.run_sources_phase(
            SourcesPhaseContext(
                source_names=["demo"],
                sources=SourcesFile(entries={}),
                queue=queue,
                update_input=True,
                native_only=False,
                config=config,
                input_refreshes=input_refreshes,
            )
        )
        assert result.details == {"demo": "no_change"}

    asyncio.run(_run())
    assert refreshes == ["input"]
