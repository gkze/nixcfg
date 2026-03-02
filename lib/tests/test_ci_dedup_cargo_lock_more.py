"""Additional tests for dedup_cargo_lock branches."""

from __future__ import annotations

from typing import TYPE_CHECKING

import lib.update.ci.dedup_cargo_lock as dcl
from lib.tests._assertions import check

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _pkg(name: str, version: str, source: str = "") -> dcl.Package:
    return dcl.Package(name=name, version=version, source=source)


def test_crates_client_and_fetch_bad_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Create crates client and handle 4xx responses."""
    client = dcl._crates_client()
    check(str(client.base_url).startswith("https://crates.io"))
    client.close()

    class _Resp:
        status_code = 404

        def json(self) -> dict[str, object]:
            return {}

    class _Client:
        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def get(self, _path: str) -> _Resp:
            return _Resp()

    monkeypatch.setattr(dcl, "_crates_client", lambda: _Client())
    check(dcl.fetch_checksum("missing", "0") is None)

    class _RespNoChecksum:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {"version": {}}

    class _ClientNoChecksum:
        def __enter__(self) -> _ClientNoChecksum:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def get(self, _path: str) -> _RespNoChecksum:
            return _RespNoChecksum()

    monkeypatch.setattr(dcl, "_crates_client", lambda: _ClientNoChecksum())
    check(dcl.fetch_checksum("no-sum", "1") is None)


def test_parse_cargo_lock_handles_string_version_and_non_dict_entries() -> None:
    """Parse string versions and ignore invalid package nodes."""
    content = 'version = "5"\n\n[[package]]\nname = "foo"\nversion = "1"\n'
    pkgs, version = dcl.parse_cargo_lock(content)
    check(version == 5)
    check(len(pkgs) == 1)

    bad_content = 'version = "not-a-number"\n'
    pkgs2, version2 = dcl.parse_cargo_lock(bad_content)
    check(version2 == 4)
    check(pkgs2 == [])


def test_parse_cargo_lock_skips_non_dict_package_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ignore package array entries that are not mapping-like payloads."""

    def _parse(_content: str) -> dict[str, object]:
        return {"version": 4, "package": ["bad", {"name": "ok", "version": "1"}]}

    monkeypatch.setattr(dcl.tomlkit, "parse", _parse)
    packages, version = dcl.parse_cargo_lock("ignored")
    check(version == 4)
    check(len(packages) == 1)
    check(packages[0].name == "ok")


def test_format_value_non_empty_list_and_extra_field_serialization() -> None:
    """Format list values and include truthy extra fields."""
    lines = dcl.format_value("deps", ["b", "a"])
    check(lines[0] == "deps = [")
    check(lines[-1] == "]")
    check(' "a",' in lines)

    pkg = dcl.Package(
        name="x", version="1", extra_fields={"metadata": ["z", "y"], "empty": []}
    )
    rendered = dcl.format_cargo_lock([pkg], 4)
    check("metadata = [" in rendered)
    check("empty" not in rendered)


def test_convert_gitoxide_no_checksum_and_remove_duplicates_no_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep git package when checksum unavailable and dedup non-registry pair."""
    git_pkg = _pkg(
        "gix",
        "0.77.0",
        "git+https://github.com/GitoxideLabs/gitoxide?branch=main#abc",
    )
    monkeypatch.setattr(dcl, "fetch_checksum", lambda *_a: None)
    converted, count = dcl.convert_gitoxide_to_registry([git_pkg])
    check(count == 0)
    check(converted[0].source == git_pkg.source)

    deduped, removed = dcl.remove_duplicates([
        _pkg("foo", "1", "git+https://x#1"),
        _pkg("foo", "1", "git+https://y#2"),
    ])
    check(len(deduped) == 1)
    check(removed == 1)


def test_dependency_map_git_other_registry_and_noop_remap() -> None:
    """Map git-other duplicates to plain dependency references."""
    original = [
        _pkg("file-id", "1", "git+https://example.com/repo#abc"),
        _pkg("file-id", "1", dcl.REGISTRY_SOURCE),
    ]
    final = [_pkg("file-id", "1", dcl.REGISTRY_SOURCE)]
    mapping = dcl.build_dep_replacement_map(original, final)
    check("file-id 1 (git+https://example.com/repo)" in mapping)
    check(
        "file-id 1 (registry+https://github.com/rust-lang/crates.io-index)" in mapping
    )

    pkg = dcl.Package(
        name="app", version="1", dependencies=["x 1"], build_dependencies=["y 1"]
    )
    same = dcl.fix_dependency_references([pkg], {})
    check(same == [pkg])
    check(dcl.strip_git_commit_hash("git+https://x") == "git+https://x")


def test_dependency_map_branches_when_final_package_is_not_registry() -> None:
    """Exercise non-registry final-package branches in replacement mapping."""
    original = [
        _pkg(
            "gix", "1", "git+https://github.com/GitoxideLabs/gitoxide?branch=main#abc"
        ),
        _pkg("other", "1", "git+https://example.com/repo#abc"),
        _pkg("reg", "1", dcl.REGISTRY_SOURCE),
        _pkg("reg", "1", "git+https://example.com/reg#abc"),
    ]
    final = [
        _pkg("gix", "1", "git+https://example.com/non-registry#def"),
        _pkg("other", "1", "git+https://example.com/non-registry#def"),
        _pkg("reg", "1", "git+https://example.com/non-registry#def"),
    ]
    mapping = dcl.build_dep_replacement_map(original, final)
    check(
        "gix 1 (git+https://github.com/GitoxideLabs/gitoxide?branch=main)"
        not in mapping
    )
    check("other 1 (git+https://example.com/repo)" not in mapping)
    check(f"reg 1 ({dcl.REGISTRY_SOURCE})" in mapping)


def test_dependency_map_branches_when_key_missing_in_final() -> None:
    """Cover git branches where key is absent from final package set."""
    original = [
        _pkg(
            "gix", "1", "git+https://github.com/GitoxideLabs/gitoxide?branch=main#abc"
        ),
        _pkg("other", "1", "git+https://example.com/repo#abc"),
        _pkg("other", "1", "git+https://example.com/repo-alt#def"),
    ]
    mapping = dcl.build_dep_replacement_map(original, [])
    check(mapping == {})


def test_run_parse_error_and_no_changes_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Return parse-error and no-changes exit codes."""
    lock = tmp_path / "Cargo.lock"
    lock.write_text("version = 4\n", encoding="utf-8")

    def _raise_parse(_content: str) -> tuple[list[dcl.Package], int]:
        raise dcl.tomlkit_exceptions.ParseError(1, 1, "bad")

    monkeypatch.setattr(dcl, "parse_cargo_lock", _raise_parse)
    check(dcl.run(cargo_lock=lock) == dcl.ExitCode.ERROR)

    monkeypatch.setattr(dcl, "parse_cargo_lock", lambda _c: ([], 4))
    monkeypatch.setattr(dcl, "convert_gitoxide_to_registry", lambda pkgs: (pkgs, 0))
    monkeypatch.setattr(dcl, "remove_duplicates", lambda pkgs: (pkgs, 0))
    monkeypatch.setattr(dcl, "build_dep_replacement_map", lambda _o, _f: {})
    monkeypatch.setattr(dcl, "fix_dependency_references", lambda pkgs, _m: pkgs)
    monkeypatch.setattr(dcl, "sort_packages", lambda pkgs: pkgs)
    check(dcl.run(cargo_lock=lock) == dcl.ExitCode.NO_CHANGES)
