"""Explicit updater registration helpers."""

from __future__ import annotations

import inspect
from typing import Any

from lib.update.updaters._sourcefile import resolve_sourcefile

UPDATERS: dict[str, type[Any]] = {}


def updater_sourcefile(cls: type[object]) -> str | None:
    """Return the source file for ``cls`` when available."""
    return resolve_sourcefile(cls, inspect_module=inspect)


def is_test_updater_class(cls: type[object]) -> bool:
    """Return whether ``cls`` comes from the updater test suite."""
    module_name = getattr(cls, "__module__", "")
    if module_name.startswith("lib.tests."):
        return True

    sourcefile = updater_sourcefile(cls)
    return sourcefile is not None and "/lib/tests/" in sourcefile


def register_updater[T](cls: type[T]) -> type[T]:
    """Register a concrete updater class in :data:`UPDATERS`."""
    name = getattr(cls, "name", None)
    if name is None or inspect.isabstract(cls):
        return cls

    existing = UPDATERS.get(name)
    if existing is not None and existing is not cls:
        if is_test_updater_class(existing) or is_test_updater_class(cls):
            UPDATERS[name] = cls
            return cls

        existing_path = updater_sourcefile(existing)
        new_path = updater_sourcefile(cls)
        if (
            existing_path is not None
            and new_path is not None
            and existing_path != new_path
        ):
            msg = (
                f"Duplicate updater registration for {name!r}: "
                f"{existing.__module__}.{existing.__qualname__} and "
                f"{cls.__module__}.{cls.__qualname__}"
            )
            raise RuntimeError(msg)

    UPDATERS[name] = cls
    return cls


__all__ = [
    "UPDATERS",
    "is_test_updater_class",
    "register_updater",
    "updater_sourcefile",
]
