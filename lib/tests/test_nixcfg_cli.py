"""CLI-level tests for the nixcfg Typer entrypoint.

These tests focus on argument parsing/dispatch glue, not the update pipeline
implementation (which is covered elsewhere).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import click
from typer.main import get_command
from typer.testing import CliRunner

import nixcfg
from lib import http_utils
from lib.github_actions.client import Workflow, WorkflowListRow, WorkflowRun
from lib.schema_codegen.runner import SchemaTargetSummary

if TYPE_CHECKING:
    from lib.update.cli import UpdateOptions


class _MonkeyPatchLike(Protocol):
    def setattr(self, target: str, value: object) -> None: ...


def _workflow_record() -> Workflow:
    timestamp = datetime(2026, 4, 2, 16, 0, tzinfo=UTC)
    return Workflow.model_construct(
        id=1,
        node_id="WF_1",
        name="Periodic Flake Update",
        path=".github/workflows/update.yml",
        state="active",
        created_at=timestamp,
        updated_at=timestamp,
        url="https://api.github.com/repos/acme/demo/actions/workflows/1",
        html_url="https://github.com/acme/demo/actions/workflows/1",
        badge_url="https://github.com/acme/demo/actions/workflows/1/badge.svg",
        deleted_at=None,
    )


def _workflow_run_record(status: str) -> WorkflowRun:
    created_at = datetime(2026, 4, 2, 16, 0, tzinfo=UTC)
    updated_at = datetime(2026, 4, 2, 16, 1, tzinfo=UTC)
    conclusion = None if status != "completed" else "success"
    return WorkflowRun.model_construct(
        id=9,
        name="update.yml",
        node_id="WR_9",
        check_suite_id=100,
        check_suite_node_id="CS_100",
        head_branch="main",
        head_sha="deadbeef",
        path=".github/workflows/update.yml@refs/heads/main",
        run_number=683,
        run_attempt=1,
        referenced_workflows=[],
        event="workflow_dispatch",
        status=status,
        conclusion=conclusion,
        workflow_id=1,
        url="https://api.github.com/repos/acme/demo/actions/runs/9",
        html_url="https://github.com/acme/demo/actions/runs/9",
        pull_requests=[],
        created_at=created_at,
        updated_at=updated_at,
        actor=None,
        triggering_actor=None,
        run_started_at=created_at,
        jobs_url="https://api.github.com/repos/acme/demo/actions/runs/9/jobs",
        logs_url="https://api.github.com/repos/acme/demo/actions/runs/9/logs",
        check_suite_url="https://api.github.com/check-suites/100",
        artifacts_url="https://api.github.com/repos/acme/demo/actions/runs/9/artifacts",
        cancel_url="https://api.github.com/repos/acme/demo/actions/runs/9/cancel",
        rerun_url="https://api.github.com/repos/acme/demo/actions/runs/9/rerun",
        previous_attempt_url=None,
        workflow_url="https://api.github.com/repos/acme/demo/actions/workflows/1",
        head_commit={},
        repository={},
        head_repository={},
        head_repository_id=1,
        display_title="Periodic Flake Update",
    )


def test_nixcfg_update_parses_native_only(monkeypatch: _MonkeyPatchLike) -> None:
    """Ensure `nixcfg update --native-only` maps to UpdateOptions.native_only."""
    called: dict[str, UpdateOptions] = {}

    async def _fake_run_updates(opts: UpdateOptions) -> int:
        called["opts"] = opts
        return 0

    monkeypatch.setattr("lib.update.cli.check_required_tools", lambda **_kw: [])
    monkeypatch.setattr("lib.update.cli.run_updates", _fake_run_updates)

    runner = CliRunner()
    result = runner.invoke(nixcfg.app, ["update", "--native-only"])

    assert result.exit_code == 0
    assert called["opts"].native_only is True


def test_nixcfg_recover_snapshot_parses_flags(monkeypatch: _MonkeyPatchLike) -> None:
    """Ensure recover snapshot forwards its argument and flags."""
    called: dict[str, object] = {}

    def _fake_run(
        generation: str = "/run/current-system",
        *,
        json_output: bool = False,
    ) -> int:
        called.update(generation=generation, json_output=json_output)
        return 0

    monkeypatch.setattr("lib.recover.cli.run_snapshot_recovery", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app, ["recover", "snapshot", "/run/current-system", "-j"]
    )

    assert result.exit_code == 0
    assert called == {"generation": "/run/current-system", "json_output": True}


def test_nixcfg_recover_files_parses_flags(monkeypatch: _MonkeyPatchLike) -> None:
    """Ensure recover files forwards selectors and flags."""
    called: dict[str, object] = {}

    def _fake_run(
        generation: str = "/run/current-system",
        *,
        apply: bool = False,
        globs: tuple[str, ...] = (),
        json_output: bool = False,
        paths: tuple[str, ...] = (),
        stage: bool = False,
        sync: bool = False,
    ) -> int:
        called.update(
            generation=generation,
            apply=apply,
            globs=globs,
            json_output=json_output,
            paths=paths,
            stage=stage,
            sync=sync,
        )
        return 0

    monkeypatch.setattr("lib.recover.cli.run_file_recovery", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app,
        [
            "recover",
            "files",
            "/run/current-system",
            "-a",
            "-g",
            "-s",
            "-j",
            "-p",
            "flake.lock",
            "-G",
            "docs/*.md",
        ],
    )

    assert result.exit_code == 0
    assert called == {
        "generation": "/run/current-system",
        "apply": True,
        "globs": ("docs/*.md",),
        "json_output": True,
        "paths": ("flake.lock",),
        "stage": True,
        "sync": True,
    }


def test_nixcfg_recover_hashes_parses_flags(monkeypatch: _MonkeyPatchLike) -> None:
    """Ensure recover hashes forwards its argument and flags."""
    called: dict[str, object] = {}

    def _fake_run(
        generation: str = "/run/current-system",
        *,
        apply: bool = False,
        json_output: bool = False,
        stage: bool = False,
        sync: bool = False,
    ) -> int:
        called.update(
            generation=generation,
            apply=apply,
            json_output=json_output,
            stage=stage,
            sync=sync,
        )
        return 0

    monkeypatch.setattr("lib.recover.cli.run_hash_recovery", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app,
        ["recover", "hashes", "/run/current-system", "-a", "-g", "-s", "-j"],
    )

    assert result.exit_code == 0
    assert called == {
        "generation": "/run/current-system",
        "apply": True,
        "json_output": True,
        "stage": True,
        "sync": True,
    }


def test_nixcfg_update_help_includes_typer_options() -> None:
    """Ensure `nixcfg update --help` shows typed option definitions."""
    runner = CliRunner()
    result = runner.invoke(nixcfg.app, ["update", "--help"])

    assert result.exit_code == 0
    assert "--native-only" in result.output
    assert "--pinned-versions" in result.output
    assert "--no-sources" in result.output


def test_nixcfg_schema_targets_lists_configured_targets(
    monkeypatch: _MonkeyPatchLike,
) -> None:
    """Ensure `nixcfg schema targets` renders target summaries."""
    monkeypatch.setattr(
        "nixcfg.list_schema_codegen_targets",
        lambda *, config_path: (
            SchemaTargetSummary(name="demo", output=config_path.parent / "demo.py"),
        ),
    )

    runner = CliRunner()
    result = runner.invoke(nixcfg.app, ["schema", "targets"])

    assert result.exit_code == 0
    assert "demo\tdemo.py" in result.output


def test_nixcfg_schema_generate_forwards_target_and_config(
    monkeypatch: _MonkeyPatchLike,
) -> None:
    """Ensure `nixcfg schema generate` forwards target and config path."""
    called: dict[str, object] = {}

    def _fake_generate(
        *, config_path: object, progress: object, target_name: str
    ) -> Path:
        called.update(
            config_path=config_path, progress=progress, target_name=target_name
        )
        return Path("/tmp/generated.py")

    monkeypatch.setattr("nixcfg.generate_schema_codegen_target", _fake_generate)

    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app,
        ["schema", "generate", "demo", "-c", "alt-config.yaml"],
    )

    assert result.exit_code == 0
    assert called["target_name"] == "demo"
    assert called["progress"] is nixcfg._schema_progress
    assert called["config_path"] == Path("alt-config.yaml")
    assert "Generated /tmp/generated.py" in result.output


def test_nixcfg_schema_lock_forwards_manifest_output_and_metadata(
    monkeypatch: _MonkeyPatchLike,
) -> None:
    """Ensure `nixcfg schema lock` forwards manifest-path arguments."""
    called: dict[str, object] = {}

    def _fake_lock(
        *,
        manifest_path: Path,
        lockfile_path: Path | None,
        include_metadata: bool,
        progress: object,
    ) -> Path:
        called.update(
            manifest_path=manifest_path,
            lockfile_path=lockfile_path,
            include_metadata=include_metadata,
            progress=progress,
        )
        return Path("/tmp/codegen.lock.json")

    monkeypatch.setattr("nixcfg.write_codegen_lockfile", _fake_lock)

    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app,
        [
            "schema",
            "lock",
            "codegen.yaml",
            "--output",
            "custom.lock.json",
            "--include-metadata",
        ],
    )

    assert result.exit_code == 0
    assert called["manifest_path"] == Path("codegen.yaml")
    assert called["lockfile_path"] == Path("custom.lock.json")
    assert called["include_metadata"] is True
    assert called["progress"] is nixcfg._schema_progress
    assert "Generated /tmp/codegen.lock.json" in result.output


def test_nixcfg_all_commands_support_short_help_alias() -> None:
    """Ensure every command accepts `-h` alongside `--help`."""
    root = get_command(nixcfg.app)
    failures: list[str] = []
    command_paths: list[list[str]] = []

    def _walk(cmd: click.Command, path: list[str]) -> None:
        command_paths.append(path)
        if isinstance(cmd, click.Group):
            for name, subcommand in cmd.commands.items():
                _walk(subcommand, [*path, name])

    _walk(root, [])

    for path in command_paths:
        try:
            root.main(args=[*path, "-h"], prog_name="nixcfg", standalone_mode=False)
        except click.exceptions.Exit as exc:
            if exc.exit_code != 0:
                path_display = "nixcfg" if not path else f"nixcfg {' '.join(path)}"
                failures.append(f"{path_display} (-h) -> exit {exc.exit_code}")

    assert failures == [], failures


def test_nixcfg_actions_workflows_renders_rows(
    monkeypatch: _MonkeyPatchLike,
) -> None:
    """Ensure `nixcfg actions workflows` renders discovered workflows."""
    monkeypatch.setattr(
        "lib.github_actions.cli._workflow_rows",
        lambda **_kwargs: (
            WorkflowListRow(
                workflow=_workflow_record(),
                latest_run=_workflow_run_record("completed"),
            ),
        ),
    )

    runner = CliRunner()
    result = runner.invoke(nixcfg.app, ["actions", "workflows"])

    assert result.exit_code == 0
    assert "Periodic Flake Update" in result.output
    assert ".github/workflows/update.yml" in result.output
    assert "#683 success" in result.output


def test_nixcfg_actions_workflows_surfaces_errors_cleanly(
    monkeypatch: _MonkeyPatchLike,
) -> None:
    """Ensure `nixcfg actions workflows` reports lookup failures without a traceback."""
    monkeypatch.setattr(
        "lib.github_actions.cli._workflow_rows",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("bad repo")),
    )

    runner = CliRunner()
    result = runner.invoke(nixcfg.app, ["actions", "workflows", "--repo", "broken"])

    assert result.exit_code != 0
    assert "bad repo" in result.output
    assert "Traceback" not in result.output


def test_nixcfg_actions_tail_forwards_workflow_and_optional_job(
    monkeypatch: _MonkeyPatchLike,
) -> None:
    """Ensure `nixcfg actions tail` wires workflow, job, and auth options through."""
    called: dict[str, object] = {}

    class _FakeTailer:
        def __init__(
            self,
            *,
            api_client: object,
            live_client: object,
            output: object,
            poll_interval: float,
        ) -> None:
            called.update(
                api_client=api_client,
                live_client=live_client,
                output=output,
                poll_interval=poll_interval,
            )

        async def tail_workflow(
            self,
            *,
            workflow: object,
            run: object,
            requested_job_name: str | None,
        ) -> None:
            called.update(
                workflow=workflow,
                run=run,
                requested_job_name=requested_job_name,
            )

    class _FakeLiveClient:
        async def aclose(self) -> None:
            called["closed"] = True

    workflow = _workflow_record()
    run = _workflow_run_record("in_progress")

    monkeypatch.setattr(
        "lib.github_actions.cli.select_named_workflow",
        lambda _workflows, _name: workflow,
    )
    monkeypatch.setattr(
        "lib.github_actions.cli.choose_live_run",
        lambda _runs: run,
    )
    monkeypatch.setattr(
        "lib.github_actions.cli.GitHubActionsTailer",
        _FakeTailer,
    )

    class _FakeApiClient:
        def list_workflows(self) -> tuple[Workflow, ...]:
            return (workflow,)

        def list_workflow_runs(
            self,
            _workflow_id: int,
            *,
            limit: int = 20,
        ) -> tuple[WorkflowRun, ...]:
            assert limit == 20
            return (run,)

    def _build_tail_clients(**kwargs: object) -> tuple[object, object]:
        called["build_kwargs"] = kwargs
        return _FakeApiClient(), _FakeLiveClient()

    monkeypatch.setattr(
        "lib.github_actions.cli._build_tail_clients",
        _build_tail_clients,
    )

    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app,
        [
            "actions",
            "tail",
            "Periodic Flake Update",
            "darwin-lock-smoke",
            "-i",
            "2.5",
            "--chrome-debugging-url",
            "http://127.0.0.1:9333",
            "--allow-playwright-login",
        ],
    )

    assert result.exit_code == 0
    assert called["workflow"] == workflow
    assert called["run"] == run
    assert called["requested_job_name"] == "darwin-lock-smoke"
    assert called["poll_interval"] == 2.5
    assert called["closed"] is True
    assert called["build_kwargs"] == {
        "repo": None,
        "server_url": None,
        "chrome_debugging_url": "http://127.0.0.1:9333",
        "allow_playwright_login": True,
    }


def test_nixcfg_actions_tail_surfaces_request_errors_cleanly(
    monkeypatch: _MonkeyPatchLike,
) -> None:
    """Ensure `nixcfg actions tail` reports live-tail transport errors cleanly."""

    class _FailingTailer:
        def __init__(
            self,
            *,
            api_client: object,
            live_client: object,
            output: object,
            poll_interval: float,
        ) -> None:
            del api_client, live_client, output, poll_interval

        async def tail_workflow(
            self,
            *,
            workflow: object,
            run: object,
            requested_job_name: str | None,
        ) -> None:
            del workflow, run, requested_job_name
            raise http_utils.SyncRequestError(
                url="https://github.com/acme/demo/actions/runs/9/job/42",
                attempts=1,
                kind="status",
                detail="HTTP 404 Not Found",
                status=404,
            )

    class _FakeLiveClient:
        async def aclose(self) -> None:
            return None

    workflow = _workflow_record()
    run = _workflow_run_record("in_progress")

    monkeypatch.setattr(
        "lib.github_actions.cli.select_named_workflow",
        lambda _workflows, _name: workflow,
    )
    monkeypatch.setattr(
        "lib.github_actions.cli.choose_live_run",
        lambda _runs: run,
    )
    monkeypatch.setattr(
        "lib.github_actions.cli.GitHubActionsTailer",
        _FailingTailer,
    )

    class _FakeApiClient:
        def list_workflows(self) -> tuple[Workflow, ...]:
            return (workflow,)

        def list_workflow_runs(
            self,
            _workflow_id: int,
            *,
            limit: int = 20,
        ) -> tuple[WorkflowRun, ...]:
            assert limit == 20
            return (run,)

    monkeypatch.setattr(
        "lib.github_actions.cli._build_tail_clients",
        lambda **_kwargs: (_FakeApiClient(), _FakeLiveClient()),
    )

    runner = CliRunner()
    result = runner.invoke(nixcfg.app, ["actions", "tail", "Periodic Flake Update"])

    assert result.exit_code != 0
    assert "HTTP 404 Not Found" in result.output
    assert "FrozenInstanceError" not in result.output


def test_nixcfg_ci_registers_sources_json_diff() -> None:
    """Ensure nested `nixcfg ci diff sources` is available."""
    runner = CliRunner()
    result = runner.invoke(nixcfg.app, ["ci", "diff", "sources", "--help"])

    assert result.exit_code == 0
    assert "--format" in result.output


def test_nixcfg_ci_subcommand_help_includes_resolve_options() -> None:
    """Ensure mounted CI apps expose their native option help."""
    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app,
        ["ci", "pipeline", "versions", "--help"],
    )

    assert result.exit_code == 0
    assert "--output" in result.output


def test_nixcfg_ci_subcommand_help_includes_crate2nix_options() -> None:
    """Ensure mounted crate2nix CI app is registered with its flags."""
    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app,
        ["ci", "pipeline", "crate2nix", "--help"],
    )

    assert result.exit_code == 0
    assert "--package" in result.output
    assert "--write" in result.output


def test_nixcfg_ci_cache_generations_help_exposes_profile_options() -> None:
    """Ensure mounted generation profiling command is registered."""
    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app,
        ["ci", "cache", "generations", "--help"],
    )

    assert result.exit_code == 0
    assert "--profile-output" in result.output


def test_nixcfg_recover_snapshot_help_exposes_recovery_options() -> None:
    """Ensure mounted snapshot recovery command is registered with its flags."""
    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app,
        ["recover", "snapshot", "--help"],
    )

    assert result.exit_code == 0
    assert "--json" in result.output


def test_nixcfg_recover_files_help_exposes_recovery_options() -> None:
    """Ensure mounted file recovery command is registered with its flags."""
    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app,
        ["recover", "files", "--help"],
    )

    assert result.exit_code == 0
    assert "--apply" in result.output
    assert "--path" in result.output
    assert "--glob" in result.output
    assert "--stage" in result.output
    assert "--sync" in result.output
    assert "--json" in result.output


def test_nixcfg_recover_hashes_help_exposes_recovery_options() -> None:
    """Ensure mounted hash recovery command is registered with its flags."""
    runner = CliRunner()
    result = runner.invoke(
        nixcfg.app,
        ["recover", "hashes", "--help"],
    )

    assert result.exit_code == 0
    assert "--apply" in result.output
    assert "--stage" in result.output
    assert "--sync" in result.output
    assert "--json" in result.output


def test_nixcfg_tree_shows_declared_command_descriptions() -> None:
    """Ensure `nixcfg tree` includes declared command help descriptions."""
    runner = CliRunner()
    result = runner.invoke(nixcfg.app, ["tree"])

    assert result.exit_code == 0
    assert (
        "actions - GitHub Actions workflow discovery and live-tail helpers."
        in result.output
    )
    assert "ci - CI helper tools for update pipelines." in result.output
    assert "pr-body - Pull request body generation workflow step." in result.output
    assert (
        "update - Update source versions/hashes and flake input refs." in result.output
    )


def test_nixcfg_tree_colors_empty_groups_like_leaf_commands() -> None:
    """Color callable groups without visible children like leaf commands."""
    root = cast("click.Group", get_command(nixcfg.app))
    ci = cast("click.Group", root.commands["ci"])
    cache = cast("click.Group", ci.commands["cache"])
    closure = cache.commands["closure"]

    assert nixcfg._command_label("cache", cache).startswith(
        "[bold cyan]cache[/bold cyan]"
    )
    assert nixcfg._command_label("closure", closure).startswith(
        "[green]closure[/green]"
    )


def test_nixcfg_all_custom_options_have_short_and_long_forms() -> None:
    """Require short+long aliases for every non-built-in CLI option."""
    root = get_command(nixcfg.app)
    exempt_names = {"help", "install_completion", "show_completion"}
    missing: list[str] = []

    def _walk(cmd: click.Command, path: list[str]) -> None:
        for param in cmd.params:
            if not isinstance(param, click.Option):
                continue
            if param.name in exempt_names:
                continue

            names = [*param.opts, *param.secondary_opts]
            has_long = any(name.startswith("--") for name in names)
            has_short = any(
                name.startswith("-") and not name.startswith("--") for name in names
            )
            if not (has_long and has_short):
                missing.append(f"{'/'.join(path)}:{param.name} -> {names}")

        if not isinstance(cmd, click.Group):
            return
        for name, subcommand in cmd.commands.items():
            _walk(subcommand, [*path, name])

    _walk(root, ["nixcfg"])
    assert missing == []


def test_nixcfg_main_uses_stable_prog_name(monkeypatch: _MonkeyPatchLike) -> None:
    """Ensure help usage keeps `nixcfg` instead of wrapper/store paths."""
    called: dict[str, str] = {}

    def _fake_app(*, prog_name: str) -> None:
        called["prog_name"] = prog_name

    monkeypatch.setattr("nixcfg.app", _fake_app)

    nixcfg.main()

    assert called["prog_name"] == "nixcfg"
