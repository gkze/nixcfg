"""Behavioral edge coverage for safe crate2nix Git hash seeding."""

import json
from pathlib import Path

import pytest

from lib.update import crate2nix


def _target() -> crate2nix.Crate2NixTarget:
    """Return the smallest target that owns a checked crate hash cache."""
    return crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo-crate2nix-src",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("test-system",),
    )


def test_checked_fetchgit_sources_accept_only_complete_literal_attribute_sets(
    tmp_path: Path,
) -> None:
    """Only a fully resolved fetchgit identity is safe to reuse as a seed."""
    cargo_nix = tmp_path / "Cargo.nix"
    cargo_nix.write_text(
        """{ pkgs, dynamicUrl }:
{
  complete = (pkgs.fetchgit) {
    url = "https://example.test/complete";
    rev = "abc123";
    sha256 = "hash-complete";
  };
  dynamic = pkgs.fetchgit {
    url = dynamicUrl;
    rev = "abc123";
    sha256 = "hash-dynamic";
  };
  incomplete = pkgs.fetchgit {
    url = "https://example.test/incomplete";
    rev = "abc123";
  };
  scalar = pkgs.fetchgit "not-an-attribute-set";
  unrelated = pkgs.fetchurl {
    url = "https://example.test/archive";
    sha256 = "hash-archive";
  };
}
""",
        encoding="utf-8",
    )

    assert crate2nix._checked_fetchgit_sources(cargo_nix) == {
        ("https://example.test/complete", "abc123", "hash-complete")
    }


def test_checked_fetchgit_sources_rejects_an_unparseable_artifact(
    tmp_path: Path,
) -> None:
    """A malformed checked artifact must fail closed instead of seeding hashes."""
    cargo_nix = tmp_path / "Cargo.nix"
    cargo_nix.write_text("{ pkgs }:", encoding="utf-8")

    with pytest.raises(
        RuntimeError, match="Could not parse checked crate2nix artifact"
    ):
        crate2nix._checked_fetchgit_sources(cargo_nix)


def test_locked_git_packages_ignores_non_git_and_malformed_entries(
    tmp_path: Path,
) -> None:
    """Only complete, revision-pinned Git package records can authorize a seed."""
    cargo_lock = tmp_path / "Cargo.lock"
    cargo_lock.write_text(
        """version = 3

package = [
  "not-a-package-table",
  { name = "registry", version = "1.0.0", source = "registry+https://index.crates.io/" },
  { name = "empty-revision", version = "2.0.0", source = "git+https://example.test/empty#" },
  { name = "complete", version = "3.0.0", source = "git+https://example.test/complete?rev=abc123#abc123" },
]
""",
        encoding="utf-8",
    )

    assert crate2nix._locked_git_packages(cargo_lock) == (
        crate2nix._LockedGitPackage(
            locator="git+https://example.test/complete?rev=abc123",
            name="complete",
            revision="abc123",
            version="3.0.0",
        ),
    )


def test_locked_git_packages_rejects_a_non_list_package_table(tmp_path: Path) -> None:
    """Cargo.lock package metadata has one required top-level collection shape."""
    cargo_lock = tmp_path / "Cargo.lock"
    cargo_lock.write_text('[package]\nname = "not-a-list"\n', encoding="utf-8")

    with pytest.raises(TypeError, match="Cargo lock package table is invalid"):
        crate2nix._locked_git_packages(cargo_lock)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        pytest.param("registry+demo#1.2.3", False, id="non-git-key"),
        pytest.param(
            "git+https://example.test/demo?rev=abc123#1.2.3",
            True,
            id="legacy-version-only-key",
        ),
        pytest.param(
            "git+https://example.test/demo?rev=abc123#2.0.0",
            False,
            id="legacy-version-mismatch",
        ),
    ],
)
def test_hash_key_matching_supports_only_valid_current_and_legacy_keys(
    key: str,
    *,
    expected: bool,
) -> None:
    """Legacy version-only keys remain supported without accepting unrelated keys."""
    package = crate2nix._LockedGitPackage(
        locator="git+https://example.test/demo?rev=abc123",
        name="demo",
        revision="abc123",
        version="1.2.3",
    )

    assert crate2nix._hash_key_matches_locked_package(key, package) is expected


@pytest.mark.parametrize(
    "invalid_hashes",
    [
        pytest.param([], id="non-object"),
        pytest.param({"git+https://example.test/demo#demo@1.0.0": 42}, id="non-string"),
    ],
)
def test_filtered_crate_hash_seed_rejects_invalid_checked_hashes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    invalid_hashes: object,
) -> None:
    """Malformed checked hash metadata must not become crate2nix input."""
    repo = tmp_path / "repo"
    package_dir = repo / "demo"
    package_dir.mkdir(parents=True)
    patched_src = tmp_path / "patched-src"
    patched_src.mkdir()
    (package_dir / "Cargo.nix").write_text("{}\n", encoding="utf-8")
    (package_dir / "crate-hashes.json").write_text(
        json.dumps(invalid_hashes),
        encoding="utf-8",
    )
    (patched_src / "Cargo.lock").write_text(
        "version = 3\npackage = []\n", encoding="utf-8"
    )
    monkeypatch.setattr(crate2nix, "REPO_ROOT", repo)

    with pytest.raises(RuntimeError, match="Invalid crate hash cache"):
        crate2nix._filtered_crate_hash_seed(_target(), patched_src)


def test_filtered_crate_hash_seed_requires_the_checked_source_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A matching package key alone cannot reuse a hash from another revision."""
    repo = tmp_path / "repo"
    package_dir = repo / "demo"
    package_dir.mkdir(parents=True)
    patched_src = tmp_path / "patched-src"
    patched_src.mkdir()
    key = "git+https://example.test/demo?branch=main#demo@1.0.0"
    (package_dir / "crate-hashes.json").write_text(
        json.dumps({key: "hash-checked"}),
        encoding="utf-8",
    )
    (package_dir / "Cargo.nix").write_text(
        """{ pkgs }:
{
  demo.src = pkgs.fetchgit {
    url = "https://example.test/demo";
    rev = "old-revision";
    sha256 = "hash-checked";
  };
}
""",
        encoding="utf-8",
    )
    (patched_src / "Cargo.lock").write_text(
        """version = 3

[[package]]
name = "demo"
version = "1.0.0"
source = "git+https://example.test/demo?branch=main#new-revision"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(crate2nix, "REPO_ROOT", repo)

    assert json.loads(crate2nix._filtered_crate_hash_seed(_target(), patched_src)) == {}
