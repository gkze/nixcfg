"""Updater for the internal T3 Code workspace dependency cache."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from lib.nix.models.sources import HashEntry, SourceEntry, SourceHashes
from lib.update.events import (
    EventStream,
    StatusInfo,
    StatusKind,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_str,
    require_value,
)
from lib.update.nix import (
    _build_repo_package_attr_expr,
    compute_expr_drv_fingerprint,
    compute_fixed_output_hash,
)
from lib.update.updaters import UpdateContext, VersionInfo, register_updater
from lib.update.updaters.flake_backed import FlakeInputHashUpdater

if TYPE_CHECKING:
    import aiohttp

_FINGERPRINT_STABILITY_EVALUATIONS = 3


@register_updater
class T3CodeWorkspaceUpdater(FlakeInputHashUpdater):
    """Compute the shared T3 Code workspace dependency cache hash."""

    DARWIN_PLATFORM = "aarch64-darwin"

    name = "t3code-workspace"
    input_name = "t3code"
    hash_type: Literal["nodeModulesHash"] = "nodeModulesHash"
    platform_specific = True
    materialize_when_current = True
    native_only = True
    supported_platforms = (DARWIN_PLATFORM,)

    @classmethod
    def _workspace_expr(cls) -> str:
        return _build_repo_package_attr_expr(
            "packages/t3code-workspace/default.nix",
            "",
            system=cls.DARWIN_PLATFORM,
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        entry = context.current if isinstance(context, UpdateContext) else context
        if entry is None or entry.version != info.version or entry.drv_hash is None:
            return False

        fingerprint = await compute_expr_drv_fingerprint(
            self.name,
            self._workspace_expr(),
            config=self.config,
        )
        if isinstance(context, UpdateContext):
            context.drv_fingerprint = fingerprint
        return entry.drv_hash == fingerprint

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute the fixed-output workspace dependency cache hash."""
        _ = (info, session, context)

        hash_drain = ValueDrain[str]()
        async for event in drain_value_events(
            compute_fixed_output_hash(
                self.name,
                self._workspace_expr(),
                config=self.config,
            ),
            hash_drain,
            parse=expect_str,
        ):
            yield event
        hash_value = require_value(hash_drain, "Missing nodeModulesHash output")

        hashes: SourceHashes = [
            HashEntry.create(self.hash_type, hash_value, platform=self.DARWIN_PLATFORM)
        ]
        yield UpdateEvent.value(self.name, hashes)

    async def _finalize_result(
        self,
        result: SourceEntry,
        *,
        info: VersionInfo | None = None,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        _ = (info, context)

        yield UpdateEvent.status(
            self.name,
            "Computing derivation fingerprint...",
            operation="compute_hash",
            status=StatusInfo(
                kind=StatusKind.COMPUTING_HASH,
                value="derivation fingerprint",
            ),
        )
        try:
            previous_drv_hash: str | None = None
            drv_hash: str | None = None
            for _evaluation in range(_FINGERPRINT_STABILITY_EVALUATIONS):
                current_drv_hash = await compute_expr_drv_fingerprint(
                    self.name,
                    self._workspace_expr(),
                    config=self.config,
                )
                if current_drv_hash == previous_drv_hash:
                    drv_hash = current_drv_hash
                    break
                previous_drv_hash = current_drv_hash
        except RuntimeError as exc:
            yield UpdateEvent.status(
                self.name,
                f"Warning: derivation fingerprint unavailable ({exc})",
                operation="compute_hash",
            )
        else:
            if drv_hash is None:
                msg = (
                    "Derivation fingerprint did not stabilize after "
                    f"{_FINGERPRINT_STABILITY_EVALUATIONS} evaluations"
                )
                raise RuntimeError(msg)
            result = result.model_copy(update={"drv_hash": drv_hash})

        yield UpdateEvent.value(self.name, result)
