"""Shared source-file resolution helpers for updater classes."""

from __future__ import annotations

from typing import Protocol, cast


class _InspectLike(Protocol):
    def getsourcefile(self, obj: object) -> str | None: ...

    def getmodule(self, obj: object) -> object | None: ...


def resolve_sourcefile(cls: type[object], *, inspect_module: object) -> str | None:
    """Return the source file for ``cls`` using a caller-provided inspect module."""
    inspect_like = cast("_InspectLike", inspect_module)
    try:
        return inspect_like.getsourcefile(cls)
    except (OSError, TypeError):
        module = inspect_like.getmodule(cls)
        module_file = getattr(module, "__file__", None)
        return module_file if isinstance(module_file, str) else None


__all__ = ["resolve_sourcefile"]
