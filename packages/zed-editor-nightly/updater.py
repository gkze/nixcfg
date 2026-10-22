"""Updater for Zed nightly flake input metadata."""

import tomllib
from typing import TYPE_CHECKING, ClassVar

from lib.update.derivation_validation import DerivationValidation
from lib.update.net import fetch_url
from lib.update.updaters import (
    Crate2NixMetadataUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import FlakeInputMetadata

if TYPE_CHECKING:
    import aiohttp

    from lib.update.updaters.core import UpdateContext


@register_updater
class ZedEditorNightlyUpdater(Crate2NixMetadataUpdater):
    """Track the current Zed nightly app version and locked commit."""

    name = "zed-editor-nightly"
    input_name = "zed"
    # Root rust-overlay is rev-pinned after #1265; this stays a no-op until
    # that nightly rustc is in gkze.cachix.org and the flake.nix pin lifts.
    additional_input_names = ("rust-overlay",)
    _MANIFEST_PATH = "crates/zed/Cargo.toml"
    # A shared Darwin cache can pair this C API leaf with incompatible Rust
    # metadata for its tree-sitter consumers (rustc E0460). Recording the one
    # affected crate version here lets Nix rebuild that leaf and its dependents
    # coherently, and keeps the mitigation removable once the version retires.
    compatibility_pins: ClassVar[dict[str, str]] = {
        "wasmtimeCApiCoherenceVersion": "48.0.1"
    }
    compatibility_pin_rationale: ClassVar[str] = (
        "wasmtime-c-api-impl 48.0.1 resolves against a Darwin cache whose Rust "
        "metadata disagrees with the cached tree-sitter consumers, so the "
        "package gives that crate a version-bounded rebuild identity."
    )

    @classmethod
    def get_derivation_validations(cls) -> tuple[DerivationValidation, ...]:
        """Eval the crate graph, then realize the package on native runners.

        Darwin host closures embed ``zed-editor-nightly`` / ``rust_*``. The
        shared ``drvPath`` eval does not populate Cachix, so root shards
        rebuild that subtree and can load a stripped ``rust_assets`` rlib
        (Update 37719249543, rustc E0786). Packages owns that realize/push.
        """
        return (
            *super().get_derivation_validations(),
            DerivationValidation(
                installable=".#pkgs.{system}.{name}",
                systems=("aarch64-darwin", "x86_64-linux"),
                mode="build",
            ),
        )

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Resolve the current app version from the locked upstream manifest."""
        _ = context
        node = self._resolve_flake_node(VersionInfo(version="ignored"))
        locked = node.locked
        owner = locked.owner if locked is not None else None
        repo = locked.repo if locked is not None else None
        rev = locked.rev if locked is not None else None
        if not all(isinstance(value, str) and value for value in (owner, repo, rev)):
            msg = "zed flake input is missing owner/repo/rev metadata"
            raise RuntimeError(msg)

        manifest_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{rev}/{self._MANIFEST_PATH}"
        manifest_bytes = await fetch_url(
            session,
            manifest_url,
            request_timeout=self.config.default_timeout,
            config=self.config,
            user_agent=self.config.default_user_agent,
        )
        manifest = tomllib.loads(manifest_bytes.decode(errors="replace"))
        package = manifest.get("package")
        version = package.get("version") if isinstance(package, dict) else None
        if not isinstance(version, str) or not version:
            msg = "Zed manifest is missing package.version"
            raise RuntimeError(msg)

        return VersionInfo(
            version=version,
            metadata=FlakeInputMetadata(node=node, commit=rev),
        )
