"""Updater for Goose desktop's pinned pnpm dependency cache."""

from __future__ import annotations

import json
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.path import NixPath
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from lib.update import nix as update_nix
from lib.update import sources as update_sources
from lib.update.nix import _build_package_path_attr_expr, _select_attrs
from lib.update.paths import REPO_ROOT, sources_file_for
from lib.update.updaters import (
    HashEntryUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.core import _coerce_context

if TYPE_CHECKING:
    from collections.abc import Iterator

    import aiohttp

    from lib.update.events import EventStream
    from lib.update.updaters import UpdateContext


@register_updater
class GooseDesktopUpdater(HashEntryUpdater):
    """Hash Goose desktop dependencies from the overlay-managed Goose source."""

    DARWIN_PLATFORM: ClassVar[str] = "aarch64-darwin"
    _GOOSE_CARGO_NIX_PATH = Path("overlays/goose-cli/Cargo.nix")

    name = "goose-desktop"
    companion_of = "goose-cli"
    supported_platforms = (DARWIN_PLATFORM,)

    def _dependency_hash_override_env(
        self,
        version: str,
        goose_cli_source: SourceEntry,
    ) -> dict[str, str]:
        payload = {
            "goose-cli": goose_cli_source.to_dict(),
            self.name: {
                "version": version,
                "hashes": [
                    {
                        "hashType": "nodeModulesHash",
                        "hash": self.config.fake_hash,
                        "platform": self.DARWIN_PLATFORM,
                    }
                ],
            },
        }
        return {"UPDATE_SOURCE_OVERRIDES_JSON": json.dumps(payload)}

    @staticmethod
    def _goose_cli_source(
        context: UpdateContext | SourceEntry | None,
    ) -> SourceEntry:
        resolved_context = _coerce_context(context)
        effective_source = resolved_context.effective_sources.get("goose-cli")
        if effective_source is not None:
            return effective_source

        source_file = sources_file_for("goose-cli")
        if source_file is None:
            msg = "goose-cli sources.json was not found"
            raise RuntimeError(msg)
        return update_sources.load_source_entry(source_file)

    async def fetch_latest(
        self,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> VersionInfo:
        """Use the effective Goose CLI source version for this update wave."""
        _ = session
        entry = self._goose_cli_source(context)
        if not entry.version:
            msg = "goose-cli sources.json is missing a pinned version"
            raise RuntimeError(msg)
        return VersionInfo(version=entry.version)

    @classmethod
    @contextmanager
    def _cargo_nix_path(
        cls,
        context: UpdateContext | SourceEntry | None,
    ) -> Iterator[Path]:
        resolved_context = _coerce_context(context)
        generated = resolved_context.generated_artifacts.get(cls._GOOSE_CARGO_NIX_PATH)
        if generated is None:
            yield REPO_ROOT / cls._GOOSE_CARGO_NIX_PATH
            return

        with tempfile.TemporaryDirectory(prefix="goose-desktop-cargo-nix-") as tmpdir:
            path = Path(tmpdir) / "Cargo.nix"
            path.write_text(generated, encoding="utf-8")
            yield path

    @classmethod
    def _goose_cli_override_expr(cls, cargo_nix_path: Path) -> Select:
        flake_lib = _select_attrs(Identifier(name="flake"), "lib")
        flake_sources = _select_attrs(flake_lib, "sources")
        fragment = FunctionCall(
            name=FunctionCall(
                name=Identifier(name="import"),
                argument=NixPath(
                    path=str(REPO_ROOT / "overlays/goose-cli/default.nix")
                ),
            ),
            argument=AttributeSet(
                values=[
                    Binding(name="prev", value=Identifier(name="pkgs")),
                    Binding(name="slib", value=flake_lib),
                    Binding(name="sources", value=flake_sources),
                    Binding(
                        name="selfSource",
                        value=_select_attrs(flake_sources, "goose-cli"),
                    ),
                    Binding(
                        name="cargoNixFn",
                        value=FunctionCall(
                            name=Identifier(name="import"),
                            argument=NixPath(path=str(cargo_nix_path)),
                        ),
                    ),
                ]
            ),
        )
        return Select(
            expression=Parenthesis(value=fragment),
            attribute="goose-cli",
        )

    @classmethod
    def _pnpm_deps_expr(cls, cargo_nix_path: Path) -> str:
        return _build_package_path_attr_expr(
            cls.name,
            ".pnpmDeps",
            system=cls.DARWIN_PLATFORM,
            package_args={
                "goose-cli": cls._goose_cli_override_expr(cargo_nix_path),
            },
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute the desktop pnpm dependency cache hash directly."""
        _ = session
        goose_cli_source = self._goose_cli_source(context)
        if goose_cli_source.version != info.version:
            msg = (
                f"goose-cli source version {goose_cli_source.version!r} does not "
                f"match goose-desktop version {info.version!r}"
            )
            raise RuntimeError(msg)

        with self._cargo_nix_path(context) as cargo_nix_path:
            hash_stream = update_nix.compute_fixed_output_hash(
                self.name,
                self._pnpm_deps_expr(cargo_nix_path),
                env=self._dependency_hash_override_env(
                    info.version,
                    goose_cli_source,
                ),
                config=self.config,
            )
            async for event in self._emit_single_hash_entry(
                hash_stream,
                error="Missing nodeModulesHash output",
                hash_type="nodeModulesHash",
            ):
                yield event

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist the Goose source version with a platform-specific dependency hash."""
        hash_collection = HashCollection.from_value(hashes)
        if hash_collection.entries is None:
            msg = "goose-desktop updater expected structured hash entries"
            raise RuntimeError(msg)
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value([
                HashEntry.create(
                    "nodeModulesHash",
                    hash_entry.hash,
                    platform=self.DARWIN_PLATFORM,
                )
                for hash_entry in hash_collection.entries
            ]),
        )
