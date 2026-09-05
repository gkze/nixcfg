"""Validate npm semantic-version ranges using the Node semver specification."""

import nodesemver


def _parse_npm_range(spec: str, *, context: str) -> nodesemver.Range:
    try:
        return nodesemver.Range(spec, loose=False)
    except (TypeError, ValueError) as error:
        msg = f"{context} range is not a valid npm semantic-version range: {spec!r}"
        raise RuntimeError(msg) from error


def _parse_semantic_version(version: str, *, context: str) -> nodesemver.SemVer:
    try:
        parsed = nodesemver.SemVer(
            version,
            loose=False,
            include_prerelease=False,
        )
    except (TypeError, ValueError) as error:
        msg = f"{context} version must be an exact semantic version, got {version!r}"
        raise RuntimeError(msg) from error
    if str(parsed) != version:
        msg = f"{context} version must be an exact semantic version, got {version!r}"
        raise RuntimeError(msg)
    return parsed


def require_valid_npm_range(spec: str, *, context: str) -> str:
    """Return an npm range after validating it against the standard grammar."""
    _parse_npm_range(spec, context=context)
    return spec


def require_exact_semantic_version(version: str, *, context: str) -> str:
    """Return an exact semantic version after standards-based validation."""
    _parse_semantic_version(version, context=context)
    return version


def npm_version_matches_spec(
    version: str,
    spec: str,
    *,
    context: str,
) -> bool:
    """Return whether one exact version satisfies a standards-based npm range."""
    required_range = _parse_npm_range(spec, context=context)
    resolved = _parse_semantic_version(version, context=context)
    return required_range.test(resolved, include_prerelease=False)


def require_npm_version_matches_spec(
    version: str,
    spec: str,
    *,
    context: str,
) -> str:
    """Return an exact version when it satisfies a standards-based npm range."""
    if not npm_version_matches_spec(version, spec, context=context):
        msg = f"{context} version {version!r} does not satisfy npm range {spec!r}"
        raise RuntimeError(msg)
    return version


__all__ = [
    "npm_version_matches_spec",
    "require_exact_semantic_version",
    "require_npm_version_matches_spec",
    "require_valid_npm_range",
]
