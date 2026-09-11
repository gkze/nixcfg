"""Resource admission stays separate from metadata and artifact ownership."""

import asyncio
import json

import pytest

from lib.update.cli import OutputOptions, _emit_run_outcome, _RunOutcome
from lib.update.config import resolve_config
from lib.update.events import CommandResult
from lib.update.process import (
    NixBuildOptions,
    RunCommandOptions,
    run_command,
    run_nix_build,
)
from lib.update.runtime import measure, runtime_scope, workspace_access


def test_mutable_evaluations_do_not_occupy_slots_while_waiting_for_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A waiting reader cannot take the slot required by the artifact writer."""

    async def run() -> None:
        config = resolve_config(max_nix_evaluations=1)
        called: list[str] = []
        waiting = asyncio.Event()

        async def command(args, *, options, **_kwargs):
            called.append(options.source)
            return CommandResult(args=args, returncode=0, stdout="done", stderr="")

        async def reader() -> None:
            waiting.set()
            await run_command(
                ["nix", "eval", "reader"],
                options=RunCommandOptions(source="reader", config=config),
            )

        monkeypatch.setattr("lib.update.process._run_command", command)
        async with asyncio.timeout(1), runtime_scope(config):
            async with workspace_access(write=True):
                task = asyncio.create_task(reader())
                await waiting.wait()
                await asyncio.sleep(0)
                await run_command(
                    ["nix", "eval", "writer"],
                    options=RunCommandOptions(source="writer", config=config),
                )
                assert called == ["writer"]
            await task
        assert called == ["writer", "reader"]

    asyncio.run(run())


def test_prepared_build_bypasses_workspace_but_obeys_build_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Immutable probes build under an owning artifact writer without deadlocking."""

    async def run() -> None:
        config = resolve_config(max_nix_builds=1)
        active = 0
        peak = 0
        calls: list[list[str]] = []

        async def command(args, **_kwargs):
            nonlocal active, peak
            calls.append(args)
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
            return CommandResult(args=args, returncode=1, stdout="", stderr="mismatch")

        monkeypatch.setattr("lib.update.process._run_command", command)
        async with (
            asyncio.timeout(1),
            runtime_scope(config) as runtime,
            workspace_access(write=True),
        ):
            await asyncio.gather(
                *(
                    run_nix_build(
                        "unused",
                        options=NixBuildOptions(
                            source=str(index),
                            derivation_path=f"/nix/store/probe-{index}.drv",
                            config=config,
                        ),
                    )
                    for index in range(3)
                )
            )
            assert all(
                runtime.timings[(str(index), "build")].nonzero_exits == 1
                for index in range(3)
            )
        assert peak == 1
        assert [args[-1] for args in calls] == [
            f"/nix/store/probe-{index}.drv^out" for index in range(3)
        ]
        assert all("--expr" not in args for args in calls)

    asyncio.run(run())


@pytest.mark.parametrize("json_output", [False, True])
def test_timings_are_opt_in_and_machine_readable(
    capsys: pytest.CaptureFixture[str], *, json_output: bool
) -> None:
    """Report counts and waits without persisting commands or cache identities."""

    async def run() -> None:
        async with runtime_scope(resolve_config()):
            with measure("demo", "eval") as timing:
                timing.stdout_bytes = 4
            assert (
                _emit_run_outcome(
                    _RunOutcome(),
                    out=OutputOptions(json_output=json_output, timings=True),
                    dry_run=True,
                )
                == 0
            )

    asyncio.run(run())
    text = capsys.readouterr().out
    if json_output:
        rows = json.loads(text)["timings"]
        assert rows[0]["source"] == "demo"
        assert rows[0]["operation"] == "eval"
        assert rows[0]["stdout_bytes"] == 4
        assert rows[0]["count"] == 1
    else:
        assert "demo eval:" in text
        assert "1 operations" in text


def test_resource_environment_defaults_and_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each budget is independently configurable and never resolves to zero."""
    monkeypatch.setenv("UPDATE_MAX_SOURCE_TASKS", "3")
    monkeypatch.setenv("UPDATE_MAX_NIX_EVALUATIONS", "2")
    monkeypatch.setenv("UPDATE_MAX_DOWNLOADS", "0")
    monkeypatch.setenv("UPDATE_MAX_MATERIALIZATIONS", "-1")
    config = resolve_config()
    assert config.max_source_tasks == 3
    assert config.max_nix_evaluations == 2
    assert config.max_downloads == 1
    assert config.max_materializations == 1
