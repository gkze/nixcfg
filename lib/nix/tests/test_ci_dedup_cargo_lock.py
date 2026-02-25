"""Tests for Cargo.lock deduplication helper script."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self

import lib.update.ci.dedup_cargo_lock as dcl
from lib.nix.tests._assertions import check

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


_LOCK_VERSION = 4


def _pkg(name: str, version: str, source: str = "") -> dcl.Package:
    return dcl.Package(name=name, version=version, source=source)


def test_classify_source_and_package_helpers() -> None:
    """Run this test case."""
    check(dcl.classify_source("registry+https://x") == dcl.SourceType.REGISTRY)
    check(
        dcl.classify_source("git+https://github.com/GitoxideLabs/gitoxide?branch=main")
        == dcl.SourceType.GIT_GITOXIDE
    )
    check(
        dcl.classify_source("git+https://example.com/repo") == dcl.SourceType.GIT_OTHER
    )
    check(dcl.classify_source("path+file") == dcl.SourceType.OTHER)

    p = dcl.Package.from_dict({
        "name": "foo",
        "version": "1.0.0",
        "source": "registry+https://example",
        "checksum": "sum",
        "dependencies": ["a 1"],
        "build-dependencies": ["b 2"],
        "x": "y",
    })
    check(p.to_key() == ("foo", "1.0.0"))
    check(p.source_type() == dcl.SourceType.REGISTRY)
    check(p.dep_key_with_source() == "foo 1.0.0 (registry+https://example)")
    check(p.extra_fields == {"x": "y"})


def test_fetch_checksum_from_cache_embedded_and_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    object.__getattribute__(dcl, "_checksum_cache").clear()
    object.__getattribute__(dcl, "_checksum_cache")[("cached", "1")] = "cachedsum"
    check(dcl.fetch_checksum("cached", "1") == "cachedsum")

    check(
        dcl.fetch_checksum("gix", "0.77.0")
        == object.__getattribute__(dcl, "_GITOXIDE_CHECKSUMS")["gix@0.77.0"]
    )

    class _Response:
        status_code = 200

        def json(self) -> dict[str, dict[str, str]]:
            return {"version": {"checksum": "apisum"}}

    class _Client:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
            _ = (exc_type, exc, tb)
            return False

        def get(self, _path: str) -> _Response:
            return _Response()

    def _client_factory() -> _Client:
        return _Client()

    monkeypatch.setattr(dcl, "_crates_client", _client_factory)
    object.__getattribute__(dcl, "_checksum_cache").pop(("crate", "1.2.3"), None)
    check(dcl.fetch_checksum("crate", "1.2.3") == "apisum")
    check(
        object.__getattribute__(dcl, "_checksum_cache")[("crate", "1.2.3")] == "apisum"
    )


def test_fetch_checksum_api_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    object.__getattribute__(dcl, "_checksum_cache").clear()

    class _FailClient:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
            _ = (exc_type, exc, tb)
            return False

        def get(self, _path: str) -> object:
            msg = "down"
            raise dcl.httpx.ConnectError(msg)

    def _fail_client_factory() -> _FailClient:
        return _FailClient()

    monkeypatch.setattr(dcl, "_crates_client", _fail_client_factory)
    check(dcl.fetch_checksum("missing", "0") is None)


def test_parse_and_format_cargo_lock_roundtrip() -> None:
    """Run this test case."""
    content = (
        "version = 4\n\n"
        "[[package]]\n"
        'name = "foo"\n'
        'version = "1.0.0"\n'
        'source = "registry+https://example"\n'
        'checksum = "sum"\n'
        "dependencies = [\n"
        ' "bar 1.0.0",\n'
        "]\n"
    )

    packages, version = dcl.parse_cargo_lock(content)
    check(version == _LOCK_VERSION)
    check(len(packages) == 1)
    formatted = dcl.format_cargo_lock(packages, version)
    check('name = "foo"' in formatted)
    check("version = 4" in formatted)

    check(dcl.format_value("deps", []) == [])
    check(dcl.format_value("x", "y") == ['x = "y"'])


def test_convert_remove_and_dependency_rewrite(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    pkgs = [
        _pkg(
            "gix",
            "0.77.0",
            "git+https://github.com/GitoxideLabs/gitoxide?branch=main#abc",
        ),
        _pkg("gix", "0.77.0", dcl.REGISTRY_SOURCE),
        _pkg("other", "1.0.0", "git+https://example/repo#123"),
    ]

    monkeypatch.setattr(dcl, "fetch_checksum", lambda _n, _v: "sum")
    converted, converted_count = dcl.convert_gitoxide_to_registry(pkgs)
    check(converted_count == 1)
    check(converted[0].source == dcl.REGISTRY_SOURCE)
    check(converted[0].checksum == "sum")

    deduped, removed = dcl.remove_duplicates(converted)
    check(removed == 1)
    check(len([p for p in deduped if p.name == "gix"]) == 1)

    replacement = dcl.build_dep_replacement_map(pkgs, deduped)
    check(
        "gix 0.77.0 (git+https://github.com/GitoxideLabs/gitoxide?branch=main)"
        in replacement
    )

    dep_pkg = dcl.Package(
        name="app",
        version="1",
        dependencies=[
            "gix 0.77.0 (git+https://github.com/GitoxideLabs/gitoxide?branch=main)",
            "gix 0.77.0 (git+https://github.com/GitoxideLabs/gitoxide?branch=main)",
        ],
        build_dependencies=[
            "other 1.0.0",
        ],
    )
    fixed = dcl.fix_dependency_references([dep_pkg], replacement)
    check(fixed[0].dependencies == ["gix 0.77.0"])

    sorted_pkgs = dcl.sort_packages([_pkg("z", "1"), _pkg("a", "2")])
    check([p.name for p in sorted_pkgs] == ["a", "z"])
    check(dcl.strip_git_commit_hash("git+https://x#abc") == "git+https://x")


def test_main_parses_flags_and_logging_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    observed: dict[str, object] = {}

    def _run(**kwargs: object) -> int:
        observed.update(kwargs)
        return 7

    monkeypatch.setattr(dcl, "run", _run)
    expected_exit_code = 7
    check(dcl.main(["Cargo.lock", "--dry-run", "--verbose"]) == expected_exit_code)
    check(observed["dry_run"] is True)
    check(observed["verbose"] is True)
    check(getattr(observed["cargo_lock"], "name", None) == "Cargo.lock")

    calls: list[dict[str, object]] = []

    def _basic_config(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(dcl.logging, "basicConfig", _basic_config)

    dcl.configure_logging(quiet=False, verbose=True)
    check(calls[-1]["level"] == logging.DEBUG)

    dcl.configure_logging(quiet=True, verbose=False)
    check(calls[-1]["level"] == logging.WARNING)


def test_main_branches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run this test case."""
    missing = tmp_path / "missing.lock"
    check(dcl.main([str(missing)]) == dcl.ExitCode.ERROR)

    bad = tmp_path / "bad.lock"
    bad.write_text("not toml", encoding="utf-8")
    check(dcl.main([str(bad)]) == dcl.ExitCode.ERROR)

    good = tmp_path / "Cargo.lock"
    good.write_text(
        'version = 4\n\n[[package]]\nname = "foo"\nversion = "1"\n',
        encoding="utf-8",
    )

    pkg = _pkg("foo", "1", dcl.REGISTRY_SOURCE)
    monkeypatch.setattr(
        dcl, "convert_gitoxide_to_registry", lambda packages: (packages, 1)
    )
    monkeypatch.setattr(dcl, "remove_duplicates", lambda packages: (packages, 1))
    monkeypatch.setattr(dcl, "build_dep_replacement_map", lambda _original, _final: {})
    monkeypatch.setattr(
        dcl,
        "fix_dependency_references",
        lambda packages, _replacement_map: packages,
    )
    monkeypatch.setattr(dcl, "sort_packages", lambda _packages: [pkg])
    monkeypatch.setattr(
        dcl,
        "format_cargo_lock",
        lambda _packages, _version=4: "version = 4\n",
    )

    output = tmp_path / "out.lock"
    rc = dcl.main([str(good), "--output", str(output)])
    check(rc == dcl.ExitCode.SUCCESS)
    check(output.read_text(encoding="utf-8") == "version = 4\n")

    rc_dry = dcl.main([str(good), "--dry-run"])
    check(rc_dry == dcl.ExitCode.SUCCESS)

    monkeypatch.setattr(
        dcl, "convert_gitoxide_to_registry", lambda packages: (packages, 0)
    )
    monkeypatch.setattr(dcl, "remove_duplicates", lambda packages: (packages, 0))
    rc_none = dcl.main([str(good)])
    check(rc_none == dcl.ExitCode.NO_CHANGES)
