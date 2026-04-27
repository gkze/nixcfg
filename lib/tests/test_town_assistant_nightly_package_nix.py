"""Town Assistant nightly package and updater contracts."""

from __future__ import annotations

import asyncio
import json
from typing import Protocol, cast

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet
from nix_manipulator.expressions.with_statement import WithStatement

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    parse_nix_expr,
)
from lib.tests._updater_helpers import load_repo_module, run_async
from lib.update.paths import REPO_ROOT
from lib.update.updaters.base import VersionInfo
from lib.update.updaters.metadata import DownloadUrlMetadata


class _UpdateConfig(Protocol):
    default_timeout: float


class _TownAssistantNightlyUpdater(Protocol):
    APPCAST_URL: str
    config: _UpdateConfig

    async def fetch_latest(self, session: object) -> VersionInfo: ...

    def get_download_url(self, platform: str, info: VersionInfo) -> str: ...


class _TownAssistantNightlyUpdaterFactory(Protocol):
    def __call__(self) -> _TownAssistantNightlyUpdater: ...


class _TownAssistantNightlyUpdaterModule(Protocol):
    TownAssistantNightlyUpdater: _TownAssistantNightlyUpdaterFactory


def _load_updater() -> _TownAssistantNightlyUpdaterModule:
    return cast(
        "_TownAssistantNightlyUpdaterModule",
        load_repo_module(
            "packages/town-assistant-nightly/updater.py",
            "town_assistant_nightly_updater_test",
        ),
    )


def test_town_assistant_nightly_package_uses_dmg_app_copy_mode() -> None:
    """The package should expose Town's arm64 nightly DMG as a managed macOS app."""
    sources = json.loads(
        (REPO_ROOT / "packages/town-assistant-nightly/sources.json").read_text(
            encoding="utf-8"
        )
    )
    package_source = (
        REPO_ROOT / "packages/town-assistant-nightly/default.nix"
    ).read_text(encoding="utf-8")
    package = expect_instance(parse_nix_expr(package_source), FunctionDefinition)
    derivation = expect_instance(package.output, FunctionCall)
    derivation_args = expect_instance(derivation.argument, AttributeSet)
    meta = expect_instance(
        expect_binding(derivation_args.values, "meta").value,
        WithStatement,
    )
    meta_attrs = expect_instance(meta.body, AttributeSet)

    assert list(sources["urls"]) == ["aarch64-darwin"]
    assert list(sources["hashes"]) == ["aarch64-darwin"]
    assert_nix_ast_equal(derivation.name, Identifier(name="mkDmgApp"))
    assert_nix_ast_equal(
        expect_binding(derivation_args.values, "pname").value,
        StringPrimitive(value="town-assistant-nightly"),
    )
    assert_nix_ast_equal(
        expect_binding(derivation_args.values, "appName").value,
        StringPrimitive(value="Town Assistant"),
    )
    assert_nix_ast_equal(
        expect_binding(derivation_args.values, "macApp").value,
        '{ installMode = "copy"; }',
    )
    assert_nix_ast_equal(
        expect_binding(meta_attrs.values, "platforms").value,
        '[ "aarch64-darwin" ]',
    )


def test_town_assistant_nightly_fetch_latest_and_download_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parse the nightly Sparkle appcast and return the immutable DMG URL."""
    module = _load_updater()
    updater = module.TownAssistantNightlyUpdater()
    xml = (
        '<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">'
        "<channel><item>"
        "<sparkle:version>32</sparkle:version>"
        "<sparkle:shortVersionString>1.8</sparkle:shortVersionString>"
        '<enclosure url="https://example.invalid/Town%20Assistant-1.8-32.dmg"/>'
        "</item></channel></rss>"
    )

    async def _fetch_url(_session: object, url: str, **kwargs: object) -> bytes:
        assert url == updater.APPCAST_URL
        assert kwargs["user_agent"] == "Sparkle/2.0"
        assert kwargs["request_timeout"] == updater.config.default_timeout
        assert kwargs["config"] == updater.config
        return xml.encode()

    monkeypatch.setattr(module, "fetch_url", _fetch_url)

    latest = run_async(updater.fetch_latest(object()))

    assert latest == VersionInfo(
        version="1.8-32",
        metadata=DownloadUrlMetadata(
            url="https://example.invalid/Town%20Assistant-1.8-32.dmg",
        ),
    )
    assert (
        updater.get_download_url("aarch64-darwin", latest)
        == "https://example.invalid/Town%20Assistant-1.8-32.dmg"
    )


@pytest.mark.parametrize(
    ("xml", "match"),
    [
        ("<", "Invalid Town Assistant appcast XML"),
        ("<rss><channel /></rss>", "No items found in Town Assistant appcast"),
        (
            "<rss><channel><item /></channel></rss>",
            "No enclosure found in Town Assistant appcast",
        ),
        (
            '<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel><item><sparkle:version>32</sparkle:version><enclosure url="https://example.invalid/app.dmg"/></item></channel></rss>',
            "No short version found in Town Assistant appcast",
        ),
        (
            '<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel><item><sparkle:shortVersionString>1.8</sparkle:shortVersionString><sparkle:version /><enclosure url="https://example.invalid/app.dmg"/></item></channel></rss>',
            "Empty build version in Town Assistant appcast",
        ),
        (
            '<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel><item><sparkle:shortVersionString>1.8</sparkle:shortVersionString><sparkle:version> </sparkle:version><enclosure url="https://example.invalid/app.dmg"/></item></channel></rss>',
            "Blank build version in Town Assistant appcast",
        ),
        (
            '<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel><item><sparkle:shortVersionString>1.8</sparkle:shortVersionString><sparkle:version>32</sparkle:version><enclosure /></item></channel></rss>',
            "No URL found in Town Assistant appcast enclosure",
        ),
        (
            '<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel><item><sparkle:shortVersionString>1.8</sparkle:shortVersionString><sparkle:version>32</sparkle:version><enclosure url=" "/></item></channel></rss>',
            "Blank URL found in Town Assistant appcast enclosure",
        ),
    ],
)
def test_town_assistant_nightly_rejects_invalid_appcast_shapes(
    monkeypatch: pytest.MonkeyPatch,
    xml: str,
    match: str,
) -> None:
    """Surface targeted appcast parsing errors."""
    module = _load_updater()
    updater = module.TownAssistantNightlyUpdater()
    monkeypatch.setattr(
        module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(0, result=xml.encode()),
    )

    with pytest.raises(RuntimeError, match=match):
        run_async(updater.fetch_latest(object()))
