"""Shared helpers for repo-managed Zen script tests."""

from __future__ import annotations

import getpass
from functools import cache
from pathlib import Path
from types import ModuleType

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT


@cache
def resolve_zen_script_path(script_name: str) -> Path:
    """Resolve one repo-managed Zen script under ``home/*/bin``."""
    preferred = REPO_ROOT / f"home/{getpass.getuser()}/bin/{script_name}"
    if preferred.is_file():
        return preferred

    candidates = sorted((REPO_ROOT / "home").glob(f"*/bin/{script_name}"))
    if len(candidates) == 1:
        return candidates[0]

    if candidates:
        candidate_paths = ", ".join(
            str(path.relative_to(REPO_ROOT)) for path in candidates
        )
        msg = (
            f"Unable to resolve {script_name} for user {getpass.getuser()!r}; "
            f"candidates: {candidate_paths}"
        )
        raise RuntimeError(msg)

    msg = f"Unable to locate {script_name} under home/*/bin/{script_name}"
    raise RuntimeError(msg)


def load_zen_script_module(script_name: str, module_name: str) -> ModuleType:
    """Load one repo-managed Zen script as a Python module."""
    return load_module_from_path(resolve_zen_script_path(script_name), module_name)
