"""Patch a copied Electron ``Info.plist`` with T3 Code desktop metadata."""

from __future__ import annotations

import argparse
import plistlib
from pathlib import Path
from typing import Any, cast

PlistDict = dict[str, Any]


def patch_info_plist(
    plist_path: Path,
    *,
    app_name: str,
    bundle_id: str,
    version: str,
    icon_file: str,
    url_scheme: str,
    category: str = "public.app-category.developer-tools",
) -> None:
    """Apply app-specific metadata while preserving Electron's existing keys."""
    with plist_path.open("rb") as handle:
        payload = plistlib.load(handle)
    if not isinstance(payload, dict):
        msg = f"Expected a plist dictionary in {plist_path}"
        raise TypeError(msg)

    info = cast("PlistDict", payload)
    info["CFBundleDisplayName"] = app_name
    info["CFBundleIdentifier"] = bundle_id
    info["CFBundleName"] = app_name
    info["CFBundleShortVersionString"] = version
    info["CFBundleVersion"] = version
    info["CFBundleIconFile"] = icon_file
    info["LSApplicationCategoryType"] = category
    info["NSHighResolutionCapable"] = True
    info["CFBundleURLTypes"] = [
        {
            "CFBundleTypeRole": "Editor",
            "CFBundleURLName": f"{bundle_id} {url_scheme}",
            "CFBundleURLSchemes": [url_scheme],
        }
    ]

    with plist_path.open("wb") as handle:
        plistlib.dump(info, handle)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "plist_path", type=Path, help="Path to the copied Info.plist file."
    )
    parser.add_argument(
        "--app-name", required=True, help="User-facing app display name."
    )
    parser.add_argument(
        "--bundle-id", required=True, help="Application bundle identifier."
    )
    parser.add_argument("--version", required=True, help="Bundle version.")
    parser.add_argument("--icon-file", required=True, help="Bundle icon file name.")
    parser.add_argument(
        "--url-scheme", required=True, help="Custom URL scheme exposed by the app."
    )
    parser.add_argument(
        "--category",
        default="public.app-category.developer-tools",
        help="Launch Services application category.",
    )
    return parser.parse_args()


def main() -> None:
    """Patch the target plist from CLI-provided desktop metadata."""
    args = _parse_args()
    patch_info_plist(
        args.plist_path,
        app_name=args.app_name,
        bundle_id=args.bundle_id,
        version=args.version,
        icon_file=args.icon_file,
        url_scheme=args.url_scheme,
        category=args.category,
    )


if __name__ == "__main__":
    main()
