"""Shared builders for update CLI orchestration tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Protocol

from lib.nix.models.sources import SourceEntry, SourcesFile
from lib.update.cli import ResolvedTargets, _RunPlan

if TYPE_CHECKING:
    from lib.update.refs import FlakeInputRef


class MonkeyPatchLike(Protocol):
    def setattr(
        self,
        target: str,
        value: object,
        *,
        raising: bool = True,
    ) -> None: ...


def make_run_plan(
    *,
    source_names: tuple[str, ...] = (),
    ref_inputs: tuple[FlakeInputRef, ...] = (),
    dry_run: bool = False,
    do_input_refresh: bool = False,
    show_phase_headers: bool = False,
) -> _RunPlan:
    """Build a complete plan from the targets relevant to a test."""
    ref_names = tuple(ref.name for ref in ref_inputs)
    order = list(dict.fromkeys((*ref_names, *source_names)))
    return _RunPlan(
        resolved=ResolvedTargets(
            all_source_names=set(source_names),
            all_ref_inputs=list(ref_inputs),
            all_ref_names=set(ref_names),
            all_known_names=set(order),
            do_refs=bool(ref_inputs),
            do_sources=bool(source_names),
            do_input_refresh=do_input_refresh,
            dry_run=dry_run,
            native_only=False,
            ref_inputs=list(ref_inputs),
            source_names=list(source_names),
        ),
        tty_enabled=False,
        show_phase_headers=show_phase_headers,
        sources=SourcesFile(
            entries={name: SourceEntry(hashes={}) for name in source_names}
        ),
        item_meta={
            name: SimpleNamespace(name=name, origin="x", op_order=()) for name in order
        },
        order=order,
    )


async def drain_events(
    queue: asyncio.Queue[object | None],
    *_args: object,
    **_kwargs: object,
) -> None:
    """Consume orchestration events without coupling tests to presentation."""
    while await queue.get() is not None:
        pass


def configure_isolated_run(
    monkeypatch: MonkeyPatchLike,
    *,
    root: Path,
    plan: _RunPlan,
    execute_result: object,
    planned_paths: tuple[str, ...] = (),
    updaters: object | None = None,
) -> None:
    """Install repeated boundaries for an isolated ``run_updates`` test."""
    monkeypatch.setattr("lib.update.cli.get_repo_root", lambda: root)
    monkeypatch.setattr("lib.update.cli._build_run_plan", lambda *_args: plan)
    monkeypatch.setattr("lib.update.cli._execute_run_plan_result", execute_result)
    monkeypatch.setattr(
        "lib.update.persistence.planned_update_paths",
        lambda *_args: tuple(Path.cwd() / path for path in planned_paths),
    )
    if updaters is not None:
        monkeypatch.setattr("lib.update.cli._get_updaters", lambda: updaters)
