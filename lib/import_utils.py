"""Helpers for loading Python modules directly from repository paths."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType


def load_module_from_path(path: Path, module_name: str) -> ModuleType:
    """Load and return a Python module from an arbitrary filesystem path."""
    loader = importlib.machinery.SourceFileLoader(module_name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None or spec.loader is None:
        msg = f"Could not load module from {path}"
        raise RuntimeError(msg)

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if sys.modules.get(module_name) is module:
            del sys.modules[module_name]
        raise
    return module


__all__ = ["load_module_from_path"]
