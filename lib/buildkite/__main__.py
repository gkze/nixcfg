"""Bootstrap command for ``.buildkite/pipeline.yml``.

``upload`` admits the build, then uploads the generated graph. ``render``
prints that graph and does not talk to Buildkite.
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from lib.buildkite.admission import (
    apply_admission,
    cancel_build,
    fetch_branch_builds,
)
from lib.buildkite.concurrency import Admission
from lib.buildkite.emit import render_pipeline
from lib.buildkite.execute import CommandResult, subprocess_runner
from lib.buildkite.graph import Workflow, load_workflows
from lib.buildkite.triggers import (
    event_from_env,
    event_source,
    expr_context,
    select_workflows,
    workflow_inputs,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from lib.buildkite.github_expr import ExprContext

_ADMISSION_ATTEMPTS = 30
_ADMISSION_INTERVAL_SECONDS = 30.0


def main(
    argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None
) -> int:
    """Upload or print the pipeline selected by the build environment."""
    args = list(sys.argv[1:] if argv is None else argv)
    values = dict(os.environ if env is None else env)
    if args == ["render"]:
        sys.stdout.write(json.dumps(pipeline_document(values)) + "\n")
        return 0
    if args == ["upload"]:
        return upload(values)
    return 2


def pipeline_document(
    env: Mapping[str, str],
    *,
    root: Path | None = None,
    runner: Callable[..., CommandResult] | None = None,
) -> dict[str, object]:
    """Return the pipeline JSON for *env* without uploading it."""
    selected, context = _selection(env, root=root, runner=runner)
    return render_pipeline(selected, context=context)


def upload(
    env: Mapping[str, str],
    *,
    root: Path | None = None,
    runner: Callable[..., CommandResult] | None = None,
    opener: Callable[..., object] | None = None,
    sleep: Callable[[float], None] | None = None,
    attempts: int = _ADMISSION_ATTEMPTS,
    interval: float = _ADMISSION_INTERVAL_SECONDS,
) -> int:
    """Admit the build when a selected workflow has concurrency, then upload."""
    run = runner or subprocess_runner
    selected, context = _selection(env, root=root, runner=run)
    if any(workflow.concurrency is not None for workflow in selected):
        coordinates = _coordinates(env)
        if coordinates is None:
            return 4
        organization, pipeline, token, current = coordinates
        decision = _await_admission(
            selected,
            context,
            env,
            organization=organization,
            pipeline=pipeline,
            token=token,
            current_id=current,
            opener=opener,
            sleep=sleep or _sleep,
            attempts=attempts,
            interval=interval,
        )
        if decision.wait:
            return 5
    document = render_pipeline(selected, context=context)
    result = run(
        ["buildkite-agent", "pipeline", "upload"],
        env=env,
        cwd=Path(env.get("BUILDKITE_BUILD_CHECKOUT_PATH", Path.cwd())),
        timeout=None,
        stdin=json.dumps(document),
    )
    return result.returncode


def changed_paths(
    env: Mapping[str, str], runner: Callable[..., CommandResult]
) -> tuple[str, ...] | None:
    """Return changed files, or ``None`` when the diff is unknown.

    An unknown diff matches path filters. An empty tuple matches nothing.
    ``NIXCFG_CHANGED_PATHS`` is the explicit list, one path per line.
    """
    if "NIXCFG_CHANGED_PATHS" in env:
        return tuple(line for line in env["NIXCFG_CHANGED_PATHS"].splitlines() if line)
    if event_source(env) in {"schedule", "workflow_dispatch"}:
        return None
    base = env.get("BUILDKITE_PULL_REQUEST_BASE_BRANCH") or env.get(
        "NIXCFG_DEFAULT_BRANCH", "main"
    )
    result = runner(
        ["git", "diff", "--name-only", f"origin/{base}...HEAD"],
        env=env,
        cwd=Path(env.get("BUILDKITE_BUILD_CHECKOUT_PATH", Path.cwd())),
        timeout=60,
        stdin=None,
    )
    if result.returncode != 0:
        return None
    return tuple(line for line in result.stdout.splitlines() if line)


def _selection(
    env: Mapping[str, str],
    *,
    root: Path | None,
    runner: Callable[..., CommandResult] | None,
) -> tuple[tuple[Workflow, ...], ExprContext]:
    checkout = (
        root
        if root is not None
        else Path(env.get("BUILDKITE_BUILD_CHECKOUT_PATH", "."))
    )
    loaded = load_workflows(checkout)
    ordered = tuple(loaded[name] for name in sorted(loaded))
    run = runner or subprocess_runner
    event = event_from_env(env, changed_paths(env, run))
    selected = select_workflows(ordered, event)
    context = expr_context(event, inputs=workflow_inputs(event, env))
    return selected, context


def _await_admission(
    workflows: Sequence[Workflow],
    context: ExprContext,
    env: Mapping[str, str],
    *,
    organization: str,
    pipeline: str,
    token: str,
    current_id: str,
    opener: Callable[..., object] | None,
    sleep: Callable[[float], None],
    attempts: int,
    interval: float,
) -> Admission:
    decision = Admission((), wait=False)
    for _attempt in range(attempts):
        builds = fetch_branch_builds(
            organization=organization,
            pipeline=pipeline,
            branch=env.get("BUILDKITE_BRANCH", ""),
            token=token,
            opener=opener,
        )
        decision = apply_admission(workflows, context, builds, current_id=current_id)
        for build_id in decision.cancel_ids:
            cancel_build(
                organization=organization,
                pipeline=pipeline,
                build_id=build_id,
                token=token,
                opener=opener,
            )
        if not decision.wait:
            return decision
        sleep(interval)
    return decision


def _coordinates(env: Mapping[str, str]) -> tuple[str, str, str, str] | None:
    organization = env.get("BUILDKITE_ORGANIZATION_SLUG", "").strip()
    pipeline = env.get("BUILDKITE_PIPELINE_SLUG", "").strip()
    token = env.get("BUILDKITE_API_TOKEN", "").strip()
    current = env.get("BUILDKITE_BUILD_ID", "").strip()
    if not organization or not pipeline or not token or not current:
        return None
    return organization, pipeline, token, current


def _sleep(interval: float) -> None:
    time.sleep(interval)


if __name__ == "__main__":  # pragma: no cover -- delegate to the tested main
    sys.exit(main())
