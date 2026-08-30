"""Contracts for updater-owned MCP runtime package pins."""

import json
from pathlib import Path
from types import ModuleType

import pytest
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._updater_helpers import collect_events, load_repo_module, run_async
from lib.update.events import UpdateEventKind
from lib.update.paths import REPO_ROOT
from lib.update.updaters import UpdateContext, VersionInfo

_PACKAGE_DIR = REPO_ROOT / "packages/mcp-runtime-tools"
_CURRENT_PINS = {
    "@padenot/firefox-devtools-mcp": "@padenot/firefox-devtools-mcp@0.7.5",
    "@steipete/macos-automator-mcp": "@steipete/macos-automator-mcp@0.4.6",
    "@vantasdk/vanta-mcp-server": "@vantasdk/vanta-mcp-server@1.2.0",
    "chrome-devtools-mcp": "chrome-devtools-mcp@1.7.0",
    "convex": "convex@1.43.0",
    "markitdown-mcp": "markitdown-mcp==0.0.1a4",
    "mcp-proxy-for-aws": "mcp-proxy-for-aws==1.6.4",
    "mcp-remote": "mcp-remote@0.1.38",
    "next-devtools-mcp": "next-devtools-mcp@0.4.0",
    "slack-mcp-server": "slack-mcp-server@1.3.0",
}


def _load_module() -> ModuleType:
    return load_repo_module(
        "packages/mcp-runtime-tools/updater.py",
        "mcp_runtime_tools_updater_test",
    )


def test_checked_in_pins_preserve_the_current_runtime_contract() -> None:
    """Moving pins to updater metadata must not silently upgrade MCP tools."""
    payload = json.loads((_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8"))

    assert payload == {
        "hashes": {},
        "pins": _CURRENT_PINS,
        "version": "registry",
    }


def test_fetch_latest_resolves_all_npm_and_pypi_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The updater refreshes every externalized spec from its package registry."""
    module = _load_module()
    updater = module.McpRuntimeToolsUpdater()
    responses = {
        "https://registry.npmjs.org/%40padenot%2Ffirefox-devtools-mcp/latest": {
            "version": "1.0.1"
        },
        "https://registry.npmjs.org/%40steipete%2Fmacos-automator-mcp/latest": {
            "version": "2.0.2"
        },
        "https://registry.npmjs.org/%40vantasdk%2Fvanta-mcp-server/latest": {
            "version": "3.0.3"
        },
        "https://registry.npmjs.org/chrome-devtools-mcp/latest": {"version": "4.0.4"},
        "https://registry.npmjs.org/convex/latest": {"version": "5.0.5"},
        "https://registry.npmjs.org/mcp-remote/latest": {"version": "6.0.6"},
        "https://registry.npmjs.org/next-devtools-mcp/latest": {"version": "7.0.7"},
        "https://registry.npmjs.org/slack-mcp-server/latest": {"version": "8.0.8"},
        "https://pypi.org/pypi/markitdown-mcp/json": {"info": {"version": "9.0.9"}},
        "https://pypi.org/pypi/mcp-proxy-for-aws/json": {
            "info": {"version": "10.0.10"}
        },
    }
    calls: list[str] = []

    async def _fetch_json(_session: object, url: str, **_kwargs: object) -> object:
        calls.append(url)
        return responses[url]

    monkeypatch.setattr(module, "fetch_json", _fetch_json)

    info = run_async(updater.fetch_latest(object()))

    assert set(calls) == set(responses)
    assert info == VersionInfo(
        version="registry",
        metadata={
            "pins": {
                "@padenot/firefox-devtools-mcp": (
                    "@padenot/firefox-devtools-mcp@1.0.1"
                ),
                "@steipete/macos-automator-mcp": (
                    "@steipete/macos-automator-mcp@2.0.2"
                ),
                "@vantasdk/vanta-mcp-server": ("@vantasdk/vanta-mcp-server@3.0.3"),
                "chrome-devtools-mcp": "chrome-devtools-mcp@4.0.4",
                "convex": "convex@5.0.5",
                "markitdown-mcp": "markitdown-mcp==9.0.9",
                "mcp-proxy-for-aws": "mcp-proxy-for-aws==10.0.10",
                "mcp-remote": "mcp-remote@6.0.6",
                "next-devtools-mcp": "next-devtools-mcp@7.0.7",
                "slack-mcp-server": "slack-mcp-server@8.0.8",
            }
        },
    )


def test_fetch_latest_rejects_an_empty_npm_registry_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An incomplete npm response must not erase an updater-owned runtime pin."""
    module = _load_module()
    updater = module.McpRuntimeToolsUpdater()
    invalid_url = "https://registry.npmjs.org/chrome-devtools-mcp/latest"

    async def _fetch_json(_session: object, url: str, **_kwargs: object) -> object:
        if url == invalid_url:
            return {"version": ""}
        if url.startswith("https://pypi.org/"):
            return {"info": {"version": "1.0.0"}}
        return {"version": "1.0.0"}

    monkeypatch.setattr(module, "fetch_json", _fetch_json)

    with pytest.raises(RuntimeError) as error:
        run_async(updater.fetch_latest(object()))

    assert str(error.value) == f"Empty npm version in {invalid_url}"


def test_fetch_latest_rejects_an_empty_pypi_registry_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An incomplete PyPI response must not erase an updater-owned runtime pin."""
    module = _load_module()
    updater = module.McpRuntimeToolsUpdater()
    invalid_url = "https://pypi.org/pypi/markitdown-mcp/json"

    async def _fetch_json(_session: object, url: str, **_kwargs: object) -> object:
        if url == invalid_url:
            return {"info": {"version": ""}}
        if url.startswith("https://pypi.org/"):
            return {"info": {"version": "1.0.0"}}
        return {"version": "1.0.0"}

    monkeypatch.setattr(module, "fetch_json", _fetch_json)

    with pytest.raises(RuntimeError) as error:
        run_async(updater.fetch_latest(object()))

    assert str(error.value) == f"Empty PyPI version in {invalid_url}"


def test_updater_persists_pin_only_sources() -> None:
    """The no-hash updater emits a valid pin-only source entry."""
    module = _load_module()
    updater = module.McpRuntimeToolsUpdater()
    info = VersionInfo(version="registry", metadata={"pins": _CURRENT_PINS})
    events = run_async(collect_events(updater.fetch_hashes(info, object())))

    assert len(events) == 1
    assert events[0].kind == UpdateEventKind.VALUE
    assert events[0].payload == {}
    assert updater.build_result(info, {}) == SourceEntry.model_validate({
        "hashes": {},
        "pins": _CURRENT_PINS,
        "version": "registry",
    })


@pytest.mark.parametrize(
    ("pins", "message"),
    [
        ([], "Expected MCP runtime pins mapping"),
        ({"convex": 123}, "Expected MCP runtime pins to contain only strings"),
    ],
)
def test_updater_rejects_malformed_runtime_pin_metadata(
    pins: object,
    message: str,
) -> None:
    """Only a string-to-string pin map may enter the shared source sidecar."""
    updater = _load_module().McpRuntimeToolsUpdater()
    info = VersionInfo(version="registry", metadata={"pins": pins})

    with pytest.raises(TypeError) as error:
        updater.build_result(info, {})

    assert str(error.value) == message


@pytest.mark.parametrize("wrapped", [False, True])
def test_latest_check_accepts_both_supported_current_source_shapes(
    *,
    wrapped: bool,
) -> None:
    """Freshness works for direct entries and the runner's update context."""
    updater = _load_module().McpRuntimeToolsUpdater()
    current = SourceEntry(
        version="registry",
        hashes={},
        pins=_CURRENT_PINS,
    )
    context = UpdateContext(current=current) if wrapped else current
    info = VersionInfo(version="registry", metadata={"pins": _CURRENT_PINS})

    assert run_async(updater._is_latest(context, info)) is True


@pytest.mark.parametrize(
    ("path", "source_path"),
    [
        (
            REPO_ROOT / "home/george/mcp-catalog.nix",
            "../../packages/mcp-runtime-tools/sources.json",
        ),
        (
            REPO_ROOT / "lib/mcp-remote-wrapper.nix",
            "../packages/mcp-runtime-tools/sources.json",
        ),
    ],
)
def test_nix_consumers_load_updater_owned_runtime_pins(
    path: Path,
    source_path: str,
) -> None:
    """Both MCP Nix consumers read the updater surface structurally."""
    function = expect_instance(
        parse_nix_expr(path.read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    body = expect_instance(function.output, AttributeSet)

    assert_nix_ast_equal(
        expect_binding(body.scope, "mcpRuntimeSource").value,
        f"builtins.fromJSON (builtins.readFile {source_path})",
    )
    assert_nix_ast_equal(
        expect_binding(body.scope, "mcpRuntimePins").value,
        "mcpRuntimeSource.pins",
    )
