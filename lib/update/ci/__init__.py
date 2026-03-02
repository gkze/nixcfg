"""Unified Typer app for CI-oriented update tooling."""

from __future__ import annotations

import typer

from lib.cli import HELP_CONTEXT_SETTINGS
from lib.update.ci._cli import make_main
from lib.update.ci.build_shared_closure import app as build_shared_closure_app
from lib.update.ci.dedup_cargo_lock import app as dedup_cargo_lock_app
from lib.update.ci.flake_lock_diff import app as flake_lock_diff_app
from lib.update.ci.merge_sources import app as merge_sources_app
from lib.update.ci.profile_generations import app as profile_generations_app
from lib.update.ci.resolve_versions import app as resolve_versions_app
from lib.update.ci.sources_json_diff import app as sources_json_diff_app
from lib.update.ci.test_pipeline import app as test_pipeline_app
from lib.update.ci.warm_fod_cache import app as warm_fod_cache_app
from lib.update.ci.workflow_steps import app as workflow_steps_app

app = typer.Typer(
    name="ci",
    help="CI helper tools for update pipelines.",
    no_args_is_help=True,
    context_settings=HELP_CONTEXT_SETTINGS,
)

cache_app = typer.Typer(
    help="Cache-warming helpers.",
    no_args_is_help=True,
    context_settings=HELP_CONTEXT_SETTINGS,
)
diff_app = typer.Typer(
    help="Diff helpers.",
    no_args_is_help=True,
    context_settings=HELP_CONTEXT_SETTINGS,
)
pipeline_app = typer.Typer(
    help="CI pipeline helper tools.",
    no_args_is_help=True,
    context_settings=HELP_CONTEXT_SETTINGS,
)

app.add_typer(cache_app, name="cache")
app.add_typer(diff_app, name="diff")
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(workflow_steps_app, name="workflow")

cache_app.add_typer(build_shared_closure_app, name="closure")
cache_app.add_typer(warm_fod_cache_app, name="fod")
cache_app.add_typer(profile_generations_app, name="generations")

diff_app.add_typer(flake_lock_diff_app, name="flake")
diff_app.add_typer(sources_json_diff_app, name="sources")

pipeline_app.add_typer(dedup_cargo_lock_app, name="cargo-lock")
pipeline_app.add_typer(merge_sources_app, name="sources")
pipeline_app.add_typer(test_pipeline_app, name="test")
pipeline_app.add_typer(resolve_versions_app, name="versions")


main = make_main(app, prog_name="nixcfg ci")
