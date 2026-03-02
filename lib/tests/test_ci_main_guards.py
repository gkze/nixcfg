"""Cover __main__ guards for CI helper modules."""

from __future__ import annotations

import runpy
import sys
from importlib.util import find_spec

import pytest


@pytest.mark.parametrize(
    "module_name",
    [
        "lib.update.ci.build_shared_closure",
        "lib.update.ci.dedup_cargo_lock",
        "lib.update.ci.flake_lock_diff",
        "lib.update.ci.merge_sources",
        "lib.update.ci.resolve_versions",
        "lib.update.ci.sources_json_diff",
        "lib.update.ci.workflow_steps",
        "lib.update.ci.warm_fod_cache",
        "lib.update.ci.profile_generations",
        "lib.update.ci.test_pipeline",
    ],
)
def test_module_main_guard_executes(
    module_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Execute modules as scripts to cover their ``if __name__ == '__main__'`` lines."""
    monkeypatch.setattr(sys, "argv", [module_name, "--help"])
    spec = find_spec(module_name)
    if spec is None or spec.origin is None:
        msg = f"unable to locate module: {module_name}"
        raise AssertionError(msg)
    with pytest.raises(SystemExit):
        runpy.run_path(spec.origin, run_name="__main__")
