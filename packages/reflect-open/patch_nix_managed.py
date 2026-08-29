"""Apply Reflect's fail-closed Nix update and entitlement policy."""

import argparse
import json
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import BinaryIO


@dataclass(frozen=True, slots=True)
class SourcePatch:
    """One exact upstream source anchor and its Nix-owned replacement."""

    relative_path: str
    old: str
    new: str


_UPDATER_PUBKEY = (
    "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDREQ0RBRkJBN0ZDODdDMzkK"
    "UldRNWZNaC91cS9OVFhtTHdrSWVOV3p2cEdhc3RZbmtXM0g0bnpxUnZXVjlQMjZRNXFlWUdPNWMK"
)
_UPDATER_CONFIG_ANCHOR = (
    "    },\n"
    '    "updater": {\n'
    f'      "pubkey": "{_UPDATER_PUBKEY}",\n'
    '      "endpoints": [\n'
    '        "https://github.com/team-reflect/reflect-open/releases/download/'
    'updater-beta/latest.json"\n'
    "      ]\n"
    "    }\n"
)
_UPDATE_PROVIDER_DECLARATION = (
    "export function UpdateProvider({ children, autoCheck }: "
    "UpdateProviderProps): ReactElement {"
)
_ARCHIVE_PATH_COMPONENTS = 2
_EXPECTED_PACKAGE_MANAGER = "pnpm@11.18.0"

_PATCHES = (
    SourcePatch(
        "apps/desktop/src-tauri/tauri.conf.json",
        _UPDATER_CONFIG_ANCHOR,
        """    }
""",
    ),
    SourcePatch(
        "apps/desktop/src-tauri/tauri.macos.conf.json",
        """      "entitlements": "Entitlements.plist",
      "files": {
        "embedded.provisionprofile": "Reflect.provisionprofile"
      }
""",
        """      "entitlements": "Entitlements.dev.plist",
      "files": {
        "embedded.provisionprofile": null
      },
      "minimumSystemVersion": "14.0"
""",
    ),
    SourcePatch(
        "apps/desktop/src-tauri/capabilities/desktop.json",
        (
            '  "description": "Desktop-only capabilities: auto-update checks, '
            'the post-install relaunch, window-state restore, and deep-link events",\n'
        ),
        (
            '  "description": "Desktop-only capabilities: window-state restore '
            'and deep-link events",\n'
        ),
    ),
    SourcePatch(
        "apps/desktop/src-tauri/capabilities/desktop.json",
        (
            '  "permissions": ["updater:default", "process:default", '
            '"window-state:default", "deep-link:default"]\n'
        ),
        ('  "permissions": ["window-state:default", "deep-link:default"]\n'),
    ),
    SourcePatch(
        "apps/desktop/src-tauri/Cargo.toml",
        """tauri-plugin-updater = "2.10.1"
tauri-plugin-process = "2.3.1"
""",
        "",
    ),
    SourcePatch(
        "apps/desktop/src-tauri/src/lib.rs",
        """    let builder = builder
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .plugin(
""",
        """    let builder = builder.plugin(
""",
    ),
    SourcePatch(
        "apps/desktop/src/providers/update-provider.tsx",
        "import { isNativeShell } from '@/lib/platform'\n",
        "",
    ),
    SourcePatch(
        "apps/desktop/src/providers/update-provider.tsx",
        f"{_UPDATE_PROVIDER_DECLARATION}\n  const supported = isNativeShell()\n",
        (
            f"{_UPDATE_PROVIDER_DECLARATION}\n"
            "  // Nix owns application updates; packaged builds expose no mutable updater.\n"
            "  const supported = false\n"
        ),
    ),
)

_REQUIRED_PATHS = tuple(
    dict.fromkeys(("package.json", *(patch.relative_path for patch in _PATCHES)))
)


def _validate_package_manager(source: str) -> None:
    message = f"expected Reflect packageManager {_EXPECTED_PACKAGE_MANAGER}"
    try:
        package = json.loads(source)
    except json.JSONDecodeError as error:
        raise RuntimeError(message) from error
    actual = package.get("packageManager") if isinstance(package, dict) else None
    if actual != _EXPECTED_PACKAGE_MANAGER:
        raise RuntimeError(message)


def _replace_anchor(source: str, patch: SourcePatch) -> str:
    matches = source.count(patch.old)
    if matches != 1:
        msg = (
            f"expected one Reflect source anchor in {patch.relative_path}, "
            f"found {matches}"
        )
        raise RuntimeError(msg)
    start = source.index(patch.old)
    end = start + len(patch.old)
    return f"{source[:start]}{patch.new}{source[end:]}"


def patch_sources(sources: Mapping[str, str]) -> dict[str, str]:
    """Return a fully validated Nix-owned source view without mutating input."""
    patched = dict(sources)
    for relative_path in _REQUIRED_PATHS:
        if relative_path not in patched:
            msg = f"missing Reflect source file: {relative_path}"
            raise RuntimeError(msg)
    _validate_package_manager(patched["package.json"])
    for patch in _PATCHES:
        patched[patch.relative_path] = _replace_anchor(
            patched[patch.relative_path],
            patch,
        )
    return patched


def patch_tree(source_root: Path) -> None:
    """Apply the Nix ownership policy to one unpacked Reflect source tree."""
    sources = {
        relative_path: (source_root / relative_path).read_text(encoding="utf-8")
        for relative_path in _REQUIRED_PATHS
    }
    patched = patch_sources(sources)
    for relative_path, patched_source in patched.items():
        (source_root / relative_path).write_text(patched_source, encoding="utf-8")


def check_tar_stream(stream: BinaryIO) -> None:
    """Validate all anchors against a GitHub source tarball without extraction."""
    sources: dict[str, str] = {}
    with tarfile.open(fileobj=stream, mode="r|gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            parts = PurePosixPath(member.name).parts
            if len(parts) < _ARCHIVE_PATH_COMPONENTS:
                continue
            relative_path = PurePosixPath(*parts[1:]).as_posix()
            if relative_path not in _REQUIRED_PATHS:
                continue
            if relative_path in sources:
                msg = f"duplicate Reflect source file in archive: {relative_path}"
                raise RuntimeError(msg)
            source_file = archive.extractfile(member)
            if source_file is None:
                msg = f"could not read Reflect source file: {relative_path}"
                raise RuntimeError(msg)
            sources[relative_path] = source_file.read().decode("utf-8")
    patch_sources(sources)


def main(argv: list[str] | None = None) -> int:
    """Patch a source tree, or dry-check an exact tarball from standard input."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", nargs="?", type=Path)
    parser.add_argument("--check-tar-stdin", action="store_true")
    args = parser.parse_args(argv)
    if args.check_tar_stdin:
        if args.source_root is not None:
            parser.error("source_root cannot be used with --check-tar-stdin")
        check_tar_stream(sys.stdin.buffer)
        return 0
    if args.source_root is None:
        parser.error("source_root is required unless --check-tar-stdin is used")
    patch_tree(args.source_root)
    return 0


if __name__ == "__main__":  # pragma: no cover -- packaged CLI guard
    raise SystemExit(main())
