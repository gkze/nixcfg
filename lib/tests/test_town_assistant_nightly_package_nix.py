"""Town Assistant distribution package and updater contracts."""

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
from lib.tests._updater_helpers import load_repo_module, run_async, updater_from_module
from lib.update.paths import REPO_ROOT
from lib.update.updaters import UpdateContext, VersionInfo
from lib.update.updaters import strategies as updater_strategies
from lib.update.updaters.metadata import DownloadUrlMetadata
from lib.update.updaters.vendor_feeds import SparkleAppcastItem


class _UpdateConfig(Protocol):
    default_timeout: float


class _TownAssistantUpdater(Protocol):
    APPCAST_URL: str
    config: _UpdateConfig

    async def fetch_latest(
        self, session: object, *, context: object
    ) -> VersionInfo: ...

    def get_download_url(self, platform: str, info: VersionInfo) -> str: ...


@pytest.fixture(params=["nightly", "internal"])
def channel(request: pytest.FixtureRequest) -> str:
    """Exercise both supported distribution packages."""
    return cast("str", request.param)


def _load_updater(channel: str) -> _TownAssistantUpdater:
    return cast(
        "_TownAssistantUpdater",
        updater_from_module(
            load_repo_module(
                f"packages/town-assistant-{channel}/updater.py",
                f"town_assistant_{channel}_updater_test",
            )
        ),
    )


def test_town_assistant_package_uses_dmg_app_copy_mode(channel: str) -> None:
    """The package should expose Town's arm64 distribution DMG as a managed macOS app."""
    sources = json.loads(
        (REPO_ROOT / f"packages/town-assistant-{channel}/sources.json").read_text(
            encoding="utf-8"
        )
    )
    package_source = (
        REPO_ROOT / f"packages/town-assistant-{channel}/default.nix"
    ).read_text(encoding="utf-8")
    package = expect_instance(parse_nix_expr(package_source), FunctionDefinition)
    derivation = expect_instance(package.output, FunctionCall)
    derivation_args = expect_instance(derivation.argument, AttributeSet)
    meta = expect_instance(
        expect_binding(derivation_args.values, "meta").value,
        WithStatement,
    )
    meta_attrs = expect_instance(meta.body, AttributeSet)

    assert sources["urls"]["aarch64-darwin"].startswith(
        f"https://town-macos-app.s3.us-east-1.amazonaws.com/desktop/{channel}/releases/"
    )
    assert list(sources["urls"]) == ["aarch64-darwin"]
    assert list(sources["hashes"]) == ["aarch64-darwin"]
    assert_nix_ast_equal(derivation.name, Identifier(name="mkDmgApp"))
    assert_nix_ast_equal(
        expect_binding(derivation_args.values, "pname").value,
        StringPrimitive(value=f"town-assistant-{channel}"),
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


def test_town_assistant_fetch_latest_and_download_url(
    monkeypatch: pytest.MonkeyPatch,
    channel: str,
) -> None:
    """Parse the selected Sparkle appcast and return the immutable DMG URL."""
    updater = _load_updater(channel)

    async def _fetch_items(_session: object, url: str, *, config: object):
        assert url == (
            "https://town-macos-app.s3.us-east-1.amazonaws.com"
            f"/desktop/{channel}/appcast.xml"
        )
        assert config == updater.config
        return (
            SparkleAppcastItem(
                "123.26.1",
                "1.46",
                "https://example.invalid/Town%20Assistant-1.46-123.26.1.dmg",
            ),
        )

    monkeypatch.setattr(updater_strategies, "fetch_sparkle_appcast_items", _fetch_items)

    latest = run_async(
        updater.fetch_latest(object(), context=UpdateContext(current=None))
    )

    assert latest == VersionInfo(
        version="1.46-123.26.1",
        metadata=DownloadUrlMetadata(
            url="https://example.invalid/Town%20Assistant-1.46-123.26.1.dmg",
        ),
    )
    assert (
        updater.get_download_url("aarch64-darwin", latest)
        == "https://example.invalid/Town%20Assistant-1.46-123.26.1.dmg"
    )


@pytest.mark.parametrize(
    ("item", "match"),
    [
        (
            SparkleAppcastItem("32", None, "https://example.invalid/app.dmg"),
            "Missing version",
        ),
        (
            SparkleAppcastItem(" ", "1.8", "https://example.invalid/app.dmg"),
            "Missing version",
        ),
        (
            SparkleAppcastItem("32", "1.8", None),
            "Missing download URL",
        ),
        (
            SparkleAppcastItem("32", "1.8", " "),
            "Missing download URL",
        ),
    ],
)
def test_town_assistant_rejects_invalid_appcast_shapes(
    monkeypatch: pytest.MonkeyPatch,
    channel: str,
    item: SparkleAppcastItem,
    match: str,
) -> None:
    """Surface targeted appcast parsing errors."""
    updater = _load_updater(channel)

    async def _fetch_items(*_args: object, **_kwargs: object):
        return (item,)

    monkeypatch.setattr(
        updater_strategies,
        "fetch_sparkle_appcast_items",
        _fetch_items,
    )

    with pytest.raises(RuntimeError, match=match):
        run_async(updater.fetch_latest(object(), context=UpdateContext(current=None)))
