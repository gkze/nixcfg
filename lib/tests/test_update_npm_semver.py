"""Behavioral tests for standards-based npm semantic-version ranges."""

import pytest

from lib.update.npm_semver import (
    is_exact_semantic_version,
    release_version_triplet,
    require_exact_semantic_version,
    require_npm_version_matches_spec,
    same_semantic_version,
)


@pytest.mark.parametrize(
    ("version", "spec"),
    [
        ("41.10.3", "41.10.3"),
        ("41.10.3", "^41.0.0"),
        ("0.2.9", "^0.2.3"),
        ("0.0.3", "^0.0.3"),
        ("41.10.3", "~41.10.0"),
        ("24.3.0", ">=22 <25"),
        ("26.1.0", "^24.0.0 || >=26.0.0"),
        ("22.19.1", "22.x"),
        ("22.12.0", "20.10.0 - 22.12.0"),
        ("24.0.0-rc.2", ">=24.0.0-rc.1 <24.0.0"),
    ],
)
def test_supported_npm_specs_accept_contained_exact_versions(
    version: str,
    spec: str,
) -> None:
    """Apply npm's complete range grammar to exact resolved versions."""
    assert (
        require_npm_version_matches_spec(version, spec, context="Electron") == version
    )


@pytest.mark.parametrize(
    ("version", "spec"),
    [
        ("41.10.4", "41.10.3"),
        ("41.0.0", "^40.9.3"),
        ("0.3.0", "^0.2.3"),
        ("0.0.4", "^0.0.3"),
        ("41.11.0", "~41.10.0"),
        ("25.0.0", ">=22 <25"),
        ("25.0.0", "^24.0.0 || >=26.0.0"),
        ("23.0.0", "22.x"),
        ("22.12.1", "20.10.0 - 22.12.0"),
        ("24.1.0-beta.1", ">=24.0.0-rc.1 <25"),
    ],
)
def test_supported_npm_specs_reject_versions_outside_their_range(
    version: str,
    spec: str,
) -> None:
    """Reject exact lock resolutions beyond the declared upper bound."""
    with pytest.raises(RuntimeError, match="does not satisfy"):
        require_npm_version_matches_spec(version, spec, context="Electron")


@pytest.mark.parametrize("spec", ["latest", "workspace:*", "not-a-range"])
def test_invalid_npm_spec_forms_are_rejected(spec: str) -> None:
    """Reject package-manager protocols and tags that are not semver ranges."""
    with pytest.raises(RuntimeError, match="valid npm semantic-version range"):
        require_npm_version_matches_spec("41.10.3", spec, context="Electron")


@pytest.mark.parametrize("version", ["41.10", "not-a-version"])
def test_locked_npm_version_must_be_an_exact_semantic_version(version: str) -> None:
    """Require the lockfile to resolve a complete semantic version."""
    with pytest.raises(RuntimeError, match="exact semantic version"):
        require_npm_version_matches_spec(version, "^41.0.0", context="Electron")


def test_prerelease_requires_an_explicit_compatible_prerelease_comparator() -> None:
    """Preserve npm's opt-in semantics for prerelease versions."""
    with pytest.raises(RuntimeError, match="does not satisfy"):
        require_npm_version_matches_spec(
            "24.0.0-rc.1",
            ">=23 <25",
            context="Node",
        )


@pytest.mark.parametrize("version", ["1.0.0+build.1", "1.0.0-rc.1+b.2", "1.2.3"])
def test_exact_versions_keep_build_metadata(version: str) -> None:
    """SemVer build metadata is part of an exact version, not a loose spelling."""
    assert require_exact_semantic_version(version, context="Pin") == version


@pytest.mark.parametrize(
    "version",
    ["1.x", "1", "x", "next", "canary", "v1.2.3", " 1.2.3", "1.0.0-01", "01.2.3"],
)
def test_ranges_tags_and_loose_spellings_are_not_exact(version: str) -> None:
    """Reject ranges, dist-tags, and spellings nodesemver would normalize."""
    assert is_exact_semantic_version(version) is False
    with pytest.raises(RuntimeError, match="exact semantic version"):
        require_exact_semantic_version(version, context="Pin")


@pytest.mark.parametrize("version", ["1.2.3-rc.1", "1.2.3+build.1"])
def test_release_only_rejects_prerelease_and_build(version: str) -> None:
    """Release-only callers accept nothing beyond major.minor.patch."""
    assert require_exact_semantic_version(version, context="Pin") == version
    with pytest.raises(RuntimeError, match="without prerelease or build"):
        require_exact_semantic_version(version, context="Pin", release_only=True)
    with pytest.raises(RuntimeError, match="without prerelease or build"):
        release_version_triplet(version, context="Pin")


def test_release_version_triplet_returns_numeric_components() -> None:
    """Expose the parsed components for numeric comparisons."""
    assert release_version_triplet("0.15.12", context="Zig") == (0, 15, 12)


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("1.2.3", "1.2.3", True),
        ("1.2.3+a", "1.2.3", True),
        ("1.2.3-rc.1", "1.2.3", False),
        ("1.2.4", "1.2.3", False),
        ("v1.2.3", "1.2.3", False),
        ("1.2.3", "next", False),
    ],
)
def test_same_semantic_version_compares_precedence(
    left: str, right: str, expected: object
) -> None:
    """Compare parsed precedence; non-exact operands never compare equal."""
    assert same_semantic_version(left, right) is expected
