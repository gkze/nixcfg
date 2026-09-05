"""Tests for the OpenCode Desktop updater module."""

import json
from types import ModuleType

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.flake_lock import FlakeLockNode
from lib.nix.models.sources import SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.config import default_config
from lib.update.electron_manifest import ElectronManifestMetadata
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo

_COMMIT = "a" * 40
_MANIFEST_VERSION = "1.2.3"
_ELECTRON_SPEC = "^42.0.0"
_ELECTRON_VERSION = "42.3.3"


def _load_updater_module() -> ModuleType:
    """Load the updater module under test."""
    return load_repo_module(
        "packages/opencode-desktop/updater.py",
        "opencode_desktop_updater_test",
    )


def _flake_node() -> FlakeLockNode:
    return FlakeLockNode.model_validate({
        "locked": {
            "type": "github",
            "owner": "example",
            "repo": "opencode",
            "rev": _COMMIT,
            "narHash": "sha256-source",
        },
        "original": {
            "type": "github",
            "owner": "example",
            "repo": "opencode",
            "ref": "main",
        },
    })


def _manifest(
    *,
    name: str = "@opencode-ai/desktop",
    version: str = _MANIFEST_VERSION,
    electron_spec: str = _ELECTRON_SPEC,
) -> dict[str, object]:
    return {
        "name": name,
        "version": version,
        "devDependencies": {"electron": electron_spec},
    }


def _lock_payload(
    *,
    workspace_path: str = "packages/desktop",
    additional_workspace_path: str | None = None,
    name: str = "@opencode-ai/desktop",
    version: str = _MANIFEST_VERSION,
    electron_spec: str = _ELECTRON_SPEC,
    resolution: object = f"electron@{_ELECTRON_VERSION}",
) -> bytes:
    workspace = {
        "name": name,
        "version": version,
        "devDependencies": {"electron": electron_spec},
    }
    workspaces = {workspace_path: workspace}
    if additional_workspace_path is not None:
        workspaces[additional_workspace_path] = workspace
    return json.dumps({
        "lockfileVersion": 1,
        "workspaces": workspaces,
        "packages": {
            "electron": [resolution, "", {}],
        },
    }).encode()


def _mock_locked_source(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    manifest: object,
    lock_payload: bytes,
) -> list[tuple[str, str, int, str]]:
    reads: list[tuple[str, str, int, str]] = []

    class _Source:
        async def read_bytes(
            self,
            relative_path: str,
            *,
            max_bytes: int,
            description: str,
        ) -> bytes:
            reads.append(("bytes", relative_path, max_bytes, description))
            return lock_payload

        async def read_json(
            self,
            relative_path: str,
            *,
            max_bytes: int,
            description: str,
        ) -> object:
            reads.append(("json", relative_path, max_bytes, description))
            return manifest

    async def _resolve_locked_source(
        node: FlakeLockNode,
        *,
        context: str,
        command_timeout: float,
    ) -> _Source:
        assert node == _flake_node()
        assert context == "OpenCode Desktop flake input"
        assert command_timeout == default_config().default_subprocess_timeout
        return _Source()

    monkeypatch.setattr(module, "resolve_locked_source", _resolve_locked_source)
    return reads


def _fetch_latest(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    manifest: object,
    lock_payload: bytes,
) -> VersionInfo:
    node = _flake_node()
    monkeypatch.setattr(
        "lib.update.flake.get_flake_input_node",
        lambda input_name: node if input_name == "opencode" else None,
    )

    _mock_locked_source(
        module,
        monkeypatch,
        manifest=manifest,
        lock_payload=lock_payload,
    )
    return _run(module.OpencodeDesktopUpdater().fetch_latest(object()))


def test_opencode_desktop_uses_the_lockfiles_exact_electron_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A manifest range must not be mistaken for the installed runtime."""
    module = _load_updater_module()
    node = _flake_node()
    workspace_path = "apps/desktop-shell"

    monkeypatch.setattr(
        "lib.update.flake.get_flake_input_node",
        lambda input_name: node if input_name == "opencode" else None,
    )

    reads = _mock_locked_source(
        module,
        monkeypatch,
        manifest=_manifest(),
        lock_payload=_lock_payload(workspace_path=workspace_path),
    )

    updater = module.OpencodeDesktopUpdater()
    info = _run(updater.fetch_latest(object()))
    result = updater.build_result(info, [])

    assert info.metadata == ElectronManifestMetadata(
        node=node,
        commit=_COMMIT,
        electron_version=_ELECTRON_VERSION,
        manifest_path=f"{workspace_path}/package.json",
        manifest_version=_MANIFEST_VERSION,
    )
    assert result.electron_version == _ELECTRON_VERSION
    assert result.pins == {"desktopWorkspace": workspace_path}
    with pytest.raises(TypeError, match="resolved Electron manifest"):
        updater.build_result(VersionInfo(version="main"), [])
    with pytest.raises(RuntimeError, match="invalid manifest path"):
        updater.build_result(
            VersionInfo(
                version="main",
                metadata=ElectronManifestMetadata(
                    node=node,
                    commit=_COMMIT,
                    electron_version=_ELECTRON_VERSION,
                    manifest_path="package.json",
                    manifest_version=_MANIFEST_VERSION,
                ),
            ),
            [],
        )
    assert reads == [
        ("bytes", "bun.lock", module._MAX_LOCK_BYTES, "bun.lock"),
        (
            "json",
            f"{workspace_path}/package.json",
            module._MAX_MANIFEST_BYTES,
            "package manifest",
        ),
    ]


@pytest.mark.parametrize(
    ("spec", "version"),
    [
        ("42.3.3", "42.3.3"),
        ("^42.0.0", "42.3.3"),
        ("~42.3.0", "42.3.3"),
        ("^0.2.3", "0.2.9"),
        ("^0.0.3", "0.0.3"),
    ],
)
def test_opencode_desktop_electron_specs_are_checked_semantically(
    spec: str,
    version: str,
) -> None:
    """Accept equivalent exact, caret, and tilde source-owned constraints."""
    module = _load_updater_module()

    module._validate_manifest_contract(
        _manifest(electron_spec=spec),
        module._lock_contract(
            _lock_payload(
                electron_spec=spec,
                resolution=f"electron@{version}",
            )
        ),
    )


@pytest.mark.parametrize(
    ("manifest", "lock_payload", "exception", "message"),
    [
        (
            _manifest(name="@opencode-ai/other"),
            _lock_payload(),
            RuntimeError,
            "workspace name",
        ),
        (
            _manifest(),
            _lock_payload(version="9.9.9"),
            RuntimeError,
            "workspace version",
        ),
        (
            _manifest(),
            _lock_payload(electron_spec="^41.0.0"),
            RuntimeError,
            "Electron spec mismatch",
        ),
        (
            _manifest(),
            _lock_payload(resolution="electron@43.0.0"),
            RuntimeError,
            "does not satisfy",
        ),
        (
            _manifest(electron_spec="latest"),
            _lock_payload(
                electron_spec="latest",
                resolution=f"electron@{_ELECTRON_VERSION}",
            ),
            RuntimeError,
            "valid npm semantic-version range",
        ),
        (
            _manifest(),
            _lock_payload(resolution=None),
            TypeError,
            "no string resolution",
        ),
        (
            _manifest(),
            _lock_payload(resolution=f"runtime@{_ELECTRON_VERSION}"),
            RuntimeError,
            "resolution is malformed",
        ),
        (
            _manifest(),
            _lock_payload(resolution=f"electron@^{_ELECTRON_VERSION}"),
            RuntimeError,
            "exact semantic version",
        ),
        (
            _manifest(),
            _lock_payload(name="@opencode-ai/other"),
            RuntimeError,
            "exactly one .* workspace, found 0",
        ),
        (
            _manifest(),
            _lock_payload(additional_workspace_path="apps/second-desktop"),
            RuntimeError,
            "exactly one .* workspace, found 2",
        ),
    ],
)
def test_opencode_desktop_rejects_inconsistent_manifest_and_lock_metadata(
    monkeypatch: pytest.MonkeyPatch,
    manifest: dict[str, object],
    lock_payload: bytes,
    exception: type[Exception],
    message: str,
) -> None:
    """Fail closed before inconsistent upstream metadata reaches sources.json."""
    module = _load_updater_module()

    with pytest.raises(exception, match=message):
        _fetch_latest(
            module,
            monkeypatch,
            manifest=manifest,
            lock_payload=lock_payload,
        )


def test_opencode_desktop_rejects_a_non_utf8_lockfile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat an undecodable immutable lockfile as invalid source metadata."""
    module = _load_updater_module()

    with pytest.raises(RuntimeError, match="not valid UTF-8"):
        _fetch_latest(
            module,
            monkeypatch,
            manifest=_manifest(),
            lock_payload=b"\xff",
        )


def test_opencode_desktop_darwin_build_exposes_only_system_codesign() -> None:
    """Upstream prepare scripts should find Apple's signer without broadening PATH."""
    package = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/opencode-desktop/default.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )
    output = package.output
    while isinstance(output, Assertion):
        output = output.body
    derivation = expect_instance(output, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)
    build_phase = expect_instance(
        expect_binding(arguments.values, "buildPhase").value,
        IfExpression,
    )
    shell = parse_shell(indented_string_body(build_phase.consequence.rebuild()))

    assert command_texts(shell, "ln") == [
        'ln -s /usr/bin/codesign "$codesignPath/codesign"'
    ]
    assert 'export PATH="$codesignPath:$PATH"' in command_texts(shell, "export")
    assert 'export PATH="/usr/bin:$PATH"' not in command_texts(shell, "export")


def test_opencode_desktop_consumes_the_updater_resolved_electron_version() -> None:
    """Nix must use updater-derived runtime and workspace metadata."""
    package = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/opencode-desktop/default.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )

    assert_nix_ast_equal(
        expect_binding(package.output.scope, "electronVersion").value,
        "selfSource.electronVersion",
    )
    assert_nix_ast_equal(
        expect_binding(package.output.scope, "desktopPackagePath").value,
        """
        (selfSource.pins or { }).desktopWorkspace
          or (throw "packages/opencode-desktop/default.nix is missing its updater-derived desktop workspace")
        """,
    )


def test_opencode_desktop_updater_tracks_all_supported_platform_hashes() -> None:
    """The updater should preserve the full persisted platform hash matrix."""
    updater_cls = _load_updater_module().OpencodeDesktopUpdater
    payload = json.loads(
        (REPO_ROOT / "packages/opencode-desktop/sources.json").read_text(
            encoding="utf-8"
        )
    )

    assert updater_cls.input_name == "opencode"
    assert updater_cls.hash_type == "nodeModulesHash"
    assert updater_cls.platform_specific is True
    assert updater_cls.native_only is False
    hashes = payload.get("hashes")
    assert isinstance(hashes, list)
    assert {
        entry["platform"]
        for entry in hashes
        if entry.get("hashType") == updater_cls.hash_type
    } == set(default_config().hash_build_platforms)


def test_opencode_desktop_platform_targets_dedupes_current_platform() -> None:
    """The current platform should not be duplicated when already supported."""
    updater = _load_updater_module().OpencodeDesktopUpdater()
    configured = updater.config.hash_build_platforms

    assert updater._platform_targets("x86_64-linux") == (
        "x86_64-linux",
        *(platform for platform in configured if platform != "x86_64-linux"),
    )


@pytest.mark.parametrize(
    ("base_latest", "current", "expected"),
    [
        pytest.param(False, None, False, id="superclass-false"),
        pytest.param(True, None, False, id="missing-entry"),
        pytest.param(
            True,
            SourceEntry.model_validate({
                "version": "1.2.3",
                "hashes": [
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                        "platform": "aarch64-darwin",
                    },
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                        "platform": "aarch64-linux",
                    },
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
                        "platform": "x86_64-linux",
                    },
                    {
                        "hashType": "sha256",
                        "hash": "sha256-DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD=",
                        "platform": "x86_64-linux",
                    },
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE=",
                    },
                ],
            }),
            True,
            id="entries-match-supported-platforms",
        ),
        pytest.param(
            True,
            SourceEntry.model_validate({
                "version": "1.2.3",
                "hashes": {
                    "aarch64-darwin": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                    "aarch64-linux": "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                    "x86_64-linux": "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
                },
            }),
            True,
            id="mapping-match-supported-platforms",
        ),
        pytest.param(
            True,
            SourceEntry.model_validate({
                "version": "1.2.3",
                "hashes": [
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                        "platform": "aarch64-darwin",
                    },
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                        "platform": "aarch64-linux",
                    },
                ],
            }),
            False,
            id="entries-mismatch-missing-platform",
        ),
    ],
)
def test_opencode_desktop_is_latest_validates_platform_hash_coverage(
    monkeypatch: pytest.MonkeyPatch,
    base_latest: bool,
    current: SourceEntry | None,
    expected: bool,
) -> None:
    """Latest checks require a base match and a complete supported-platform set."""
    module = _load_updater_module()
    updater = module.OpencodeDesktopUpdater()

    async def _base_is_latest(self, context, info):
        _ = (self, context, info)
        return base_latest

    monkeypatch.setattr(module.FlakeInputHashUpdater, "_is_latest", _base_is_latest)

    assert _run(updater._is_latest(current, VersionInfo(version="1.2.3"))) is expected
