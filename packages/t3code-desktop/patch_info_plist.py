"""Patch a copied Electron ``Info.plist`` with T3 Code desktop metadata."""

import argparse
import plistlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Sequence

PlistDict = dict[str, Any]


def patch_info_plist(
    plist_path: Path,
    *,
    app_name: str,
    bundle_id: str,
    version: str,
    icon_file: str,
    url_schemes: Sequence[str],
    category: str = "public.app-category.developer-tools",
) -> None:
    """Apply app-specific metadata while preserving Electron's existing keys.

    ``url_schemes`` carries every scheme the app answers on. Upstream registers
    both ``t3code`` and ``t3code-dev``, and a missing scheme silently breaks the
    deep-link and OAuth handoff that the running app performs.
    """
    schemes = list(url_schemes)
    if not schemes:
        msg = "at least one URL scheme is required"
        raise ValueError(msg)
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
    # Keep other URL types Electron registered (its stock plist declares a
    # Viewer entry for http/https) and replace only the ones that claim our
    # schemes, so a rebuild cannot leave two owners for `t3code:`.
    existing_types = info.get("CFBundleURLTypes")
    entries = existing_types if isinstance(existing_types, list) else []
    claimed = set(schemes)
    preserved = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        if not claimed.intersection(entry.get("CFBundleURLSchemes") or [])
    ]
    info["CFBundleURLTypes"] = [
        {
            "CFBundleTypeRole": "Editor",
            "CFBundleURLName": f"{bundle_id} {schemes[0]}",
            "CFBundleURLSchemes": schemes,
        },
        *preserved,
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
        "--url-scheme",
        required=True,
        action="append",
        dest="url_schemes",
        help="Custom URL scheme exposed by the app; repeat for each scheme.",
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
        url_schemes=args.url_schemes,
        category=args.category,
    )


if __name__ == "__main__":
    main()
