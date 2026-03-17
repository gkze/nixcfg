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

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from lib.update.events import (
    CapturedValue,
    EventStream,
    UpdateEvent,
    capture_stream_value,
    expect_str,
)
from lib.update.flake import nixpkgs_expression
from lib.update.nix import compute_fixed_output_hash
from lib.update.nix_expr import compact_nix_expr, identifier_attr_path
from lib.update.paths import REPO_ROOT
from lib.update.updaters.base import (
    FlakeInputUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
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

        npm_hash: str | None = None
        async for item in capture_stream_value(
            compute_fixed_output_hash(
                self.name,
                self._expr_for_npm_deps(),
                config=self.config,
            ),
            error="Missing npmDepsHash output",
        ):
            if isinstance(item, CapturedValue):
                npm_hash = expect_str(item.captured)
            else:
                yield item
        if npm_hash is None:
            msg = "Missing npmDepsHash output"
            raise RuntimeError(msg)

        cargo_hash: str | None = None
        async for item in capture_stream_value(
            compute_fixed_output_hash(
                self.name,
                self._expr_for_cargo_vendor(),
                config=self.config,
            ),
            error="Missing cargoHash output",
        ):
            if isinstance(item, CapturedValue):
                cargo_hash = expect_str(item.captured)
            else:
                yield item
        if cargo_hash is None:
            msg = "Missing cargoHash output"
            raise RuntimeError(msg)

        entries: list[HashEntry] = [
            HashEntry.create("npmDepsHash", npm_hash),
            HashEntry.create("cargoHash", cargo_hash),
        ]
        yield UpdateEvent.value(self.name, entries)

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build source entry including resolved version and commit."""
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
            input=self.input_name,
            commit=info.commit,
        )
