"""Additional branch coverage tests for update CLI internals."""

import asyncio
from typing import TYPE_CHECKING, ClassVar

import aiohttp
import pytest

from lib.nix.models.sources import SourceEntry, SourcesFile
from lib.update.artifacts import GeneratedArtifact
from lib.update.cli import (
    OutputOptions,
    ResolvedTargets,
    UpdateOptions,
    UpdateSummary,
    _build_item_meta,
    _build_run_plan,
    _build_update_options,
    _emit_summary,
    _is_tty,
    run_update_command,
)
from lib.update.cli_inventory import (
    _InventoryHandles,
    _InventoryRefTarget,
    _InventorySourceTarget,
    _InventoryTarget,
    handle_list_targets_request,
)
from lib.update.cli_validation import handle_validate_request
from lib.update.config import resolve_config
from lib.update.events import UpdateEvent, UpdateEventKind
from lib.update.persistence import persist_source_updates
from lib.update.planner import (
    add_companion_source_children,
    add_companion_source_parents,
    companion_source_depths,
    select_target_source_names,
    source_update_waves,
)
from lib.update.refs import FlakeInputRef
from lib.update.source_runner import (
    SourcesPhaseContext,
    SourceTaskContext,
    SourceTaskResult,
    run_ref_phase,
    run_sources_phase,
    update_source_task,
)
from lib.update.updaters import DenoDepsHashUpdater, UpdateContext, VersionInfo

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from lib.update.ui_state import SummaryStatus


def _run[T](awaitable: object) -> T:
    return asyncio.run(awaitable)  # type: ignore[arg-type]


def test_build_options_and_is_tty_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cover json alias absence and env-driven tty default resolution."""
    opts = _build_update_options({"source": "demo", "check": True})
    assert opts.source == "demo"
    assert opts.target_names == ("demo",)
    assert opts.json is False

    multi_opts = _build_update_options({"targets": ("demo", "other")})
    assert multi_opts.source is None
    assert multi_opts.target_names == ("demo", "other")

    monkeypatch.setenv("UPDATE_FORCE_TTY", "0")
    monkeypatch.setenv("UPDATE_NO_TTY", "0")
    monkeypatch.setenv("UPDATE_ZELLIJ_GUARD", "0")
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    assert _is_tty() is True


def test_update_summary_does_not_downgrade_status() -> None:
    """Keep higher-priority status when a lower status is merged later."""
    summary = UpdateSummary()
    summary._set_status("demo", "error")
    summary._set_status("demo", "no_change")
    summary._rebuild_lists()
    assert summary.errors == ["demo"]


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
    assert resolved_ref.do_refs is True
    assert resolved_ref.do_sources is False

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
    assert meta["both"].origin.endswith("flake.nix + sources.json)")
    assert meta["flake-only"].origin.endswith("flake.nix)")
    assert meta["src-with-input"].origin.endswith("sources.json)")
    assert meta["src-no-input"].origin.endswith("sources.json)")


def test_source_selection_detects_companion_cycles_and_empty_waves() -> None:
    """Reject cyclic companion source graphs before scheduling update waves."""

    class _A:
        companion_of = "b"

    class _B:
        companion_of = "a"

    with pytest.raises(RuntimeError, match="Companion source cycle"):
        select_target_source_names((), {"a": _A, "b": _B})

    assert source_update_waves([], {}) == []


def test_companion_source_graph_helpers_cover_cycles_and_revisits() -> None:
    """Exercise helper-only branches that protect companion source graph traversal."""

    class _Root:
        pass

    class _Child:
        companion_of = "root"

    class _CycleA:
        companion_of = "cycle-b"

    class _CycleB:
        companion_of = "cycle-a"

    with pytest.raises(RuntimeError, match="cycle-a -> cycle-b -> cycle-a"):
        companion_source_depths(
            {"cycle-a", "cycle-b"},
            {"cycle-a": _CycleA, "cycle-b": _CycleB},
        )

    names = {"child", "root"}
    add_companion_source_parents(names, {"child": _Child, "root": _Root})
    assert names == {"child", "root"}

    children: set[str] = set()
    add_companion_source_children(
        children,
        roots={"child", "root"},
        updaters={"child": _Child, "root": _Root},
    )
    assert children == {"child"}


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
    assert code == 1
    captured = capsys.readouterr()
    assert "Available updates" in captured.out
    assert "Failed: b" in captured.err


def test_list_and_validate_non_json_paths(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exercise non-JSON list/validate printing branches and early returns."""
    monkeypatch.setattr(
        "lib.update.cli_inventory.build_update_inventory",
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
    assert (
        handle_list_targets_request(UpdateOptions(list_targets=True, json=False)) == 0
    )
    rendered = capsys.readouterr().out
    assert "nixcfg update inventory" in rendered
    assert "name" in rendered
    assert "class" in rendered
    assert "touches" in rendered
    assert "selector" in rendered
    assert "writes" in rendered

    out = OutputOptions(json_output=False, quiet=False)
    assert handle_validate_request(UpdateOptions(validate=False), out) is None

    monkeypatch.setattr(
        "lib.update.sources.load_all_sources",
        lambda: SourcesFile(entries={"a": SourceEntry(hashes={})}),
    )
    monkeypatch.setattr(
        "lib.update.sources.validate_source_discovery_consistency", lambda: None
    )
    assert handle_validate_request(UpdateOptions(validate=True, json=False), out) == 0
    assert "Validated sources.json entries" in capsys.readouterr().out

    def _boom() -> None:
        msg = "broken"
        raise RuntimeError(msg)

    monkeypatch.setattr(
        "lib.update.sources.validate_source_discovery_consistency", _boom
    )
    assert handle_validate_request(UpdateOptions(validate=True, json=False), out) == 1
    assert "Validation failed" in capsys.readouterr().err


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
    assert meta["src"].origin.endswith("flake.nix + sources.json)")
    assert order == ["src"]

    monkeypatch.setattr(
        "lib.update.cli_inventory.build_update_inventory",
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
    assert (
        handle_list_targets_request(UpdateOptions(list_targets=True, json=False)) == 0
    )
    rendered = capsys.readouterr().out
    assert "nixcfg update inventory" in rendered
    assert "source" in rendered


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
        ) -> AsyncIterator[UpdateEvent]:
            _ = (current, session)
            yield UpdateEvent.status("demo", "updated")
            yield UpdateEvent.result("demo")

    monkeypatch.setattr("lib.update.source_runner.UPDATERS", {"demo": _DummyUpdater})

    async def _run_queue_task(
        *, source: str, queue: asyncio.Queue[UpdateEvent | None], task
    ) -> None:
        _ = (source, queue)
        await task()

    async def _update_input(
        _input_name: str, *, source: str
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.status(source, "input refreshed")

    monkeypatch.setattr("lib.update.process.run_queue_task", _run_queue_task)
    monkeypatch.setattr("lib.update.flake.update_flake_input", _update_input)

    async def _run_source_task() -> list[UpdateEvent]:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        async with aiohttp.ClientSession() as session:
            await update_source_task(
                "demo",
                context=SourceTaskContext(
                    sources=SourcesFile(entries={"demo": SourceEntry(hashes={})}),
                    update_input=True,
                    native_only=False,
                    session=session,
                    update_input_lock=asyncio.Lock(),
                    update_input_tasks={},
                    queue=queue,
                    generated_artifacts={},
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
    assert any(event.message == "Starting update" for event in source_events)
    assert any(event.message == "input refreshed" for event in source_events)

    async def _update_ref(
        input_ref: FlakeInputRef,
        session: aiohttp.ClientSession,
        queue: asyncio.Queue[UpdateEvent | None],
        *,
        options: object | None = None,
    ) -> SummaryStatus:
        _ = (session, options)
        await queue.put(UpdateEvent.status(input_ref.name, "ref phase"))
        return "no_change"

    monkeypatch.setattr("lib.update.refs.update_refs_task", _update_ref)

    async def _run_refs() -> list[UpdateEvent]:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        await run_ref_phase(
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
    assert any(event.message == "ref phase" for event in ref_events)

    calls: list[tuple[str, int]] = []

    async def _update_source(
        name: str, *, context: SourceTaskContext
    ) -> SourceTaskResult:
        calls.append((name, id(context.update_input_tasks)))
        return SourceTaskResult(completed=True)

    monkeypatch.setattr("lib.update.source_runner.update_source_task", _update_source)
    _run(
        run_sources_phase(
            context=SourcesPhaseContext(
                source_names=["demo", "other"],
                sources=SourcesFile(
                    entries={
                        "demo": SourceEntry(hashes={}),
                        "other": SourceEntry(hashes={}),
                    }
                ),
                queue=asyncio.Queue(),
                update_input=False,
                native_only=False,
                config=resolve_config(),
            )
        )
    )
    assert {name for name, _ in calls} == {"demo", "other"}
    assert len({task_map_id for _name, task_map_id in calls}) == 1


def test_update_source_task_collects_artifact_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return source and artifact results while forwarding effective source state."""
    updated_entry = SourceEntry(version="2.0.0", hashes={})
    effective_sources = {"dependency": SourceEntry(version="1.0.0", hashes={})}
    seen_contexts: list[UpdateContext] = []

    class _ArtifactUpdater:
        def __init__(self, *, config: object | None = None) -> None:
            _ = config

        async def update_stream(
            self,
            current: SourceEntry | None,
            session: aiohttp.ClientSession,
            *,
            context: UpdateContext | None = None,
        ) -> AsyncIterator[UpdateEvent]:
            _ = (current, session)
            if context is None:
                raise AssertionError
            seen_contexts.append(context)
            yield UpdateEvent.artifact(
                "demo",
                [
                    GeneratedArtifact.text("packages/demo/z.txt", "z\n"),
                    GeneratedArtifact.text("packages/demo/a.txt", "a\n"),
                ],
            )
            yield UpdateEvent.result("demo", updated_entry)

    async def _run_queue_task(
        *, source: str, queue: asyncio.Queue[UpdateEvent | None], task
    ) -> None:
        _ = (source, queue)
        await task()

    monkeypatch.setattr("lib.update.source_runner.UPDATERS", {"demo": _ArtifactUpdater})
    monkeypatch.setattr("lib.update.process.run_queue_task", _run_queue_task)

    async def _run_case() -> SourceTaskResult:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        async with aiohttp.ClientSession() as session:
            return await update_source_task(
                "demo",
                context=SourceTaskContext(
                    sources=SourcesFile(entries={"demo": SourceEntry(hashes={})}),
                    update_input=False,
                    native_only=False,
                    session=session,
                    update_input_lock=asyncio.Lock(),
                    update_input_tasks={},
                    queue=queue,
                    generated_artifacts={},
                    effective_sources=effective_sources,
                    config=resolve_config(),
                ),
            )

    result = _run(_run_case())

    assert result.completed is True
    assert result.source_update is updated_entry
    assert seen_contexts[0].effective_sources is effective_sources
    assert [artifact.path for artifact in result.artifacts] == [
        GeneratedArtifact.text("packages/demo/a.txt", "a\n").path,
        GeneratedArtifact.text("packages/demo/z.txt", "z\n").path,
    ]


def test_run_sources_phase_serializes_when_max_nix_builds_is_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run source updates sequentially when nix builds are already serialized."""
    active = 0
    max_active = 0

    async def _update_source(
        _name: str, *, context: SourceTaskContext
    ) -> SourceTaskResult:
        nonlocal active, max_active
        _ = context
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        active -= 1
        return SourceTaskResult(completed=True)

    monkeypatch.setattr("lib.update.source_runner.update_source_task", _update_source)

    _run(
        run_sources_phase(
            context=SourcesPhaseContext(
                source_names=["demo", "other"],
                sources=SourcesFile(
                    entries={
                        "demo": SourceEntry(hashes={}),
                        "other": SourceEntry(hashes={}),
                    }
                ),
                queue=asyncio.Queue(),
                update_input=False,
                native_only=False,
                config=resolve_config(max_nix_builds=1),
            )
        )
    )

    assert max_active == 1


def test_run_sources_phase_returns_authoritative_domain_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Callers receive updates and failures without reconstructing them in the UI."""
    updated_entry = SourceEntry(version="2.0.0", hashes={})

    async def _update_source(
        name: str, *, context: SourceTaskContext
    ) -> SourceTaskResult:
        _ = context
        if name == "failed":
            return SourceTaskResult(completed=False)
        return SourceTaskResult(completed=True, source_update=updated_entry)

    monkeypatch.setattr("lib.update.source_runner.update_source_task", _update_source)

    result = _run(
        run_sources_phase(
            SourcesPhaseContext(
                source_names=["updated", "failed"],
                sources=SourcesFile(
                    entries={
                        "updated": SourceEntry(version="1.0.0", hashes={}),
                        "failed": SourceEntry(version="1.0.0", hashes={}),
                    }
                ),
                queue=asyncio.Queue(),
                update_input=False,
                native_only=False,
                config=resolve_config(),
            )
        )
    )

    assert result.details == {"updated": "updated", "failed": "error"}
    assert result.source_updates == {"updated": updated_entry}
    assert result.errors == 1


def test_run_sources_phase_bounds_concurrent_tasks_within_wave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Honor the configured source-task concurrency limit within one wave."""
    active = 0
    max_active = 0
    completed: list[str] = []

    async def _update_source(
        name: str, *, context: SourceTaskContext
    ) -> SourceTaskResult:
        nonlocal active, max_active
        _ = context
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        completed.append(name)
        active -= 1
        return SourceTaskResult(completed=True)

    monkeypatch.setattr("lib.update.source_runner.update_source_task", _update_source)

    source_names = ["one", "two", "three", "four", "five"]
    _run(
        run_sources_phase(
            context=SourcesPhaseContext(
                source_names=source_names,
                sources=SourcesFile(
                    entries={name: SourceEntry(hashes={}) for name in source_names}
                ),
                queue=asyncio.Queue(),
                update_input=False,
                native_only=False,
                config=resolve_config(max_nix_builds=2),
            )
        )
    )

    assert max_active == 2
    assert sorted(completed) == sorted(source_names)


def test_run_sources_phase_serializes_flake_input_refreshes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not let concurrent lock refreshes overwrite each other's flake.lock."""

    class _FirstUpdater:
        input_name = "first-input"

        def __init__(self, *, config: object | None = None) -> None:
            _ = config

        async def update_stream(
            self,
            current: SourceEntry | None,
            session: aiohttp.ClientSession,
        ) -> AsyncIterator[UpdateEvent]:
            _ = (current, session)
            yield UpdateEvent.result("first")

    class _SecondUpdater(_FirstUpdater):
        input_name = "second-input"

        async def update_stream(
            self,
            current: SourceEntry | None,
            session: aiohttp.ClientSession,
        ) -> AsyncIterator[UpdateEvent]:
            _ = (current, session)
            yield UpdateEvent.result("second")

    active_refreshes = 0
    max_active_refreshes = 0

    async def _update_input(
        _input_name: str,
        *,
        source: str,
    ) -> AsyncIterator[UpdateEvent]:
        nonlocal active_refreshes, max_active_refreshes
        active_refreshes += 1
        max_active_refreshes = max(max_active_refreshes, active_refreshes)
        try:
            await asyncio.sleep(0.01)
            yield UpdateEvent.status(source, "input refreshed")
        finally:
            active_refreshes -= 1

    monkeypatch.setattr(
        "lib.update.source_runner.UPDATERS",
        {"first": _FirstUpdater, "second": _SecondUpdater},
    )
    monkeypatch.setattr("lib.update.flake.update_flake_input", _update_input)

    _run(
        run_sources_phase(
            context=SourcesPhaseContext(
                source_names=["first", "second"],
                sources=SourcesFile(
                    entries={
                        "first": SourceEntry(hashes={}),
                        "second": SourceEntry(hashes={}),
                    }
                ),
                queue=asyncio.Queue(),
                update_input=True,
                native_only=False,
                config=resolve_config(max_nix_builds=2),
            )
        )
    )

    assert max_active_refreshes == 1


@pytest.mark.parametrize("native_only", [False, True])
def test_run_sources_phase_passes_companion_state_between_waves(
    monkeypatch: pytest.MonkeyPatch,
    *,
    native_only: bool,
) -> None:
    """Companions see source and artifact updates emitted by earlier primaries."""

    class _CodexUpdater:
        pass

    class _CodexV8Updater:
        companion_of = "codex"

    seen_overrides: list[dict[str, str]] = []
    seen_sources: list[SourceEntry] = []
    baseline_entry = SourceEntry(
        version="1.0.0",
        hashes={},
        pins={"removed": "obsolete", "runtimeVersion": "1.0.0"},
        urls={"existing": "https://example.com/existing"},
    )
    source_update = SourceEntry(
        version="2.0.0",
        hashes={},
        pins={"runtimeVersion": "2.0.0"},
        urls={"added": "https://example.com/added"},
    )

    async def _update_source(
        name: str, *, context: SourceTaskContext
    ) -> SourceTaskResult:
        if name == "codex":
            return SourceTaskResult(
                completed=True,
                artifacts=(
                    GeneratedArtifact.text(
                        "packages/codex/Cargo.nix",
                        '{ "v8" = rec { version = "147.4.0"; }; }\n',
                    ),
                ),
                source_update=source_update,
            )

        seen_overrides.append({
            str(path): content for path, content in context.generated_artifacts.items()
        })
        seen_sources.append(context.effective_sources["codex"])
        return SourceTaskResult(completed=True)

    monkeypatch.setattr(
        "lib.update.source_runner.UPDATERS",
        {"codex": _CodexUpdater, "codex-v8": _CodexV8Updater},
    )
    monkeypatch.setattr("lib.update.source_runner.update_source_task", _update_source)

    _run(
        run_sources_phase(
            context=SourcesPhaseContext(
                source_names=["codex", "codex-v8"],
                sources=SourcesFile(
                    entries={
                        "codex": baseline_entry,
                        "codex-v8": SourceEntry(hashes={}),
                    }
                ),
                queue=asyncio.Queue(),
                update_input=False,
                native_only=native_only,
                config=resolve_config(),
            )
        )
    )

    assert seen_overrides == [
        {"packages/codex/Cargo.nix": '{ "v8" = rec { version = "147.4.0"; }; }\n'}
    ]
    assert seen_sources[0].pins == {"runtimeVersion": "2.0.0"}
    if native_only:
        assert seen_sources[0].urls == {
            "added": "https://example.com/added",
            "existing": "https://example.com/existing",
        }
    else:
        assert seen_sources == [source_update]


def test_run_sources_phase_skips_companions_after_failed_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not run companion sources after their prerequisite source fails."""

    class _ParentUpdater:
        pass

    class _ChildUpdater:
        companion_of = "parent"

    async def _update_source(
        name: str, *, context: SourceTaskContext
    ) -> SourceTaskResult:
        _ = context
        if name == "parent":
            return SourceTaskResult(completed=False)
        msg = "child updater should not run after parent failure"
        raise AssertionError(msg)

    monkeypatch.setattr(
        "lib.update.source_runner.UPDATERS",
        {"parent": _ParentUpdater, "child": _ChildUpdater},
    )
    monkeypatch.setattr("lib.update.source_runner.update_source_task", _update_source)

    async def _run_case() -> list[UpdateEvent]:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        await run_sources_phase(
            context=SourcesPhaseContext(
                source_names=["parent", "child"],
                sources=SourcesFile(
                    entries={
                        "parent": SourceEntry(hashes={}),
                        "child": SourceEntry(hashes={}),
                    }
                ),
                queue=queue,
                update_input=False,
                native_only=False,
                config=resolve_config(),
            )
        )
        events: list[UpdateEvent] = []
        while not queue.empty():
            item = queue.get_nowait()
            if isinstance(item, UpdateEvent):
                events.append(item)
        return events

    events = _run(_run_case())

    assert [event.message for event in events] == ["Prerequisite update failed: parent"]


def test_update_source_task_dedupes_shared_input_refreshes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refresh each backing flake input at most once per sources phase."""

    class _Updater:
        def __init__(self, *, config: object | None = None) -> None:
            _ = config

        input_name = "shared-input"

        async def update_stream(
            self,
            current: SourceEntry | None,
            session: aiohttp.ClientSession,
        ) -> AsyncIterator[UpdateEvent]:
            _ = (current, session)
            yield UpdateEvent.result("demo")

    async def _run_queue_task(
        *, source: str, queue: asyncio.Queue[UpdateEvent | None], task
    ) -> None:
        _ = (source, queue)
        await task()

    called = {"count": 0}

    async def _update_input(
        _input_name: str, *, source: str
    ) -> AsyncIterator[UpdateEvent]:
        called["count"] += 1
        await asyncio.sleep(0)
        yield UpdateEvent.status(source, f"input refreshed for {_input_name}")

    monkeypatch.setattr(
        "lib.update.source_runner.UPDATERS", {"one": _Updater, "two": _Updater}
    )
    monkeypatch.setattr("lib.update.process.run_queue_task", _run_queue_task)
    monkeypatch.setattr("lib.update.flake.update_flake_input", _update_input)

    async def _run_case() -> list[UpdateEvent]:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        shared_lock = asyncio.Lock()
        shared_tasks: dict[str, asyncio.Task[None]] = {}
        shared_sources = SourcesFile(
            entries={
                "one": SourceEntry(hashes={}),
                "two": SourceEntry(hashes={}),
            }
        )
        async with aiohttp.ClientSession() as session:
            await asyncio.gather(
                update_source_task(
                    "one",
                    context=SourceTaskContext(
                        sources=shared_sources,
                        update_input=True,
                        native_only=False,
                        session=session,
                        update_input_lock=shared_lock,
                        update_input_tasks=shared_tasks,
                        queue=queue,
                        generated_artifacts={},
                        config=resolve_config(),
                    ),
                ),
                update_source_task(
                    "two",
                    context=SourceTaskContext(
                        sources=shared_sources,
                        update_input=True,
                        native_only=False,
                        session=session,
                        update_input_lock=shared_lock,
                        update_input_tasks=shared_tasks,
                        queue=queue,
                        generated_artifacts={},
                        config=resolve_config(),
                    ),
                ),
            )
        events: list[UpdateEvent] = []
        while not queue.empty():
            item = queue.get_nowait()
            if isinstance(item, UpdateEvent):
                events.append(item)
        return events

    events = _run(_run_case())
    assert called["count"] == 1
    assert any(
        event.message == "Updating flake input 'shared-input'..." for event in events
    )
    assert any(
        event.message == "Reusing flake input 'shared-input' refresh..."
        for event in events
    )


def test_update_source_task_refreshes_additional_inputs_before_updater(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refresh every declared flake input before resolving source metadata."""
    calls: list[str] = []

    class _Updater:
        input_name = "zed"
        additional_input_names = ("rust-overlay",)

        def __init__(self, *, config: object | None = None) -> None:
            _ = config

        async def update_stream(
            self,
            current: SourceEntry | None,
            session: aiohttp.ClientSession,
        ) -> AsyncIterator[UpdateEvent]:
            _ = (current, session)
            calls.append("update")
            yield UpdateEvent.result("zed-editor-nightly")

    async def _run_queue_task(
        *, source: str, queue: asyncio.Queue[UpdateEvent | None], task
    ) -> None:
        _ = (source, queue)
        await task()

    async def _update_input(
        input_name: str, *, source: str
    ) -> AsyncIterator[UpdateEvent]:
        _ = source
        calls.append(f"refresh:{input_name}")
        if False:
            yield UpdateEvent.status("zed-editor-nightly", "unused")

    monkeypatch.setattr(
        "lib.update.source_runner.UPDATERS",
        {"zed-editor-nightly": _Updater},
    )
    monkeypatch.setattr("lib.update.process.run_queue_task", _run_queue_task)
    monkeypatch.setattr("lib.update.flake.update_flake_input", _update_input)

    async def _run_case() -> None:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        async with aiohttp.ClientSession() as session:
            await update_source_task(
                "zed-editor-nightly",
                context=SourceTaskContext(
                    sources=SourcesFile(
                        entries={"zed-editor-nightly": SourceEntry(hashes={})}
                    ),
                    update_input=True,
                    native_only=False,
                    session=session,
                    update_input_lock=asyncio.Lock(),
                    update_input_tasks={},
                    queue=queue,
                    generated_artifacts={},
                    config=resolve_config(),
                ),
            )

    _run(_run_case())

    assert calls == ["refresh:zed", "refresh:rust-overlay", "update"]


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
        ) -> AsyncIterator[UpdateEvent]:
            _ = (current, session)
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

    monkeypatch.setattr("lib.update.source_runner.UPDATERS", {"demo": _DenoUpdater})
    monkeypatch.setattr("lib.update.process.run_queue_task", _run_queue_task)
    monkeypatch.setattr("lib.update.flake.update_flake_input", _update_input)

    async def _run_case() -> None:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        async with aiohttp.ClientSession() as session:
            await update_source_task(
                "demo",
                context=SourceTaskContext(
                    sources=SourcesFile(entries={"demo": SourceEntry(hashes={})}),
                    update_input=True,
                    native_only=True,
                    session=session,
                    update_input_lock=asyncio.Lock(),
                    update_input_tasks={},
                    queue=queue,
                    generated_artifacts={},
                    config=resolve_config(),
                ),
            )

    _run(_run_case())
    assert len(created) == 1
    assert created[0].native_only is True
    assert called["count"] == 1
    assert called["input_name"] == "demo"
    assert called["source"] == "demo"


def test_update_source_task_reports_incoherent_native_identity_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Translate native partial identity rejection into one source error event."""

    class _DenoUpdater(DenoDepsHashUpdater):
        name = "demo"
        input_name = None
        source_pins: ClassVar[dict[str, str]] = {"runtimeVersion": "2.0.0"}

        async def fetch_latest(self, session: object) -> VersionInfo:
            _ = session
            return VersionInfo(version="1.0.0", metadata={})

    async def _compute_deno_deps_hash(
        source: str,
        input_name: str,
        *,
        native_only: bool = False,
        config: object | None = None,
        source_override: SourceEntry | None = None,
    ) -> AsyncIterator[UpdateEvent]:
        _ = (config, source_override)
        assert source == "demo"
        assert input_name == "demo"
        assert native_only is True
        yield UpdateEvent.value(
            source,
            {"aarch64-darwin": "sha256-newDarwin"},
        )

    monkeypatch.setattr("lib.update.source_runner.UPDATERS", {"demo": _DenoUpdater})
    monkeypatch.setattr(
        "lib.update.nix_deno.compute_deno_deps_hash",
        _compute_deno_deps_hash,
    )
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform",
        lambda: "aarch64-darwin",
    )
    current = SourceEntry.model_validate({
        "drvHash": "old-drv",
        "hashes": [
            {
                "hashType": "denoDepsHash",
                "hash": "sha256-oldDarwin",
                "platform": "aarch64-darwin",
            },
            {
                "hashType": "denoDepsHash",
                "hash": "sha256-oldLinux",
                "platform": "x86_64-linux",
            },
        ],
        "input": "demo",
        "pins": {"removed": "obsolete", "runtimeVersion": "1.0.0"},
        "version": "1.0.0",
    })

    async def _run_case() -> tuple[SourceTaskResult, list[UpdateEvent]]:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        async with aiohttp.ClientSession() as session:
            result = await update_source_task(
                "demo",
                context=SourceTaskContext(
                    sources=SourcesFile(entries={"demo": current}),
                    update_input=False,
                    native_only=True,
                    session=session,
                    update_input_lock=asyncio.Lock(),
                    update_input_tasks={},
                    queue=queue,
                    generated_artifacts={},
                    config=resolve_config(
                        hash_build_platforms=(
                            "aarch64-darwin",
                            "x86_64-linux",
                        )
                    ),
                ),
            )
        return result, [queue.get_nowait() for _ in range(queue.qsize())]

    result, events = _run(_run_case())
    error_events = [event for event in events if event.kind is UpdateEventKind.ERROR]

    assert result.completed is False
    assert result.source_update is None
    assert [event.message for event in error_events] == [
        "Cannot apply native-only update for demo: updater-owned source identity "
        "changed (pins) while foreign-platform hashes would be preserved; rerun "
        "without --native-only"
    ]


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
        ) -> AsyncIterator[UpdateEvent]:
            _ = (current, session)
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

    monkeypatch.setattr("lib.update.source_runner.UPDATERS", {"demo": _Updater})
    monkeypatch.setattr("lib.update.process.run_queue_task", _run_queue_task)
    monkeypatch.setattr("lib.update.flake.update_flake_input", _update_input)

    async def _run_case() -> None:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        async with aiohttp.ClientSession() as session:
            await update_source_task(
                "demo",
                context=SourceTaskContext(
                    sources=SourcesFile(entries={"demo": SourceEntry(hashes={})}),
                    update_input=False,
                    native_only=False,
                    session=session,
                    update_input_lock=asyncio.Lock(),
                    update_input_tasks={},
                    queue=queue,
                    generated_artifacts={},
                    config=resolve_config(),
                ),
            )

    _run(_run_case())
    assert called["update_input"] == 0


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
    persist_source_updates(
        do_sources=resolved_skip.do_sources,
        source_names=resolved_skip.source_names,
        dry_run=resolved_skip.dry_run,
        native_only=resolved_skip.native_only,
        sources=sources,
        source_updates={"a": SourceEntry(hashes={"x86_64-linux": "sha256-1"})},
        details={"a": "updated"},
    )

    saved: list[SourcesFile] = []
    monkeypatch.setattr(
        "lib.update.sources.save_sources", lambda src: saved.append(src)
    )
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
    persist_source_updates(
        do_sources=resolved.do_sources,
        source_names=resolved.source_names,
        dry_run=resolved.dry_run,
        native_only=resolved.native_only,
        sources=sources,
        source_updates={},
        details={"a": "no_change"},
    )
    assert saved == []

    monkeypatch.setattr("lib.update.cli.UPDATERS", {})
    monkeypatch.setattr("lib.update.cli.get_flake_inputs_with_refs", list)
    assert _build_run_plan(UpdateOptions()) is None


def test_run_update_command_source_ref_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require flake-edit only when the selected target needs ref updates."""
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
    assert run_update_command(source="src") == 0
    assert seen["include_flake_edit"] is False

    seen.clear()
    assert run_update_command(check=True) == 0
    assert seen["include_flake_edit"] is True
