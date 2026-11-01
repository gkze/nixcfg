"""Buildkite pipeline that follows the GitHub Actions workflows in this repo.

The workflow YAML remains the only job-graph authority. Generation, admission,
and the step runner read it; they do not keep a second copy of the job list.
"""

from lib.buildkite.emit import render_pipeline
from lib.buildkite.graph import load_workflows

__all__ = ["load_workflows", "render_pipeline"]
