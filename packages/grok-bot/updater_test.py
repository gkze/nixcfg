"""Behavioral and package-shape tests for Grok Bot."""

from pathlib import Path
from types import ModuleType

import pytest
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
from lib.update.updaters.metadata import PlatformAPIMetadata

_VERSION = "0.29.0"
_URLS = {
    "aarch64-darwin": (
        "https://downloads.cursor.com/grokbot/stable/darwin-arm64/0.29.0/"
        "Grok_Bot_0.29.0.zip"
    ),
    "x86_64-darwin": (
        "https://downloads.cursor.com/grokbot/stable/darwin-x64/0.29.0/"
        "Grok_Bot_0.29.0_x64.zip"
    ),
}
_HASHES = {
    "aarch64-darwin": "sha256-UHdIGeaZ44mBHYMxf0NucarEC5N09iQ03uzsueKVysE=",
    "x86_64-darwin": "sha256-2tTsg9vzoDB9D68n1THd2/eFUYOho2umJ/KTdZYLDiw=",
}


def _load_module() -> ModuleType:
    return load_repo_module(
        "packages/grok-bot/updater.py",
        "grok_bot_updater_test",
    )


def _metadata(
    *,
    version: str = _VERSION,
    urls: dict[str, str] = _URLS,
) -> PlatformAPIMetadata:
    return PlatformAPIMetadata(
        platform_info={
            platform: {"name": version, "url": url} for platform, url in urls.items()
        },
        equality_fields={},
    )


def test_grok_bot_resolves_current_vendor_feed_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live feed topology should become one validated update candidate."""
    module = _load_module()
    updater = module.GrokBotUpdater()
    calls: list[tuple[object, str, object]] = []

    async def _fetch_json(
        _session: object,
        url: str,
        *,
        config: object,
    ) -> object:
        assert config is updater.config
        calls.append((_session, url, config))
        platform = "aarch64-darwin" if "/darwin-arm64/" in url else "x86_64-darwin"
        return {"name": _VERSION, "url": _URLS[platform]}

    monkeypatch.setattr(module, "fetch_json", _fetch_json)

    session = object()
    info = _run(updater.fetch_latest(session))
    result = updater.build_result(info, _HASHES)

    assert info == VersionInfo(version=_VERSION, metadata=_metadata())
    assert result == SourceEntry.model_validate({
        "hashes": _HASHES,
        "urls": _URLS,
        "version": _VERSION,
    })
    assert updater.required_tools == ("nix", "nix-prefetch-url")
    assert updater.supported_platforms == (
        "aarch64-darwin",
        "x86_64-darwin",
    )
    assert calls == [
        (
            session,
            "https://api2.cursor.sh/updates/api/update/darwin-arm64/sand/"
            "0.0.0/00000000-0000-0000-0000-000000000000/stable",
            updater.config,
        ),
        (
            session,
            "https://api2.cursor.sh/updates/api/update/darwin-x64/sand/"
            "0.0.0/00000000-0000-0000-0000-000000000000/stable",
            updater.config,
        ),
    ]


def test_grok_bot_rehashes_same_version_republication() -> None:
    """Versioned URLs cannot prove that the vendor has not replaced the bytes."""
    updater = _load_module().GrokBotUpdater()
    current = SourceEntry.model_validate({
        "hashes": _HASHES,
        "urls": _URLS,
        "version": _VERSION,
    })
    unchanged = VersionInfo(version=_VERSION, metadata=_metadata())

    assert _run(updater._is_latest(current, unchanged)) is False


@pytest.mark.parametrize(
    ("payload", "exception", "message"),
    [
        ([], TypeError, "Grok Bot feed for aarch64-darwin"),
        (
            {"name": _VERSION},
            RuntimeError,
            "returned unexpected fields",
        ),
        (
            {"name": _VERSION, "url": _URLS["aarch64-darwin"], "extra": True},
            RuntimeError,
            "returned unexpected fields",
        ),
        (
            {"name": 20, "url": _URLS["aarch64-darwin"]},
            TypeError,
            "string field 'name'",
        ),
        (
            {"name": "0.20", "url": _URLS["aarch64-darwin"]},
            RuntimeError,
            "returned invalid version",
        ),
        (
            {"name": _VERSION, "url": 20},
            TypeError,
            "string field 'url'",
        ),
        (
            {"name": "0.29.1", "url": _URLS["aarch64-darwin"]},
            RuntimeError,
            "does not match feed version",
        ),
    ],
)
def test_grok_bot_rejects_malformed_feed_payloads(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    exception: type[Exception],
    message: str,
) -> None:
    """Malformed update metadata must fail before any artifact is hashed."""
    module = _load_module()
    updater = module.GrokBotUpdater()

    async def _fetch_json(*_args: object, **_kwargs: object) -> object:
        return payload

    monkeypatch.setattr(module, "fetch_json", _fetch_json)

    with pytest.raises(exception, match=message):
        _run(updater.fetch_latest(object()))


@pytest.mark.parametrize(
    ("platform", "url"),
    [
        (
            "aarch64-darwin",
            _URLS["aarch64-darwin"].replace("https://", "http://"),
        ),
        (
            "aarch64-darwin",
            _URLS["aarch64-darwin"].replace(
                "downloads.cursor.com",
                "example.test",
            ),
        ),
        ("aarch64-darwin", f"{_URLS['aarch64-darwin']}?mutable=1"),
        ("aarch64-darwin", f"{_URLS['aarch64-darwin']}?"),
        ("aarch64-darwin", f"{_URLS['aarch64-darwin']}#mutable"),
        ("aarch64-darwin", f"{_URLS['aarch64-darwin']}#"),
        ("aarch64-darwin", _URLS["x86_64-darwin"]),
        (
            "x86_64-darwin",
            _URLS["x86_64-darwin"].replace("/grokbot/stable/", "/cursor/stable/"),
        ),
        (
            "x86_64-darwin",
            _URLS["x86_64-darwin"].replace(
                "Grok_Bot_0.29.0_x64.zip",
                "Grok_Bot_0.29.1_x64.zip",
            ),
        ),
        (
            "aarch64-darwin",
            _URLS["aarch64-darwin"].replace(
                "Grok_Bot_0.29.0.zip",
                "Grok_Bot_0.29.0_arm64.zip",
            ),
        ),
        (
            "x86_64-darwin",
            _URLS["x86_64-darwin"].replace("_x64.zip", ".zip"),
        ),
        (
            "x86_64-darwin",
            _URLS["x86_64-darwin"].replace(".zip", ".dmg"),
        ),
    ],
)
def test_grok_bot_rejects_untrusted_or_mutable_artifact_urls(
    platform: str,
    url: str,
) -> None:
    """Only exact versioned Grok Bot production ZIP paths may be persisted."""
    updater = _load_module().GrokBotUpdater()

    with pytest.raises(RuntimeError, match=f"invalid {platform} artifact URL"):
        updater._parse_artifact_url(platform, url)


def test_grok_bot_requires_one_release_across_platforms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An update transaction must not combine independently published releases."""
    module = _load_module()
    updater = module.GrokBotUpdater()

    async def _fetch_json(
        _session: object,
        url: str,
        *,
        config: object,
    ) -> object:
        assert config is updater.config
        if "/darwin-arm64/" in url:
            return {"name": _VERSION, "url": _URLS["aarch64-darwin"]}
        return {
            "name": "0.29.1",
            "url": _URLS["x86_64-darwin"].replace("0.29.0", "0.29.1"),
        }

    monkeypatch.setattr(module, "fetch_json", _fetch_json)

    with pytest.raises(RuntimeError, match="mismatched versions"):
        _run(updater.fetch_latest(object()))


def test_grok_bot_download_url_revalidates_typed_metadata() -> None:
    """Persisted URLs must still agree with their release version."""
    updater = _load_module().GrokBotUpdater()
    info = VersionInfo(version=_VERSION, metadata=_metadata())

    assert updater._download_url("darwin-arm64", info) == _URLS["aarch64-darwin"]
    assert updater._download_url("darwin-x64", info) == _URLS["x86_64-darwin"]

    with pytest.raises(RuntimeError, match="Unknown Grok Bot API platform"):
        updater._download_url("darwin-universal", info)

    with pytest.raises(TypeError, match="Expected Grok Bot platform payload"):
        updater._download_url(
            "darwin-arm64",
            VersionInfo(
                version=_VERSION,
                metadata=PlatformAPIMetadata(
                    platform_info={"aarch64-darwin": "bad"},  # type: ignore[dict-item]
                    equality_fields={},
                ),
            ),
        )

    with pytest.raises(RuntimeError, match="does not match release version"):
        updater._download_url(
            "darwin-arm64",
            VersionInfo(version="0.20.1", metadata=_metadata()),
        )

    with pytest.raises(RuntimeError, match="does not match feed version"):
        updater._download_url(
            "darwin-arm64",
            VersionInfo(
                version=_VERSION,
                metadata=_metadata(version="0.29.1"),
            ),
        )


def test_grok_bot_package_preserves_the_signed_vendor_bundle() -> None:
    """The ZIP package should copy Grok Bot.app without fixups or re-signing."""
    source = Path(REPO_ROOT / "packages/grok-bot/default.nix").read_text(
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
        expect_binding(arguments.values, "pname").value,
        StringPrimitive(value="grok-bot"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "appName").value,
        StringPrimitive(value="Grok Bot"),
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


def test_grok_bot_sources_pin_the_current_official_artifacts() -> None:
    """Checked-in metadata must pin both version-coherent official vendor ZIPs."""
    source = SourceEntry.model_validate_json(
        (REPO_ROOT / "packages/grok-bot/sources.json").read_text(encoding="utf-8")
    )
    version = assert_release_version(source.version)
    _hashes, urls = assert_platform_source_entry(
        source,
        platforms={"aarch64-darwin", "x86_64-darwin"},
    )
    assert len(set(urls.values())) == 2
    for url in urls.values():
        assert_https_url(url, host="downloads.cursor.com")
        assert_url_contains_version(url, version)
        assert url.endswith(".zip")
    assert "/darwin-arm64/" in urls["aarch64-darwin"]
    assert "/darwin-x64/" in urls["x86_64-darwin"]
