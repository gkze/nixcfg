"""Compatibility helpers for updater modules loaded by older nixcfg CLIs."""

# ruff: noqa: SLF001

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from lib.update.paths import get_repo_file

if TYPE_CHECKING:
    from collections.abc import Callable, MutableMapping

    from lib.update.crate2nix import Crate2NixTarget, RefreshResult


class _RefreshResultFactory(Protocol):
    def __call__(self, *, cargo_nix: str, crate_hashes: str) -> RefreshResult: ...


class _Crate2NixModule(Protocol):
    TARGETS: MutableMapping[str, Crate2NixTarget]
    RefreshResult: _RefreshResultFactory
    _refresh_target: Callable[[Crate2NixTarget], RefreshResult]

    def _build_patched_src(self, target: Crate2NixTarget) -> Path: ...

    def _load_normalizer(
        self,
        path: Path,
    ) -> Callable[[str], tuple[str, int, bool]]: ...

    def _crate2nix_cargo_home(self) -> Path: ...

    def _run_crate2nix_generate(
        self,
        args: list[str],
        *,
        env: dict[str, str],
        generated_outputs: tuple[Path, Path],
    ) -> None: ...

    def _stabilize_generated_root_src_paths(
        self,
        cargo_text: str,
        *,
        patched_src: Path,
        generated_cargo: Path,
    ) -> str: ...

    def _stabilize_generated_command_comment(
        self,
        target: Crate2NixTarget,
        cargo_text: str,
    ) -> str: ...

    def _normalize_trailing_newline(self, text: str) -> str: ...

    def _normalize_json_text(self, text: str) -> str: ...


def patch_installed_crate2nix_target(crate2nix: object, name: str) -> bool:
    """Rewrite path installables when an older installed CLI loads this worktree."""
    crate2nix = cast("_Crate2NixModule", crate2nix)
    if hasattr(crate2nix, "_local_flake_installable"):
        return False
    target = crate2nix.TARGETS.get(name)
    if target is None or not target.patched_src_installable.startswith("path:.#"):
        return False
    attr = target.patched_src_installable.removeprefix("path:.#")
    crate2nix.TARGETS[name] = replace(
        target,
        patched_src_installable=f"git+file://{get_repo_file('.').resolve()}?dirty=1#{attr}",
    )
    return True


def patch_installed_crate2nix_missing_hashes(crate2nix: object) -> bool:
    """Let older installed CLIs handle crate2nix targets without git hashes."""
    crate2nix = cast("_Crate2NixModule", crate2nix)
    if hasattr(crate2nix, "_read_generated_hash_text"):
        return False

    def _refresh_target(target: Crate2NixTarget) -> RefreshResult:
        patched_src = crate2nix._build_patched_src(target)
        normalize = crate2nix._load_normalizer(target.normalizer_path)

        with tempfile.TemporaryDirectory(prefix=f"crate2nix-{target.name}-") as tmp_dir:
            tmp_root = Path(tmp_dir)
            cargo_home = crate2nix._crate2nix_cargo_home()
            cargo_home.mkdir(parents=True, exist_ok=True)
            generated_cargo = tmp_root / "Cargo.nix"
            generated_hashes = tmp_root / "crate-hashes.json"

            crate2nix._run_crate2nix_generate(
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
            cargo_text = crate2nix._stabilize_generated_root_src_paths(
                cargo_text,
                patched_src=patched_src,
                generated_cargo=generated_cargo,
            )
            cargo_text = crate2nix._stabilize_generated_command_comment(
                target, cargo_text
            )
            cargo_text = crate2nix._normalize_trailing_newline(cargo_text)
            hash_input = (
                generated_hashes.read_text(encoding="utf-8")
                if generated_hashes.exists()
                else "{}\n"
            )
            hash_text = crate2nix._normalize_json_text(hash_input)
            hash_text = crate2nix._normalize_trailing_newline(hash_text)
            return crate2nix.RefreshResult(
                cargo_nix=cargo_text,
                crate_hashes=hash_text,
            )

    crate2nix._refresh_target = _refresh_target
    return True


__all__ = [
    "patch_installed_crate2nix_missing_hashes",
    "patch_installed_crate2nix_target",
]
