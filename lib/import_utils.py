"""Helpers for loading Python modules directly from repository paths."""

import importlib.machinery
import importlib.util
import sys
from typing import TYPE_CHECKING, cast

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
    missing = object()
    previous = sys.modules.get(module_name, missing)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if sys.modules.get(module_name) is module:
            if previous is missing:
                del sys.modules[module_name]
            else:
                sys.modules[module_name] = cast("ModuleType", previous)
        raise
    return module


__all__ = ["load_module_from_path"]
