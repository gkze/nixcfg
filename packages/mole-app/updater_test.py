"""Updater ownership contracts for Mole's complete source closure."""

from types import ModuleType
from typing import TYPE_CHECKING

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition

from lib.nix.models.sources import HashEntry, SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._updater_helpers import collect_events, load_repo_module, run_async
from lib.update.events import UpdateEvent, UpdateEventKind
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo

if TYPE_CHECKING:
    import pytest

_PACKAGE_DIR = REPO_ROOT / "packages/mole-app"
_VERSION = "1.39.0"
_SOURCE_URL = f"https://github.com/tw93/Mole/archive/refs/tags/V{_VERSION}.tar.gz"
_SOURCE_HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
_BINARY_HASHES = {
    "aarch64-darwin": "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
    "x86_64-darwin": "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
}


def _load_updater_module() -> ModuleType:
    return load_repo_module("packages/mole-app/updater.py", "mole_app_updater_test")


def test_mole_updater_owns_source_and_binary_downloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One updater result must contain every fixed-output input used by Mole."""
    module = _load_updater_module()
    updater = module.MoleAppUpdater()
    binary_urls = updater._platform_urls(VersionInfo(_VERSION))
    expected_urls = {"source": _SOURCE_URL, **binary_urls}

    async def compute_url_hashes(
        name: str,
        urls: object,
        *,
        config: object,
    ):
        assert name == "mole-app"
        assert config is updater.config
        assert list(urls) == list(expected_urls.values())
        yield UpdateEvent.status(name, "hashing Mole closure")
        yield UpdateEvent.value(
            name,
            {
                _SOURCE_URL: _SOURCE_HASH,
                **{
                    binary_urls[platform]: hash_value
                    for platform, hash_value in _BINARY_HASHES.items()
                },
            },
        )

    monkeypatch.setattr("lib.update.process.compute_url_hashes", compute_url_hashes)

    events = run_async(
        collect_events(updater.fetch_hashes(VersionInfo(_VERSION), object()))
    )

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.VALUE,
    ]
    hashes = [
        HashEntry.create("srcHash", _SOURCE_HASH, url=_SOURCE_URL),
        *[
            HashEntry.create("sha256", _BINARY_HASHES[platform], platform=platform)
            for platform in sorted(_BINARY_HASHES)
        ],
    ]
    assert events[-1].payload == hashes
    assert updater.build_result(VersionInfo(_VERSION), hashes) == SourceEntry(
        version=_VERSION,
        urls=binary_urls,
        hashes=hashes,
    )


def test_mole_derivation_consumes_only_updater_owned_downloads() -> None:
    """The derivation must acquire both archives through sources.json metadata."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    scope = package.output.scope
    source_tarball_metadata = expect_binding(scope, "sourceTarballMetadata").value
    source_tarball = expect_instance(
        expect_binding(scope, "sourceTarball").value,
        FunctionCall,
    )
    binary_archive = expect_instance(
        expect_binding(scope, "binaryArchive").value,
        FunctionCall,
    )

    assert_nix_ast_equal(
        source_tarball_metadata,
        'outputs.lib.sourceHashEntry pname "srcHash"',
    )
    assert_nix_ast_equal(
        source_tarball,
        """fetchurl {
          inherit (sourceTarballMetadata) hash url;
        }""",
    )
    assert_nix_ast_equal(
        binary_archive,
        """fetchurl {
          url = selfSource.urls.${system};
          hash = outputs.lib.sourceHashForPlatform pname "sha256" system;
        }""",
    )
