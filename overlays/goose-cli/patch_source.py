"""Patch Goose's source tree into the shape expected by this overlay."""

import argparse
import re
import shutil
import tomllib
from pathlib import Path

from lib.codemods.errors import CodemodError
from lib.codemods.text import regex_replace_file_exactly, replace_exactly

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


def _rewrite_goose_cli_crate_logo_paths(crate_root: Path) -> bool:
    """Rewrite logo references inside one filtered Goose CLI crate."""
    goose_cli_src = crate_root / "src"
    rewrote_logo_paths = False
    if not goose_cli_src.exists():
        return False

    for path in goose_cli_src.rglob("*.rs"):
        text = path.read_text()
        updated = text
        for old, new in _LOGO_REWRITES.items():
            match_count = updated.count(old)
            if match_count:
                updated = replace_exactly(
                    updated,
                    old,
                    new,
                    expected_count=match_count,
                    context=f"Goose logo path in {path}",
                )
        if updated == text:
            continue
        path.write_text(updated)
        rewrote_logo_paths = True
    return rewrote_logo_paths


def rewrite_goose_logo_paths(root: Path) -> bool:
    """Rewrite Goose CLI logo references and return whether anything changed."""
    return _rewrite_goose_cli_crate_logo_paths(root / "crates/goose-cli")


def _copy_goose_cli_logos(crate_root: Path, logo_root: Path) -> None:
    """Copy the two runtime logo assets into one filtered Goose CLI crate."""
    static_img_dir = crate_root / "static/img"
    static_img_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(logo_root / "logo_dark.png", static_img_dir / "logo_dark.png")
    shutil.copy2(logo_root / "logo_light.png", static_img_dir / "logo_light.png")


def copy_goose_logos(root: Path) -> None:
    """Copy rewritten logo assets into the vendored Goose CLI crate."""
    _copy_goose_cli_logos(
        root / "crates/goose-cli",
        root / "documentation/static/img",
    )


def patch_goose_cli_crate(crate_root: Path, logo_root: Path) -> None:
    """Prepare the filtered Goose CLI crate used by the production build."""
    if _rewrite_goose_cli_crate_logo_paths(crate_root):
        _copy_goose_cli_logos(crate_root, logo_root)


def rewrite_v8_dependency(root: Path) -> None:
    """Point Goose's vendored V8 crate at the locally patched rusty_v8 fork."""
    v8_cargo_toml = root / "vendor/v8/Cargo.toml"
    try:
        regex_replace_file_exactly(
            v8_cargo_toml,
            r"^v8-goose\s*=\s*.*$",
            'v8-goose = { path = "../v8-goose-src" }',
            expected_count=1,
            flags=re.MULTILINE,
            context="Goose vendor/v8 v8-goose dependency",
        )
    except CodemodError as exc:
        msg = "expected one v8-goose dependency line in vendor/v8/Cargo.toml"
        raise SystemExit(msg) from exc


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


def _workspace_inherited_versions(root: Path) -> dict[str, str]:
    """Return local workspace packages whose version comes from the root."""
    root_manifest = tomllib.loads((root / "Cargo.toml").read_text())
    workspace = root_manifest.get("workspace")
    if not isinstance(workspace, dict):
        msg = "expected [workspace] in Goose Cargo.toml"
        raise SystemExit(msg)
    workspace_package = workspace.get("package")
    if not isinstance(workspace_package, dict):
        msg = "expected [workspace.package] in Goose Cargo.toml"
        raise SystemExit(msg)
    workspace_version = workspace_package.get("version")
    if not isinstance(workspace_version, str):
        msg = "expected workspace.package.version in Goose Cargo.toml"
        raise SystemExit(msg)
    members = workspace.get("members")
    if not isinstance(members, list) or not all(
        isinstance(member, str) for member in members
    ):
        msg = "expected workspace.members in Goose Cargo.toml"
        raise SystemExit(msg)
    exclude = workspace.get("exclude", [])
    if not isinstance(exclude, list) or not all(
        isinstance(excluded, str) for excluded in exclude
    ):
        msg = "expected workspace.exclude in Goose Cargo.toml"
        raise SystemExit(msg)

    member_manifests = {
        member_path / "Cargo.toml"
        for member_pattern in members
        for member_path in root.glob(member_pattern)
        if (member_path / "Cargo.toml").is_file()
    }
    excluded_manifests = {
        excluded_path / "Cargo.toml"
        for excluded_pattern in exclude
        for excluded_path in root.glob(excluded_pattern)
    }

    inherited_versions: dict[str, str] = {}
    for manifest_path in sorted(member_manifests - excluded_manifests):
        manifest = tomllib.loads(manifest_path.read_text())
        package = manifest.get("package")
        if not isinstance(package, dict):
            continue
        version = package.get("version")
        if not (isinstance(version, dict) and version.get("workspace") is True):
            continue
        name = package.get("name")
        if not isinstance(name, str):
            msg = f"expected package.name in {manifest_path}"
            raise SystemExit(msg)
        if name in inherited_versions:
            msg = f"duplicate inherited workspace package name: {name}"
            raise SystemExit(msg)
        inherited_versions[name] = workspace_version
    return inherited_versions


def rewrite_workspace_lock_versions(root: Path) -> None:
    """Reconcile local inherited package versions before locked metadata runs."""
    expected_versions = _workspace_inherited_versions(root)
    if not expected_versions:
        return

    lock_file = root / "Cargo.lock"
    sections = lock_file.read_text().split("[[package]]\n")
    rewritten = [sections[0]]
    matched: set[str] = set()
    for section in sections[1:]:
        package = tomllib.loads("[[package]]\n" + section)["package"][0]
        name = package.get("name")
        if name not in expected_versions or "source" in package:
            rewritten.append("[[package]]\n" + section)
            continue
        if name in matched:
            msg = f"duplicate local Cargo.lock entry for workspace package {name}"
            raise SystemExit(msg)
        matched.add(name)
        version_lines = re.findall(r'^version\s*=\s*"[^"]+"$', section, re.MULTILINE)
        if len(version_lines) != 1:
            msg = f"expected one version in Cargo.lock entry for {name}"
            raise SystemExit(msg)
        updated = replace_exactly(
            section,
            version_lines[0],
            f'version = "{expected_versions[name]}"',
            context=f"local Cargo.lock version for {name}",
        )
        rewritten.append("[[package]]\n" + updated)

    missing = sorted(expected_versions.keys() - matched)
    if missing:
        msg = "missing local Cargo.lock entries for workspace packages: " + ", ".join(
            missing
        )
        raise SystemExit(msg)
    lock_file.write_text("".join(rewritten))


def patch_source(root: Path) -> None:
    """Apply all Goose source-tree rewrites needed before crate2nix builds."""
    if rewrite_goose_logo_paths(root):
        copy_goose_logos(root)

    rewrite_v8_dependency(root)
    v8_goose_cargo_text = strip_v8_goose_workspace_sections(root)
    v8_manifest = tomllib.loads(v8_goose_cargo_text)
    v8_version = v8_manifest["package"]["version"]
    rewrite_v8_goose_lock_entry(root, v8_version)
    rewrite_workspace_lock_versions(root)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--goose-cli-crate",
        action="store_true",
        help="Patch one filtered goose-cli crate instead of the full workspace.",
    )
    parser.add_argument(
        "--logo-source-dir",
        type=Path,
        help="Directory containing logo_dark.png and logo_light.png.",
    )
    parser.add_argument("root", type=Path, help="Copied Goose source tree to patch.")
    args = parser.parse_args(argv)
    if args.goose_cli_crate and args.logo_source_dir is None:
        parser.error("--goose-cli-crate requires --logo-source-dir")
    if not args.goose_cli_crate and args.logo_source_dir is not None:
        parser.error("--logo-source-dir requires --goose-cli-crate")
    return args


def main(argv: list[str] | None = None) -> None:
    """Patch one copied Goose source tree from the command line."""
    args = _parse_args(argv)
    if args.goose_cli_crate:
        patch_goose_cli_crate(args.root, args.logo_source_dir)
    else:
        patch_source(args.root)


if __name__ == "__main__":  # pragma: no cover -- CLI entrypoint
    main()
