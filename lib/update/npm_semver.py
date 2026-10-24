"""Validate npm semantic-version ranges using the Node semver specification."""

import nodesemver


def _parse_npm_range(spec: str, *, context: str) -> nodesemver.Range:
    try:
        return nodesemver.Range(spec, loose=False)
    except (TypeError, ValueError) as error:
        msg = f"{context} range is not a valid npm semantic-version range: {spec!r}"
        raise RuntimeError(msg) from error


def _parse_semantic_version(
    version: str,
    *,
    context: str,
    release_only: bool = False,
) -> nodesemver.SemVer:
    """Parse one exact SemVer 2.0.0 version, build metadata included.

    The round-trip check rejects loose spellings nodesemver normalizes away
    (``v1.2.3``, surrounding whitespace). ``release_only`` also rejects
    prerelease and build suffixes.
    """
    msg = f"{context} version must be an exact semantic version, got {version!r}"
    try:
        parsed = nodesemver.SemVer(
            version,
            loose=False,
            include_prerelease=False,
        )
    except (TypeError, ValueError) as error:
        raise RuntimeError(msg) from error
    build = f"+{'.'.join(parsed.build)}" if parsed.build else ""
    if f"{parsed}{build}" != version:
        raise RuntimeError(msg)
    if release_only and (parsed.prerelease or parsed.build):
        msg = (
            f"{context} version must be an exact semantic version without "
            f"prerelease or build metadata, got {version!r}"
        )
        raise RuntimeError(msg)
    return parsed


def require_valid_npm_range(spec: str, *, context: str) -> str:
    """Return an npm range after validating it against the standard grammar."""
    _parse_npm_range(spec, context=context)
    return spec


def require_exact_semantic_version(
    version: str,
    *,
    context: str,
    release_only: bool = False,
) -> str:
    """Return an exact semantic version after standards-based validation."""
    _parse_semantic_version(version, context=context, release_only=release_only)
    return version


def is_exact_semantic_version(version: str) -> bool:
    """Return whether ``version`` is one exact SemVer version, not a range or tag."""
    try:
        _parse_semantic_version(version, context="npm")
    except RuntimeError:
        return False
    return True


def same_semantic_version(left: str, right: str) -> bool:
    """Return whether two exact versions have equal SemVer precedence."""
    if not (is_exact_semantic_version(left) and is_exact_semantic_version(right)):
        return False
    return (
        _parse_semantic_version(left, context="npm").compare(
            _parse_semantic_version(right, context="npm")
        )
        == 0
    )


def release_version_triplet(version: str, *, context: str) -> tuple[int, int, int]:
    """Return ``(major, minor, patch)`` of an exact release version."""
    parsed = _parse_semantic_version(version, context=context, release_only=True)
    return parsed.major, parsed.minor, parsed.patch


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
    "is_exact_semantic_version",
    "npm_version_matches_spec",
    "release_version_triplet",
    "require_exact_semantic_version",
    "require_npm_version_matches_spec",
    "require_valid_npm_range",
    "same_semantic_version",
]
