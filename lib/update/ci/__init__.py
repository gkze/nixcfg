"""Unified Typer app for CI-oriented update tooling."""

from __future__ import annotations

import typer

from lib.update.ci._cli import run_main
from lib.update.ci.build_shared_closure import app as build_shared_closure_app
from lib.update.ci.dedup_cargo_lock import app as dedup_cargo_lock_app
from lib.update.ci.flake_lock_diff import app as flake_lock_diff_app
from lib.update.ci.merge_sources import app as merge_sources_app
from lib.update.ci.resolve_versions import app as resolve_versions_app
from lib.update.ci.sources_json_diff import app as sources_json_diff_app
from lib.update.ci.test_pipeline import app as test_pipeline_app
from lib.update.ci.warm_fod_cache import app as warm_fod_cache_app
from lib.update.ci.workflow_steps import app as workflow_steps_app

app = typer.Typer(
    name="ci",
    help="CI helper tools for update pipelines.",
    no_args_is_help=True,
)

app.add_typer(build_shared_closure_app, name="build-shared-closure")
app.add_typer(dedup_cargo_lock_app, name="dedup-cargo-lock")
app.add_typer(flake_lock_diff_app, name="flake-lock-diff")
app.add_typer(merge_sources_app, name="merge-sources")
app.add_typer(resolve_versions_app, name="resolve-versions")
app.add_typer(sources_json_diff_app, name="sources-diff")
app.add_typer(sources_json_diff_app, name="sources-json-diff")
app.add_typer(test_pipeline_app, name="test-pipeline")
app.add_typer(warm_fod_cache_app, name="warm-fod-cache")

# Flatten workflow-step commands directly under `nixcfg ci`.
app.add_typer(workflow_steps_app)


def main(argv: list[str] | None = None) -> int:
    """Run the CI sub-application and return an exit code."""
    return run_main(app, argv=argv, prog_name="nixcfg ci")
