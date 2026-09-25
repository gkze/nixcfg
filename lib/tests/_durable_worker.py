"""Subprocess fixture: real DBOS, SQLite, updater pipeline and filesystem commits."""

import asyncio
import os
import signal
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from lib.nix.commands.base import CommandResult, NixCommandError
from lib.tests._run_updates_helpers import make_run_plan
from lib.update import cli, durable, flake, refs, source_runner
from lib.update.artifacts import GeneratedArtifact
from lib.update.config import resolve_config
from lib.update.events import UpdateEvent
from lib.update.paths import get_repo_root
from lib.update.persistence import IsolatedUpdateWorkspace
from lib.update.ui_consumer import consume_events
from lib.update.updaters import Updater, VersionInfo


def record(message: str) -> None:
    with Path(os.environ["TEST_OPERATIONS"]).open("a") as stream:
        stream.write(message + "\n")


def stop_at(point: str) -> None:
    if os.environ.get("TEST_CRASH") == point:
        os._exit(42)


class Alpha(Updater):
    name = "alpha"
    generated_artifact_files = ("generated.txt",)

    async def _candidate_update_stream(self, info, session, *, context, emit):
        # T3 also wraps this hook with generator work outside the base method.
        record(f"generate:{self.name}")
        return await super()._candidate_update_stream(
            info, session, context=context, emit=emit
        )

    async def fetch_latest(self, session, *, context):
        _ = session, context
        record(f"resolve:{self.name}")
        return VersionInfo(version="2.0")

    async def fetch_hashes(self, info, session, *, context, emit):
        _ = session
        record(f"hash:{self.name}:{info.version}")
        if self.name == "beta":
            assert context.effective_sources["alpha"].version == "2.0"
            assert (
                context.generated_artifacts[Path("packages/alpha/generated.txt")]
                == "2.0"
            )
            stop_at("source")
            if os.environ.get("TEST_CRASH") == "cancel":
                os.kill(os.getpid(), signal.SIGINT)
                try:
                    await asyncio.Event().wait()
                finally:
                    assert get_repo_root().is_dir()
                    record("cleanup")
        await emit(
            UpdateEvent.artifact(
                self.name,
                GeneratedArtifact.text(
                    get_repo_root() / f"packages/{self.name}/generated.txt",
                    info.version,
                ),
            )
        )
        return {"x86_64-linux": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}


class Beta(Alpha):
    name = "beta"
    companion_of = "alpha"


class Gamma(Alpha):
    name = "gamma"

    async def fetch_hashes(self, info, session, *, context, emit):
        _ = info, session, context, emit
        record("fail:gamma")
        if os.environ.get("TEST_SCENARIO") == "nix-failure":
            raise NixCommandError(
                CommandResult(
                    ["nix", "store", "prefetch-file"], 1, "", "fixture transfer failed"
                )
            )
        msg = "original source failure"
        raise RuntimeError(msg)


def validate(*_args, **_kwargs):
    for name in ("alpha", "beta"):
        assert (get_repo_root() / f"packages/{name}/generated.txt").read_text() == "2.0"
    record("validate")
    stop_at("validation")
    return ()


async def consume(*args, options):
    record(f"live-ui:{options.is_tty}")
    await consume_events(*args, options=options)


async def run_fixture(
    root: Path,
    run_root: Path,
    patch: pytest.MonkeyPatch,
    *,
    resume=None,
    json_output=False,
    tty="off",
    run_id=None,
    patch_path=None,
) -> int:
    patch.setenv("REPO_ROOT", str(root))
    patch.setenv("UPDATE_RUN_LOG_DIR", str(run_root))
    patch.setenv("UPDATE_RUN_LOG", "1")
    patch.setattr(durable, "runtime_identity", lambda _: "test-runtime")
    patch.setattr(cli, "_maybe_reexec_checkout_update", lambda: None)
    patch.setattr(cli, "_handle_required_tool_check", lambda _: None)
    updaters = {"alpha": Alpha, "beta": Beta}
    scenario = os.environ.get("TEST_SCENARIO")
    if scenario in {"failure", "phases", "nix-failure"}:
        updaters["gamma"] = Gamma
    ref_inputs = ()
    if scenario == "phases":
        ref_inputs = tuple(
            refs.FlakeInputRef(name, "owner", "repo", "v1", "github")
            for name in ("good-ref", "bad-ref")
        )

        async def check_ref(input_ref, *_args, **_kwargs):
            record(f"ref:{input_ref.name}")
            return refs.RefUpdateResult(
                input_ref.name,
                "v1",
                "v1",
                error="original ref failure" if input_ref.name == "bad-ref" else None,
            )

        async def update_inputs(*_args, **_kwargs):
            record("input-refresh")
            msg = "original input failure"
            raise RuntimeError(msg)

        patch.setattr(refs, "check_flake_ref_update", check_ref)
        patch.setattr(cli, "get_flake_inputs_with_refs", lambda: list(ref_inputs))
        patch.setattr(
            refs, "read_flake_input_state", lambda _: (b"declaration", b"lock")
        )
        patch.setattr(
            flake, "read_flake_input_state", lambda _: (b"declaration", b"lock")
        )
        patch.setattr(flake, "update_flake_inputs", update_inputs)
        patch.setattr(
            source_runner,
            "_source_input_requests",
            lambda *_: (
                {"broken-input": "gamma"},
                {"alpha": (), "beta": (), "gamma": ("broken-input",)},
            ),
        )
    patch.setattr(
        cli,
        "_build_run_plan",
        lambda _: make_run_plan(
            source_names=tuple(updaters),
            ref_inputs=ref_inputs,
            do_input_refresh=scenario == "phases",
        ),
    )
    for module in (cli, source_runner):
        patch.setattr(module, "_get_updaters", lambda: updaters)
    patch.setattr(cli.update_derivation_validation, "validate_derivations", validate)
    patch.setattr(cli.update_derivation_validation, "validate_root_closures", validate)
    patch.setattr(cli, "_requires_root_closure_validation", lambda *_: True)
    patch.setattr(cli, "consume_events", consume)
    original_source = source_runner.update_source_task

    async def source(name, *, context):
        result = await original_source(name, context=context)
        if name == "alpha":
            stop_at("source-result")
        if name == "gamma":
            stop_at("source-error")
        return result

    patch.setattr(source_runner, "update_source_task", source)
    original_promote = IsolatedUpdateWorkspace.promote

    def promote(workspace, paths):
        stop_at("promotion")
        result = original_promote(workspace, paths)
        record("promote")
        stop_at("acknowledgement")
        return result

    patch.setattr(IsolatedUpdateWorkspace, "promote", promote)
    opts = (
        cli.UpdateOptions(resume=resume, tty=tty)
        if resume
        else cli.UpdateOptions(
            targets=("alpha", "beta"),
            no_refs=scenario != "phases",
            no_input=scenario != "phases",
            tty=tty,
            timings=True,
        )
    )
    opts = replace(opts, json=json_output, run_id=run_id, patch=patch_path)
    return await durable.execute(
        opts, root, replace(resolve_config(), heartbeat_interval=60)
    )


if __name__ == "__main__":
    root, run_root = map(Path, sys.argv[1:3])
    with pytest.MonkeyPatch.context() as patch:
        raise SystemExit(
            asyncio.run(
                run_fixture(
                    root,
                    run_root,
                    patch,
                    resume=sys.argv[3] if len(sys.argv) > 3 else None,
                    json_output=os.environ.get("TEST_JSON") == "1",
                    tty=os.environ.get("TEST_TTY", "off"),
                    run_id=os.environ.get("TEST_RUN_ID") or None,
                    patch_path=os.environ.get("TEST_PATCH") or None,
                )
            )
        )
