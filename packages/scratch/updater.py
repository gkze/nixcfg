"""Updater for scratch npm and cargo dependency hashes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.let import LetExpression
from nix_manipulator.expressions.operator import Operator
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

if TYPE_CHECKING:
    import aiohttp
    from nix_manipulator.expressions.expression import NixExpression

    from lib.update.events import EventStream

from lib.nix.models.sources import HashCollection, SourceEntry, SourceHashes
from lib.update.flake import nixpkgs_expression
from lib.update.nix_expr import compact_nix_expr, identifier_attr_path
from lib.update.paths import REPO_ROOT
from lib.update.updaters.base import (
    FixedOutputHashStep,
    FlakeInputUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
    stream_fixed_output_hashes,
)


@register_updater
class ScratchUpdater(FlakeInputUpdater):
    """Compute npm and cargo hashes for the scratch flake input."""

    name = "scratch"
    input_name = "scratch"
    required_tools = ("nix",)

    @staticmethod
    def _wrap_expr_with_flake_and_pkgs(body_expr: NixExpression) -> str:
        repo_url = f"git+file://{REPO_ROOT}?dirty=1"
        expression = LetExpression(
            local_variables=[
                Binding(
                    name="flake",
                    value=FunctionCall(
                        name=identifier_attr_path("builtins", "getFlake"),
                        argument=StringPrimitive(value=repo_url),
                    ),
                ),
                Binding(name="pkgs", value=nixpkgs_expression()),
            ],
            value=body_expr,
        )
        return compact_nix_expr(expression.rebuild())

    @staticmethod
    def _expr_for_npm_deps() -> str:
        fetch_npm_deps_expr = FunctionCall(
            name=identifier_attr_path("pkgs", "fetchNpmDeps"),
            argument=AttributeSet.from_dict(
                {
                    "name": "scratch-npm-deps",
                    "src": identifier_attr_path("flake", "inputs", "scratch"),
                    "hash": identifier_attr_path("pkgs", "lib", "fakeHash"),
                },
            ),
        )
        return ScratchUpdater._wrap_expr_with_flake_and_pkgs(fetch_npm_deps_expr)

    @staticmethod
    def _expr_for_cargo_vendor() -> str:
        cargo_vendor_expr = FunctionCall(
            name=identifier_attr_path("pkgs", "rustPlatform", "fetchCargoVendor"),
            argument=AttributeSet.from_dict(
                {
                    "src": BinaryExpression(
                        left=identifier_attr_path("flake", "inputs", "scratch"),
                        operator=Operator(name="+"),
                        right=StringPrimitive(value="/src-tauri"),
                    ),
                    "hash": identifier_attr_path("pkgs", "lib", "fakeHash"),
                },
            ),
        )
        return ScratchUpdater._wrap_expr_with_flake_and_pkgs(cargo_vendor_expr)

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute npmDepsHash and cargoHash from the package derivation."""
        _ = (info, session, context)

        async for event in stream_fixed_output_hashes(
            self.name,
            steps=(
                FixedOutputHashStep(
                    hash_type="npmDepsHash",
                    error="Missing npmDepsHash output",
                    expr=lambda _resolved: self._expr_for_npm_deps(),
                ),
                FixedOutputHashStep(
                    hash_type="cargoHash",
                    error="Missing cargoHash output",
                    expr=lambda _resolved: self._expr_for_cargo_vendor(),
                ),
            ),
            config=self.config,
        ):
            yield event

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build source entry including resolved version and commit."""
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
            input=self.input_name,
            commit=info.commit,
        )
