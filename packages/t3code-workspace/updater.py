"""Updater for the internal T3 Code workspace Bun dependency cache."""

from __future__ import annotations

import asyncio
import os
import subprocess
from typing import TYPE_CHECKING, Literal

from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.let import LetExpression
from nix_manipulator.expressions.path import NixPath
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.commands.base import CommandResult, HashMismatchError
from lib.nix.models.sources import HashEntry, SourceEntry, SourceHashes
from lib.update.events import EventStream, UpdateEvent
from lib.update.nix import (
    _build_nix_expr,
    _tail_output_excerpt,
    compute_expr_drv_fingerprint,
)
from lib.update.nix_expr import compact_nix_expr, identifier_attr_path
from lib.update.paths import REPO_ROOT
from lib.update.updaters.base import UpdateContext, VersionInfo, register_updater
from lib.update.updaters.flake_backed import FlakeInputHashUpdater

if TYPE_CHECKING:
    import aiohttp
    from nix_manipulator.expressions.expression import NixExpression


def _workspace_build_args(expr: str) -> list[str]:
    return [
        "nix",
        "build",
        "-L",
        "--no-link",
        "--impure",
        "--expr",
        _build_nix_expr(expr),
    ]


async def _compute_workspace_hash(expr: str) -> str:
    args = _workspace_build_args(expr)

    result = await asyncio.to_thread(
        subprocess.run,
        args,
        check=False,
        capture_output=True,
        cwd=REPO_ROOT,
        encoding="utf-8",
        env={**os.environ, "FAKE_HASHES": "1"},
        text=True,
    )
    stdout = result.stdout if isinstance(result.stdout, str) else str(result.stdout)
    stderr = result.stderr if isinstance(result.stderr, str) else str(result.stderr)
    command_result = CommandResult(
        args=args,
        returncode=result.returncode,
        stdout=stdout,
        stderr=stderr,
    )
    if result.returncode == 0:
        msg = "Expected nix build to fail with hash mismatch, but it succeeded"
        raise RuntimeError(msg)

    output = stderr + stdout
    mismatch = HashMismatchError.from_output(output, command_result)
    if mismatch is None:
        msg = (
            "Could not find hash in nix output. Output tail:\n"
            f"{_tail_output_excerpt(output, max_lines=10)}"
        )
        raise RuntimeError(msg)
    if mismatch.is_sri:
        return mismatch.hash
    return await mismatch.to_sri()


@register_updater
class T3CodeWorkspaceUpdater(FlakeInputHashUpdater):
    """Compute the shared T3 Code workspace Bun cache hash."""

    DARWIN_PLATFORM = "aarch64-darwin"

    name = "t3code-workspace"
    input_name = "t3code"
    hash_type: Literal["nodeModulesHash"] = "nodeModulesHash"
    platform_specific = True
    native_only = True
    supported_platforms = (DARWIN_PLATFORM,)

    @classmethod
    def _workspace_expression(cls) -> NixExpression:
        import_nixpkgs = FunctionCall(
            name=Identifier(name="import"),
            argument=identifier_attr_path("flake", "inputs", "nixpkgs"),
        )
        call_package = FunctionCall(
            name=identifier_attr_path("pkgs", "callPackage"),
            argument=NixPath(path="./packages/t3code-workspace/default.nix"),
        )
        return LetExpression(
            local_variables=[
                Binding(
                    name="flake",
                    value=FunctionCall(
                        name=identifier_attr_path("builtins", "getFlake"),
                        argument=StringPrimitive(
                            value=f"git+file://{REPO_ROOT}?dirty=1"
                        ),
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
        """Compute the fixed-output workspace Bun cache hash."""
        _ = (info, session, context)

        hash_value = await _compute_workspace_hash(self._workspace_expr())

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
            status="computing_hash",
            detail="derivation fingerprint",
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
