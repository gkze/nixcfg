"""Additional branch coverage tests for update CLI internals."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING

import aiohttp
import pytest

from lib.nix.models.sources import SourceEntry, SourcesFile
from lib.tests._assertions import check
from lib.update.cli import (
    OutputOptions,
    ResolvedTargets,
    UpdateOptions,
    UpdateSummary,
    _build_item_meta,
    _build_run_plan,
    _build_update_options,
    _emit_summary,
    _execute_run_plan,
    _handle_list_targets_request,
    _handle_validate_request,
    _InventoryHandles,
    _InventoryRefTarget,
    _InventorySourceTarget,
    _InventoryTarget,
    _is_tty,
    _persist_source_updates,
    _run_ref_phase,
    _run_sources_phase,
    _RunPlan,
    _SourcesPhaseContext,
    _SourceTaskContext,
    _update_source_task,
    run_update_command,
)
from lib.update.config import resolve_config
from lib.update.events import UpdateEvent
from lib.update.refs import FlakeInputRef
from lib.update.updaters.base import DenoDepsHashUpdater, VersionInfo

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _run[T](awaitable: object) -> T:
    return asyncio.run(awaitable)  # type: ignore[arg-type]


def test_build_options_and_is_tty_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cover json alias absence and env-driven tty default resolution."""
    opts = _build_update_options({"source": "demo", "check": True})
    check(opts.source == "demo")
    check(opts.json is False)

    monkeypatch.setenv("UPDATE_FORCE_TTY", "0")
    monkeypatch.setenv("UPDATE_NO_TTY", "0")
    monkeypatch.setenv("UPDATE_ZELLIJ_GUARD", "0")
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    check(_is_tty() is True)


def test_update_summary_does_not_downgrade_status() -> None:
    """Keep higher-priority status when a lower status is merged later."""
    summary = UpdateSummary()
    summary._set_status("demo", "error")
    summary._set_status("demo", "no_change")
    summary._rebuild_lists()
    check(summary.errors == ["demo"])


def test_resolved_targets_ref_source_and_item_meta_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover ref-only source selection and all item-meta origin branches."""
    monkeypatch.setattr("lib.update.cli.UPDATERS", {"src": object})
    monkeypatch.setattr(
        "lib.update.cli.get_flake_inputs_with_refs",
        lambda: [
            FlakeInputRef(
                name="ref-only", owner="o", repo="r", ref="v1", input_type="github"
            )
        ],
    )

    resolved_ref = ResolvedTargets.from_options(UpdateOptions(source="ref-only"))
    check(resolved_ref.do_refs is True)
    check(resolved_ref.do_sources is False)

    resolved = ResolvedTargets(
        all_source_names={"both", "src-with-input", "src-no-input"},
        all_ref_inputs=[
            FlakeInputRef(
                name="both", owner="o", repo="r", ref="v1", input_type="github"
            ),
            FlakeInputRef(
                name="flake-only", owner="o", repo="r", ref="v1", input_type="github"
            ),
        ],
        all_ref_names={"both", "flake-only"},
        all_known_names={"both", "flake-only", "src-with-input", "src-no-input"},
        do_refs=True,
        do_sources=True,
        do_input_refresh=True,
        dry_run=False,
        native_only=False,
        ref_inputs=[
            FlakeInputRef(
                name="both", owner="o", repo="r", ref="v1", input_type="github"
            ),
            FlakeInputRef(
                name="flake-only", owner="o", repo="r", ref="v1", input_type="github"
            ),
        ],
        source_names=["both", "src-with-input", "src-no-input"],
    )
    sources = SourcesFile(
        entries={
            "both": SourceEntry(hashes={}, input="inp"),
            "src-with-input": SourceEntry(hashes={}, input="inp"),
            "src-no-input": SourceEntry(hashes={}, input=None),
        }
    )
    meta, _order = _build_item_meta(resolved, sources)
    check(meta["both"].origin.endswith("flake.nix + sources.json)"))
    check(meta["flake-only"].origin.endswith("flake.nix)"))
    check(meta["src-with-input"].origin.endswith("sources.json)"))
    check(meta["src-no-input"].origin.endswith("sources.json)"))


def test_emit_summary_dry_run_updates_and_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Print dry-run update list and failed sources in human mode."""
    summary = UpdateSummary(updated=["a"], errors=["b"], no_change=[])
    code = _emit_summary(
        summary,
        had_errors=True,
        out=OutputOptions(json_output=False, quiet=False),
        dry_run=True,
    )
    check(code == 1)
    captured = capsys.readouterr()
    check("Available updates" in captured.out)
    check("Failed: b" in captured.err)


def test_list_and_validate_non_json_paths(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exercise non-JSON list/validate printing branches and early returns."""
    monkeypatch.setattr(
        "lib.update.cli._build_update_inventory",
        lambda: [
            _InventoryTarget(
                name="inp",
                handles=_InventoryHandles(
                    ref_update=True,
                    input_refresh=False,
                    source_update=False,
                    artifact_write=False,
                ),
                classification="refOnly",
                backing_input="inp",
                ref_target=_InventoryRefTarget(
                    input_name="inp",
                    source_type="github",
                    owner="o",
                    repo="r",
                    selector="v1",
                    locked_rev="deadbeef",
                ),
                source_target=None,
                generated_artifacts=(),
            ),
            _InventoryTarget(
                name="a",
                handles=_InventoryHandles(
                    ref_update=False,
                    input_refresh=False,
                    source_update=True,
                    artifact_write=False,
                ),
                classification="sourceOnly",
                backing_input=None,
                ref_target=None,
                source_target=_InventorySourceTarget(
                    path="packages/a/sources.json",
                    version="1.0.0",
                    commit=None,
                    hash_kinds=("sha256",),
                    updater_kind="download",
                    updater_class="AUpdater",
                ),
                generated_artifacts=(),
            ),
        ],
    )
    check(
        _handle_list_targets_request(UpdateOptions(list_targets=True, json=False)) == 0
    )
    rendered = capsys.readouterr().out
    check("nixcfg update inventory" in rendered)
    check("name" in rendered)
    check("class" in rendered)
    check("touches" in rendered)
    check("selector" in rendered)
    check("writes" in rendered)

    out = OutputOptions(json_output=False, quiet=False)
    check(_handle_validate_request(UpdateOptions(validate=False), out) is None)

    monkeypatch.setattr(
        "lib.update.cli.load_all_sources",
        lambda: SourcesFile(entries={"a": SourceEntry(hashes={})}),
    )
    monkeypatch.setattr(
        "lib.update.cli.validate_source_discovery_consistency", lambda: None
    )
    check(_handle_validate_request(UpdateOptions(validate=True, json=False), out) == 0)
    check("Validated sources.json entries" in capsys.readouterr().out)

    def _boom() -> None:
        msg = "broken"
        raise RuntimeError(msg)

    monkeypatch.setattr("lib.update.cli.validate_source_discovery_consistency", _boom)
    check(_handle_validate_request(UpdateOptions(validate=True, json=False), out) == 1)
    check("Validation failed" in capsys.readouterr().err)


def test_build_item_meta_without_sources_and_list_targets_without_refs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Cover no-sources metadata path and list-targets without flake refs."""
    resolved = ResolvedTargets(
        all_source_names={"src"},
        all_ref_inputs=[
            FlakeInputRef(
                name="src", owner="o", repo="r", ref="v1", input_type="github"
            )
        ],
        all_ref_names={"src"},
        all_known_names={"src"},
        do_refs=True,
        do_sources=True,
        do_input_refresh=True,
        dry_run=False,
        native_only=False,
        ref_inputs=[
            FlakeInputRef(
                name="src", owner="o", repo="r", ref="v1", input_type="github"
            )
        ],
        source_names=["src"],
    )
    monkeypatch.setattr("lib.update.cli.UPDATERS", {})
    meta, order = _build_item_meta(resolved, None)
    check(meta["src"].origin.endswith("flake.nix + sources.json)"))
    check(order == ["src"])

    monkeypatch.setattr(
        "lib.update.cli._build_update_inventory",
        lambda: [
            _InventoryTarget(
                name="src",
                handles=_InventoryHandles(
                    ref_update=False,
                    input_refresh=False,
                    source_update=True,
                    artifact_write=False,
                ),
                classification="sourceOnly",
                backing_input=None,
                ref_target=None,
                source_target=_InventorySourceTarget(
                    path="packages/src/sources.json",
                    version="1.0.0",
                    commit=None,
                    hash_kinds=("sha256",),
                    updater_kind="download",
                    updater_class="SrcUpdater",
                ),
                generated_artifacts=(),
            )
        ],
    )
    check(
        _handle_list_targets_request(UpdateOptions(list_targets=True, json=False)) == 0
    )
    rendered = capsys.readouterr().out
    check("nixcfg update inventory" in rendered)
    check("source" in rendered)


def test_update_source_task_and_phase_runners(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run source/ref phases and execute queued update tasks."""

    class _DummyUpdater:
        input_name = "dummy-input"

        def __init__(self, *, config: object | None = None) -> None:
            self.config = config

        async def update_stream(
            self,
            current: SourceEntry | None,
            session: aiohttp.ClientSession,
            *,
            pinned_version: VersionInfo | None = None,
        ) -> AsyncIterator[UpdateEvent]:
            _ = (current, session, pinned_version)
            yield UpdateEvent.status("demo", "updated")
            yield UpdateEvent.result("demo")

    monkeypatch.setattr("lib.update.cli.UPDATERS", {"demo": _DummyUpdater})

    async def _run_queue_task(
        *, source: str, queue: asyncio.Queue[UpdateEvent | None], task
    ) -> None:
        _ = (source, queue)
        await task()

    async def _update_input(
        _input_name: str, *, source: str
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.status(source, "input refreshed")

    monkeypatch.setattr("lib.update.cli.run_queue_task", _run_queue_task)
    monkeypatch.setattr("lib.update.cli.update_flake_input", _update_input)

    async def _run_source_task() -> list[UpdateEvent]:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        async with aiohttp.ClientSession() as session:
            await _update_source_task(
                "demo",
                context=_SourceTaskContext(
                    sources=SourcesFile(entries={"demo": SourceEntry(hashes={})}),
                    update_input=True,
                    native_only=False,
                    session=session,
                    update_input_lock=asyncio.Lock(),
                    queue=queue,
                    config=resolve_config(),
                ),
            )
        events: list[UpdateEvent] = []
        while not queue.empty():
            item = queue.get_nowait()
            if isinstance(item, UpdateEvent):
                events.append(item)
        return events

    source_events = _run(_run_source_task())
    check(any(event.message == "Starting update" for event in source_events))
    check(any(event.message == "input refreshed" for event in source_events))

    async def _update_ref(
        input_ref: FlakeInputRef,
        session: aiohttp.ClientSession,
        queue: asyncio.Queue[UpdateEvent | None],
        *,
        options: object | None = None,
    ) -> None:
        _ = (session, options)
        await queue.put(UpdateEvent.status(input_ref.name, "ref phase"))

    monkeypatch.setattr("lib.update.cli.update_refs_task", _update_ref)

    async def _run_refs() -> list[UpdateEvent]:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        await _run_ref_phase(
            ref_inputs=[
                FlakeInputRef(
                    name="inp", owner="o", repo="r", ref="v1", input_type="github"
                )
            ],
            queue=queue,
            dry_run=False,
            config=resolve_config(),
        )
        events: list[UpdateEvent] = []
        while not queue.empty():
            item = queue.get_nowait()
            if isinstance(item, UpdateEvent):
                events.append(item)
        return events

    ref_events = _run(_run_refs())
    check(any(event.message == "ref phase" for event in ref_events))

    calls: list[str] = []

    async def _update_source(name: str, *, context: object) -> None:
        _ = context
        calls.append(name)

    monkeypatch.setattr("lib.update.cli._update_source_task", _update_source)
    _run(
        _run_sources_phase(
            context=_SourcesPhaseContext(
                source_names=["demo"],
                sources=SourcesFile(entries={"demo": SourceEntry(hashes={})}),
                queue=asyncio.Queue(),
                update_input=False,
                native_only=False,
                config=resolve_config(),
                pinned={},
            )
        )
    )
    check(calls == ["demo"])


def test_update_source_task_sets_native_only_for_deno_updater(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Set Deno updater native-only flag without invoking a real nix process."""
    created: list[DenoDepsHashUpdater] = []

    class _DenoUpdater(DenoDepsHashUpdater):
        name = "demo"
        input_name = None

        def __init__(self, *, config=None) -> None:
            super().__init__(config=config)
            created.append(self)

        async def update_stream(
            self,
            current: SourceEntry | None,
            session: aiohttp.ClientSession,
            *,
            pinned_version: VersionInfo | None = None,
        ) -> AsyncIterator[UpdateEvent]:
            _ = (current, session, pinned_version)
            yield UpdateEvent.result("demo")

    async def _run_queue_task(
        *, source: str, queue: asyncio.Queue[UpdateEvent | None], task
    ) -> None:
        _ = (source, queue)
        await task()

    called = {"count": 0, "input_name": "", "source": ""}

    async def _update_input(
        _input_name: str, *, source: str
    ) -> AsyncIterator[UpdateEvent]:
        called["count"] += 1
        called["input_name"] = _input_name
        called["source"] = source
        if False:
            yield UpdateEvent.status("demo", "unused")

    monkeypatch.setattr("lib.update.cli.UPDATERS", {"demo": _DenoUpdater})
    monkeypatch.setattr("lib.update.cli.run_queue_task", _run_queue_task)
    monkeypatch.setattr("lib.update.cli.update_flake_input", _update_input)

    async def _run_case() -> None:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        async with aiohttp.ClientSession() as session:
            await _update_source_task(
                "demo",
                context=_SourceTaskContext(
                    sources=SourcesFile(entries={"demo": SourceEntry(hashes={})}),
                    update_input=True,
                    native_only=True,
                    session=session,
                    update_input_lock=asyncio.Lock(),
                    queue=queue,
                    config=resolve_config(),
                ),
            )

    _run(_run_case())
    check(len(created) == 1)
    check(created[0].native_only is True)
    check(called["count"] == 1)
    check(called["input_name"] == "demo")
    check(called["source"] == "demo")


def test_update_source_task_skips_input_update_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skip update_flake_input branch when update_input is false."""

    class _Updater:
        input_name = "demo-input"

        def __init__(self, *, config: object | None = None) -> None:
            _ = config

        async def update_stream(
            self,
            current: SourceEntry | None,
            session: aiohttp.ClientSession,
            *,
            pinned_version: VersionInfo | None = None,
        ) -> AsyncIterator[UpdateEvent]:
            _ = (current, session, pinned_version)
            yield UpdateEvent.result("demo")

    async def _run_queue_task(
        *, source: str, queue: asyncio.Queue[UpdateEvent | None], task
    ) -> None:
        _ = (source, queue)
        await task()

    called = {"update_input": 0}

    async def _update_input(
        _input_name: str, *, source: str
    ) -> AsyncIterator[UpdateEvent]:
        _ = source
        called["update_input"] += 1
        if False:
            yield UpdateEvent.status("demo", "unused")

    monkeypatch.setattr("lib.update.cli.UPDATERS", {"demo": _Updater})
    monkeypatch.setattr("lib.update.cli.run_queue_task", _run_queue_task)
    monkeypatch.setattr("lib.update.cli.update_flake_input", _update_input)

    async def _run_case() -> None:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        async with aiohttp.ClientSession() as session:
            await _update_source_task(
                "demo",
                context=_SourceTaskContext(
                    sources=SourcesFile(entries={"demo": SourceEntry(hashes={})}),
                    update_input=False,
                    native_only=False,
                    session=session,
                    update_input_lock=asyncio.Lock(),
                    queue=queue,
                    config=resolve_config(),
                ),
            )

    _run(_run_case())
    check(called["update_input"] == 0)


def test_persist_updates_and_build_plan_edge_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover persist early-return/save branches and empty-target run plan."""
    sources = SourcesFile(entries={"a": SourceEntry(hashes={})})
    resolved_skip = ResolvedTargets(
        all_source_names={"a"},
        all_ref_inputs=[],
        all_ref_names=set(),
        all_known_names={"a"},
        do_refs=False,
        do_sources=False,
        do_input_refresh=True,
        dry_run=False,
        native_only=False,
        ref_inputs=[],
        source_names=["a"],
    )
    _persist_source_updates(
        resolved=resolved_skip,
        sources=sources,
        source_updates={"a": SourceEntry(hashes={"x86_64-linux": "sha256-1"})},
        details={"a": "updated"},
    )

    saved: list[SourcesFile] = []
    monkeypatch.setattr("lib.update.cli.save_sources", lambda src: saved.append(src))
    resolved = ResolvedTargets(
        all_source_names={"a"},
        all_ref_inputs=[],
        all_ref_names=set(),
        all_known_names={"a"},
        do_refs=False,
        do_sources=True,
        do_input_refresh=True,
        dry_run=False,
        native_only=False,
        ref_inputs=[],
        source_names=["a"],
    )
    _persist_source_updates(
        resolved=resolved,
        sources=sources,
        source_updates={},
        details={"a": "no_change"},
    )
    check(saved == [])

    monkeypatch.setattr("lib.update.cli.UPDATERS", {})
    monkeypatch.setattr("lib.update.cli.get_flake_inputs_with_refs", list)
    plan = _build_run_plan(
        UpdateOptions(), OutputOptions(json_output=False, quiet=True)
    )
    check(plan == 0)


def test_execute_run_plan_branches_and_run_update_command_source_ref_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover phase-header false branches and source-specific flake-edit detection."""
    resolved = ResolvedTargets(
        all_source_names={"src"},
        all_ref_inputs=[
            FlakeInputRef(
                name="inp", owner="o", repo="r", ref="v1", input_type="github"
            )
        ],
        all_ref_names={"inp"},
        all_known_names={"src", "inp"},
        do_refs=True,
        do_sources=True,
        do_input_refresh=True,
        dry_run=False,
        native_only=False,
        ref_inputs=[
            FlakeInputRef(
                name="inp", owner="o", repo="r", ref="v1", input_type="github"
            )
        ],
        source_names=["src"],
    )
    plan = _RunPlan(
        resolved=resolved,
        tty_enabled=False,
        show_phase_headers=False,
        sources=SourcesFile(entries={"src": SourceEntry(hashes={})}),
        item_meta={"src": SimpleNamespace(name="src", origin="x", op_order=())},
        order=["src"],
    )

    phase_calls: list[str] = []

    async def _consume(
        queue: asyncio.Queue[UpdateEvent | None],
        _order: list[str],
        _sources: SourcesFile,
        *,
        options: object,
    ) -> SimpleNamespace:
        _ = options
        while await queue.get() is not None:
            pass
        return SimpleNamespace(
            updated=False,
            errors=0,
            details={"src": "no_change"},
            source_updates={},
            artifact_updates={},
        )

    async def _run_ref_phase(**_kwargs: object) -> None:
        phase_calls.append("refs")

    async def _run_sources_phase(*, context: object) -> None:
        _ = context
        phase_calls.append("sources")

    monkeypatch.setattr("lib.update.cli.consume_events", _consume)
    monkeypatch.setattr("lib.update.cli._run_ref_phase", _run_ref_phase)
    monkeypatch.setattr("lib.update.cli._run_sources_phase", _run_sources_phase)
    monkeypatch.setattr(
        "lib.update.cli._persist_materialized_updates", lambda **_kwargs: None
    )

    cfg = resolve_config()
    code = _run(
        _execute_run_plan(
            UpdateOptions(), OutputOptions(json_output=False, quiet=True), cfg, plan
        )
    )
    check(code == 0)
    check(phase_calls == ["refs", "sources"])

    # Skip refs/sources branches in execute plan.
    phase_calls.clear()
    skip_plan = _RunPlan(
        resolved=ResolvedTargets(
            all_source_names=set(),
            all_ref_inputs=[],
            all_ref_names=set(),
            all_known_names=set(),
            do_refs=False,
            do_sources=False,
            do_input_refresh=False,
            dry_run=False,
            native_only=False,
            ref_inputs=[],
            source_names=[],
        ),
        tty_enabled=False,
        show_phase_headers=False,
        sources=SourcesFile(entries={}),
        item_meta={},
        order=[],
    )
    code = _run(
        _execute_run_plan(
            UpdateOptions(),
            OutputOptions(json_output=False, quiet=True),
            cfg,
            skip_plan,
        )
    )
    check(code == 0)
    check(phase_calls == [])

    seen: dict[str, object] = {}

    def _check_required_tools(**kwargs: object) -> list[str]:
        seen.update(kwargs)
        return []

    monkeypatch.setattr("lib.update.cli.check_required_tools", _check_required_tools)
    monkeypatch.setattr(
        "lib.update.cli.get_flake_inputs_with_refs",
        lambda: [
            FlakeInputRef(
                name="inp", owner="o", repo="r", ref="v1", input_type="github"
            )
        ],
    )
    monkeypatch.setattr(
        "lib.update.cli.run_updates", lambda _opts: asyncio.sleep(0, result=0)
    )
    check(run_update_command(source="src") == 0)
    check(seen["include_flake_edit"] is False)
