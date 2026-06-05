"""Updater for goose-cli source hashes and crate2nix artifacts."""
# ruff: noqa: SLF001

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from lib.nix.models.sources import HashEntry, SourceHashes
from lib.update import crate2nix as _crate2nix
from lib.update.events import (
    EventStream,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_str,
    require_value,
)
from lib.update.nix import _build_fetch_from_github_expr, compute_fixed_output_hash
from lib.update.paths import get_repo_file
from lib.update.updaters.base import (
    Crate2NixArtifactsMixin,
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.github_release import GitHubReleaseUpdater

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry


def _patch_installed_crate2nix_target(name: str) -> None:
    """Keep worktree updaters compatible with older installed nixcfg CLIs."""
    if hasattr(_crate2nix, "_local_flake_installable"):
        return
    target = _crate2nix.TARGETS.get(name)
    if target is None or not target.patched_src_installable.startswith("path:.#"):
        return
    attr = target.patched_src_installable.removeprefix("path:.#")
    _crate2nix.TARGETS[name] = replace(
        target,
        patched_src_installable=f"git+file://{get_repo_file('.').resolve()}?dirty=1#{attr}",
    )


def _patch_installed_crate2nix_missing_hashes() -> None:
    """Let older installed nixcfg CLIs handle crate2nix targets without git hashes."""
    if hasattr(_crate2nix, "_read_generated_hash_text"):
        return

    def _refresh_target(
        target: _crate2nix.Crate2NixTarget,
    ) -> _crate2nix.RefreshResult:
        patched_src = _crate2nix._build_patched_src(target)
        normalize = _crate2nix._load_normalizer(target.normalizer_path)

        with tempfile.TemporaryDirectory(prefix=f"crate2nix-{target.name}-") as tmp_dir:
            tmp_root = Path(tmp_dir)
            cargo_home = _crate2nix._crate2nix_cargo_home()
            cargo_home.mkdir(parents=True, exist_ok=True)
            generated_cargo = tmp_root / "Cargo.nix"
            generated_hashes = tmp_root / "crate-hashes.json"

            _crate2nix._run_crate2nix_generate(
                [
                    "nix",
                    "run",
                    "--inputs-from",
                    ".",
                    "nixpkgs#crate2nix",
                    "--",
                    "generate",
                    "-f",
                    str(patched_src / target.cargo_manifest_relpath),
                    "-o",
                    str(generated_cargo),
                    "-h",
                    str(generated_hashes),
                    "--default-features",
                ],
                env={
                    "CARGO_HOME": str(cargo_home),
                    "CARGO_NET_GIT_FETCH_WITH_CLI": "true",
                },
                generated_outputs=(generated_cargo, generated_hashes),
            )

            cargo_text, _rewrites, _added_root_src = normalize(
                generated_cargo.read_text(encoding="utf-8")
            )
            cargo_text = _crate2nix._stabilize_generated_root_src_paths(
                cargo_text,
                patched_src=patched_src,
                generated_cargo=generated_cargo,
            )
            cargo_text = _crate2nix._stabilize_generated_command_comment(
                target, cargo_text
            )
            cargo_text = _crate2nix._normalize_trailing_newline(cargo_text)
            hash_input = (
                generated_hashes.read_text(encoding="utf-8")
                if generated_hashes.exists()
                else "{}\n"
            )
            hash_text = _crate2nix._normalize_json_text(hash_input)
            hash_text = _crate2nix._normalize_trailing_newline(hash_text)
            return _crate2nix.RefreshResult(
                cargo_nix=cargo_text,
                crate_hashes=hash_text,
            )

    _crate2nix._refresh_target = _refresh_target


_patch_installed_crate2nix_missing_hashes()
_patch_installed_crate2nix_target("goose-cli")


@register_updater
class GooseCliUpdater(Crate2NixArtifactsMixin, GitHubReleaseUpdater):
    """Resolve the latest Goose release and compute its source hash."""

    name = "goose-cli"
    GITHUB_OWNER = "block"
    GITHUB_REPO = "goose"

    @staticmethod
    def _src_expr(version: str) -> str:
        return _build_fetch_from_github_expr(
            "block",
            "goose",
            tag=f"v{version}",
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute the fixed-output source hash for Goose."""
        _ = (session, context)

        async for event in self.stream_materialized_artifacts():
            yield event

        src_hash_drain = ValueDrain[str]()
        async for event in drain_value_events(
            compute_fixed_output_hash(
                self.name,
                self._src_expr(info.version),
                config=self.config,
            ),
            src_hash_drain,
            parse=expect_str,
        ):
            yield event
        src_hash = require_value(src_hash_drain, "Missing srcHash output")

        hashes: SourceHashes = [HashEntry.create("srcHash", src_hash)]
        yield UpdateEvent.value(self.name, hashes)
