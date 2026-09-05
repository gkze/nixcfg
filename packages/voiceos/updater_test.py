"""Behavioral and package-shape tests for VoiceOS desktop."""

from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    binding_map,
    expect_binding,
    parse_nix_expr,
)
from lib.tests._source_metadata import (
    assert_https_url,
    assert_platform_source_entry,
    assert_release_version,
    assert_url_contains_version,
)
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo
from lib.update.updaters.metadata import AssetURLsMetadata

if TYPE_CHECKING:
    import pytest

_VERSION = "0.1.24"
_STALE_VERSION = "0.1.23"
_ARTIFACT_URL = (
    "https://voiceos-staging-releases.s3.amazonaws.com/releases/"
    "VoiceOS-0.1.24-universal-mac.zip"
)
_HASH = "sha256-Mh3i8d5MKp6on8VfDMzrKc75iDJA7AMENaVtqYCd/Cg="


def _load_module() -> ModuleType:
    return load_repo_module("packages/voiceos/updater.py", "voiceos_updater_test")


def test_voiceos_resolves_one_immutable_universal_vendor_zip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The official feed should pin one versioned ZIP for both Darwin systems."""
    module = _load_module()
    updater = module.VoiceOSUpdater()

    async def _fetch_asset_urls(
        _session: object,
        url: str,
        selectors: object,
        *,
        config: object,
    ) -> tuple[str, dict[str, str]]:
        assert url == updater.FEED_URL
        assert selectors is updater.SELECTORS
        assert config == updater.config
        return _VERSION, dict.fromkeys(updater.PLATFORMS, _ARTIFACT_URL)

    monkeypatch.setattr(
        "lib.update.updaters.strategies.fetch_electron_builder_asset_urls",
        _fetch_asset_urls,
    )

    info = _run(updater.fetch_latest(object()))
    hashes = dict.fromkeys(updater.PLATFORMS, _HASH)
    result = updater.build_result(info, hashes)

    assert updater.PLATFORMS == {
        "aarch64-darwin": "universal",
        "x86_64-darwin": "universal",
    }
    assert info == VersionInfo(
        version=_VERSION,
        metadata=AssetURLsMetadata(dict.fromkeys(updater.PLATFORMS, _ARTIFACT_URL)),
    )
    assert result.urls == dict.fromkeys(updater.PLATFORMS, _ARTIFACT_URL)
    assert result.hashes.to_json() == hashes


def test_voiceos_selectors_and_fallback_only_accept_the_versioned_zip() -> None:
    """The mutable installer DMG and stale releases must never become package inputs."""
    updater = _load_module().VoiceOSUpdater()

    for platform, selector in updater.SELECTORS.items():
        assert selector(_VERSION, _ARTIFACT_URL)
        assert not selector(_VERSION, _ARTIFACT_URL.replace(".zip", ".dmg"))
        assert not selector(_STALE_VERSION, _ARTIFACT_URL)
        assert (
            updater.get_download_url(platform, VersionInfo(_VERSION)) == _ARTIFACT_URL
        )


def test_voiceos_package_preserves_the_signed_universal_bundle() -> None:
    """The ZIP package must copy VoiceOS.app without fixups or signing hooks."""
    source = Path(REPO_ROOT / "packages/voiceos/default.nix").read_text(
        encoding="utf-8"
    )
    package = expect_instance(parse_nix_expr(source), FunctionDefinition)
    derivation = expect_instance(package.output, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)
    bindings = binding_map(arguments.values)

    assert_nix_ast_equal(derivation.name, Identifier(name="mkSimpleDarwinApp"))
    assert_nix_ast_equal(
        expect_binding(arguments.values, "builder").value,
        Identifier(name="mkZipApp"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "appName").value,
        StringPrimitive(value="VoiceOS"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "dontFixup").value,
        Primitive(value=True),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "platforms").value,
        '[ "aarch64-darwin" "x86_64-darwin" ]',
    )
    assert "postInstallApp" not in bindings
    assert "codesignApp" not in bindings


def test_voiceos_sources_pin_the_same_universal_zip_for_both_systems() -> None:
    """Checked-in metadata must route both systems to one official universal ZIP."""
    source = SourceEntry.model_validate_json(
        (REPO_ROOT / "packages/voiceos/sources.json").read_text(encoding="utf-8")
    )
    version = assert_release_version(source.version)
    hashes, urls = assert_platform_source_entry(
        source,
        platforms={"aarch64-darwin", "x86_64-darwin"},
    )
    assert len(set(hashes.values())) == 1
    assert len(set(urls.values())) == 1
    url = urls["aarch64-darwin"]
    assert_https_url(url, host="voiceos-staging-releases.s3.amazonaws.com")
    assert_url_contains_version(url, version)
    assert url.endswith("-universal-mac.zip")
