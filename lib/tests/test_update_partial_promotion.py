"""Partial promotion: failed targets are withheld and the rest still lands."""

import asyncio
import json
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from lib.tests._run_updates_helpers import drain_events, make_run_plan
from lib.tests._update_workspace_helpers import init_update_workspace_repo
from lib.update.cli import (
    OutputOptions,
    UpdateOptions,
    UpdateSummary,
    _emit_summary,
    _execute_run_plan_result,
    _failed_targets_touched_flake,
    _handle_status_request,
    _input_backed_targets,
    _live_ui_enabled,
    _run_log_root,
    _RunOutcome,
    _start_run_monitor,
    _validation_display,
    _withhold_failed_clusters,
    run_updates,
)
from lib.update.config import resolve_config
from lib.update.derivation_validation import (
    DerivationValidation,
    DerivationValidationFailure,
)
from lib.update.events import UpdateEvent
from lib.update.persistence import UpdateValidationSnapshot
from lib.update.refs import FlakeInputRef
from lib.update.run_monitor import RunMonitor
from lib.update.run_store import DATABASE_FILE, RunStore
from lib.update.source_runner import UpdatePhaseResult
from lib.update.ui_render import ValidationDisplay
from lib.update.updaters import Updater

_LOCK_TEMPLATE = {
    "nodes": {
        "root": {"inputs": {"tool": "tool", "other": "other"}},
        "tool": {
            "locked": {
                "type": "github",
                "owner": "o",
                "repo": "tool",
                "rev": "a" * 40,
                "narHash": "sha256-tool",
            },
            "original": {"type": "github", "owner": "o", "repo": "tool", "ref": "v1"},
        },
        "other": {
            "locked": {
                "type": "github",
                "owner": "o",
                "repo": "other",
                "rev": "b" * 40,
                "narHash": "sha256-other",
            },
            "original": {"type": "github", "owner": "o", "repo": "other", "ref": "v8"},
        },
    },
    "root": "root",
    "version": 7,
}


def _lock_json(*, tool_rev: str = "a" * 40) -> str:
    lock = json.loads(json.dumps(_LOCK_TEMPLATE))
    lock["nodes"]["tool"]["locked"]["rev"] = tool_rev
    return json.dumps(lock, indent=2) + "\n"


def _run(options: UpdateOptions) -> int:
    return asyncio.run(run_updates(options))


def _source_path(name: str) -> Path:
    return Path.cwd() / "packages" / name / "sources.json"


def _planned_paths(names: list[str], _updaters: object) -> tuple[Path, ...]:
    return tuple(_source_path(name) for name in names)


def _install_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    live: Path,
    plan: object,
    updaters: dict[str, type[object]],
    execute_result: object,
    validate_derivations: object | None = None,
) -> list[Path]:
    """Wire a run against a real isolated workspace with faked phases."""
    roots: list[Path] = []

    def _roots(**kwargs: object) -> tuple[object, ...]:
        roots.append(cast("Path", kwargs["flake_root"]))
        return ()

    monkeypatch.setattr("lib.update.cli.get_repo_root", lambda: live)
    monkeypatch.setattr("lib.update.cli._build_run_plan", lambda *_args: plan)
    monkeypatch.setattr("lib.update.cli._execute_run_plan_result", execute_result)
    monkeypatch.setattr("lib.update.cli._get_updaters", lambda: updaters)
    monkeypatch.setattr("lib.update.persistence.planned_update_paths", _planned_paths)
    monkeypatch.setattr(
        "lib.update.derivation_validation.validate_root_closures", _roots
    )
    if validate_derivations is not None:
        monkeypatch.setattr(
            "lib.update.derivation_validation.validate_derivations",
            validate_derivations,
        )
    return roots


def _execution(
    statuses: dict[str, str],
    *,
    writes: dict[str, str],
    flake: tuple[str, str] | None = None,
) -> object:
    async def _execute_result(*_args: object, **_kwargs: object) -> SimpleNamespace:
        written: list[Path] = []
        for name, content in writes.items():
            path = _source_path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written.append(path)
        if flake is not None:
            (Path.cwd() / "flake.nix").write_text(flake[0], encoding="utf-8")
            (Path.cwd() / "flake.lock").write_text(flake[1], encoding="utf-8")
        summary = UpdateSummary()
        summary.accumulate(cast("Any", statuses))
        return SimpleNamespace(
            summary=summary,
            candidate_updates=tuple(
                name for name, status in statuses.items() if status == "updated"
            ),
            had_errors="error" in statuses.values(),
            written_paths=tuple(written),
        )

    return _execute_result


def test_coupled_failure_withholds_companion_and_promotes_the_rest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One failed target withholds its companion; unrelated updates land."""
    live = tmp_path / "live"
    init_update_workspace_repo(
        live,
        tracked_files={
            "packages/good/sources.json": "good v1\n",
            "packages/bad/sources.json": "bad v1\n",
            "packages/child/sources.json": "child v1\n",
        },
    )

    class _Good(Updater):
        pass

    class _Bad(Updater):
        pass

    class _Child(Updater):
        companion_of = "bad"

    updaters: dict[str, type[object]] = {"good": _Good, "bad": _Bad, "child": _Child}
    roots = _install_run(
        monkeypatch,
        live=live,
        plan=make_run_plan(source_names=("good", "bad", "child")),
        updaters=updaters,
        execute_result=_execution(
            {"good": "updated", "bad": "error", "child": "updated"},
            writes={"good": "good v2\n", "child": "child v2\n"},
        ),
    )

    assert _run(UpdateOptions()) == 1

    assert (live / "packages/good/sources.json").read_text("utf-8") == "good v2\n"
    assert (live / "packages/child/sources.json").read_text("utf-8") == "child v1\n"
    assert (live / "packages/bad/sources.json").read_text("utf-8") == "bad v1\n"
    assert len(roots) == 1
    captured = capsys.readouterr()
    assert "Continuing without: child (coupled to failed target bad)" in captured.out
    assert "Updated: good" in captured.out
    assert (
        "Withheld from promotion: child (coupled to failed target bad)" in captured.out
    )
    assert "Failed: bad" in captured.err


def test_failed_input_backed_target_reverts_flake_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A failure whose input moved reverts every input-backed candidate together."""
    live = tmp_path / "live"
    init_update_workspace_repo(
        live,
        tracked_files={
            "flake.nix": 'tool.ref = "v1"; other.ref = "v8";\n',
            "flake.lock": _lock_json(),
            "packages/good/sources.json": "good v1\n",
            "packages/tool-src/sources.json": "tool-src v1\n",
        },
    )

    class _Good(Updater):
        pass

    class _ToolSource(Updater):
        input_name = "tool"

    refs = (
        FlakeInputRef("tool", "o", "tool", "v1", "github"),
        FlakeInputRef("other", "o", "other", "v8", "github"),
    )
    monkeypatch.setattr(
        "lib.update.cli.get_flake_inputs_with_refs",
        lambda: [
            FlakeInputRef("tool", "o", "tool", "v2", "github"),
            FlakeInputRef("other", "o", "other", "v9", "github"),
        ],
    )
    updaters: dict[str, type[object]] = {"good": _Good, "tool-src": _ToolSource}
    _install_run(
        monkeypatch,
        live=live,
        plan=make_run_plan(
            source_names=("good", "tool-src"), ref_inputs=refs, do_input_refresh=True
        ),
        updaters=updaters,
        execute_result=_execution(
            {
                "tool": "updated",
                "other": "updated",
                "good": "updated",
                "tool-src": "error",
            },
            writes={"good": "good v2\n"},
            flake=(
                'tool.ref = "v2"; other.ref = "v9";\n',
                _lock_json(tool_rev="c" * 40),
            ),
        ),
    )

    assert _run(UpdateOptions(json=True)) == 1

    assert (live / "flake.nix").read_text(
        "utf-8"
    ) == 'tool.ref = "v1"; other.ref = "v8";\n'
    assert (live / "flake.lock").read_text("utf-8") == _lock_json()
    assert (live / "packages/good/sources.json").read_text("utf-8") == "good v2\n"
    payload = json.loads(capsys.readouterr().out)
    assert payload["updated"] == ["good"]
    assert payload["dropped"] == ["tool", "other"]
    assert payload["errors"] == ["tool-src"]
    assert payload["withheld"] == {
        "tool": "coupled to failed target tool-src",
        "other": "flake inputs reverted after tool-src failed",
    }
    assert payload["success"] is False


def test_failed_lock_only_input_rolls_back_and_promotes_the_rest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A failed lock-only input rolls back its node; unrelated updates promote."""
    live = tmp_path / "live"
    init_update_workspace_repo(
        live,
        tracked_files={
            "flake.lock": _lock_json(),
            "packages/good/sources.json": "good v1\n",
            "packages/nightly/sources.json": "nightly v1\n",
            "packages/stable/sources.json": "stable v1\n",
        },
    )

    class _Good(Updater):
        pass

    class _Nightly(Updater):
        input_name = "tool"

    class _Stable(Updater):
        input_name = "tool"

    updaters: dict[str, type[object]] = {
        "good": _Good,
        "nightly": _Nightly,
        "stable": _Stable,
    }

    async def _execute_with_moved_lock(
        *_args: object, **_kwargs: object
    ) -> SimpleNamespace:
        (Path.cwd() / "flake.lock").write_text(
            _lock_json(tool_rev="c" * 40), encoding="utf-8"
        )
        written: list[Path] = []
        for name, content in (
            ("good", "good v2\n"),
            ("stable", "stable v2\n"),
            ("nightly", "nightly v2\n"),
        ):
            path = _source_path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written.append(path)
        summary = UpdateSummary()
        summary.accumulate(cast("Any", {"good": "updated", "stable": "updated"}))
        summary.accumulate({"nightly": "error"})
        return SimpleNamespace(
            summary=summary,
            candidate_updates=("good", "stable"),
            had_errors=True,
            written_paths=(
                _source_path("good"),
                _source_path("stable"),
                _source_path("nightly"),
                Path.cwd() / "flake.lock",
            ),
        )

    _install_run(
        monkeypatch,
        live=live,
        plan=make_run_plan(
            source_names=("good", "nightly", "stable"), do_input_refresh=True
        ),
        updaters=updaters,
        execute_result=_execute_with_moved_lock,
    )

    assert _run(UpdateOptions(json=True)) == 1

    assert (live / "flake.lock").read_text("utf-8") == _lock_json()
    assert (live / "packages/good/sources.json").read_text("utf-8") == "good v2\n"
    assert (live / "packages/stable/sources.json").read_text("utf-8") == "stable v1\n"
    payload = json.loads(capsys.readouterr().out)
    assert payload["updated"] == ["good"]
    assert payload["dropped"] == ["stable"]
    assert payload["errors"] == ["nightly"]
    assert payload["success"] is False


def test_failed_targets_touched_flake_compares_lock_nodes(tmp_path: Path) -> None:
    """Without a ref change, a moved lock node still marks the input as touched."""

    class _ToolSource(Updater):
        input_name = "tool"

    class _Plain(Updater):
        pass

    updaters: dict[str, type[object]] = {"tool-src": _ToolSource, "plain": _Plain}
    plan = make_run_plan(
        source_names=("tool-src", "plain"),
        ref_inputs=(FlakeInputRef("tool", "o", "tool", "v1", "github"),),
    )
    (tmp_path / "flake.lock").write_text(
        _lock_json(tool_rev="d" * 40), encoding="utf-8"
    )
    workspace = SimpleNamespace(
        root=tmp_path,
        baseline_content=lambda _path: _lock_json().encode(),
    )
    refs = [FlakeInputRef("tool", "o", "tool", "v1", "github")]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("lib.update.cli.get_flake_inputs_with_refs", lambda: refs)
        touched = _failed_targets_touched_flake(
            cast("Any", workspace), plan, ["tool-src", "plain"], updaters
        )
        assert touched == ("tool-src",)

        (tmp_path / "flake.lock").write_text(_lock_json(), encoding="utf-8")
        assert (
            _failed_targets_touched_flake(
                cast("Any", workspace), plan, ["tool-src"], updaters
            )
            == ()
        )

        (tmp_path / "flake.lock").unlink()
        absent = SimpleNamespace(root=tmp_path, baseline_content=lambda _path: None)
        assert (
            _failed_targets_touched_flake(cast("Any", absent), plan, ["tool"], updaters)
            == ()
        )

    assert _input_backed_targets(plan, updaters) == {"tool", "tool-src"}


def test_failed_targets_touched_flake_compares_lock_on_direct_path(
    tmp_path: Path,
) -> None:
    """A ref-only failed target is compared even when refs did not move."""

    class _ToolRef(Updater):
        pass

    updaters: dict[str, type[object]] = {"tool": _ToolRef}
    plan = make_run_plan(
        source_names=(),
        ref_inputs=(FlakeInputRef("tool", "o", "tool", "v1", "github"),),
    )
    (tmp_path / "flake.lock").write_text(_lock_json(), encoding="utf-8")
    workspace = SimpleNamespace(
        root=tmp_path,
        baseline_content=lambda _path: _lock_json().encode(),
    )
    refs = [FlakeInputRef("tool", "o", "tool", "v1", "github")]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("lib.update.cli.get_flake_inputs_with_refs", lambda: refs)
        assert (
            _failed_targets_touched_flake(
                cast("Any", workspace), plan, ["tool"], updaters
            )
            == ()
        )


def test_failed_targets_touched_flake_lazy_reads_refs_once(tmp_path: Path) -> None:
    """Current refs are read once and reused across failed targets."""

    class _ToolSource(Updater):
        input_name = "tool"

    class _OtherSource(Updater):
        input_name = "other"

    updaters: dict[str, type[object]] = {
        "tool-src": _ToolSource,
        "other-src": _OtherSource,
    }
    plan = make_run_plan(
        source_names=("tool-src", "other-src"),
        ref_inputs=(
            FlakeInputRef("tool", "o", "tool", "v1", "github"),
            FlakeInputRef("other", "o", "other", "v8", "github"),
        ),
    )
    (tmp_path / "flake.lock").write_text(_lock_json(), encoding="utf-8")
    workspace = SimpleNamespace(
        root=tmp_path,
        baseline_content=lambda _path: _lock_json().encode(),
    )
    calls: list[str] = []

    def _current_refs() -> list[FlakeInputRef]:
        calls.append("refs")
        return [
            FlakeInputRef("tool", "o", "tool", "v2", "github"),
            FlakeInputRef("other", "o", "other", "v9", "github"),
        ]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("lib.update.cli.get_flake_inputs_with_refs", _current_refs)
        touched = _failed_targets_touched_flake(
            cast("Any", workspace),
            plan,
            ["tool-src", "other-src"],
            updaters,
        )
    assert touched == ("tool-src", "other-src")
    assert calls == ["refs"]


def test_failed_targets_touched_flake_ignores_undeclared_inputs(
    tmp_path: Path,
) -> None:
    """An input absent from the baseline refs is skipped without reading refs."""

    class _ToolSource(Updater):
        input_name = "undeclared"

    updaters: dict[str, type[object]] = {"tool-src": _ToolSource}
    plan = make_run_plan(source_names=("tool-src",))
    workspace = SimpleNamespace(
        root=tmp_path,
        baseline_content=lambda _path: None,
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            "lib.update.cli.get_flake_inputs_with_refs",
            lambda: pytest.fail("undeclared inputs must not read current refs"),
        )
        assert (
            _failed_targets_touched_flake(
                cast("Any", workspace), plan, ["tool-src"], updaters
            )
            == ()
        )


def test_withhold_failed_clusters_returns_without_failures() -> None:
    """A run without failed targets skips the withholding scan entirely."""
    outcome = _RunOutcome()
    outcome.summary.accumulate({"demo": "updated"})
    plan = make_run_plan(source_names=("demo",))
    workspace = SimpleNamespace(
        root=Path("/unused"),
        baseline_content=lambda _path: None,
    )
    _withhold_failed_clusters(
        cast("Any", workspace),
        plan,
        outcome,
        {},
        OutputOptions(),
        None,
    )
    assert outcome.dropped == {}
    assert outcome.summary.statuses == {"demo": "updated"}


def test_validation_failure_withholds_and_revalidates_the_remainder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A derivation failure removes its target and the rest is validated again."""
    live = tmp_path / "live"
    init_update_workspace_repo(
        live,
        tracked_files={
            "packages/good/sources.json": "good v1\n",
            "packages/bad/sources.json": "bad v1\n",
        },
    )

    class _Good(Updater):
        derivation_validations = (
            DerivationValidation(installable=".#packages.good.drvPath"),
        )

    class _Bad(Updater):
        derivation_validations = (
            DerivationValidation(installable=".#packages.bad.drvPath"),
        )

    rounds: list[list[str]] = []

    def _validate(
        source_names: list[str], **_kwargs: object
    ) -> tuple[DerivationValidationFailure, ...]:
        rounds.append(list(source_names))
        if "bad" in source_names:
            return (
                DerivationValidationFailure(
                    source="bad",
                    installable=".#packages.bad.drvPath",
                    message="attribute 'missing' missing",
                ),
            )
        return ()

    updaters: dict[str, type[object]] = {"good": _Good, "bad": _Bad}
    _install_run(
        monkeypatch,
        live=live,
        plan=make_run_plan(source_names=("good", "bad")),
        updaters=updaters,
        execute_result=_execution(
            {"good": "updated", "bad": "updated"},
            writes={"good": "good v2\n", "bad": "bad v2\n"},
        ),
        validate_derivations=_validate,
    )

    assert _run(UpdateOptions()) == 1
    assert rounds == [["good", "bad"], ["good"]]
    assert (live / "packages/good/sources.json").read_text("utf-8") == "good v2\n"
    assert (live / "packages/bad/sources.json").read_text("utf-8") == "bad v1\n"
    captured = capsys.readouterr()
    assert "Phase 3: derivation validation (round 2)" in captured.out
    assert "Updated: good" in captured.out
    assert "attribute 'missing' missing" in captured.err
    assert "Failed: bad" in captured.err


def test_withhold_failed_clusters_notes_the_monitor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Withholding is also recorded on the run monitor when one is present."""
    live = tmp_path / "live"
    init_update_workspace_repo(
        live,
        tracked_files={
            "packages/good/sources.json": "good v1\n",
            "packages/bad/sources.json": "bad v1\n",
        },
    )

    class _Bad(Updater):
        companion_of = "good"

    class _Good(Updater):
        pass

    updaters: dict[str, type[object]] = {"good": _Good, "bad": _Bad}
    outcome = _RunOutcome()
    outcome.summary.accumulate({"good": "updated", "bad": "error"})
    outcome.written_paths = (_source_path("good"), _source_path("bad"))
    plan = make_run_plan(source_names=("good", "bad"))

    class _Workspace:
        @property
        def root(self) -> Path:
            """Point workspace-relative resolution at the live repo copy."""
            return Path.cwd()

        def baseline_content(self, _path: object) -> bytes | None:
            return None

        def restore_baseline(self, paths: object) -> tuple[Path, ...]:
            return tuple(Path(str(path)) for path in cast("list[object]", list(paths)))

    monitor = RunMonitor(targets=("good", "bad"), phase_count=4)
    monkeypatch.setattr(
        "lib.update.persistence.planned_update_paths",
        _planned_paths,
    )
    monkeypatch.setattr("lib.update.flake.invalidate_flake_lock", lambda: None)
    _withhold_failed_clusters(
        cast("Any", _Workspace()),
        plan,
        outcome,
        updaters,
        OutputOptions(),
        monitor,
    )
    monitor.close()
    assert outcome.dropped == {"good": "coupled to failed target bad"}
    assert (live / "packages/good/sources.json").read_text("utf-8") == "good v1\n"


def test_withhold_failed_clusters_runs_without_a_monitor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Withholding works identically when no run monitor was created."""
    live = tmp_path / "live"
    init_update_workspace_repo(
        live,
        tracked_files={
            "packages/good/sources.json": "good v1\n",
            "packages/bad/sources.json": "bad v1\n",
        },
    )

    class _Bad(Updater):
        companion_of = "good"

    class _Good(Updater):
        pass

    updaters: dict[str, type[object]] = {"good": _Good, "bad": _Bad}
    outcome = _RunOutcome()
    outcome.summary.accumulate({"good": "updated", "bad": "error"})
    outcome.written_paths = (_source_path("good"), _source_path("bad"))
    plan = make_run_plan(source_names=("good", "bad"))

    class _Workspace:
        @property
        def root(self) -> Path:
            """Point workspace-relative resolution at the live repo copy."""
            return Path.cwd()

        def baseline_content(self, _path: object) -> bytes | None:
            return None

        def restore_baseline(self, paths: object) -> tuple[Path, ...]:
            return tuple(Path(str(path)) for path in cast("list[object]", list(paths)))

    monkeypatch.setattr(
        "lib.update.persistence.planned_update_paths",
        _planned_paths,
    )
    monkeypatch.setattr("lib.update.flake.invalidate_flake_lock", lambda: None)
    _withhold_failed_clusters(
        cast("Any", _Workspace()),
        plan,
        outcome,
        updaters,
        OutputOptions(),
        None,
    )
    assert outcome.dropped == {"good": "coupled to failed target bad"}
    assert (live / "packages/good/sources.json").read_text("utf-8") == "good v1\n"


def test_validation_rounds_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Persistent validation failures stop after a few rounds without promoting."""
    live = tmp_path / "live"
    names = ("one", "two", "three", "four")
    init_update_workspace_repo(
        live,
        tracked_files={
            f"packages/{name}/sources.json": f"{name} v1\n" for name in names
        },
    )

    class _Validating(Updater):
        derivation_validations = (
            DerivationValidation(installable=".#packages.{name}.drvPath"),
        )

    def _always_fail(
        source_names: list[str], **_kwargs: object
    ) -> tuple[DerivationValidationFailure, ...]:
        return (
            DerivationValidationFailure(
                source=source_names[0], installable="x", message="broken"
            ),
        )

    _install_run(
        monkeypatch,
        live=live,
        plan=make_run_plan(source_names=names),
        updaters=dict.fromkeys(names, _Validating),
        execute_result=_execution(
            dict.fromkeys(names, "updated"),
            writes={name: f"{name} v2\n" for name in names},
        ),
        validate_derivations=_always_fail,
    )

    assert _run(UpdateOptions()) == 1
    for name in names:
        assert (live / f"packages/{name}/sources.json").read_text("utf-8") == (
            f"{name} v1\n"
        )
    captured = capsys.readouterr()
    assert "kept failing after 3 rounds" in captured.err
    assert "Candidate updates discarded" in captured.out


def test_strict_mode_discards_everything_on_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Strict runs keep the historical all-or-nothing contract."""
    live = tmp_path / "live"
    init_update_workspace_repo(
        live,
        tracked_files={
            "packages/good/sources.json": "good v1\n",
            "packages/bad/sources.json": "bad v1\n",
        },
    )

    class _Plain(Updater):
        pass

    rounds: list[list[str]] = []

    def _fail_bad(
        source_names: list[str], **_kwargs: object
    ) -> tuple[DerivationValidationFailure, ...]:
        rounds.append(list(source_names))
        return (
            DerivationValidationFailure(source="bad", installable="x", message="no"),
        )

    _install_run(
        monkeypatch,
        live=live,
        plan=make_run_plan(source_names=("good", "bad")),
        updaters={"good": _Plain, "bad": _Plain},
        execute_result=_execution(
            {"good": "updated", "bad": "updated"},
            writes={"good": "good v2\n", "bad": "bad v2\n"},
        ),
        validate_derivations=_fail_bad,
    )

    assert _run(UpdateOptions(strict=True)) == 1
    assert rounds == [["good", "bad"]]
    assert (live / "packages/good/sources.json").read_text("utf-8") == "good v1\n"
    assert "Candidate updates discarded: good, bad" in capsys.readouterr().out


def test_run_log_records_the_run_and_is_reported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With a run log root configured, the run leaves an inspectable directory."""
    monkeypatch.setenv("UPDATE_RUN_LOG", "1")
    monkeypatch.setenv("UPDATE_RUN_LOG_DIR", str(tmp_path / "runs"))

    class _Passthrough:
        def __init__(self, root: Path) -> None:
            self.root = root

        def __enter__(self) -> _Passthrough:
            return self

        def __exit__(self, *_exc_info: object) -> None:
            return None

        def validation_snapshot(self) -> nullcontext[UpdateValidationSnapshot]:
            return nullcontext(
                UpdateValidationSnapshot(root=self.root, changed_paths=())
            )

        def promote(self, _allowed: object) -> tuple[Path, ...]:
            return ()

        def validate_changes(self, _allowed: object) -> tuple[Path, ...]:
            return ()

        def baseline_content(self, _path: object) -> bytes | None:
            return None

        def restore_baseline(self, paths: object) -> tuple[Path, ...]:
            return tuple(Path(str(path)) for path in cast("list[object]", list(paths)))

    monkeypatch.setattr("lib.update.persistence.IsolatedUpdateWorkspace", _Passthrough)
    monkeypatch.setattr(
        "lib.update.cli._build_run_plan",
        lambda _opts: make_run_plan(source_names=("demo",)),
    )
    monkeypatch.setattr("lib.update.cli._get_updaters", dict)
    monkeypatch.setattr("lib.update.cli.consume_events", drain_events)
    monkeypatch.setattr(
        "lib.update.source_runner.run_sources_phase",
        lambda _context: asyncio.sleep(
            0, result=UpdatePhaseResult(details={"demo": "no_change"})
        ),
    )
    monkeypatch.setattr(
        "lib.update.persistence.persist_materialized_updates", lambda **_kwargs: ()
    )
    monkeypatch.setattr(
        "lib.update.persistence.planned_update_paths", lambda *_args: ()
    )

    assert _run(UpdateOptions(targets=("demo",), json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    run_dir = Path(payload["runLog"])
    assert run_dir.parent == tmp_path / "runs"
    events = RunStore(run_dir, readonly=True).events()
    assert {"kind": "phase", "phase": "sources", "index": 2}.items() <= events[
        0
    ].items()
    assert events[-1]["kind"] == "summary"
    assert events[-1]["promoted"] is False
    assert (run_dir / DATABASE_FILE).exists()
    assert (tmp_path / "runs" / "latest").resolve() == run_dir.resolve()


def test_run_log_line_is_printed_for_text_check_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Non-JSON check runs also print where the run log lives."""
    monkeypatch.setenv("UPDATE_RUN_LOG", "1")
    monkeypatch.setenv("UPDATE_RUN_LOG_DIR", str(tmp_path / "runs"))

    class _Passthrough:
        def __init__(self, root: Path) -> None:
            self.root = root

        def __enter__(self) -> _Passthrough:
            return self

        def __exit__(self, *_exc_info: object) -> None:
            return None

        def validation_snapshot(self) -> nullcontext[UpdateValidationSnapshot]:
            return nullcontext(
                UpdateValidationSnapshot(root=self.root, changed_paths=())
            )

        def promote(self, _allowed: object) -> tuple[Path, ...]:
            return ()

        def validate_changes(self, _allowed: object) -> tuple[Path, ...]:
            return ()

        def baseline_content(self, _path: object) -> bytes | None:
            return None

        def restore_baseline(self, paths: object) -> tuple[Path, ...]:
            return tuple(Path(str(path)) for path in cast("list[object]", list(paths)))

    monkeypatch.setattr("lib.update.persistence.IsolatedUpdateWorkspace", _Passthrough)
    monkeypatch.setattr(
        "lib.update.cli._build_run_plan",
        lambda _opts: make_run_plan(source_names=("demo",)),
    )
    monkeypatch.setattr("lib.update.cli._get_updaters", dict)
    monkeypatch.setattr("lib.update.cli.consume_events", drain_events)
    monkeypatch.setattr(
        "lib.update.source_runner.run_sources_phase",
        lambda _context: asyncio.sleep(
            0, result=UpdatePhaseResult(details={"demo": "no_change"})
        ),
    )
    monkeypatch.setattr(
        "lib.update.persistence.persist_materialized_updates", lambda **_kwargs: ()
    )
    monkeypatch.setattr("lib.update.persistence.planned_update_paths", lambda *_: ())

    assert _run(UpdateOptions(targets=("demo",), check=True)) == 0
    out = capsys.readouterr().out
    assert "Run log: " in out
    run_dir = Path(out.split("Run log: ")[1].splitlines()[0].strip())
    assert (run_dir / DATABASE_FILE).exists()


def test_execute_run_plan_reports_phases_without_a_monitor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase headers and target execution also run when no monitor exists."""

    def _sources_phase(_context: object) -> asyncio.Future[UpdatePhaseResult]:
        future: asyncio.Future[UpdatePhaseResult] = (
            asyncio.get_running_loop().create_future()
        )
        future.set_result(UpdatePhaseResult(details={"demo": "no_change"}))
        return future

    monkeypatch.setattr(
        "lib.update.source_runner.run_sources_phase",
        lambda _context: _sources_phase(_context),
    )

    async def _run() -> object:
        return await _execute_run_plan_result(
            UpdateOptions(),
            OutputOptions(),
            resolve_config(),
            make_run_plan(
                source_names=("demo",),
                ref_inputs=(FlakeInputRef("tool", "o", "tool", "v1", "github"),),
            ),
        )

    result = asyncio.run(_run())
    assert result is not None


def test_start_run_monitor_degrades_when_the_run_log_is_unwritable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unwritable run log root warns and continues in memory."""
    monkeypatch.setenv("UPDATE_RUN_LOG", "1")
    monkeypatch.setenv("UPDATE_RUN_LOG_DIR", str(tmp_path / "runs"))
    original_start = RunMonitor.start
    printers: list[Callable[[str], None] | None] = []

    def _start(**kwargs: object) -> RunMonitor:
        if kwargs["run_root"] is not None:
            msg = "read-only file system"
            raise OSError(msg)
        return original_start(**cast("Any", kwargs))

    monkeypatch.setattr(RunMonitor, "start", _start)
    monkeypatch.setattr(
        RunMonitor,
        "start_heartbeat",
        lambda _self, _interval, printer: printers.append(printer),
    )
    plan = make_run_plan(source_names=("demo",))
    out = OutputOptions()
    monitor = _start_run_monitor(plan, UpdateOptions(tty="off"), out, resolve_config())

    assert monitor.run_dir is None
    captured = capsys.readouterr()
    assert "Warning: run log unavailable" in captured.err
    assert "Run log:" not in captured.out
    printer = printers[0]
    assert printer is not None
    printer("Phase 2/4 sources · 0/1 done")
    assert "Phase 2/4 sources" in capsys.readouterr().out
    monitor.close()

    quiet = _start_run_monitor(plan, UpdateOptions(quiet=True), out, resolve_config())
    assert printers[1] is None
    quiet.close()


def test_status_request_reads_latest_and_named_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--status`` reports the latest or a named run, in text or JSON."""
    root = tmp_path / "runs"
    run_id = "20260913-190800-ab12"
    monitor = RunMonitor(
        targets=("demo",), phase_count=4, run_dir=root / run_id, run_id=run_id
    )
    monitor.begin_phase("sources", 2)
    monitor.record(UpdateEvent.status("demo", "Fetching latest"))
    monitor.close()
    monkeypatch.setenv("UPDATE_RUN_LOG_DIR", str(root))
    config = resolve_config()

    assert _handle_status_request(UpdateOptions(), OutputOptions(), config) is None

    assert (
        _handle_status_request(UpdateOptions(status=True), OutputOptions(), config) == 0
    )
    text = capsys.readouterr().out
    assert f"Run {run_id} started" in text
    assert "State written" in text
    assert f"Run log: {(root / run_id).resolve()}" in text

    json_out = OutputOptions(json_output=True)
    assert (
        _handle_status_request(
            UpdateOptions(status=True, targets=(run_id,), json=True), json_out, config
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["runDir"] == str((root / run_id).resolve())
    assert payload["status"]["run_id"] == run_id
    assert payload["status"]["finished"] is True

    assert (
        _handle_status_request(
            UpdateOptions(status=True, targets=("missing",)), OutputOptions(), config
        )
        == 1
    )
    assert "No recorded update run" in capsys.readouterr().err
    assert (
        _handle_status_request(
            UpdateOptions(status=True, targets=("missing",), json=True),
            json_out,
            config,
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out)["success"] is False


def test_run_log_root_and_live_ui_gating(monkeypatch: pytest.MonkeyPatch) -> None:
    """The run log root honors the on/off switch; the panel needs a real TTY."""
    monkeypatch.setenv("UPDATE_RUN_LOG", "0")
    assert _run_log_root(resolve_config()) is None
    monkeypatch.setenv("UPDATE_RUN_LOG", "1")
    monkeypatch.setenv("UPDATE_RUN_LOG_DIR", "/tmp/nixcfg-runs")
    assert _run_log_root(resolve_config()) == Path("/tmp/nixcfg-runs")

    assert not _live_ui_enabled(UpdateOptions(tty="off"))
    assert _live_ui_enabled(UpdateOptions(tty="force"))
    assert not _live_ui_enabled(UpdateOptions(tty="force", quiet=True))
    assert not _live_ui_enabled(UpdateOptions(tty="force", json=True))


def test_validation_display_only_for_the_live_panel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The validation block appears only where the item panel was live."""
    config = resolve_config()
    monitor = RunMonitor(targets=("demo",), phase_count=4)
    plan = make_run_plan(source_names=("demo",))

    assert isinstance(
        _validation_display(plan, UpdateOptions(), config, None), nullcontext
    )
    assert isinstance(
        _validation_display(None, UpdateOptions(), config, monitor), nullcontext
    )
    assert isinstance(
        _validation_display(plan, UpdateOptions(), config, monitor), nullcontext
    )

    monkeypatch.setattr(
        "lib.update.ui_render.Live",
        lambda **_kwargs: SimpleNamespace(start=lambda: None, stop=lambda: None),
    )
    display = _validation_display(plan, UpdateOptions(tty="force"), config, monitor)
    assert isinstance(display, ValidationDisplay)
    tail = object.__getattribute__(display, "_tail")
    assert tail() == ()
    monitor.validation_started("roots: nix build")
    monitor.validation_output("roots: nix build", "building")
    assert tail() == ("building",)
    monitor.close()


def test_emit_summary_reports_withheld_and_unpromoted_runs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Text and JSON summaries name withheld candidates and empty promotions."""
    summary = UpdateSummary(statuses={"bad": "error", "child": "dropped"})
    exit_code = _emit_summary(
        summary,
        had_errors=True,
        out=OutputOptions(),
        dry_run=False,
        dropped={"child": "coupled to failed target bad"},
    )
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "No updates promoted." in captured.out
    assert (
        "Withheld from promotion: child (coupled to failed target bad)" in captured.out
    )
    assert "Failed: bad" in captured.err

    exit_code = _emit_summary(
        summary,
        had_errors=True,
        out=OutputOptions(json_output=True),
        dry_run=False,
        dropped={"child": "coupled to failed target bad"},
    )
    assert exit_code == 1
    assert json.loads(capsys.readouterr().out) == {
        "updated": [],
        "dropped": ["child"],
        "errors": ["bad"],
        "noChange": [],
        "success": False,
        "withheld": {"child": "coupled to failed target bad"},
    }


@pytest.mark.parametrize(
    "case",
    [
        "no-input",
        "no-baseline",
        "missing",
        "invalid-json",
        "bad-nodes",
        "bad-root",
        "bad-inputs",
        "follow-edge",
        "bad-node",
        "unchanged",
    ],
)
def test_failed_input_rollback_preserves_unusable_or_unchanged_locks(
    tmp_path: Path, case: str
) -> None:
    """Incomplete lock graphs must not cause a speculative partial rewrite."""
    from lib.update.cli import _rollback_failed_input_locks

    class Tool(Updater):
        input_name = "tool"

    baseline = json.loads(_lock_json())
    current = json.loads(_lock_json(tool_rev="c" * 40))
    if case == "bad-nodes":
        baseline["nodes"] = []
    elif case == "bad-root":
        current["root"] = None
    elif case == "bad-inputs":
        baseline["nodes"]["root"]["inputs"] = []
    elif case == "follow-edge":
        baseline["nodes"]["root"]["inputs"]["tool"] = ["other"]
    elif case == "bad-node":
        baseline["nodes"]["tool"] = "invalid"
    elif case == "unchanged":
        current = baseline
    encoded = json.dumps(baseline).encode()
    if case == "invalid-json":
        encoded = b"{"
    path = tmp_path / "flake.lock"
    if case != "missing":
        path.write_text(json.dumps(current))
    before = path.read_bytes() if path.exists() else None
    workspace = SimpleNamespace(
        root=tmp_path,
        baseline_content=lambda _: None if case == "no-baseline" else encoded,
    )
    result = _rollback_failed_input_locks(
        workspace,
        make_run_plan(source_names=("tool",)),
        ("tool",),
        {"tool": Updater if case == "no-input" else Tool},
    )
    assert result == ()
    assert (path.read_bytes() if path.exists() else None) == before


def test_prune_lock_graph_preserves_reachable_leaves_cycles_and_follows() -> None:
    """Garbage collection terminates on cycles and removes only unreachable nodes."""
    from lib.update.cli import _prune_unreachable_lock_nodes

    for invalid in ({"nodes": []}, {"nodes": {}, "root": None}):
        before = json.loads(json.dumps(invalid))
        _prune_unreachable_lock_nodes(invalid)
        assert invalid == before
    lock = {
        "root": "root",
        "nodes": {
            "root": {
                "inputs": {
                    "self": "root",
                    "leaf": "leaf",
                    "opaque": "opaque",
                    "missing": "missing",
                    "follows": ["leaf"],
                }
            },
            "leaf": {},
            "opaque": None,
            "unreachable": {},
        },
    }
    _prune_unreachable_lock_nodes(lock)
    assert set(lock["nodes"]) == {"root", "leaf", "opaque"}
