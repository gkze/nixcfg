"""Explicit updater registration helpers."""

import inspect
from typing import TYPE_CHECKING

from lib.update.updaters._sourcefile import resolve_sourcefile

if TYPE_CHECKING:
    from lib.update.updaters.core import Updater


type UpdaterClass = type[Updater]

UPDATERS: dict[str, UpdaterClass] = {}


def updater_sourcefile(cls: type[object]) -> str | None:
    """Return the source file for ``cls`` when available."""
    return resolve_sourcefile(cls, inspect_module=inspect)


def register_updater[T: Updater](cls: type[T]) -> type[T]:
    """Register a concrete updater class in :data:`UPDATERS`."""
    name = getattr(cls, "name", None)
    if name is None or inspect.isabstract(cls):
        return cls

    existing = UPDATERS.get(name)
    if existing is not None and existing is not cls:
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
    "UpdaterClass",
    "register_updater",
    "updater_sourcefile",
]
