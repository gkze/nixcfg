"""Assert that a packaged T3 Code bundle carries its channel identity.

The app reads ``CFBundleShortVersionString`` at runtime to decide which update
channel it belongs to, and macOS routes ``t3code://`` deep links by the declared
URL schemes. A bundle that drifted on either point still launches, so the
package checks them explicitly instead of trusting the packager.
"""

import argparse
import plistlib
from pathlib import Path
from typing import Any, cast


class BundleIdentityError(RuntimeError):
    """Packaged bundle metadata does not match the intended release."""


def _load_plist(plist_path: Path) -> dict[str, Any]:
    with plist_path.open("rb") as handle:
        payload = plistlib.load(handle)
    if not isinstance(payload, dict):
        msg = f"Expected a plist dictionary in {plist_path}"
        raise BundleIdentityError(msg)
    return cast("dict[str, Any]", payload)


def _schemes(info: dict[str, Any]) -> list[str]:
    url_types = info.get("CFBundleURLTypes")
    if not isinstance(url_types, list):
        return []
    schemes: list[str] = []
    for url_type in url_types:
        if not isinstance(url_type, dict):
            continue
        declared = cast("dict[str, Any]", url_type).get("CFBundleURLSchemes")
        if isinstance(declared, list):
            schemes.extend(item for item in declared if isinstance(item, str))
    return schemes


def verify_bundle_identity(
    plist_path: Path,
    *,
    app_name: str,
    bundle_id: str,
    version: str,
    icon_file: str,
    url_schemes: list[str],
) -> None:
    """Raise unless the bundle metadata matches the requested identity."""
    info = _load_plist(plist_path)
    expected: dict[str, object] = {
        "CFBundleName": app_name,
        "CFBundleDisplayName": app_name,
        "CFBundleExecutable": app_name,
        "CFBundleIdentifier": bundle_id,
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "CFBundleIconFile": icon_file,
    }
    mismatched = {
        key: (info.get(key), want)
        for key, want in expected.items()
        if info.get(key) != want
    }
    if mismatched:
        details = ", ".join(
            f"{key}={actual!r} (expected {want!r})"
            for key, (actual, want) in sorted(mismatched.items())
        )
        msg = f"packaged bundle identity drifted: {details}"
        raise BundleIdentityError(msg)

    declared = _schemes(info)
    missing = [scheme for scheme in url_schemes if scheme not in declared]
    if missing:
        msg = f"bundle does not declare URL schemes {missing} (declared {declared})"
        raise BundleIdentityError(msg)


def main() -> None:
    """Verify the given ``Info.plist`` against the expected identity."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plist", type=Path, help="Path to the packaged Info.plist.")
    parser.add_argument("--app-name", required=True, help="Expected app name.")
    parser.add_argument(
        "--bundle-id", required=True, help="Expected bundle identifier."
    )
    parser.add_argument("--version", required=True, help="Expected bundle version.")
    parser.add_argument("--icon-file", required=True, help="Expected icon file name.")
    parser.add_argument(
        "--url-scheme",
        required=True,
        action="append",
        dest="url_schemes",
        help="URL scheme the bundle must declare; repeat for each scheme.",
    )
    args = parser.parse_args()
    try:
        verify_bundle_identity(
            args.plist,
            app_name=args.app_name,
            bundle_id=args.bundle_id,
            version=args.version,
            icon_file=args.icon_file,
            url_schemes=args.url_schemes,
        )
    except BundleIdentityError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
