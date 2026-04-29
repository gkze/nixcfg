"""Patch Goose's source tree into the shape expected by this overlay."""

from __future__ import annotations

import argparse
import re
import shutil
import tomllib
from pathlib import Path

_LOGO_REWRITES = {
    "../../../../documentation/static/img/logo_dark.png": "../../static/img/logo_dark.png",
    "../../../../documentation/static/img/logo_light.png": "../../static/img/logo_light.png",
}
_V8_GOOSE_SOURCE_HEADERS_TO_DROP = frozenset({
    "[workspace]",
    "[profile.dev]",
    "[dev-dependencies]",
    "[[example]]",
    "[[test]]",
    "[[bench]]",
})


def drop_top_level_sections(text: str, headers: frozenset[str]) -> str:
    """Drop TOML sections and child tables that cannot live inside ``vendor/``."""
    child_prefixes = tuple(
        header[:-1] + "."
        for header in headers
        if header.startswith("[")
        and header.endswith("]")
        and not header.startswith("[[")
    )

    def should_remove(header: str) -> bool:
        return header in headers or any(
            header.startswith(prefix) for prefix in child_prefixes
        )

    kept: list[str] = []
    removing = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            removing = should_remove(stripped)
            if removing:
                continue
        if not removing:
            kept.append(line)
    return "".join(kept)


def rewrite_goose_logo_paths(root: Path) -> bool:
    """Rewrite Goose CLI logo references and return whether anything changed."""
    goose_cli_src = root / "crates/goose-cli/src"
    rewrote_logo_paths = False
    if not goose_cli_src.exists():
        return False

    for path in goose_cli_src.rglob("*.rs"):
        text = path.read_text()
        updated = text
        for old, new in _LOGO_REWRITES.items():
            updated = updated.replace(old, new)
        if updated == text:
            continue
        path.write_text(updated)
        rewrote_logo_paths = True
    return rewrote_logo_paths


def copy_goose_logos(root: Path) -> None:
    """Copy rewritten logo assets into the vendored Goose CLI crate."""
    static_img_dir = root / "crates/goose-cli/static/img"
    static_img_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        root / "documentation/static/img/logo_dark.png",
        static_img_dir / "logo_dark.png",
    )
    shutil.copy2(
        root / "documentation/static/img/logo_light.png",
        static_img_dir / "logo_light.png",
    )


def rewrite_v8_dependency(root: Path) -> None:
    """Point Goose's vendored V8 crate at the locally patched rusty_v8 fork."""
    v8_cargo_toml = root / "vendor/v8/Cargo.toml"
    v8_cargo_text = v8_cargo_toml.read_text()
    v8_cargo_text, replacements = re.subn(
        r"^v8-goose\s*=\s*.*$",
        'v8-goose = { path = "../v8-goose-src" }',
        v8_cargo_text,
        count=1,
        flags=re.MULTILINE,
    )
    if replacements != 1:
        msg = "expected one v8-goose dependency line in vendor/v8/Cargo.toml"
        raise SystemExit(msg)
    v8_cargo_toml.write_text(v8_cargo_text)


def strip_v8_goose_workspace_sections(root: Path) -> str:
    """Remove standalone-workspace TOML sections from the vendored V8 fork."""
    v8_goose_cargo_toml = root / "vendor/v8-goose-src/Cargo.toml"
    v8_goose_cargo_text = drop_top_level_sections(
        v8_goose_cargo_toml.read_text(),
        _V8_GOOSE_SOURCE_HEADERS_TO_DROP,
    )
    v8_goose_cargo_toml.write_text(v8_goose_cargo_text)
    return v8_goose_cargo_text


def rewrite_v8_goose_lock_entry(root: Path, v8_version: str) -> None:
    """Rewrite the Cargo.lock entry for the locally vendored V8 fork."""
    lock_file = root / "Cargo.lock"
    sections = lock_file.read_text().split("[[package]]\n")
    updated = False
    patched = [sections[0]]
    for section in sections[1:]:
        lines = section.splitlines()
        patched_section = section
        if lines and lines[0] == 'name = "v8-goose"':
            next_lines = []
            for line in lines:
                if line.startswith("version = "):
                    next_lines.append(f'version = "{v8_version}"')
                elif line.startswith(("source = ", "checksum = ")):
                    continue
                else:
                    next_lines.append(line)
            patched_section = "\n".join(next_lines)
            updated = True
        patched.append("[[package]]\n" + patched_section)
    if not updated:
        msg = "expected v8-goose Cargo.lock entry not found"
        raise SystemExit(msg)
    lock_file.write_text("".join(patched))


def patch_source(root: Path) -> None:
    """Apply all Goose source-tree rewrites needed before crate2nix builds."""
    if rewrite_goose_logo_paths(root):
        copy_goose_logos(root)

    rewrite_v8_dependency(root)
    v8_goose_cargo_text = strip_v8_goose_workspace_sections(root)
    v8_manifest = tomllib.loads(v8_goose_cargo_text)
    v8_version = v8_manifest["package"]["version"]
    rewrite_v8_goose_lock_entry(root, v8_version)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Copied Goose source tree to patch.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Patch one copied Goose source tree from the command line."""
    args = _parse_args(argv)
    patch_source(args.root)


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    main()
