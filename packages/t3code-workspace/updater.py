"""Updater for the internal T3 Code workspace dependency cache."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.let import LetExpression
from nix_manipulator.expressions.operator import Operator
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.path import NixPath
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

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
    compute_expr_drv_fingerprint,
    compute_fixed_output_hash,
)
from lib.update.nix_expr import compact_nix_expr, identifier_attr_path
from lib.update.paths import REPO_ROOT, local_flake_url
from lib.update.updaters import UpdateContext, VersionInfo, register_updater
from lib.update.updaters.flake_backed import FlakeInputHashUpdater

if TYPE_CHECKING:
    import aiohttp
    from nix_manipulator.expressions.expression import NixExpression


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
    def _workspace_expression(cls) -> NixExpression:
        import_nixpkgs = FunctionCall(
            name=Identifier(name="import"),
            argument=identifier_attr_path("flake", "inputs", "nixpkgs"),
        )
        call_package = FunctionCall(
            name=FunctionCall(
                name=identifier_attr_path("pkgs", "lib", "callPackageWith"),
                argument=Identifier(name="applied"),
            ),
            argument=NixPath(path="./packages/t3code-workspace/default.nix"),
        )
        overlay_fn = identifier_attr_path("flake", "overlays", "default")
        overlay_applied = FunctionCall(
            name=FunctionCall(name=overlay_fn, argument=Identifier(name="self")),
            argument=Identifier(name="pkgs"),
        )
        return LetExpression(
            local_variables=[
                Binding(
                    name="flake",
                    value=FunctionCall(
                        name=identifier_attr_path("builtins", "getFlake"),
                        argument=StringPrimitive(value=local_flake_url(REPO_ROOT)),
                    ),
                ),
                Binding(
                    name="system", value=StringPrimitive(value=cls.DARWIN_PLATFORM)
                ),
                Binding(
                    name="pkgs",
                    value=FunctionCall(
                        name=import_nixpkgs,
                        argument=AttributeSet.from_dict({
                            "system": Identifier(name="system"),
                            "config": AttributeSet.from_dict({
                                "allowUnfree": Primitive(value=True),
                                "allowInsecurePredicate": FunctionDefinition(
                                    argument_set=Identifier(name="_"),
                                    output=Primitive(value=True),
                                ),
                            }),
                        }),
                    ),
                ),
                Binding(
                    name="applied",
                    value=FunctionCall(
                        name=identifier_attr_path("pkgs", "lib", "fix"),
                        argument=Parenthesis(
                            value=FunctionDefinition(
                                argument_set=Identifier(name="self"),
                                output=BinaryExpression(
                                    left=Identifier(name="pkgs"),
                                    operator=Operator(name="//"),
                                    right=overlay_applied,
                                ),
                            ),
                        ),
                    ),
                ),
            ],
            value=FunctionCall(
                name=call_package,
                argument=AttributeSet.from_dict({
                    "inputs": identifier_attr_path("flake", "inputs"),
                    "outputs": Identifier(name="flake"),
                }),
            ),
        )

    @classmethod
    def _workspace_expr(cls) -> str:
        return compact_nix_expr(cls._workspace_expression().rebuild())

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
                env={"FAKE_HASHES": "1"},
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
        _ = info

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
            drv_hash = (
                context.drv_fingerprint if isinstance(context, UpdateContext) else None
            )
            if drv_hash is None:
                drv_hash = await compute_expr_drv_fingerprint(
                    self.name,
                    self._workspace_expr(),
                    config=self.config,
                )
            result = result.model_copy(update={"drv_hash": drv_hash})
        except RuntimeError as exc:
            yield UpdateEvent.status(
                self.name,
                f"Warning: derivation fingerprint unavailable ({exc})",
                operation="compute_hash",
            )

        yield UpdateEvent.value(self.name, result)
