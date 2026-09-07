"""Behavioral coverage for reusing ref refreshes across updater phases."""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lib.nix.models.sources import SourceEntry, SourcesFile
from lib.update import flake, refs, source_runner
from lib.update.config import resolve_config
from lib.update.events import EventSink, UpdateEvent, UpdateEventKind, ignore_event
from lib.update.refs import FlakeInputRef, RefUpdateResult
from lib.update.source_runner import SourcesPhaseContext, UpdatePhaseResult

if TYPE_CHECKING:
    import aiohttp

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
    (tmp_path / "flake.nix").write_bytes(b"original declarations")
    (tmp_path / "flake.lock").write_bytes(b"original lock")
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
        (tmp_path / "flake.nix").write_bytes(b"candidate declarations")
        (tmp_path / "flake.lock").write_bytes(b"candidate lock")

    async def _update_input(
        input_name: str,
        *,
        source: str,
        emit: EventSink = ignore_event,
    ) -> None:
        source_refreshes.append(input_name)
        await emit(UpdateEvent.status(source, "lock refreshed"))

    monkeypatch.setattr(refs, "check_flake_ref_update", _check_ref)
    monkeypatch.setattr(refs, "update_flake_ref", _update_ref)
    monkeypatch.setattr(flake, "update_flake_input", _update_input)

    async def _run() -> list[UpdateEvent | None]:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        config = resolve_config()
        result = await source_runner.run_ref_phase(
            ref_inputs=[FlakeInputRef("input", "owner", "repo", "v1", "github")],
            queue=queue,
            config=config,
        )
        assert result.details == {
            "input": "error" if ref_status == "update_error" else ref_status
        }
        assert result.input_refreshes == (
            {"input": (b"candidate declarations", b"candidate lock")}
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


def test_later_ref_cannot_recapture_an_earlier_inputs_refresh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A later lock rewrite invalidates A's receipt even if both refs succeeded."""
    (tmp_path / "flake.nix").write_bytes(b"declarations")
    (tmp_path / "flake.lock").write_bytes(b"initial")
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
            (tmp_path / "flake.lock").write_bytes(b"A refreshed")
        else:
            assert (tmp_path / "flake.lock").read_bytes() == b"A refreshed"
            (tmp_path / "flake.lock").write_bytes(b"B rewrote A and shared topology")

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
        assert result.input_refreshes == {
            "input": (b"declarations", b"A refreshed"),
            "other": (b"declarations", b"B rewrote A and shared topology"),
        }
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
    assert refreshes == ["input"]


@pytest.mark.parametrize("cancel", [False, True])
def test_ref_result_failure_never_publishes_a_refresh_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    cancel: bool,
) -> None:
    """A cancelled or failed ref task cannot seed a later successful source phase."""
    (tmp_path / "flake.nix").write_bytes(b"declarations")
    (tmp_path / "flake.lock").write_bytes(b"initial")
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
        (tmp_path / "flake.lock").write_bytes(b"candidate")

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
