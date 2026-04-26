"""Explicit updater registration helpers."""

from __future__ import annotations

import functools
import importlib
import inspect
from typing import TYPE_CHECKING, cast

from lib.update.updaters._sourcefile import resolve_sourcefile

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType
    from typing import Protocol

    import aiohttp

    from lib.nix.models.sources import SourceEntry
    from lib.update.events import EventStream
    from lib.update.updaters.base import Updater
    from lib.update.updaters.core import UpdateContext
    from lib.update.updaters.metadata import VersionInfo

    class _AutoMaterializedUpdater(Protocol):
        name: str

        def stream_materialized_artifacts(self) -> EventStream: ...


type UpdaterClass = type[Updater]

UPDATERS: dict[str, UpdaterClass] = {}
_AUTO_CRATE2NIX_WRAPPED_ATTR = "__auto_crate2nix_wrapped__"


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


def _crate2nix_module() -> ModuleType | None:
    try:
        return importlib.import_module("lib.update.crate2nix")
    except ImportError:
        return None


def _materialization_mixin_class() -> type[object]:
    module = importlib.import_module("lib.update.updaters.materialization")
    mixin = getattr(module, "MaterializesArtifactsMixin", None)
    if not isinstance(mixin, type):
        msg = "Could not resolve MaterializesArtifactsMixin"
        raise TypeError(msg)
    return mixin


def _has_crate2nix_target(name: object) -> bool:
    if not isinstance(name, str) or not name:
        return False
    module = _crate2nix_module()
    if module is None:
        return False
    targets = getattr(module, "TARGETS", {})
    return name in targets


def _auto_enable_crate2nix_materialization[T: Updater](cls: type[T]) -> type[T]:
    name = getattr(cls, "name", None)
    if not _has_crate2nix_target(name):
        return cls

    cls.materialize_when_current = True
    cls.shows_materialize_artifacts_phase = True

    if getattr(cls, _AUTO_CRATE2NIX_WRAPPED_ATTR, False):
        return cls

    if issubclass(cls, _materialization_mixin_class()):
        setattr(cls, _AUTO_CRATE2NIX_WRAPPED_ATTR, True)
        return cls

    original_fetch_hashes = cls.fetch_hashes

    async def _stream_materialized_artifacts(
        self: _AutoMaterializedUpdater,
    ) -> EventStream:
        module = _crate2nix_module()
        if module is None:
            if False:
                yield  # pragma: no cover
            return
        stream_updates = module.stream_crate2nix_artifact_updates
        async for event in stream_updates(self.name):
            yield event

    original_fetch_hashes_typed: Callable[..., EventStream] = original_fetch_hashes

    @functools.wraps(original_fetch_hashes)
    async def _fetch_hashes(
        self: _AutoMaterializedUpdater,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        stream_materialized_artifacts = self.stream_materialized_artifacts
        async for event in stream_materialized_artifacts():
            yield event
        async for event in original_fetch_hashes_typed(
            cast("Updater", self),
            info,
            session,
            context=context,
        ):
            yield event

    setattr(  # noqa: B010
        cls,
        "stream_materialized_artifacts",
        _stream_materialized_artifacts,
    )
    setattr(cls, "fetch_hashes", _fetch_hashes)  # noqa: B010
    setattr(cls, _AUTO_CRATE2NIX_WRAPPED_ATTR, True)
    return cls


def register_updater[T: Updater](cls: type[T]) -> type[T]:
    """Register a concrete updater class in :data:`UPDATERS`."""
    name = getattr(cls, "name", None)
    if name is None or inspect.isabstract(cls):
        return cls

    cls = _auto_enable_crate2nix_materialization(cls)

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
    "UpdaterClass",
    "is_test_updater_class",
    "register_updater",
    "updater_sourcefile",
]
