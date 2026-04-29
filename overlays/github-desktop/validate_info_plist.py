"""Validate GitHub Desktop's packaged macOS bundle metadata."""

from __future__ import annotations

import argparse
import plistlib
from pathlib import Path
from typing import Any, cast

EXPECTED_BUNDLE_IDENTIFIER = "com.github.GitHubClient"
EXPECTED_URL_SCHEMES = frozenset({
    "github-mac",
    "x-github-client",
    "x-github-desktop-auth",
})


class InfoPlistValidationError(ValueError):
    """Raised when GitHub Desktop's packaged ``Info.plist`` is not usable."""


def _load_info_plist(plist_path: Path) -> dict[str, Any]:
    with plist_path.open("rb") as handle:
        payload = plistlib.load(handle)
    if not isinstance(payload, dict):
        msg = f"expected Info.plist dictionary in {plist_path}"
        raise InfoPlistValidationError(msg)
    return cast("dict[str, Any]", payload)


def _declared_url_schemes(info: dict[str, Any]) -> set[str]:
    return {
        scheme
        for url_type in info.get("CFBundleURLTypes", [])
        if isinstance(url_type, dict)
        for scheme in url_type.get("CFBundleURLSchemes", [])
        if isinstance(scheme, str)
    }


def validate_info_plist(plist_path: Path) -> None:
    """Validate the app bundle identifier and URL schemes."""
    info = _load_info_plist(plist_path)
    actual_identifier = info.get("CFBundleIdentifier")
    if actual_identifier != EXPECTED_BUNDLE_IDENTIFIER:
        msg = (
            f"expected CFBundleIdentifier {EXPECTED_BUNDLE_IDENTIFIER!r}, "
            f"got {actual_identifier!r}"
        )
        raise InfoPlistValidationError(msg)

    missing = sorted(EXPECTED_URL_SCHEMES - _declared_url_schemes(info))
    if missing:
        msg = f"missing GitHub Desktop URL schemes: {missing!r}"
        raise InfoPlistValidationError(msg)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plist_path", type=Path, help="Packaged app Info.plist path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Validate the target plist from the command line."""
    args = _parse_args(argv)
    try:
        validate_info_plist(args.plist_path)
    except InfoPlistValidationError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    main()
