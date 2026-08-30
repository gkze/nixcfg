"""Focused contracts for the exact-source OpenChamber foundation."""

import asyncio
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.path import NixPath
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.tests._updater_helpers import (
    collect_events,
    install_fixed_hash_stream,
    load_repo_module,
    run_async,
)
from lib.update.events import UpdateEventKind, expect_source_hashes
from lib.update.nix import _build_fetch_from_github_call
from lib.update.nix_expr import compact_nix_expr, identifier_attr_path
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo

_PACKAGE_DIR = REPO_ROOT / "packages/openchamber"
_VERSION = "1.21.0"
_TAG = f"v{_VERSION}"
_COMMIT = "ad7fd356339ccc5c9af5af1a6786662572d53ed0"
_BUN_VERSION = "1.3.14"
_ELECTRON_VERSION = "43.3.0"
_OPENCODE_VERSION = "1.18.23"
_OPENCODE_COMMIT = "ef2880f379129aa048be9e9353e30aa168d42c17"
_SHERPA_VERSION = "1.13.3"
_SHERPA_COMMIT = "330609dab49be6ee8b30702918ca7abbbad1286a"
_SHERPA_WRAPPER_VERSION = "1.12.28"
_SOURCE_PINS = {
    "bunVersion": _BUN_VERSION,
    "opencodeCommit": _OPENCODE_COMMIT,
    "opencodeVersion": _OPENCODE_VERSION,
    "sherpaCommit": _SHERPA_COMMIT,
    "sherpaVersion": _SHERPA_VERSION,
    "sherpaWrapperVersion": _SHERPA_WRAPPER_VERSION,
}
_OPENCODE_NODE_MODULES_HASH = "sha256-ObS50y/oy6fM9wSGUL/wx6O0+fTWHC04mXJNd7w/2Z0="
_BUN_HASH = "sha256-2LliIYKK1vl6x6wKt+lYcjQa92MAHogD6CZ2UsJlJiA="
_BUN_URL = (
    "https://github.com/oven-sh/bun/releases/download/"
    f"bun-v{_BUN_VERSION}/bun-darwin-aarch64.zip"
)
_SOURCE_HASHES = (
    "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
    "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
)
_URL_HASHES = (
    _BUN_HASH,
    "sha256-DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD=",
    "sha256-EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE=",
)
_OPENCHAMBER_NODE_MODULES_HASH = "sha256-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF="
_PROMOTED_OPENCHAMBER_SRC_HASH = "sha256-q9/c9bbIKAsdkkJuxrH7b6r5/WIo1XgrOQausMbULmg="
_PROMOTED_OPENCODE_SRC_HASH = "sha256-1iMdRFkZh6J82EDoPq3mFLXMGmdtbnLBgURtgrJRAlw="
_PROMOTED_SHERPA_ONNX_SRC_HASH = "sha256-xwu45dJOT1yUdU0P6Vjr8XexSeGOOfQ/zt1lhcASm/8="
_PROMOTED_OPENCHAMBER_NODE_MODULES_HASH = (
    "sha256-bhjpFfKPhaBuAGJTnIiL5tYnp+UYc3HFTinxMzfVPDY="
)
_PROMOTED_NODE_ADDON_API_HASH = "sha256-oM5nZTolH1bqQNLqsIeXk0ts/J201IdmeV2Xu5tNlg0="
_PROMOTED_SHERPA_ONNX_NODE_HASH = "sha256-YN10TB8kR8u1ekFfu6ZZTFo3RpT6j7fou/mLgveVlHE="

_REQUIRED_SUPPRESSION_SURFACES = {
    "electron-auto-updater-setup": (
        "patch",
        "openchamber",
        "packages/electron/main.mjs",
        1,
    ),
    "electron-ipc-check": (
        "patch",
        "openchamber",
        "packages/electron/main.mjs",
        1,
    ),
    "electron-ipc-download": (
        "patch",
        "openchamber",
        "packages/electron/main.mjs",
        1,
    ),
    "electron-ipc-restart": (
        "patch",
        "openchamber",
        "packages/electron/main.mjs",
        1,
    ),
    "electron-menu-check": (
        "patch",
        "openchamber",
        "packages/electron/main.mjs",
        2,
    ),
    "electron-asar-native-unpack": (
        "patch",
        "openchamber",
        "packages/electron/package.json",
        1,
    ),
    "electron-notarization": (
        "patch",
        "openchamber",
        "packages/electron/package.json",
        1,
    ),
    "electron-source-only-node-pty": (
        "patch",
        "openchamber",
        "packages/electron/scripts/rebuild-native.mjs",
        1,
    ),
    "renderer-update-polling": (
        "patch",
        "openchamber",
        "packages/ui/src/hooks/useUpdatePolling.ts",
        1,
    ),
    "renderer-managed-state": (
        "patch",
        "openchamber",
        "packages/ui/src/stores/useUpdateStore.ts",
        1,
    ),
    "renderer-check": (
        "patch",
        "openchamber",
        "packages/ui/src/stores/useUpdateStore.ts",
        1,
    ),
    "renderer-download": (
        "patch",
        "openchamber",
        "packages/ui/src/stores/useUpdateStore.ts",
        1,
    ),
    "renderer-restart": (
        "patch",
        "openchamber",
        "packages/ui/src/stores/useUpdateStore.ts",
        1,
    ),
    "web-update-check": (
        "patch",
        "openchamber",
        "packages/web/server/lib/opencode/openchamber-routes.js",
        1,
    ),
    "web-update-install": (
        "patch",
        "openchamber",
        "packages/web/server/lib/opencode/openchamber-routes.js",
        1,
    ),
    "cli-update-command": (
        "patch",
        "openchamber",
        "packages/web/bin/lib/commands-update.js",
        1,
    ),
    "lifecycle-opencode-managed-env": (
        "patch",
        "openchamber",
        "packages/web/server/lib/opencode/lifecycle.js",
        1,
    ),
    "opencode-cli-upgrade": (
        "patch",
        "opencode",
        "packages/opencode/src/cli/cmd/upgrade.ts",
        1,
    ),
    "opencode-global-http-upgrade": (
        "patch",
        "opencode",
        "packages/opencode/src/server/routes/instance/httpapi/handlers/global.ts",
        1,
    ),
    "root-postinstall-download": ("anchor", "openchamber", "package.json", 1),
    "electron-prepare-opencode-cli": (
        "anchor",
        "openchamber",
        "packages/electron/package.json",
        1,
    ),
    "electron-rebuild-native": (
        "anchor",
        "openchamber",
        "packages/electron/package.json",
        1,
    ),
    "opencode-auto-updater": (
        "anchor",
        "opencode",
        "packages/opencode/src/cli/upgrade.ts",
        1,
    ),
}


def _load_updater_module() -> ModuleType:
    return load_repo_module(
        "packages/openchamber/updater.py",
        "openchamber_updater_dedicated_test",
    )


def _load_patcher_module() -> ModuleType:
    return load_repo_module(
        "packages/openchamber/patch_nix_managed.py",
        "openchamber_patcher_dedicated_test",
    )


def _write_patcher_fixture(module: ModuleType, root: Path) -> dict[str, Path]:
    roots = {
        "openchamber": root / "openchamber",
        "opencode": root / "opencode",
    }
    fragments: dict[Path, list[str]] = {}
    for item in (*module._ANCHORS, *module._PATCHES):
        path = roots[item.component] / item.relative_path
        fragment = item.old if hasattr(item, "old") else item.text
        fragments.setdefault(path, []).extend([fragment] * item.expected_count)
    for path, values in fragments.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(values), encoding="utf-8")
    return roots


def _urls() -> dict[str, str]:
    return {
        "bunUrl": _BUN_URL,
        "nodeAddonApiUrl": (
            "https://registry.npmjs.org/node-addon-api/-/node-addon-api-8.3.0.tgz"
        ),
        "openchamberUrl": (
            f"https://github.com/openchamber/openchamber/archive/{_COMMIT}.tar.gz"
        ),
        "opencodeUrl": (
            f"https://github.com/anomalyco/opencode/archive/{_OPENCODE_COMMIT}.tar.gz"
        ),
        "sherpaOnnxUrl": (
            f"https://github.com/k2-fsa/sherpa-onnx/archive/{_SHERPA_COMMIT}.tar.gz"
        ),
        "sherpaOnnxNodeUrl": (
            "https://registry.npmjs.org/sherpa-onnx-node/-/"
            f"sherpa-onnx-node-{_SHERPA_WRAPPER_VERSION}.tgz"
        ),
    }


def _version_info() -> VersionInfo:
    return VersionInfo(
        version=_VERSION,
        metadata={
            "commit": _COMMIT,
            "electronVersion": _ELECTRON_VERSION,
            "opencodeNodeModulesHash": _OPENCODE_NODE_MODULES_HASH,
            "tag": _TAG,
            **_urls(),
        },
    )


def _expected_node_modules_fake_hash_expr() -> str:
    """Build the updater expression from a test-owned exact Bun contract."""
    bun_source = FunctionCall(
        name=identifier_attr_path("pkgs", "fetchurl"),
        argument=AttributeSet(
            values=[
                Binding(name="url", value=StringPrimitive(value=_BUN_URL)),
                Binding(name="hash", value=StringPrimitive(value=_BUN_HASH)),
            ]
        ),
    )
    bun = FunctionCall(
        name=FunctionCall(
            name=identifier_attr_path("pkgs", "callPackage"),
            argument=NixPath(path=str(_PACKAGE_DIR / "bun.nix")),
        ),
        argument=AttributeSet(
            values=[
                Binding(name="bunSource", value=bun_source),
                Binding(name="version", value=StringPrimitive(value=_BUN_VERSION)),
            ],
        ),
    )
    package_call = FunctionCall(
        name=FunctionCall(
            name=identifier_attr_path("pkgs", "callPackage"),
            argument=NixPath(path=str(_PACKAGE_DIR / "node-modules.nix")),
        ),
        argument=AttributeSet(
            values=[
                Binding(name="bun", value=bun),
                Binding(
                    name="bunVersion",
                    value=StringPrimitive(value=_BUN_VERSION),
                ),
                Binding(
                    name="src",
                    value=_build_fetch_from_github_call(
                        "openchamber",
                        "openchamber",
                        rev=_COMMIT,
                        hash_value=_SOURCE_HASHES[0],
                        fetch_submodules=False,
                    ),
                ),
                Binding(name="version", value=StringPrimitive(value=_VERSION)),
                Binding(
                    name="hash",
                    value=identifier_attr_path("pkgs", "lib", "fakeHash"),
                ),
            ]
        ),
    )
    return compact_nix_expr(package_call.rebuild())


def _lock_text() -> str:
    return "\n".join((
        f'"@opencode-ai/sdk": ["@opencode-ai/sdk@{_OPENCODE_VERSION}", ""]',
        f'"electron": ["electron@{_ELECTRON_VERSION}", ""]',
        (f'"sherpa-onnx-node": ["sherpa-onnx-node@{_SHERPA_WRAPPER_VERSION}", ""]'),
        (
            '"sherpa-onnx-darwin-arm64": '
            f'["sherpa-onnx-darwin-arm64@{_SHERPA_VERSION}", ""]'
        ),
    ))


def test_openchamber_resolves_one_exact_release_and_companion_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery must prove every immutable source and locked runtime version."""
    module = _load_updater_module()
    updater = module.OpenChamberUpdater()
    api_paths: list[str] = []
    fetched_urls: list[str] = []

    async def release_payload(
        _session: object,
        path: str,
        *,
        config: object,
    ) -> dict[str, str]:
        assert config == updater.config
        api_paths.append(path)
        return {"tag_name": _TAG}

    commits = {
        f"repos/openchamber/openchamber/commits/{_TAG}": _COMMIT,
        f"repos/anomalyco/opencode/commits/v{_OPENCODE_VERSION}": _OPENCODE_COMMIT,
        "repos/k2-fsa/sherpa-onnx/commits/v1.13.3": _SHERPA_COMMIT,
    }

    async def commit_payload(
        _session: object,
        path: str,
        *,
        config: object,
    ) -> dict[str, str]:
        assert config == updater.config
        api_paths.append(path)
        return {"sha": commits[path]}

    async def json_payload(
        _session: object,
        url: str,
        *,
        config: object,
    ) -> object:
        assert config == updater.config
        fetched_urls.append(url)
        if url.endswith(f"/{_COMMIT}/package.json"):
            return {
                "version": _VERSION,
                "packageManager": "bun@1.3.14",
                "engines": {"node": ">=22.0.0"},
            }
        if url.endswith("/packages/electron/package.json"):
            return {
                "version": _VERSION,
                "build": {
                    "appId": "dev.openchamber.desktop",
                    "productName": "OpenChamber",
                },
                "devDependencies": {"electron": "^43.3.0"},
            }
        if url.endswith("/packages/web/package.json"):
            return {
                "version": _VERSION,
                "dependencies": {
                    "@opencode-ai/sdk": _OPENCODE_VERSION,
                    "sherpa-onnx-node": "1.12.28",
                },
            }
        if url.endswith(f"/{_OPENCODE_COMMIT}/package.json"):
            return {"packageManager": "bun@1.3.14"}
        if url.endswith("/nix/hashes.json"):
            return {
                "nodeModules": {
                    "aarch64-darwin": _OPENCODE_NODE_MODULES_HASH,
                }
            }
        if url.endswith("/scripts/node-addon-api/package.json"):
            return {"dependencies": {"node-addon-api": "^8.3.0"}}
        msg = f"unexpected exact-source URL: {url}"
        raise AssertionError(msg)

    async def bytes_payload(
        _session: object,
        url: str,
        *,
        config: object,
    ) -> bytes:
        assert config == updater.config
        fetched_urls.append(url)
        return _lock_text().encode()

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        release_payload,
    )
    monkeypatch.setattr(module, "fetch_github_api", commit_payload)
    monkeypatch.setattr(module, "fetch_json", json_payload)
    monkeypatch.setattr(module, "fetch_url", bytes_payload)

    assert run_async(updater.fetch_latest(object())) == _version_info()
    assert api_paths == [
        "repos/openchamber/openchamber/releases/latest",
        f"repos/openchamber/openchamber/commits/{_TAG}",
        f"repos/anomalyco/opencode/commits/v{_OPENCODE_VERSION}",
        "repos/k2-fsa/sherpa-onnx/commits/v1.13.3",
    ]
    assert len(fetched_urls) == 7


@pytest.mark.parametrize(
    ("lock_text", "package"),
    [
        ("", "electron"),
        (
            "\n".join((
                '"electron": ["electron@43.3.0", ""]',
                '"electron": ["electron@43.3.0", ""]',
            )),
            "electron",
        ),
    ],
)
def test_openchamber_lock_requires_one_exact_resolution(
    lock_text: str,
    package: str,
) -> None:
    """Missing or ambiguous lock entries must stop metadata promotion."""
    module = _load_updater_module()
    with pytest.raises(RuntimeError, match="must contain exactly one"):
        module._locked_package_version(lock_text, package)


def test_openchamber_hashes_every_source_and_closure_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hashing must include sources, npm inputs, upstream OpenCode, and Bun graph."""
    module = _load_updater_module()
    updater = module.OpenChamberUpdater()
    outputs = (*_SOURCE_HASHES, *_URL_HASHES, _OPENCHAMBER_NODE_MODULES_HASH)
    calls = install_fixed_hash_stream(
        monkeypatch,
        tuple((f"hash-step-{index}", value) for index, value in enumerate(outputs)),
    )

    events = run_async(collect_events(updater.fetch_hashes(_version_info(), object())))
    status_events = [event for event in events if event.kind is UpdateEventKind.STATUS]
    value_events = [event for event in events if event.kind is UpdateEventKind.VALUE]
    hashes = expect_source_hashes(value_events[-1].payload)
    entries = cast("list[HashEntry]", hashes)

    assert len(calls) == 7
    assert [event.message for event in status_events] == [
        f"hash-step-{index}" for index in range(7)
    ]
    assert_nix_ast_equal(
        str(calls[0]["expr"]),
        _build_fetch_from_github_call(
            "openchamber",
            "openchamber",
            rev=_COMMIT,
            fetch_submodules=False,
        ),
    )
    assert_nix_ast_equal(
        str(calls[-1]["expr"]),
        _expected_node_modules_fake_hash_expr(),
    )
    assert (
        HashEntry.create(
            "nodeModulesHash",
            _OPENCODE_NODE_MODULES_HASH,
            platform="aarch64-darwin",
            url=_urls()["opencodeUrl"],
        )
        in entries
    )
    assert (
        HashEntry.create(
            "nodeModulesHash",
            _OPENCHAMBER_NODE_MODULES_HASH,
            platform="aarch64-darwin",
            url=_urls()["openchamberUrl"],
        )
        in entries
    )


def test_openchamber_build_result_requires_and_persists_complete_closure() -> None:
    """Partial hashes must never replace the explicit blocked source metadata."""
    module = _load_updater_module()
    updater = module.OpenChamberUpdater()
    urls = _urls()
    entries = [
        HashEntry.create("srcHash", value, url=url)
        for value, url in zip(
            _SOURCE_HASHES,
            (
                urls["openchamberUrl"],
                urls["opencodeUrl"],
                urls["sherpaOnnxUrl"],
            ),
            strict=True,
        )
    ]
    entries.extend(
        HashEntry.create("sha256", value, url=url)
        for value, url in zip(
            _URL_HASHES,
            (
                urls["bunUrl"],
                urls["nodeAddonApiUrl"],
                urls["sherpaOnnxNodeUrl"],
            ),
            strict=True,
        )
    )
    entries.extend((
        HashEntry.create(
            "nodeModulesHash",
            _OPENCODE_NODE_MODULES_HASH,
            platform="aarch64-darwin",
            url=urls["opencodeUrl"],
        ),
        HashEntry.create(
            "nodeModulesHash",
            _OPENCHAMBER_NODE_MODULES_HASH,
            platform="aarch64-darwin",
            url=urls["openchamberUrl"],
        ),
    ))

    with pytest.raises(RuntimeError, match="complete exact-source hash closure"):
        updater.build_result(_version_info(), entries[:-1])

    with pytest.raises(TypeError, match="structured hash entries"):
        updater.build_result(
            _version_info(),
            {"aarch64-darwin": _OPENCHAMBER_NODE_MODULES_HASH},
        )

    mismatched = [
        *entries[:-2],
        entries[-2].model_copy(update={"hash": _SOURCE_HASHES[0]}),
        entries[-1],
    ]
    with pytest.raises(RuntimeError, match="differs from exact upstream"):
        updater.build_result(_version_info(), mismatched)

    result = updater.build_result(_version_info(), entries)
    assert result == SourceEntry.model_validate({
        "version": _VERSION,
        "commit": _COMMIT,
        "electronVersion": _ELECTRON_VERSION,
        "pins": _SOURCE_PINS,
        "urls": {
            "bun": urls["bunUrl"],
            "nodeAddonApi": urls["nodeAddonApiUrl"],
            "openchamber": urls["openchamberUrl"],
            "opencode": urls["opencodeUrl"],
            "sherpaOnnx": urls["sherpaOnnxUrl"],
            "sherpaOnnxNode": urls["sherpaOnnxNodeUrl"],
        },
        "hashes": HashCollection.from_value(entries),
    })


@pytest.mark.parametrize(
    ("helper", "args", "message"),
    [
        ("_require_object", ([],), "is not a JSON object"),
        ("_require_string", ({}, "version"), "is missing version"),
    ],
)
def test_openchamber_json_helpers_fail_closed(
    helper: str,
    args: tuple[object, ...],
    message: str,
) -> None:
    """Malformed upstream JSON must fail at the typed validation boundary."""
    module = _load_updater_module()
    kwargs = {"context": "release metadata"}
    with pytest.raises(TypeError, match=message):
        getattr(module, helper)(*args, **kwargs)


def test_openchamber_required_suppression_surface_manifest_is_complete() -> None:
    """A test-owned manifest must independently pin every mutation surface."""
    module = _load_patcher_module()
    records = [
        (
            item.surface,
            ("patch", item.component, item.relative_path, item.expected_count),
        )
        for item in module._PATCHES
    ]
    records.extend(
        (
            item.surface,
            ("anchor", item.component, item.relative_path, item.expected_count),
        )
        for item in module._ANCHORS
    )

    assert len(records) == len({surface for surface, _record in records})
    assert dict(records) == _REQUIRED_SUPPRESSION_SURFACES
    assert sum(record[3] for _surface, record in records) == 24


def test_openchamber_patcher_is_transactional_and_component_scoped(
    tmp_path: Path,
) -> None:
    """The policy patch must dry-run, apply atomically, and reject anchor drift."""
    module = _load_patcher_module()
    drifted = _write_patcher_fixture(module, tmp_path / "drifted")
    anchor = module._ANCHORS[0]
    anchor_path = drifted[anchor.component] / anchor.relative_path
    anchor_path.write_text(
        anchor_path.read_text(encoding="utf-8").replace(anchor.text, "", 1),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="managed-source anchor"):
        module.patch_trees(drifted["openchamber"], drifted["opencode"], check=True)

    roots = _write_patcher_fixture(module, tmp_path / "complete")
    before = {
        path: path.read_text(encoding="utf-8")
        for root in roots.values()
        for path in root.rglob("*")
        if path.is_file()
    }

    module.patch_trees(roots["openchamber"], roots["opencode"], check=True)
    assert {path: path.read_text(encoding="utf-8") for path in before} == before

    module.patch_trees(roots["openchamber"], roots["opencode"])
    for item in module._PATCHES:
        path = roots[item.component] / item.relative_path
        source = path.read_text(encoding="utf-8")
        assert source.count(item.new) == item.expected_count
        assert source.count(item.old) == item.new.count(item.old) * item.expected_count

    with pytest.raises(RuntimeError, match="managed-source patch anchor"):
        module.patch_trees(roots["openchamber"], roots["opencode"], check=True)

    scoped = _write_patcher_fixture(module, tmp_path / "scoped")
    opencode_before = {
        path: path.read_text(encoding="utf-8")
        for path in scoped["opencode"].rglob("*")
        if path.is_file()
    }
    module.patch_component("openchamber", scoped["openchamber"])
    assert {
        path: path.read_text(encoding="utf-8") for path in opencode_before
    } == opencode_before


def test_openchamber_patcher_cli_validates_root_arity(tmp_path: Path) -> None:
    """The patch CLI must distinguish complete and component-scoped invocations."""
    module = _load_patcher_module()
    roots = _write_patcher_fixture(module, tmp_path)
    assert (
        module.main([
            str(roots["openchamber"]),
            str(roots["opencode"]),
            "--check",
        ])
        == 0
    )
    assert (
        module.main([
            str(roots["openchamber"]),
            "--component",
            "openchamber",
            "--check",
        ])
        == 0
    )
    with pytest.raises(SystemExit) as component_error:
        module.main([
            str(roots["openchamber"]),
            str(roots["opencode"]),
            "--component",
            "openchamber",
        ])
    assert component_error.value.code == 2
    with pytest.raises(SystemExit) as complete_error:
        module.main([str(roots["openchamber"])])
    assert complete_error.value.code == 2


def test_openchamber_source_metadata_contains_promoted_exact_hashes() -> None:
    """Persist every promoted hash without weakening the package's gate boundary."""
    source = SourceEntry.model_validate_json(
        (_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8")
    )
    urls = _urls()
    expected_hashes = HashCollection.from_value([
        HashEntry.create(
            "srcHash",
            _PROMOTED_OPENCHAMBER_SRC_HASH,
            url=urls["openchamberUrl"],
        ),
        HashEntry.create(
            "srcHash",
            _PROMOTED_OPENCODE_SRC_HASH,
            url=urls["opencodeUrl"],
        ),
        HashEntry.create(
            "srcHash",
            _PROMOTED_SHERPA_ONNX_SRC_HASH,
            url=urls["sherpaOnnxUrl"],
        ),
        HashEntry.create("sha256", _BUN_HASH, url=urls["bunUrl"]),
        HashEntry.create(
            "nodeModulesHash",
            _OPENCODE_NODE_MODULES_HASH,
            platform="aarch64-darwin",
            url=urls["opencodeUrl"],
        ),
        HashEntry.create(
            "nodeModulesHash",
            _PROMOTED_OPENCHAMBER_NODE_MODULES_HASH,
            platform="aarch64-darwin",
            url=urls["openchamberUrl"],
        ),
        HashEntry.create(
            "sha256",
            _PROMOTED_NODE_ADDON_API_HASH,
            url=urls["nodeAddonApiUrl"],
        ),
        HashEntry.create(
            "sha256",
            _PROMOTED_SHERPA_ONNX_NODE_HASH,
            url=urls["sherpaOnnxNodeUrl"],
        ),
    ])
    assert source.version == _VERSION
    assert source.commit == _COMMIT
    assert source.electron_version == _ELECTRON_VERSION
    assert source.pins == _SOURCE_PINS
    assert source.hashes.equivalent_to(expected_hashes)

    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    condition = expect_instance(final.condition, BinaryExpression)
    assert condition.operator.name == "=="
    assert expect_instance(condition.left, Identifier).name == "unresolvedBuildGates"
    assert expect_instance(condition.right, NixList).value == []
    assert expect_instance(final.consequence, Identifier).name == "realPackage"
    assert expect_instance(final.alternative, Identifier).name == "blockedPackage"


def test_openchamber_derivations_consume_updater_owned_source_pins() -> None:
    """Every source identity in handwritten Nix must come from source metadata."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    for name, expression in {
        "bunVersion": "selfSource.pins.bunVersion",
        "openCodeCommit": "selfSource.pins.opencodeCommit",
        "openCodeVersion": "selfSource.pins.opencodeVersion",
        "sherpaCommit": "selfSource.pins.sherpaCommit",
        "sherpaVersion": "selfSource.pins.sherpaVersion",
        "sherpaWrapperVersion": "selfSource.pins.sherpaWrapperVersion",
    }.items():
        assert_nix_ast_equal(expect_binding(final.scope, name).value, expression)

    for source_name, revision in {
        "openChamberSrc": "selfSource.commit",
        "openCodeSrc": "openCodeCommit",
        "sherpaSrc": "sherpaCommit",
    }.items():
        source = expect_instance(
            expect_binding(final.scope, source_name).value,
            FunctionCall,
        )
        source_arguments = expect_instance(source.argument, AttributeSet)
        assert_nix_ast_equal(
            expect_binding(source_arguments.values, "rev").value,
            revision,
        )

    helper_contracts = {
        "bunExact": {"version": "bunVersion"},
        "openChamberNodeModules": {"bunVersion": "bunVersion"},
        "openCodeNodeModules": {
            "bunVersion": "bunVersion",
            "version": "openCodeVersion",
        },
        "sherpaNodeAddon": {
            "version": "sherpaVersion",
            "wrapperVersion": "sherpaWrapperVersion",
        },
    }
    for helper_name, expected_arguments in helper_contracts.items():
        helper = expect_instance(
            expect_binding(final.scope, helper_name).value,
            FunctionCall,
        )
        arguments = expect_instance(helper.argument, AttributeSet)
        for name, expression in expected_arguments.items():
            assert_nix_ast_equal(
                expect_binding(arguments.values, name).value,
                expression,
            )

    bun = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "bun.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    bun_derivation = expect_instance(bun.output, FunctionCall)
    assert_nix_ast_equal(
        expect_binding(
            expect_instance(bun_derivation.argument, AttributeSet).values,
            "version",
        ).value,
        "version",
    )

    for name in ("node-modules.nix", "opencode-node-modules.nix"):
        node_modules = expect_instance(
            parse_nix_expr((_PACKAGE_DIR / name).read_text(encoding="utf-8")),
            FunctionDefinition,
        )
        assertion = expect_instance(node_modules.output, Assertion)
        assert_nix_ast_equal(assertion.expression, "bun.version == bunVersion")

    sherpa = expect_instance(
        parse_nix_expr(
            (_PACKAGE_DIR / "sherpa-node-addon.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    sherpa_derivation = expect_instance(sherpa.output, FunctionCall)
    sherpa_attributes = expect_instance(sherpa_derivation.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(sherpa_attributes.values, "version").value,
        "version",
    )
    passthru = expect_instance(
        expect_binding(sherpa_attributes.values, "passthru").value,
        AttributeSet,
    )
    provenance = expect_instance(
        expect_binding(passthru.values, "runtimeProvenance").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(provenance.values, "addonSourceVersion").value,
        "version",
    )
    assert_nix_ast_equal(
        expect_binding(provenance.values, "wrapperSourceVersion").value,
        "wrapperVersion",
    )


def test_openchamber_nix_files_are_structurally_parseable() -> None:
    """Every package-local Nix expression must parse without evaluation or realization."""
    for path in sorted(_PACKAGE_DIR.glob("*.nix")):
        assert parse_nix_expr(path.read_text(encoding="utf-8")) is not None, Path(path)


def test_openchamber_node_modules_normalizes_bun_private_bin_links() -> None:
    """Bun's package-local bin-link drift must not change the fixed-output hash."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "node-modules.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    assertion = expect_instance(package.output, Assertion)
    derivation = expect_instance(assertion.body, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)
    build_phase = expect_instance(
        expect_binding(arguments.values, "buildPhase").value,
        IndentedString,
    )
    shell = parse_shell(indented_string_body(build_phase.rebuild()))
    normalization = (
        "find node_modules/.bun -path '*/node_modules/.bin' -type d -prune "
        "-exec rm -rf {} +"
    )

    assert command_texts(shell, "find") == [normalization]
    commands = command_texts(shell)
    assert commands.index(normalization) < commands.index("runHook postBuild")


def test_openchamber_opencode_resigns_its_final_bun_executable() -> None:
    """The wrapped Bun target must be signed after fixup and verified as arm64."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "opencode.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    derivation = expect_instance(package.output, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)

    post_fixup = expect_instance(
        expect_binding(arguments.values, "postFixup").value,
        IndentedString,
    )
    assert command_texts(
        parse_shell(indented_string_body(post_fixup.rebuild())),
        "/usr/bin/codesign",
    ) == ['/usr/bin/codesign --force --sign - "$wrappedExecutable"']

    install_check = expect_instance(
        expect_binding(arguments.values, "installCheckPhase").value,
        IndentedString,
    )
    install_check_commands = parse_shell(
        indented_string_body(install_check.rebuild()),
    )
    assert command_texts(install_check_commands, "/usr/bin/codesign") == [
        '/usr/bin/codesign --verify --strict --verbose=2 "$wrappedExecutable"',
    ]
    assert command_texts(install_check_commands, "/usr/bin/lipo") == [
        '/usr/bin/lipo -archs "$wrappedExecutable"',
    ]
    assert command_texts(install_check_commands, "test") == [
        'test -x "$wrappedExecutable"',
        'test "$(/usr/bin/lipo -archs "$wrappedExecutable")" = arm64',
        'test "$(HOME="$TMPDIR" "$out/bin/opencode" --version)" = "__NIX_INTERP__"',
    ]


def test_openchamber_uses_onnxruntime_soname_and_declares_store_linkage() -> None:
    """The copied ONNX library must match its SONAME and retain managed closure links."""
    addon = expect_instance(
        parse_nix_expr(
            (_PACKAGE_DIR / "sherpa-node-addon.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(addon.output, FunctionCall)
    runtime_libraries = expect_instance(
        expect_binding(derivation.scope, "runtimeLibraries").value,
        NixList,
    )
    onnx_library = expect_instance(runtime_libraries.value[1], AttributeSet)
    assert_nix_ast_equal(
        expect_binding(onnx_library.values, "name").value,
        '"libonnxruntime.1.dylib"',
    )
    assert_nix_ast_equal(
        expect_binding(onnx_library.values, "source").value,
        '"${lib.getLib onnxruntime}/lib/libonnxruntime.1.dylib"',
    )

    attributes = expect_instance(derivation.argument, AttributeSet)
    passthru = expect_instance(
        expect_binding(attributes.values, "passthru").value,
        AttributeSet,
    )
    provenance = expect_instance(
        expect_binding(passthru.values, "runtimeProvenance").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(provenance.values, "managedNixStoreDependencies").value,
        "true",
    )

    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    expected_paths = expect_instance(
        expect_binding(final.scope, "expectedNativeRelativePaths").value,
        NixList,
    )
    expected_path_values = {
        expect_instance(path, StringPrimitive).value for path in expected_paths.value
    }
    assert (
        "Contents/Resources/app.asar.unpacked/node_modules/"
        "sherpa-onnx-darwin-arm64/libonnxruntime.1.dylib" in expected_path_values
    )


def test_openchamber_uses_realized_workspace_cli_paths() -> None:
    """Build and verification CLIs must use paths emitted by Bun's workspace install."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    assert_nix_ast_equal(
        expect_binding(final.scope, "electronBuilderExecutable").value,
        '"./node_modules/.bin/electron-builder"',
    )
    assert_nix_ast_equal(
        expect_binding(final.scope, "asarExecutable").value,
        '"node_modules/.bun/node_modules/@electron/asar/bin/asar.js"',
    )


def test_openchamber_retains_only_source_built_node_pty_runtime() -> None:
    """Electron keeps node-pty's rebuilt outputs and excludes all vendor binaries."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)

    excluded_packages = expect_instance(
        expect_binding(final.scope, "electronExcludedRuntimePackages").value,
        NixList,
    )
    assert [
        expect_instance(item, StringPrimitive).value for item in excluded_packages.value
    ] == ["bun-pty"]

    discarded_subtrees = expect_instance(
        expect_binding(final.scope, "nodePtyDiscardedRuntimeSubtrees").value,
        NixList,
    )
    assert [
        expect_instance(item, StringPrimitive).value
        for item in discarded_subtrees.value
    ] == ["bin", "prebuilds"]

    forbidden_formats = expect_instance(
        expect_binding(final.scope, "forbiddenRuntimeBinaryFormats").value,
        NixList,
    )
    assert [
        expect_instance(item, StringPrimitive).value for item in forbidden_formats.value
    ] == ["*ELF*", "*PE32*"]

    expected_paths = expect_instance(
        expect_binding(final.scope, "expectedNativeRelativePaths").value,
        NixList,
    )
    node_pty_paths = {
        expect_instance(item, StringPrimitive).value
        for item in expected_paths.value
        if "/node-pty/" in expect_instance(item, StringPrimitive).value
    }
    assert node_pty_paths == {
        "Contents/Resources/app.asar.unpacked/node_modules/"
        "node-pty/build/Release/pty.node",
        "Contents/Resources/app.asar.unpacked/node_modules/"
        "node-pty/build/Release/spawn-helper",
    }


def test_openchamber_install_check_is_parseable_shell() -> None:
    """The builder's modern Bash must be able to parse the full install gate."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    real_package = expect_instance(
        expect_binding(final.scope, "realPackage").value,
        FunctionCall,
    )
    arguments = expect_instance(real_package.argument, AttributeSet)
    install_check = expect_instance(
        expect_binding(arguments.values, "installCheckPhase").value,
        IndentedString,
    )

    parse_shell(indented_string_body(install_check.rebuild()))


def test_openchamber_revalidates_even_current_metadata() -> None:
    """A current version must still refresh all exact-source evidence."""
    module = _load_updater_module()
    assert (
        run_async(module.OpenChamberUpdater()._is_latest(None, _version_info()))
        is False
    )


def test_openchamber_rejects_non_exact_release_metadata() -> None:
    """The audited package must fail closed on a new, unaudited release."""
    module = _load_updater_module()
    monkeypatch_target = "lib.update.updaters.github_release.fetch_github_api"

    async def latest(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {"tag_name": "v1.20.0"}

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(monkeypatch_target, latest)
        with pytest.raises(RuntimeError, match="release version must be '1.21.0'"):
            run_async(module.OpenChamberUpdater().fetch_latest(object()))


def test_openchamber_commit_metadata_must_be_immutable() -> None:
    """Mutable companion tag responses cannot enter the source graph."""
    module = _load_updater_module()

    async def commit(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {"sha": "main"}

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(module, "fetch_github_api", commit)
        with pytest.raises(RuntimeError, match="is not an immutable commit"):
            run_async(
                module.OpenChamberUpdater()._resolve_tag_commit(
                    object(),
                    owner="anomalyco",
                    repo="opencode",
                    tag=f"v{_OPENCODE_VERSION}",
                    config=module.OpenChamberUpdater().config,
                )
            )


def test_openchamber_manifest_validation_rejects_prebuilt_drift() -> None:
    """The lock must keep the audited sherpa wrapper/platform split exact."""
    module = _load_updater_module()
    updater = module.OpenChamberUpdater
    drifted = _lock_text().replace(
        "sherpa-onnx-darwin-arm64@1.13.3",
        "sherpa-onnx-darwin-arm64@1.13.4",
    )
    with pytest.raises(RuntimeError, match="locked sherpa-onnx-darwin-arm64"):
        updater._validate_openchamber_manifests(
            root_payload={
                "version": _VERSION,
                "packageManager": "bun@1.3.14",
                "engines": {"node": ">=22.0.0"},
            },
            electron_payload={
                "version": _VERSION,
                "build": {
                    "appId": "dev.openchamber.desktop",
                    "productName": "OpenChamber",
                },
                "devDependencies": {"electron": "^43.3.0"},
            },
            web_payload={
                "version": _VERSION,
                "dependencies": {
                    "@opencode-ai/sdk": _OPENCODE_VERSION,
                    "sherpa-onnx-node": "1.12.28",
                },
            },
            lock_text=drifted,
        )


def test_openchamber_missing_hash_event_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The updater cannot promote metadata when any fixed-output probe is empty."""
    module = _load_updater_module()
    install_fixed_hash_stream(monkeypatch, ((None, object()),))
    with pytest.raises(TypeError, match="Expected string payload"):
        run_async(
            collect_events(
                module.OpenChamberUpdater().fetch_hashes(_version_info(), object())
            )
        )


def test_openchamber_async_helpers_do_not_leak_tasks() -> None:
    """Focused updater tests leave the event loop quiescent."""

    async def pending() -> set[asyncio.Task[object]]:
        current = asyncio.current_task()
        return {
            cast("asyncio.Task[object]", task)
            for task in asyncio.all_tasks()
            if task is not current and not task.done()
        }

    assert run_async(pending()) == set()
