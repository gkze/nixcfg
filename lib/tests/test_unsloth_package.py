"""Static contracts for the deliberately gated Unsloth source build."""

import ast
import importlib.metadata
import importlib.util
import io
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from nix_manipulator import parse
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.inherit import Inherit
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    binding_map,
    expect_binding,
    parse_nix_expr,
)
from lib.tests._nix_source import nix_file_expr, nix_source_fragment_expr
from lib.tests._package_registry import registry_override_metadata
from lib.tests._shell_ast import (
    command_texts,
    indented_string_body,
    iter_nodes,
    node_text,
    parse_shell,
)
from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from nix_manipulator.expressions.expression import NixExpression
    from nix_manipulator.expressions.scope import Scope

_PACKAGE_DIR = REPO_ROOT / "packages/unsloth"
_NIX_FILES = (
    "backend.nix",
    "desktop.nix",
    "frontend.nix",
    "llama-cpp.nix",
    "oxc-node-modules.nix",
    "package.nix",
    "stable-diffusion-cpp.nix",
    "whisper-cpp.nix",
)
_SELECTORS = (
    "nodeRuntimeContract",
    "oxcNodeModules",
    "oxcNodeModules",
    "frontend",
    "frontend",
    "stableDiffusionSource",
    "llamaCpp",
    "whisperCpp",
    "stableDiffusionCpp",
    "backend",
    "appCandidate",
    "appCandidate",
    "storePathAppCandidateSmoke",
)
_SENTINEL_TEST_PID = 49061
_SENTINEL_TEST_PORT = 49152


def _sentinel_listener(
    validator: ModuleType,
    *,
    address: str | None = None,
    pid: int = _SENTINEL_TEST_PID,
):
    return validator.Listener(
        pid,
        "Python",
        address or f"127.0.0.1:{_SENTINEL_TEST_PORT}",
        _SENTINEL_TEST_PORT,
    )


def _sentinel_baseline(validator: ModuleType):
    identity = (
        (
            _SENTINEL_TEST_PID,
            "Python",
            f"127.0.0.1:{_SENTINEL_TEST_PORT}",
        ),
    )
    return validator.SentinelBaseline(
        port=_SENTINEL_TEST_PORT,
        identity=identity,
        identity_sha256="a" * 64,
    )


class _FakeSentinelSocket:
    def __init__(
        self,
        port: object,
        *,
        bind_error: OSError | None = None,
        listen_error: OSError | None = None,
        close_error: OSError | None = None,
    ) -> None:
        self.port = port
        self.bind_error = bind_error
        self.listen_error = listen_error
        self.close_error = close_error
        self.bound_to: tuple[str, int] | None = None
        self.listen_backlog: int | None = None
        self.closed = False

    def bind(self, address: tuple[str, int]) -> None:
        self.bound_to = address
        if self.bind_error is not None:
            raise self.bind_error

    def getsockname(self) -> tuple[str, object]:
        return "127.0.0.1", self.port

    def listen(self, backlog: int) -> None:
        self.listen_backlog = backlog
        if self.listen_error is not None:
            raise self.listen_error

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


def _json(name: str) -> object:
    return json.loads((_PACKAGE_DIR / name).read_text(encoding="utf-8"))


def _indented_string_interpolations(value: IndentedString) -> list[NixExpression]:
    source = value.rebuild()
    parsed = parse(source)
    assert parsed.contains_error is False
    source_bytes = source.encode("utf-8")
    return [
        parse_nix_expr(source_bytes[node.start_byte + 2 : node.end_byte - 1].decode())
        for node in iter_nodes(parsed.node, "interpolation")
    ]


def _runtime_validator_module() -> ModuleType:
    path = _PACKAGE_DIR / "validate_store_runtime.py"
    spec = importlib.util.spec_from_file_location("unsloth_store_runtime", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _backend_python_audit_source(name: str) -> str:
    backend = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "backend.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    derivation = expect_instance(
        expect_instance(backend.output, Assertion).body,
        FunctionCall,
    )
    audit = expect_instance(
        expect_binding(derivation.scope, name).value,
        IndentedString,
    )
    return textwrap.dedent(indented_string_body(audit.rebuild())).replace(
        "${backendVersion}",
        "__NIX_INTERP__",
    )


def _backend_oxc_smoke_audit_source() -> str:
    return _backend_python_audit_source("oxcSmokeAudit")


def _backend_version_audit_source() -> str:
    return _backend_python_audit_source("backendVersionAudit")


def _run_backend_oxc_smoke_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    valid: object,
    invalid: object,
) -> None:
    valid_path = tmp_path / "valid.json"
    invalid_path = tmp_path / "invalid.json"
    valid_path.write_text(json.dumps(valid), encoding="utf-8")
    invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["oxc-smoke-audit", str(valid_path), str(invalid_path)],
    )
    exec(  # noqa: S102 -- executes the repository-owned build audit under test
        compile(_backend_oxc_smoke_audit_source(), "<oxc-smoke-audit>", "exec"),
        {"__name__": "oxc_smoke_audit"},
    )


def _backend_desktop_capabilities_audit_source() -> str:
    return _backend_python_audit_source("desktopCapabilitiesAudit")


def _run_backend_desktop_capabilities_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
) -> None:
    payload_path = tmp_path / "desktop-capabilities.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["desktop-capabilities-audit", str(payload_path)],
    )
    exec(  # noqa: S102 -- executes the repository-owned build audit under test
        compile(
            _backend_desktop_capabilities_audit_source(),
            "<desktop-capabilities-audit>",
            "exec",
        ),
        {"__name__": "desktop_capabilities_audit"},
    )


def _package_scope() -> Scope:
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    output = expect_instance(package.output, Assertion).body
    return output.scope


def _store_smoke_python_tree() -> ast.Module:
    source = expect_instance(
        expect_binding(_package_scope(), "storePathSmokePython").value,
        IndentedString,
    )
    return ast.parse(textwrap.dedent(indented_string_body(source.rebuild())))


def _derivation_arguments(name: str) -> AttributeSet:
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / name).read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    output = expect_instance(package.output, Assertion).body
    derivation = expect_instance(output, FunctionCall)
    return expect_instance(derivation.argument, AttributeSet)


def _select_path(expression: Identifier | Select) -> tuple[str, ...]:
    attributes: list[str] = []
    while isinstance(expression, Select):
        attributes.append(expression.attribute)
        parent = expression.expression
        assert isinstance(parent, Identifier | Select)
        expression = parent
    return (expression.name, *reversed(attributes))


def test_unsloth_source_build_leaves_are_parseable() -> None:
    """Every audited leaf must remain parseable after package promotion."""
    for name in _NIX_FILES:
        package = expect_instance(
            parse_nix_expr((_PACKAGE_DIR / name).read_text(encoding="utf-8")),
            FunctionDefinition,
        )
        assert package.output is not None


def test_unsloth_backend_workspace_identity_uses_only_python_project_inputs() -> None:
    """Evidence and export files beside the lock must not rebuild the backend."""
    backend = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "backend.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    derivation = expect_instance(
        expect_instance(backend.output, Assertion).body,
        FunctionCall,
    )
    workspace = expect_instance(
        expect_binding(derivation.scope, "workspace").value,
        FunctionCall,
    )
    workspace_arguments = expect_instance(workspace.argument, AttributeSet)

    workspace_root = expect_binding(workspace_arguments.values, "workspaceRoot").value
    assert_nix_ast_equal(
        f"{{ lib }}: {workspace_root.rebuild()}",
        """
{ lib }:
lib.fileset.toSource {
  root = pythonWorkspaceRoot;
  fileset = lib.fileset.unions [
    (pythonWorkspaceRoot + "/pyproject.toml")
    (pythonWorkspaceRoot + "/uv.lock")
  ];
}
""",
    )


def test_unsloth_backend_derives_the_validator_path_without_importing_studio() -> None:
    """The venv layout owns this path; runtime imports are unnecessary here."""
    backend = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "backend.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    derivation = expect_instance(
        expect_instance(backend.output, Assertion).body,
        FunctionCall,
    )
    script = expect_instance(derivation.argument, IndentedString)
    shell = parse_shell(indented_string_body(script.rebuild()))
    validator_assignments = [
        node
        for node in iter_nodes(shell.tree.root_node, "variable_assignment")
        if node_text(node, shell.sanitized).startswith("validator=")
    ]

    assert len(validator_assignments) == 1
    validator_assignment = validator_assignments[0]
    assert not list(iter_nodes(validator_assignment, "command_substitution"))
    assert node_text(validator_assignment, shell.sanitized) == (
        'validator="__NIX_INTERP__/__NIX_INTERP__/studio/backend/core/'
        'data_recipe/oxc-validator/validate.mjs"'
    )


def test_unsloth_backend_avoids_pipe_backed_python_heredocs_on_darwin() -> None:
    """Bash 5.3 can deadlock before forking when Darwin yields 512-byte pipes."""
    backend = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "backend.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    derivation = expect_instance(
        expect_instance(backend.output, Assertion).body,
        FunctionCall,
    )
    script = expect_instance(derivation.argument, IndentedString)
    shell = parse_shell(indented_string_body(script.rebuild()))

    assert not list(iter_nodes(shell.tree.root_node, "heredoc_body"))
    assert [
        command
        for command in command_texts(shell)
        if command.startswith("__NIX_INTERP__/bin/python")
    ] == [
        "__NIX_INTERP__/bin/python -c __NIX_INTERP__ \\\n"
        '      "$TMPDIR/oxc-valid.json" "$TMPDIR/oxc-invalid.json"',
        "__NIX_INTERP__/bin/python -c __NIX_INTERP__",
        "__NIX_INTERP__/bin/python -c __NIX_INTERP__ \\\n"
        '      "$TMPDIR/desktop-capabilities.json"',
    ]
    escaped_audits = [
        expect_instance(expression.argument, Identifier).name
        for expression in _indented_string_interpolations(script)
        if isinstance(expression, FunctionCall)
        and isinstance(expression.name, Identifier | Select)
        and _select_path(expression.name) == ("lib", "escapeShellArg")
    ]
    assert escaped_audits == [
        "oxcSmokeAudit",
        "backendVersionAudit",
        "desktopCapabilitiesAudit",
    ]


def test_unsloth_default_wrapper_imports_the_audited_package() -> None:
    """Discovery must expose the already-gated package without another code path."""
    assert_nix_ast_equal(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        "import ./package.nix",
    )


def test_unsloth_registry_exports_only_on_arm64_darwin() -> None:
    """Discovery metadata must retain the candidate's audited target platform."""
    registry = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/registry.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    registry_output = expect_instance(registry.output, AttributeSet)

    assert registry_override_metadata(registry_output)["unsloth"] == {
        "constraint": ["aarch64-darwin"]
    }


def test_unsloth_binary_darwin_overlay_exports_the_audited_package() -> None:
    """The shared overlay must expose the same source-backed package path."""
    overlay = expect_instance(
        nix_file_expr("overlays/binary-darwin-apps.nix"),
        FunctionDefinition,
    )
    exports = expect_instance(overlay.output, AttributeSet)

    assert_nix_ast_equal(
        expect_binding(exports.values, "unsloth").value,
        'callDarwinAppPackage "unsloth"',
    )


def test_unsloth_is_routed_as_a_system_application() -> None:
    """The promoted package must replace the unmanaged /Applications bundle."""
    routing = expect_instance(
        nix_source_fragment_expr(
            "home/george/work.nix",
            "  routing = ",
            ";\n  projection =",
        ),
        AttributeSet,
    )

    unsloth_route = expect_instance(
        expect_binding(routing.values, "unsloth").value,
        FunctionCall,
    )
    assert_nix_ast_equal(unsloth_route.name, "systemApp")
    assert unsloth_route.argument is not None
    assert_nix_ast_equal(unsloth_route.argument, "pkgs.unsloth")


def test_unsloth_desktop_cargo_consistency_patches_are_shared_with_vendoring() -> None:
    """Cargo vendoring must consume the source-derived consistency patch."""
    desktop = _derivation_arguments("desktop.nix")
    assert_nix_ast_equal(
        expect_binding(desktop.values, "cargoPatches").value,
        "[ cargoConsistencyPatch ]",
    )

    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "desktop.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    output = expect_instance(package.output, Assertion).body
    cargo_patch = expect_instance(
        expect_binding(output.scope, "cargoConsistencyPatch").value,
        FunctionCall,
    )
    assert_nix_ast_equal(
        cargo_patch.name,
        """runCommand "unsloth-${version}-cargo-consistency.patch" {
          nativeBuildInputs = [ diffutils python3 ];
        }""",
    )
    assert_nix_ast_equal(
        expect_instance(cargo_patch.argument, IndentedString),
        r"""''
          for tree in before after; do
            mkdir -p "$tree/studio/src-tauri"
            cp ${src}/studio/src-tauri/Cargo.toml "$tree/studio/src-tauri/Cargo.toml"
            cp ${src}/studio/src-tauri/Cargo.lock "$tree/studio/src-tauri/Cargo.lock"
            chmod -R u+w "$tree"
          done

          PYTHONPATH=${patchSupport} ${lib.getExe python3} \
            ${patchSupport}/packages/unsloth/patch_nix_managed.py after \
            --cargo-only --desktop-version ${lib.escapeShellArg version}

          : > "$out"
          for cargoFile in Cargo.toml Cargo.lock; do
            diffStatus=0
            diff -u \
              --label "a/studio/src-tauri/$cargoFile" \
              --label "b/studio/src-tauri/$cargoFile" \
              "before/studio/src-tauri/$cargoFile" \
              "after/studio/src-tauri/$cargoFile" >> "$out" || diffStatus=$?
            test "$diffStatus" -le 1
          done
        ''""",
    )


def test_unsloth_backend_oxc_smoke_accepts_one_result_per_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The build audit must consume the list schema emitted by validate.mjs."""
    _run_backend_oxc_smoke_audit(
        tmp_path,
        monkeypatch,
        valid=[{"is_valid": True}],
        invalid=[{"is_valid": False}],
    )


@pytest.mark.parametrize(
    ("valid", "invalid", "message"),
    [
        ({}, [{"is_valid": False}], "valid OXC result must be a one-element list"),
        ([], [{"is_valid": False}], "valid OXC result must be a one-element list"),
        (
            [{"is_valid": True}, {"is_valid": True}],
            [{"is_valid": False}],
            "valid OXC result must be a one-element list",
        ),
        (
            [True],
            [{"is_valid": False}],
            "valid OXC result item must be a mapping",
        ),
        ([{"is_valid": True}], {}, "invalid OXC result must be a one-element list"),
        ([{"is_valid": True}], [], "invalid OXC result must be a one-element list"),
        (
            [{"is_valid": True}],
            [{"is_valid": False}, {"is_valid": False}],
            "invalid OXC result must be a one-element list",
        ),
        (
            [{"is_valid": True}],
            [False],
            "invalid OXC result item must be a mapping",
        ),
    ],
)
def test_unsloth_backend_oxc_smoke_rejects_malformed_result_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    valid: object,
    invalid: object,
    message: str,
) -> None:
    """Every one-code audit response must contain exactly one result mapping."""
    with pytest.raises(SystemExit, match=message):
        _run_backend_oxc_smoke_audit(
            tmp_path,
            monkeypatch,
            valid=valid,
            invalid=invalid,
        )


def test_unsloth_backend_requires_the_selected_python_package_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The version audit accepts the selected release and rejects drift."""
    source = _backend_version_audit_source()
    calls: list[str] = []

    def selected_version(name: str) -> str:
        calls.append(name)
        return "__NIX_INTERP__"

    monkeypatch.setattr(importlib.metadata, "version", selected_version)
    exec(  # noqa: S102 -- executes the repository-owned build audit under test
        compile(source, "<backend-version-audit>", "exec"),
        {"__name__": "backend_version_audit"},
    )
    assert calls == ["unsloth"]

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "drifted")
    with pytest.raises(SystemExit, match="unexpected Unsloth backend version"):
        exec(  # noqa: S102 -- executes the repository-owned build audit under test
            compile(source, "<backend-version-audit>", "exec"),
            {"__name__": "backend_version_audit"},
        )


def test_unsloth_backend_requires_nix_managed_desktop_capabilities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The backend build must prove Tauri sees the immutable closure as ready."""
    expected = {
        "desktop_manageability_version": 2,
        "desktop_protocol_version": 1,
        "studio_install_ok": True,
        "studio_install_reason": None,
        "supports_api_only": True,
        "supports_desktop_backend_ownership": True,
        "supports_provision_desktop_auth": True,
        "version": "__NIX_INTERP__",
    }
    _run_backend_desktop_capabilities_audit(tmp_path, monkeypatch, expected)
    with pytest.raises(SystemExit, match="desktop-capabilities contract mismatch"):
        _run_backend_desktop_capabilities_audit(
            tmp_path,
            monkeypatch,
            expected | {"studio_install_ok": False},
        )


def test_unsloth_uses_nixpkgs_node_24_and_smokes_its_installed_clis() -> None:
    """The selected nixpkgs Node must report its package version and usable CLIs."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    assert Identifier(name="nodejs-slim_24") not in package.argument_set
    scope = _package_scope()
    assert "nodeSource" not in binding_map(scope)
    assert "nodejsRecipe" not in binding_map(scope)
    assert_nix_ast_equal(expect_binding(scope, "nodejs").value, "nodejs_24")
    assert_nix_ast_equal(
        expect_binding(scope, "nodeVersion").value,
        "lib.getVersion nodejs",
    )

    backend = expect_instance(expect_binding(scope, "backend").value, FunctionCall)
    backend_arguments = expect_instance(backend.argument, AttributeSet)
    backend_inherit = expect_instance(
        next(value for value in backend_arguments.values if isinstance(value, Inherit)),
        Inherit,
    )
    assert "nodeRuntimeContract" not in {
        expect_instance(name, Identifier).name for name in backend_inherit.names
    }

    closure_passthru = expect_instance(
        expect_binding(scope, "closurePassthru").value,
        AttributeSet,
    )
    closure_inherit = expect_instance(
        next(value for value in closure_passthru.values if isinstance(value, Inherit)),
        Inherit,
    )
    assert "nodeRuntimeContract" in {
        expect_instance(name, Identifier).name for name in closure_inherit.names
    }
    contract = expect_instance(
        expect_binding(scope, "nodeRuntimeContract").value,
        FunctionCall,
    )
    contract_with_attrs = expect_instance(contract.name, FunctionCall)
    contract_name = expect_instance(contract_with_attrs.name, FunctionCall)
    assert expect_instance(contract_name.name, Identifier).name == "runCommand"
    assert expect_instance(contract_name.argument, StringPrimitive).value == (
        "unsloth-node-${nodeVersion}-runtime-contract"
    )
    contract_attrs = expect_instance(contract_with_attrs.argument, AttributeSet)
    native_inputs = expect_instance(
        expect_binding(contract_attrs.values, "nativeBuildInputs").value,
        NixList,
    )
    assert [expect_instance(item, Identifier).name for item in native_inputs.value] == [
        "nodejs"
    ]
    contract_script = expect_instance(contract.argument, IndentedString)
    shell = parse_shell(indented_string_body(contract_script.rebuild()))
    assert command_texts(shell) == [
        "test -x __NIX_INTERP__/bin/node",
        "test -x __NIX_INTERP__/bin/npm",
        "test -x __NIX_INTERP__/bin/npx",
        "__NIX_INTERP__/bin/node --version",
        'test "$actualNodeVersion" = "v__NIX_INTERP__"',
        "__NIX_INTERP__/bin/npm --version",
        'test -n "$actualNpmVersion"',
        "__NIX_INTERP__/bin/npx --version",
        'test -n "$actualNpxVersion"',
        'mkdir -p "$out"',
        "printf '%s\\n' \"$actualNodeVersion\"",
        "printf '%s\\n' \"$actualNpmVersion\"",
        "printf '%s\\n' \"$actualNpxVersion\"",
    ]


def test_unsloth_cargo_vendoring_does_not_overclaim_the_reference_archive() -> None:
    """The audited archive is provenance; cargoHash remains the build authority."""
    scope = _package_scope()
    assert "fixPathEnvSource" not in binding_map(scope)

    runtime_sources = _json("runtime-sources.json")
    assert isinstance(runtime_sources, dict)
    assert runtime_sources["fixPathEnv"]["role"] == (
        "cargo-lock-git-source-provenance-reference-only"
    )


def test_unsloth_app_runtime_smoke_is_a_pre_export_store_path_check() -> None:
    """Artifact validation must smoke the realized candidate without host export."""
    scope = _package_scope()
    closure_passthru = expect_instance(
        expect_binding(scope, "closurePassthru").value,
        AttributeSet,
    )
    closure_inherit = expect_instance(
        next(value for value in closure_passthru.values if isinstance(value, Inherit)),
        Inherit,
    )
    assert "storePathAppCandidateSmoke" in {
        expect_instance(name, Identifier).name for name in closure_inherit.names
    }
    smoke = expect_instance(
        expect_binding(scope, "storePathAppCandidateSmoke").value,
        FunctionCall,
    )
    smoke_script = expect_instance(smoke.argument, IndentedString)
    shell = parse_shell(indented_string_body(smoke_script.rebuild()))
    assert [
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "variable_assignment")
    ] == [
        'app="__NIX_INTERP__/Applications/Unsloth.app"',
        'executable="$app/Contents/MacOS/unsloth-studio"',
        'backendExecutable="__NIX_INTERP__/bin/unsloth"',
        'backendRuntimeEntrypoint="__NIX_INTERP__/bin/unsloth"',
    ]
    assert command_texts(shell) == [
        'test -d "$app"',
        'test -x "$executable"',
        'test -x "$backendExecutable"',
        'test -x "$backendRuntimeEntrypoint"',
        'test -L "__NIX_INTERP__/bin/unsloth-studio"',
        '__NIX_INTERP__/bin/strings -a "$executable"',
        'grep -F "$backendExecutable"',
        (
            "__NIX_INTERP__/bin/python -c __NIX_INTERP__ \\\n"
            '          "$backendExecutable" \\\n'
            '          "$backendRuntimeEntrypoint" \\\n'
            '          "$TMPDIR/backend-health.json" \\\n'
            '          "$TMPDIR/backend-startup.log" \\\n'
            '          "$TMPDIR/backend-home"'
        ),
        'mkdir -p "$out"',
        'ln -s "__NIX_INTERP__" "$out/app-candidate"',
        "printf '%s\\n' \"$backendExecutable\"",
        "printf '%s\\n' \"$backendRuntimeEntrypoint\"",
        'cp "$TMPDIR/backend-health.json" "$out/backend-health.json"',
    ]


def test_unsloth_store_smoke_allows_darwin_loopback_networking() -> None:
    """The store smoke must opt into the Darwin sandbox's loopback access."""
    smoke = expect_instance(
        expect_binding(_package_scope(), "storePathAppCandidateSmoke").value,
        FunctionCall,
    )
    smoke_with_attributes = expect_instance(smoke.name, FunctionCall)
    attributes = expect_instance(smoke_with_attributes.argument, AttributeSet)

    assert_nix_ast_equal(
        expect_binding(attributes.values, "__darwinAllowLocalNetworking").value,
        "true",
    )


def test_unsloth_store_smoke_starts_the_real_packaged_backend() -> None:
    """The pre-export store gate must prove backend health and bounded teardown."""
    smoke = expect_instance(
        expect_binding(_package_scope(), "storePathAppCandidateSmoke").value,
        FunctionCall,
    )
    smoke_script = expect_instance(smoke.argument, IndentedString)
    shell = parse_shell(indented_string_body(smoke_script.rebuild()))
    assert not list(iter_nodes(shell.tree.root_node, "heredoc_body"))
    tree = _store_smoke_python_tree()

    popen = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Popen"
    )
    argv = expect_instance(popen.args[0], ast.List)
    assert [ast.unparse(item) for item in argv.elts] == [
        "str(backend_executable)",
        "'studio'",
        "'--api-only'",
        "'-H'",
        "'127.0.0.1'",
        "'-p'",
        "'0'",
    ]

    required_health = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "required_health"
            for target in node.targets
        )
    )
    assert ast.literal_eval(required_health) == {
        "service": "Unsloth UI Backend",
        "status": "healthy",
    }
    calls = [ast.unparse(node) for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert "process.terminate()" in calls
    assert "process.wait(timeout=20)" in calls
    assert "process.kill()" in calls
    assert any("/api/health" in call for call in calls)


def test_unsloth_store_smoke_drops_sandbox_certificate_sentinels() -> None:
    """The backend child must not inherit Nix's deliberately invalid CA paths."""
    tree = _store_smoke_python_tree()

    popen = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Popen"
    )
    popen_environment = next(
        keyword.value for keyword in popen.keywords if keyword.arg == "env"
    )
    assert ast.unparse(popen_environment) == "environment"

    removed_environment_variables = {
        ast.literal_eval(call.args[0])
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "environment"
        and call.func.attr == "pop"
        and len(call.args) == 2
        and isinstance(call.args[1], ast.Constant)
        and call.args[1].value is None
    }
    assert {"SSL_CERT_DIR", "SSL_CERT_FILE"} <= removed_environment_variables


def test_unsloth_runtime_sandbox_profile_is_accepted_by_macos(
    tmp_path: Path,
) -> None:
    """The real sandbox permits only writes below an aliased macOS temp root."""
    sandbox_exec = Path("/usr/bin/sandbox-exec")
    if not sandbox_exec.is_file():
        pytest.skip("macOS sandbox-exec is unavailable")

    canonical_var = Path("/private/var")
    try:
        relative_tmp_path = tmp_path.resolve().relative_to(canonical_var)
    except ValueError:
        pytest.skip("pytest temp root is not below macOS /private/var")
    aliased_tmp_path = Path("/var") / relative_tmp_path
    runtime_root = aliased_tmp_path / "runtime-root"
    runtime_root.mkdir()

    validator = _runtime_validator_module()
    profile = tmp_path / "containment.sb"
    profile.write_text(validator.sandbox_profile(runtime_root), encoding="utf-8")

    allowed_target = runtime_root / "home/.unsloth/studio/share-test"
    allowed = subprocess.run(  # noqa: S603 -- fixed macOS system executable
        [
            str(sandbox_exec),
            "-f",
            str(profile),
            "/bin/mkdir",
            "-p",
            str(allowed_target),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert allowed.returncode == 0, allowed.stderr
    assert (tmp_path / "runtime-root/home/.unsloth/studio/share-test").is_dir()

    outside_target = aliased_tmp_path / "outside-write"
    denied = subprocess.run(  # noqa: S603 -- fixed macOS system executable
        [
            str(sandbox_exec),
            "-f",
            str(profile),
            "/bin/mkdir",
            "-p",
            str(outside_target),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert denied.returncode != 0
    assert not (tmp_path / "outside-write").exists()


@pytest.mark.parametrize("session_readback", [ProcessLookupError(), 999])
def test_unsloth_runtime_tears_down_a_spawn_when_session_readback_fails(
    monkeypatch: pytest.MonkeyPatch,
    session_readback: BaseException | int,
) -> None:
    """Every successful spawn is owned before fallible session verification."""
    validator = _runtime_validator_module()

    class SpawnedApp:
        pid = 321

    app = SpawnedApp()
    store = validator.StoreEvidence(
        app_candidate=Path("/nix/store/app"),
        app_bundle=Path("/nix/store/app/Applications/Unsloth.app"),
        app_executable=Path("/nix/store/app/bin/unsloth-studio"),
        backend_executable=Path("/nix/store/backend/bin/unsloth"),
        backend_runtime_entrypoint=Path("/nix/store/venv/bin/unsloth"),
    )
    monkeypatch.setattr(validator, "_launch_direct_app", lambda **_kwargs: app)

    def read_session(_pid: int) -> int:
        if isinstance(session_readback, BaseException):
            raise session_readback
        return session_readback

    monkeypatch.setattr(validator.os, "getsid", read_session)
    teardown_calls: list[tuple[object, int]] = []
    monkeypatch.setattr(
        validator,
        "_teardown_session",
        lambda *, app, session_id, **_kwargs: teardown_calls.append((app, session_id)),
    )

    with pytest.raises(validator.ValidationError, match="isolated session"):
        validator._run_contained_runtime(
            store=store,
            sentinel=_sentinel_baseline(validator),
            startup_timeout=1,
            teardown_timeout=1,
        )
    assert teardown_calls == [(app, 321)]


def test_unsloth_session_teardown_signals_the_spawn_even_if_ps_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disappearing session snapshot cannot make a live Popen escape cleanup."""
    validator = _runtime_validator_module()

    class SpawnedApp:
        pid = 321
        terminated = False

        def poll(self) -> int | None:
            return 0 if self.terminated else None

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.terminated = True

    app = SpawnedApp()
    sentinel_listener = _sentinel_listener(validator)
    monkeypatch.setattr(validator, "_listeners", lambda: (sentinel_listener,))
    monkeypatch.setattr(validator, "_processes", dict)

    validator._teardown_session(
        app=app,
        session_id=321,
        sentinel=_sentinel_baseline(validator),
        timeout=1,
    )
    assert app.terminated


def test_unsloth_runtime_parsers_reject_ambiguous_process_evidence() -> None:
    """Malformed snapshots fail closed instead of weakening ownership evidence."""
    validator = _runtime_validator_module()
    assert validator.parse_process_snapshot("\n") == {}
    with pytest.raises(validator.ValidationError, match="invalid ps row"):
        validator.parse_process_snapshot("1 2 3 only-four")
    with pytest.raises(validator.ValidationError, match="non-numeric ps identity"):
        validator.parse_process_snapshot("pid 2 3 4 command")
    with pytest.raises(validator.ValidationError, match="duplicate PID 1"):
        validator.parse_process_snapshot("1 0 1 1 app\n1 0 1 1 duplicate")

    cycle = validator.parse_process_snapshot("1 2 1 1 one\n2 1 2 1 two")
    assert not validator.is_descendant(cycle, pid=1, ancestor_pid=99)
    assert validator.owned_process_groups(cycle, root_pid=99, session_id=1) == ()


def test_unsloth_runtime_parses_lsof_rows_and_rejects_bad_identity() -> None:
    """The lsof seam accepts only numeric PIDs and numeric listener ports."""
    validator = _runtime_validator_module()
    assert validator.parse_lsof_listeners("\nxignored\nn127.0.0.1:1\n") == ()
    with pytest.raises(validator.ValidationError, match="invalid lsof PID"):
        validator.parse_lsof_listeners("pnot-a-pid")
    with pytest.raises(validator.ValidationError, match="invalid lsof listener"):
        validator.parse_lsof_listeners("p9\ncbackend\nn127.0.0.1:http (LISTEN)")
    assert validator.parse_lsof_listeners(
        "p9\ncbackend\nn127.0.0.1:8888 (LISTEN)\n"
    ) == (
        validator.Listener(
            pid=9,
            command="backend",
            address="127.0.0.1:8888",
            port=8888,
        ),
    )


def test_unsloth_runtime_health_requires_every_source_backed_field() -> None:
    """Missing or wrongly typed required health fields never become evidence."""
    validator = _runtime_validator_module()
    assert validator.required_health_evidence(None) is None
    assert (
        validator.required_health_evidence({
            "service": "wrong",
            "status": "healthy",
            "studio_root_id": "aa",
        })
        is None
    )
    assert (
        validator.required_health_evidence({
            "service": "Unsloth UI Backend",
            "status": "healthy",
            "studio_root_id": 123,
        })
        is None
    )


def test_unsloth_store_path_validation_is_fail_closed(tmp_path: Path) -> None:
    """Only existing paths beneath the configured store root are accepted."""
    validator = _runtime_validator_module()
    store_root = tmp_path / "nix/store"
    output = store_root / "aaaaaaaa-output"
    output.mkdir(parents=True)
    validator._STORE_ROOT = store_root

    assert validator._require_store_path(output, label="output") == output
    with pytest.raises(validator.ValidationError, match="does not resolve"):
        validator._require_store_path(store_root / "missing", label="missing")
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(validator.ValidationError, match="is not in"):
        validator._require_store_path(outside, label="outside")
    with pytest.raises(
        validator.ValidationError, match="not inside a Nix store output"
    ):
        validator._require_store_path(store_root, label="root")


def _materialize_runtime_smoke(tmp_path: Path, validator: ModuleType):
    store_root = tmp_path / "nix/store"
    smoke = store_root / "aaaaaaaa-smoke"
    candidate = store_root / "bbbbbbbb-candidate"
    backend = store_root / "cccccccc-backend/bin/unsloth"
    backend_runtime_entrypoint = store_root / "dddddddd-unsloth-venv/bin/unsloth"
    executable = candidate / "Applications/Unsloth.app/Contents/MacOS/unsloth-studio"
    smoke.mkdir(parents=True)
    executable.parent.mkdir(parents=True)
    backend.parent.mkdir(parents=True)
    backend_runtime_entrypoint.parent.mkdir(parents=True)
    executable.write_text("app", encoding="utf-8")
    backend.write_text("backend", encoding="utf-8")
    backend_runtime_entrypoint.write_text("backend runtime", encoding="utf-8")
    executable.chmod(0o755)
    backend.chmod(0o755)
    backend_runtime_entrypoint.chmod(0o755)
    (smoke / "app-candidate").symlink_to(candidate)
    (smoke / "backend-path").write_text(f"{backend}\n", encoding="utf-8")
    (smoke / "backend-runtime-entrypoint-path").write_text(
        f"{backend_runtime_entrypoint}\n",
        encoding="utf-8",
    )
    validator._STORE_ROOT = store_root
    return smoke, candidate, backend, backend_runtime_entrypoint, executable


def test_unsloth_runtime_loads_exact_store_smoke_evidence(tmp_path: Path) -> None:
    """The host gate distinguishes the compiled wrapper from its exec target."""
    validator = _runtime_validator_module()
    smoke, candidate, backend, backend_runtime_entrypoint, executable = (
        _materialize_runtime_smoke(tmp_path, validator)
    )
    assert validator._load_store_evidence(smoke) == validator.StoreEvidence(
        app_candidate=candidate,
        app_bundle=candidate / "Applications/Unsloth.app",
        app_executable=executable,
        backend_executable=backend,
        backend_runtime_entrypoint=backend_runtime_entrypoint,
    )


def test_unsloth_runtime_rejects_incomplete_store_smoke_evidence(
    tmp_path: Path,
) -> None:
    """Unreadable, ambiguous, or non-executable smoke paths stop the host gate."""
    validator = _runtime_validator_module()
    smoke, _candidate, backend, backend_runtime_entrypoint, executable = (
        _materialize_runtime_smoke(tmp_path, validator)
    )
    (smoke / "backend-path").unlink()
    with pytest.raises(validator.ValidationError, match="cannot read backend-path"):
        validator._load_store_evidence(smoke)

    (smoke / "backend-path").write_text("\n", encoding="utf-8")
    with pytest.raises(validator.ValidationError, match="exactly one non-empty"):
        validator._load_store_evidence(smoke)

    (smoke / "backend-path").write_text(f"{backend}\n", encoding="utf-8")
    executable.chmod(0o644)
    with pytest.raises(validator.ValidationError, match="CFBundleExecutable"):
        validator._load_store_evidence(smoke)

    executable.chmod(0o755)
    backend.chmod(0o644)
    with pytest.raises(validator.ValidationError, match="backend executable"):
        validator._load_store_evidence(smoke)

    backend.chmod(0o755)
    runtime_entrypoint_evidence = smoke / "backend-runtime-entrypoint-path"
    runtime_entrypoint_evidence.unlink()
    with pytest.raises(
        validator.ValidationError,
        match="cannot read backend-runtime-entrypoint-path",
    ):
        validator._load_store_evidence(smoke)

    runtime_entrypoint_evidence.write_text("\n", encoding="utf-8")
    with pytest.raises(validator.ValidationError, match="exactly one non-empty"):
        validator._load_store_evidence(smoke)

    runtime_entrypoint_evidence.write_text(
        f"{backend_runtime_entrypoint}\n",
        encoding="utf-8",
    )
    backend_runtime_entrypoint.chmod(0o644)
    with pytest.raises(validator.ValidationError, match="runtime entrypoint"):
        validator._load_store_evidence(smoke)


def test_unsloth_runtime_snapshot_runner_handles_exit_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Snapshot tools expose allowed empty results but surface execution failures."""
    validator = _runtime_validator_module()

    def run_error(*_args, **_kwargs):
        raise OSError("missing")

    monkeypatch.setattr(validator.subprocess, "run", run_error)
    with pytest.raises(validator.ValidationError, match="could not execute tool"):
        validator._run_snapshot(("tool",))

    result = SimpleNamespace(returncode=1, stdout="", stderr="no rows")
    monkeypatch.setattr(validator.subprocess, "run", lambda *_args, **_kwargs: result)
    assert validator._run_snapshot(("tool",), no_rows_exit=1) == ""
    with pytest.raises(validator.ValidationError, match="exited 1"):
        validator._run_snapshot(("tool",))

    result.returncode = 0
    result.stdout = "answer"
    assert validator._run_snapshot(("tool",)) == "answer"


def test_unsloth_runtime_snapshot_adapters_parse_lsof_and_ps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fixed tool argv is converted into typed listener and process evidence."""
    validator = _runtime_validator_module()
    outputs = {
        str(validator._LSOF): "p9\ncbackend\nn127.0.0.1:8888 (LISTEN)\n",
        str(validator._PS): (
            "\ninvalid-pid 1 1 ignored\n10 1 10 app\n11 10 11 backend command\n"
        ),
    }
    monkeypatch.setattr(
        validator,
        "_run_snapshot",
        lambda argv, **_kwargs: outputs[argv[0]],
    )

    def getsid(pid: int) -> int:
        if pid == 11:
            raise ProcessLookupError
        return 10

    monkeypatch.setattr(validator.os, "getsid", getsid)
    assert validator._listeners() == (
        validator.Listener(9, "backend", "127.0.0.1:8888", 8888),
    )
    assert validator._processes() == {10: validator.Process(10, 1, 10, 10, "app")}

    outputs[str(validator._PS)] = "1 2 3\n"
    with pytest.raises(validator.ValidationError, match="invalid ps row"):
        validator._processes()


class _FakeHttpResponse:
    def __init__(self, status: int, payload: bytes) -> None:
        self.status = status
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class _FakeHttpConnection:
    def __init__(
        self,
        response: _FakeHttpResponse,
        *,
        request_error: BaseException | None = None,
    ) -> None:
        self.response = response
        self.request_error = request_error
        self.closed = False

    def request(self, *_args, **_kwargs) -> None:
        if self.request_error is not None:
            raise self.request_error

    def getresponse(self) -> _FakeHttpResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("response", "request_error", "expected"),
    [
        (_FakeHttpResponse(503, b"{}"), None, None),
        (_FakeHttpResponse(200, b"\xff"), None, None),
        (_FakeHttpResponse(200, b"not-json"), None, None),
        (_FakeHttpResponse(200, b'{"status":"healthy"}'), None, {"status": "healthy"}),
        (_FakeHttpResponse(200, b"{}"), OSError("closed"), None),
    ],
)
def test_unsloth_runtime_health_request_handles_http_boundary(
    monkeypatch: pytest.MonkeyPatch,
    response: _FakeHttpResponse,
    request_error: BaseException | None,
    expected: object,
) -> None:
    """Candidate health treats transport, status, encoding, and JSON failures as pending."""
    validator = _runtime_validator_module()
    connection = _FakeHttpConnection(response, request_error=request_error)
    monkeypatch.setattr(
        validator.http.client,
        "HTTPConnection",
        lambda *_args, **_kwargs: connection,
    )
    assert validator.request_candidate_health(8888) == expected
    assert connection.closed


def test_unsloth_runtime_health_request_rejects_non_candidate_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The health entry point must never probe a non-candidate listener."""
    validator = _runtime_validator_module()

    def _unexpected_connection(*_args: object, **_kwargs: object) -> None:
        pytest.fail("invalid candidate port reached the HTTP boundary")

    monkeypatch.setattr(validator.http.client, "HTTPConnection", _unexpected_connection)

    with pytest.raises(validator.ValidationError, match="restricted.*49152"):
        validator.request_candidate_health(_SENTINEL_TEST_PORT)


def test_unsloth_runtime_listener_filters_and_backend_argv_are_exact() -> None:
    """Listener identity and backend argv checks preserve exact paths and ports."""
    validator = _runtime_validator_module()
    sentinel_listener = _sentinel_listener(validator, pid=1)
    candidate = validator.Listener(2, "backend", "127.0.0.1:8888", 8888)
    assert validator._candidate_listeners((sentinel_listener, candidate)) == (
        candidate,
    )
    baseline = validator.SentinelBaseline(
        port=_SENTINEL_TEST_PORT,
        identity=((1, "Python", f"127.0.0.1:{_SENTINEL_TEST_PORT}"),),
        identity_sha256="a" * 64,
    )
    validator._require_sentinel_listener((sentinel_listener,), baseline)
    with pytest.raises(validator.ValidationError, match="identity changed"):
        validator._require_sentinel_listener((), baseline)
    backend = Path("/nix/store/backend/bin/unsloth")
    assert not validator.backend_argv_matches_evidence(
        "unterminated 'quote", backend_runtime_entrypoint=backend, port=8888
    )
    assert not validator.backend_argv_matches_evidence(
        f"{backend} studio --api-only",
        backend_runtime_entrypoint=backend,
        port=8888,
    )


def test_unsloth_runtime_backend_argv_matches_real_venv_entrypoint_only() -> None:
    """Listener argv is matched to the exec target, not the compiled wrapper."""
    validator = _runtime_validator_module()
    wrapper = Path("/nix/store/aaaaaaaa-unsloth-backend/bin/unsloth")
    runtime_entrypoint = Path("/nix/store/bbbbbbbb-unsloth-venv/bin/unsloth")
    command_prefix = "/nix/store/cccccccc-python/bin/python3.12"
    runtime_args = "studio --api-only -H 127.0.0.1 -p 8888"

    assert validator.backend_argv_matches_evidence(
        f"{command_prefix} {runtime_entrypoint} {runtime_args}",
        backend_runtime_entrypoint=runtime_entrypoint,
        port=8888,
    )
    assert not validator.backend_argv_matches_evidence(
        f"{command_prefix} {wrapper} {runtime_args}",
        backend_runtime_entrypoint=runtime_entrypoint,
        port=8888,
    )


def _runtime_processes(validator: ModuleType) -> dict[int, object]:
    backend_runtime_entrypoint = "/nix/store/dddddddd-unsloth-venv/bin/unsloth"
    return {
        100: validator.Process(100, 1, 100, 100, "/nix/store/app/unsloth-studio"),
        200: validator.Process(
            200,
            100,
            200,
            100,
            f"/nix/store/python/bin/python {backend_runtime_entrypoint} "
            "studio --api-only -H "
            "127.0.0.1 -p 8888",
        ),
    }


@pytest.mark.parametrize(
    ("scenario", "message"),
    [
        ("app-missing", "app exited"),
        ("app-session", "app escaped"),
        ("multiple", "multiple candidate listeners"),
        ("backend-missing", "listener PID is absent"),
        ("backend-session", "backend listener escaped"),
        ("not-descendant", "not a descendant"),
        ("wrong-argv", "does not match backend argv"),
        ("wrong-address", "exact candidate loopback"),
        ("groups-missing", "process group was not captured"),
        ("sentinel-loss", "sentinel listener identity changed"),
        ("health-pending", "timed out"),
        ("no-listener", "timed out"),
    ],
)
def test_unsloth_runtime_wait_rejects_invalid_listener_evidence(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    message: str,
) -> None:
    """Every listener ownership invariant fails closed before evidence is emitted."""
    validator = _runtime_validator_module()
    sentinel_listener = _sentinel_listener(validator)
    candidate = validator.Listener(200, "backend", "127.0.0.1:8888", 8888)
    listeners = [sentinel_listener, candidate]
    processes = _runtime_processes(validator)

    if scenario == "app-missing":
        processes.pop(100)
    elif scenario == "app-session":
        processes[100] = validator.Process(100, 1, 100, 999, "app")
    elif scenario == "multiple":
        listeners.append(validator.Listener(201, "other", "127.0.0.1:8889", 8889))
    elif scenario == "backend-missing":
        processes.pop(200)
    elif scenario == "backend-session":
        old = processes[200]
        processes[200] = validator.Process(
            old.pid, old.ppid, old.pgid, 999, old.command
        )
    elif scenario == "not-descendant":
        old = processes[200]
        processes[200] = validator.Process(old.pid, 1, old.pgid, old.sid, old.command)
    elif scenario == "wrong-argv":
        old = processes[200]
        processes[200] = validator.Process(
            old.pid,
            old.ppid,
            old.pgid,
            old.sid,
            old.command.replace(
                "/nix/store/dddddddd-unsloth-venv/bin/unsloth", "/tmp/unsloth"
            ),
        )
    elif scenario == "wrong-address":
        listeners[1] = validator.Listener(200, "backend", "*:8888", 8888)
    elif scenario == "groups-missing":
        monkeypatch.setattr(
            validator, "owned_process_groups", lambda *_args, **_kwargs: (100,)
        )
    elif scenario == "sentinel-loss":
        listeners.pop(0)
    elif scenario == "no-listener":
        listeners.pop()

    times = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(validator.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(validator.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(validator, "_listeners", lambda: tuple(listeners))
    monkeypatch.setattr(validator, "_processes", lambda: processes)
    monkeypatch.setattr(
        validator,
        "request_candidate_health",
        lambda _port: (
            None
            if scenario == "health-pending"
            else {
                "service": "Unsloth UI Backend",
                "status": "healthy",
                "studio_root_id": "a09f",
            }
        ),
    )
    with pytest.raises(validator.ValidationError, match=message):
        validator._wait_for_runtime(
            app_pid=100,
            backend_runtime_entrypoint=Path(
                "/nix/store/dddddddd-unsloth-venv/bin/unsloth"
            ),
            session_id=100,
            sentinel=_sentinel_baseline(validator),
            timeout=1,
        )


def test_unsloth_runtime_wait_emits_owned_listener_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matching descendant backend yields the normalized evidence object."""
    validator = _runtime_validator_module()
    sentinel_listener = _sentinel_listener(validator)
    candidate = validator.Listener(200, "backend", "127.0.0.1:8888", 8888)
    monkeypatch.setattr(validator.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(
        validator,
        "_listeners",
        lambda: (sentinel_listener, candidate),
    )
    monkeypatch.setattr(validator, "_processes", lambda: _runtime_processes(validator))
    monkeypatch.setattr(
        validator,
        "request_candidate_health",
        lambda _port: {
            "service": "Unsloth UI Backend",
            "status": "healthy",
            "studio_root_id": "a09f",
            "extra": True,
        },
    )
    assert validator._wait_for_runtime(
        app_pid=100,
        backend_runtime_entrypoint=Path("/nix/store/dddddddd-unsloth-venv/bin/unsloth"),
        session_id=100,
        sentinel=_sentinel_baseline(validator),
        timeout=1,
    ) == validator.RuntimeEvidence(
        app_pid=100,
        backend_pid=200,
        health={
            "service": "Unsloth UI Backend",
            "status": "healthy",
            "studio_root_id": "a09f",
        },
        listener_address="127.0.0.1:8888",
        owned_process_groups=(100, 200),
        port=8888,
        session_id=100,
    )


def test_unsloth_runtime_session_group_helpers_signal_only_owned_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Group signaling is limited to the selected session and tolerates races."""
    validator = _runtime_validator_module()
    processes = _runtime_processes(validator)
    processes[300] = validator.Process(300, 1, 300, 300, "unrelated")
    assert validator._groups_in_session(processes, 100) == (100, 200)
    monkeypatch.setattr(validator, "_processes", lambda: processes)
    assert not validator._session_is_gone(100)

    calls: list[tuple[int, object]] = []

    def killpg(pgid: int, sig: object) -> None:
        calls.append((pgid, sig))
        if pgid == 200:
            raise ProcessLookupError

    monkeypatch.setattr(validator.os, "killpg", killpg)
    assert validator._signal_session_groups(100, validator.signal.SIGTERM) == (100, 200)
    assert [pgid for pgid, _sig in calls] == [200, 100]
    monkeypatch.setattr(validator, "_processes", dict)
    assert validator._session_is_gone(100)


def test_unsloth_runtime_spawn_signal_handles_exit_kill_and_race() -> None:
    """The exact Popen seam handles already-exited, TERM, KILL, and ESRCH states."""
    validator = _runtime_validator_module()

    class App:
        def __init__(self, *, exited: bool = False, race: bool = False) -> None:
            self.exited = exited
            self.race = race
            self.signals: list[str] = []

        def poll(self) -> int | None:
            return 0 if self.exited else None

        def terminate(self) -> None:
            if self.race:
                raise ProcessLookupError
            self.signals.append("term")

        def kill(self) -> None:
            if self.race:
                raise ProcessLookupError
            self.signals.append("kill")

    exited = App(exited=True)
    validator._signal_spawned_app(exited, validator.signal.SIGTERM)
    assert exited.signals == []
    live = App()
    validator._signal_spawned_app(live, validator.signal.SIGTERM)
    validator._signal_spawned_app(live, validator.signal.SIGKILL)
    assert live.signals == ["term", "kill"]
    validator._signal_spawned_app(App(race=True), validator.signal.SIGTERM)


def test_unsloth_runtime_teardown_surfaces_sentinel_listener_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graceful cleanup still fails when the sentinel identity changes."""
    validator = _runtime_validator_module()

    class App:
        pid = 100

        def poll(self) -> int:
            return 0

        def terminate(self) -> None:
            return None

        def kill(self) -> None:
            return None

    monkeypatch.setattr(validator, "_signal_session_groups", lambda *_args: ())
    monkeypatch.setattr(validator, "_session_is_gone", lambda _sid: True)
    monkeypatch.setattr(validator, "_listeners", lambda: ())
    with pytest.raises(validator.ValidationError, match="identity changed"):
        validator._teardown_session(
            app=App(),
            session_id=100,
            sentinel=_sentinel_baseline(validator),
            timeout=1,
        )


def test_unsloth_runtime_teardown_retries_then_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live session receives repeated TERM until both process and listener vanish."""
    validator = _runtime_validator_module()

    class App:
        pid = 100
        polls = iter((None, 0, 0))

        def poll(self) -> int | None:
            return next(self.polls)

        def terminate(self) -> None:
            return None

        def kill(self) -> None:
            return None

    sentinel_listener = _sentinel_listener(validator)
    session_states = iter((False, True))
    term_calls: list[object] = []
    times = iter((0.0, 0.0, 0.5))
    monkeypatch.setattr(validator.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(validator.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(validator, "_listeners", lambda: (sentinel_listener,))
    monkeypatch.setattr(
        validator, "_session_is_gone", lambda _sid: next(session_states)
    )
    monkeypatch.setattr(
        validator,
        "_signal_session_groups",
        lambda _sid, sig: term_calls.append(sig) or (),
    )
    validator._teardown_session(
        app=App(),
        session_id=100,
        sentinel=_sentinel_baseline(validator),
        timeout=1,
    )
    assert term_calls == [validator.signal.SIGTERM, validator.signal.SIGTERM]


def test_unsloth_runtime_forced_teardown_reports_scope_and_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forced cleanup kills the exact spawn/session and reports sentinel drift."""
    validator = _runtime_validator_module()

    class App:
        pid = 100
        killed = False

        def poll(self) -> int | None:
            return -9 if self.killed else None

        def terminate(self) -> None:
            return None

        def kill(self) -> None:
            self.killed = True

    app = App()
    times = iter((0.0, 1.0, 2.0, 3.0))
    listener_calls = 0

    def listeners():
        nonlocal listener_calls
        listener_calls += 1
        if listener_calls == 1:
            raise validator.ValidationError("snapshot failed")
        return ()

    monkeypatch.setattr(validator.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(validator, "_listeners", listeners)
    monkeypatch.setattr(validator, "_session_is_gone", lambda _sid: True)
    monkeypatch.setattr(
        validator,
        "_signal_session_groups",
        lambda _sid, sig: (100, 200) if sig == validator.signal.SIGKILL else (),
    )
    with pytest.raises(
        validator.ValidationError,
        match=r"forced teardown.*\(100, 200\).*identity changed",
    ):
        validator._teardown_session(
            app=app,
            session_id=100,
            sentinel=_sentinel_baseline(validator),
            timeout=0,
        )
    assert app.killed


def test_unsloth_runtime_forced_teardown_bounds_post_kill_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forced cleanup has a finite second deadline even if the session stays visible."""
    validator = _runtime_validator_module()

    class App:
        pid = 100

        def poll(self) -> int:
            return -9

        def terminate(self) -> None:
            return None

        def kill(self) -> None:
            return None

    sentinel_listener = _sentinel_listener(validator)
    times = iter((0.0, 1.0, 2.0, 3.0, 8.0))
    monkeypatch.setattr(validator.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(validator.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(validator, "_listeners", lambda: (sentinel_listener,))
    monkeypatch.setattr(validator, "_session_is_gone", lambda _sid: False)
    monkeypatch.setattr(validator, "_signal_session_groups", lambda *_args: ())
    with pytest.raises(validator.ValidationError, match="forced teardown"):
        validator._teardown_session(
            app=App(),
            session_id=100,
            sentinel=_sentinel_baseline(validator),
            timeout=0,
        )


def test_unsloth_runtime_teardown_forces_spawn_after_group_snapshot_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed session snapshot cannot prevent forced cleanup of the exact spawn."""
    validator = _runtime_validator_module()

    class App:
        pid = 100
        killed = False

        def poll(self) -> int | None:
            return -9 if self.killed else None

        def terminate(self) -> None:
            return None

        def kill(self) -> None:
            self.killed = True

    app = App()
    sentinel_listener = _sentinel_listener(validator)
    times = iter((0.0, 1.0, 2.0, 3.0, 8.0))
    monkeypatch.setattr(validator.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(validator, "_listeners", lambda: (sentinel_listener,))

    def fail_snapshot(*_args):
        raise validator.ValidationError("process snapshot failed")

    def fail_session(_session_id: int) -> bool:
        raise validator.ValidationError("session snapshot failed")

    monkeypatch.setattr(validator, "_signal_session_groups", fail_snapshot)
    monkeypatch.setattr(validator, "_session_is_gone", fail_session)
    with pytest.raises(validator.ValidationError, match="process snapshot failed"):
        validator._teardown_session(
            app=app,
            session_id=100,
            sentinel=_sentinel_baseline(validator),
            timeout=0,
        )
    assert app.killed


def test_unsloth_runtime_parameters_and_listener_baseline_are_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timeout, port-range, free-port, and sentinel preconditions are explicit."""
    validator = _runtime_validator_module()
    validator._require_runtime_parameters(1, 1)
    with pytest.raises(validator.ValidationError, match="timeouts"):
        validator._require_runtime_parameters(0, 1)
    with pytest.raises(validator.ValidationError, match="timeouts"):
        validator._require_runtime_parameters(1, 0)
    monkeypatch.setattr(validator, "CANDIDATE_PORTS", (_SENTINEL_TEST_PORT,))
    with pytest.raises(validator.ValidationError, match="internally inconsistent"):
        validator._require_runtime_parameters(1, 1)
    monkeypatch.setattr(validator, "CANDIDATE_PORTS", (8888,))
    with pytest.raises(validator.ValidationError, match="internally inconsistent"):
        validator._require_runtime_parameters(1, 1)

    monkeypatch.setattr(validator, "CANDIDATE_PORTS", tuple(range(8888, 8909)))
    monkeypatch.setattr(validator.os, "getpid", lambda: _SENTINEL_TEST_PID)
    sentinel_listener = _sentinel_listener(validator)
    occupied = validator.Listener(200, "backend", "127.0.0.1:8888", 8888)
    monkeypatch.setattr(
        validator,
        "_listeners",
        lambda: (sentinel_listener, occupied),
    )
    with pytest.raises(validator.ValidationError, match="must all be free"):
        validator._listener_baseline(_SENTINEL_TEST_PORT)
    monkeypatch.setattr(validator, "_listeners", lambda: (sentinel_listener,))
    baseline = validator._listener_baseline(_SENTINEL_TEST_PORT)
    assert baseline.port == _SENTINEL_TEST_PORT
    assert baseline.identity == _sentinel_baseline(validator).identity
    assert len(baseline.identity_sha256) == 64


def test_unsloth_sentinel_listener_baseline_rejects_absent_listener() -> None:
    """The validator must independently observe its listener after binding it."""
    validator = _runtime_validator_module()

    with pytest.raises(validator.ValidationError, match="absent"):
        validator.sentinel_listener_baseline(
            (),
            port=_SENTINEL_TEST_PORT,
            owner_pid=_SENTINEL_TEST_PID,
        )


@pytest.mark.parametrize(
    ("listener", "message"),
    [
        (
            (
                _SENTINEL_TEST_PID,
                "Python",
                f"*:{_SENTINEL_TEST_PORT}",
                _SENTINEL_TEST_PORT,
            ),
            "bind exactly",
        ),
        (
            (1, "Python", f"127.0.0.1:{_SENTINEL_TEST_PORT}", _SENTINEL_TEST_PORT),
            "owned exclusively",
        ),
    ],
)
def test_unsloth_sentinel_listener_baseline_rejects_wrong_identity(
    listener: tuple[int, str, str, int],
    message: str,
) -> None:
    """The sentinel baseline requires the exact address and validator PID."""
    validator = _runtime_validator_module()

    with pytest.raises(validator.ValidationError, match=message):
        validator.sentinel_listener_baseline(
            (validator.Listener(*listener),),
            port=_SENTINEL_TEST_PORT,
            owner_pid=_SENTINEL_TEST_PID,
        )


def test_unsloth_runtime_sentinel_uses_an_os_assigned_non_candidate_port() -> None:
    """The real socket boundary keeps an OS-selected port reserved while open."""
    validator = _runtime_validator_module()

    listener, port = validator._sentinel_socket()
    try:
        assert port == listener.getsockname()[1]
        assert port not in validator.CANDIDATE_PORTS
        assert port > 0
    finally:
        listener.close()


def test_unsloth_runtime_sentinel_retries_an_os_candidate_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unlucky ephemeral assignment cannot consume a candidate runtime port."""
    validator = _runtime_validator_module()
    candidate = _FakeSentinelSocket(8888)
    sentinel = _FakeSentinelSocket(_SENTINEL_TEST_PORT)
    sockets = iter((candidate, sentinel))
    monkeypatch.setattr(
        validator.socket,
        "socket",
        lambda _family, _kind: next(sockets),
    )

    listener, port = validator._sentinel_socket()

    assert listener is sentinel
    assert port == _SENTINEL_TEST_PORT
    assert candidate.closed
    assert candidate.listen_backlog is None
    assert sentinel.bound_to == (validator.HEALTH_HOST, 0)
    assert sentinel.listen_backlog == 1


@pytest.mark.parametrize("stage", ["bind", "listen"])
def test_unsloth_runtime_sentinel_closes_socket_setup_failures(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    """A failed bind or listen cannot leak a partially initialized socket."""
    validator = _runtime_validator_module()
    error = OSError(f"{stage} failed")
    listener = _FakeSentinelSocket(
        _SENTINEL_TEST_PORT,
        bind_error=error if stage == "bind" else None,
        listen_error=error if stage == "listen" else None,
    )
    monkeypatch.setattr(
        validator.socket,
        "socket",
        lambda _family, _kind: listener,
    )

    with pytest.raises(validator.ValidationError, match="could not establish"):
        validator._sentinel_socket()
    assert listener.closed


def test_unsloth_runtime_sentinel_reports_socket_creation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A socket-construction error is normalized without assuming a live socket."""
    validator = _runtime_validator_module()
    monkeypatch.setattr(
        validator.socket,
        "socket",
        lambda _family, _kind: (_ for _ in ()).throw(OSError("unavailable")),
    )

    with pytest.raises(validator.ValidationError, match="could not establish"):
        validator._sentinel_socket()


def test_unsloth_runtime_sentinel_preserves_setup_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A socket cleanup failure is attached to the causal setup error."""
    validator = _runtime_validator_module()
    listener = _FakeSentinelSocket(
        _SENTINEL_TEST_PORT,
        bind_error=OSError("bind failed"),
        close_error=OSError("close failed"),
    )
    monkeypatch.setattr(
        validator.socket,
        "socket",
        lambda _family, _kind: listener,
    )

    with pytest.raises(validator.ValidationError) as raised:
        validator._sentinel_socket()
    assert raised.value.__cause__ is not None
    assert raised.value.__cause__.__notes__ == [
        "sentinel socket cleanup also failed: close failed"
    ]


def test_unsloth_runtime_cli_surfaces_setup_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI must expose setup-cleanup notes in its SystemExit message."""
    validator = _runtime_validator_module()
    listener = _FakeSentinelSocket(
        _SENTINEL_TEST_PORT,
        bind_error=OSError("bind failed"),
        close_error=OSError("close failed"),
    )
    monkeypatch.setattr(
        validator,
        "_load_store_evidence",
        lambda _path: _contained_store(validator),
    )
    monkeypatch.setattr(
        validator.socket,
        "socket",
        lambda _family, _kind: listener,
    )

    with pytest.raises(SystemExit) as raised:
        validator.main(["--smoke-result", "/nix/store/smoke"])

    assert raised.value.code == (
        "Unsloth store runtime validation failed: "
        "could not establish validator listener sentinel: bind failed; "
        "sentinel socket cleanup also failed: close failed"
    )


@pytest.mark.parametrize("port", [0, True, "49152"])
def test_unsloth_runtime_sentinel_rejects_invalid_os_port(
    monkeypatch: pytest.MonkeyPatch,
    port: object,
) -> None:
    """Malformed socket metadata fails closed and releases the socket."""
    validator = _runtime_validator_module()
    listener = _FakeSentinelSocket(port)
    monkeypatch.setattr(
        validator.socket,
        "socket",
        lambda _family, _kind: listener,
    )

    with pytest.raises(validator.ValidationError, match="invalid sentinel port"):
        validator._sentinel_socket()
    assert listener.closed


def test_unsloth_runtime_sentinel_rejects_reserved_port_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated candidate-range assignments fail rather than weakening isolation."""
    validator = _runtime_validator_module()
    listener = _FakeSentinelSocket(8888)
    monkeypatch.setattr(validator, "_SENTINEL_BIND_ATTEMPTS", 1)
    monkeypatch.setattr(
        validator.socket,
        "socket",
        lambda _family, _kind: listener,
    )

    with pytest.raises(validator.ValidationError, match="repeatedly assigned"):
        validator._sentinel_socket()
    assert listener.closed


def test_unsloth_runtime_sentinel_rejects_reserved_port_close_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A candidate-range socket must be released before another bind is attempted."""
    validator = _runtime_validator_module()
    listener = _FakeSentinelSocket(8888, close_error=OSError("close failed"))
    monkeypatch.setattr(
        validator.socket,
        "socket",
        lambda _family, _kind: listener,
    )

    with pytest.raises(validator.ValidationError, match="release reserved"):
        validator._sentinel_socket()


def test_unsloth_runtime_sentinel_start_and_teardown_own_the_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The validator carries one baseline through setup and proves final absence."""
    validator = _runtime_validator_module()
    listener = _FakeSentinelSocket(_SENTINEL_TEST_PORT)
    baseline = _sentinel_baseline(validator)
    monkeypatch.setattr(
        validator,
        "_sentinel_socket",
        lambda: (listener, _SENTINEL_TEST_PORT),
    )
    monkeypatch.setattr(validator, "_listener_baseline", lambda _port: baseline)
    sentinel = validator._start_listener_sentinel()
    assert sentinel.baseline is baseline

    monkeypatch.setattr(validator, "_listeners", tuple)
    validator._teardown_listener_sentinel(sentinel)
    assert listener.closed


def test_unsloth_runtime_sentinel_start_closes_after_baseline_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed independent baseline cannot leave validator infrastructure behind."""
    validator = _runtime_validator_module()
    listener = _FakeSentinelSocket(_SENTINEL_TEST_PORT)
    monkeypatch.setattr(
        validator,
        "_sentinel_socket",
        lambda: (listener, _SENTINEL_TEST_PORT),
    )
    monkeypatch.setattr(
        validator,
        "_listener_baseline",
        lambda _port: (_ for _ in ()).throw(validator.ValidationError("missing")),
    )

    with pytest.raises(validator.ValidationError, match="missing"):
        validator._start_listener_sentinel()
    assert listener.closed


def test_unsloth_runtime_sentinel_start_reports_baseline_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Baseline failure remains causal while a close failure is retained as a note."""
    validator = _runtime_validator_module()
    listener = _FakeSentinelSocket(
        _SENTINEL_TEST_PORT,
        close_error=OSError("close failed"),
    )
    monkeypatch.setattr(
        validator,
        "_sentinel_socket",
        lambda: (listener, _SENTINEL_TEST_PORT),
    )
    monkeypatch.setattr(
        validator,
        "_listener_baseline",
        lambda _port: (_ for _ in ()).throw(validator.ValidationError("missing")),
    )

    with pytest.raises(validator.ValidationError, match="missing") as raised:
        validator._start_listener_sentinel()
    assert raised.value.__notes__ == [
        "sentinel socket cleanup also failed: close failed"
    ]


@pytest.mark.parametrize(
    ("close_error", "remaining", "message"),
    [
        (OSError("close failed"), False, "could not close"),
        (None, True, "survived teardown"),
        (OSError("close failed"), True, "survived teardown"),
    ],
)
def test_unsloth_runtime_sentinel_teardown_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    close_error: OSError | None,
    remaining: bool,
    message: str,
) -> None:
    """Socket-close errors and a remaining listener are reported distinctly."""
    validator = _runtime_validator_module()
    listener = _FakeSentinelSocket(
        _SENTINEL_TEST_PORT,
        close_error=close_error,
    )
    sentinel = validator.ListenerSentinel(
        listener=listener,
        baseline=_sentinel_baseline(validator),
    )
    listeners = (_sentinel_listener(validator),) if remaining else ()
    monkeypatch.setattr(validator, "_listeners", lambda: listeners)

    with pytest.raises(validator.ValidationError, match=message) as raised:
        validator._teardown_listener_sentinel(sentinel)
    if close_error is not None and remaining:
        assert raised.value.__notes__ == [
            "sentinel socket close also failed: close failed"
        ]


def test_unsloth_runtime_sentinel_teardown_preserves_close_and_inspection_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Listener inspection stays primary while a simultaneous close error is retained."""
    validator = _runtime_validator_module()
    listener = _FakeSentinelSocket(
        _SENTINEL_TEST_PORT,
        close_error=OSError("close failed"),
    )
    sentinel = validator.ListenerSentinel(
        listener=listener,
        baseline=_sentinel_baseline(validator),
    )
    inspection_error = validator.ValidationError("listener inspection failed")
    monkeypatch.setattr(
        validator,
        "_listeners",
        lambda: (_ for _ in ()).throw(inspection_error),
    )

    with pytest.raises(validator.ValidationError) as raised:
        validator._teardown_listener_sentinel(sentinel)

    assert raised.value is inspection_error
    assert raised.value.__notes__ == ["sentinel socket close also failed: close failed"]


def test_unsloth_runtime_sentinel_teardown_propagates_inspection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A listener-inspection failure remains unchanged when socket close succeeds."""
    validator = _runtime_validator_module()
    sentinel = validator.ListenerSentinel(
        listener=_FakeSentinelSocket(_SENTINEL_TEST_PORT),
        baseline=_sentinel_baseline(validator),
    )
    inspection_error = validator.ValidationError("listener inspection failed")
    monkeypatch.setattr(
        validator,
        "_listeners",
        lambda: (_ for _ in ()).throw(inspection_error),
    )

    with pytest.raises(validator.ValidationError) as raised:
        validator._teardown_listener_sentinel(sentinel)

    assert raised.value is inspection_error
    assert not hasattr(raised.value, "__notes__")


def test_unsloth_runtime_direct_launch_uses_sandbox_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The only Popen argv is sandbox-exec followed by the direct app executable."""
    validator = _runtime_validator_module()
    store = validator.StoreEvidence(
        Path("/nix/store/app"),
        Path("/nix/store/app/Applications/Unsloth.app"),
        Path("/nix/store/app/bin/app"),
        Path("/nix/store/backend/bin/unsloth"),
        Path("/nix/store/venv/bin/unsloth"),
    )
    expected_app = SimpleNamespace(pid=100)
    captured: dict[str, object] = {}

    def popen(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return expected_app

    monkeypatch.setattr(validator.subprocess, "Popen", popen)
    log = io.BytesIO()
    assert (
        validator._launch_direct_app(
            store=store,
            environment={"PATH": "/usr/bin"},
            log=log,
            profile=Path("/tmp/containment.sb"),
        )
        is expected_app
    )
    assert captured["argv"] == validator.sandboxed_app_argv(
        Path("/tmp/containment.sb"), store.app_bundle
    )
    assert captured["start_new_session"] is True

    monkeypatch.setattr(
        validator.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("denied")),
    )
    with pytest.raises(validator.ValidationError, match="could not launch"):
        validator._launch_direct_app(
            store=store,
            environment={},
            log=log,
            profile=Path("/tmp/containment.sb"),
        )


def test_unsloth_runtime_isolated_session_accepts_only_pid_sid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The session readback must equal the exact successful spawn PID."""
    validator = _runtime_validator_module()
    app = SimpleNamespace(pid=100)
    monkeypatch.setattr(validator.os, "getsid", lambda _pid: 100)
    assert validator._isolated_session_id(app) == 100


def _contained_store(validator: ModuleType):
    return validator.StoreEvidence(
        app_candidate=Path("/nix/store/aaaaaaaa-app"),
        app_bundle=Path("/nix/store/aaaaaaaa-app/Applications/Unsloth.app"),
        app_executable=Path(
            "/nix/store/aaaaaaaa-app/Applications/Unsloth.app/Contents/MacOS/"
            "unsloth-studio"
        ),
        backend_executable=Path("/nix/store/bbbbbbbb-backend/bin/unsloth"),
        backend_runtime_entrypoint=Path("/nix/store/cccccccc-unsloth-venv/bin/unsloth"),
    )


def _contained_evidence(validator: ModuleType):
    return validator.RuntimeEvidence(
        app_pid=100,
        backend_pid=200,
        health={
            "service": "Unsloth UI Backend",
            "status": "healthy",
            "studio_root_id": "a09f",
        },
        listener_address="127.0.0.1:8888",
        owned_process_groups=(100, 200),
        port=8888,
        session_id=100,
    )


def test_unsloth_contained_runtime_returns_evidence_after_successful_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runtime result is released only after the owned session is torn down."""
    validator = _runtime_validator_module()
    app = SimpleNamespace(pid=100)
    evidence = _contained_evidence(validator)
    events: list[str] = []
    monkeypatch.setattr(validator, "_launch_direct_app", lambda **_kwargs: app)
    monkeypatch.setattr(validator, "_isolated_session_id", lambda _app: 100)
    monkeypatch.setattr(
        validator,
        "_wait_for_runtime",
        lambda **_kwargs: events.append("validated") or evidence,
    )
    monkeypatch.setattr(
        validator,
        "_teardown_session",
        lambda **_kwargs: events.append("torn-down"),
    )

    assert validator._run_contained_runtime(
        store=_contained_store(validator),
        sentinel=_sentinel_baseline(validator),
        startup_timeout=1,
        teardown_timeout=1,
    ) == (evidence, 100)
    assert events == ["validated", "torn-down"]


@pytest.mark.parametrize(
    ("runtime_result", "runtime_error", "teardown_error", "message"),
    [
        (None, "validation failed", "teardown failed", "teardown also failed"),
        ("evidence", None, "teardown failed", "teardown failed"),
        (None, None, None, "runtime evidence was not captured"),
    ],
)
def test_unsloth_contained_runtime_preserves_failure_precedence(
    monkeypatch: pytest.MonkeyPatch,
    runtime_result: str | None,
    runtime_error: str | None,
    teardown_error: str | None,
    message: str,
) -> None:
    """Validation, teardown, and missing-evidence failures remain distinguishable."""
    validator = _runtime_validator_module()
    app = SimpleNamespace(pid=100)
    evidence = _contained_evidence(validator)
    monkeypatch.setattr(validator, "_launch_direct_app", lambda **_kwargs: app)
    monkeypatch.setattr(validator, "_isolated_session_id", lambda _app: 100)

    def wait(**_kwargs):
        if runtime_error is not None:
            raise validator.ValidationError(runtime_error)
        return evidence if runtime_result == "evidence" else None

    def teardown(**_kwargs) -> None:
        if teardown_error is not None:
            raise validator.ValidationError(teardown_error)

    monkeypatch.setattr(validator, "_wait_for_runtime", wait)
    monkeypatch.setattr(validator, "_teardown_session", teardown)
    with pytest.raises(validator.ValidationError, match=message):
        validator._run_contained_runtime(
            store=_contained_store(validator),
            sentinel=_sentinel_baseline(validator),
            startup_timeout=1,
            teardown_timeout=1,
        )


@pytest.mark.parametrize(
    ("candidate_survives", "session_survives", "message"),
    [
        (True, False, "candidate listener survived"),
        (False, True, "process survived"),
    ],
)
def test_unsloth_final_teardown_rejects_runtime_survivors(
    monkeypatch: pytest.MonkeyPatch,
    candidate_survives: bool,
    session_survives: bool,
    message: str,
) -> None:
    """The final independent snapshot rejects either listener or process residue."""
    validator = _runtime_validator_module()
    sentinel_listener = _sentinel_listener(validator)
    candidate = validator.Listener(200, "backend", "127.0.0.1:8888", 8888)
    listeners = (
        (sentinel_listener, candidate) if candidate_survives else (sentinel_listener,)
    )
    monkeypatch.setattr(validator, "_listeners", lambda: listeners)
    monkeypatch.setattr(
        validator,
        "_session_is_gone",
        lambda _session_id: not session_survives,
    )
    with pytest.raises(validator.ValidationError, match=message):
        validator._require_final_teardown(
            session_id=100,
            sentinel=_sentinel_baseline(validator),
        )


def test_unsloth_final_teardown_accepts_clean_independent_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preserved validator sentinel and absent candidate session pass."""
    validator = _runtime_validator_module()
    sentinel_listener = _sentinel_listener(validator)
    monkeypatch.setattr(validator, "_listeners", lambda: (sentinel_listener,))
    monkeypatch.setattr(validator, "_session_is_gone", lambda _session_id: True)
    validator._require_final_teardown(
        session_id=100,
        sentinel=_sentinel_baseline(validator),
    )


def test_unsloth_validate_store_runtime_emits_complete_gate_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public gate result contains every persisted runtime-evidence field."""
    validator = _runtime_validator_module()
    store = _contained_store(validator)
    evidence = _contained_evidence(validator)
    sentinel = _sentinel_baseline(validator)
    final_calls: list[tuple[int, object]] = []
    sentinel_teardown_calls: list[object] = []
    monkeypatch.setattr(validator, "_load_store_evidence", lambda _path: store)
    listener_sentinel = validator.ListenerSentinel(
        listener=SimpleNamespace(close=lambda: None),
        baseline=sentinel,
    )
    monkeypatch.setattr(
        validator,
        "_start_listener_sentinel",
        lambda: listener_sentinel,
    )
    monkeypatch.setattr(
        validator,
        "_run_contained_runtime",
        lambda **_kwargs: (evidence, 100),
    )
    monkeypatch.setattr(
        validator,
        "_require_final_teardown",
        lambda *, session_id, sentinel: final_calls.append((
            session_id,
            sentinel,
        )),
    )
    monkeypatch.setattr(
        validator,
        "_teardown_listener_sentinel",
        sentinel_teardown_calls.append,
    )

    assert validator.validate_store_runtime(Path("/nix/store/smoke")) == {
        "appCandidate": str(store.app_candidate),
        "appPid": 100,
        "backendPid": 200,
        "backendExecutable": str(store.backend_executable),
        "backendRuntimeEntrypoint": str(store.backend_runtime_entrypoint),
        "health": evidence.health,
        "listenerAddress": "127.0.0.1:8888",
        "listenerOwnership": "passed",
        "ownedProcessGroups": [100, 200],
        "port": 8888,
        "protectedListenerCount": 1,
        "protectedListenerIdentitySha256": sentinel.identity_sha256,
        "sandbox": "passed",
        "schemaVersion": 2,
        "sessionId": 100,
        "status": "passed",
        "teardown": "passed",
    }
    assert final_calls == [(100, sentinel)]
    assert sentinel_teardown_calls == [listener_sentinel]


@pytest.mark.parametrize(
    ("unexpected", "cleanup_fails"),
    [
        (False, False),
        (False, True),
        (True, True),
    ],
)
def test_unsloth_validate_store_runtime_always_tears_down_its_sentinel(
    monkeypatch: pytest.MonkeyPatch,
    *,
    unexpected: bool,
    cleanup_fails: bool,
) -> None:
    """Sentinel cleanup preserves the primary runtime failure and its own state."""
    validator = _runtime_validator_module()
    store = _contained_store(validator)
    sentinel = validator.ListenerSentinel(
        listener=_FakeSentinelSocket(_SENTINEL_TEST_PORT),
        baseline=_sentinel_baseline(validator),
    )
    primary = (
        ValueError("unexpected runtime failure")
        if unexpected
        else validator.ValidationError("runtime failed")
    )
    teardown_calls: list[object] = []
    monkeypatch.setattr(validator, "_load_store_evidence", lambda _path: store)
    monkeypatch.setattr(validator, "_start_listener_sentinel", lambda: sentinel)
    monkeypatch.setattr(
        validator,
        "_run_contained_runtime",
        lambda **_kwargs: (_ for _ in ()).throw(primary),
    )

    def teardown(actual: object) -> None:
        teardown_calls.append(actual)
        if cleanup_fails:
            cleanup_error = validator.ValidationError("listener inspection failed")
            cleanup_error.add_note("sentinel socket close also failed: close failed")
            raise cleanup_error

    monkeypatch.setattr(validator, "_teardown_listener_sentinel", teardown)
    expected = ValueError if unexpected else validator.ValidationError
    message = "unexpected runtime failure" if unexpected else "runtime failed"
    with pytest.raises(expected, match=message) as raised:
        validator.validate_store_runtime(Path("/nix/store/smoke"))

    assert teardown_calls == [sentinel]
    if unexpected:
        assert raised.value.__notes__ == [
            "sentinel teardown also failed: listener inspection failed; "
            "sentinel socket close also failed: close failed"
        ]
    elif cleanup_fails:
        assert str(raised.value) == (
            "runtime failed; sentinel teardown also failed: "
            "listener inspection failed; "
            "sentinel socket close also failed: close failed"
        )


def test_unsloth_runtime_cli_prints_json_and_surfaces_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI parses timeouts, emits one JSON object, and fails without a traceback."""
    validator = _runtime_validator_module()
    calls: list[tuple[Path, float, float]] = []

    def validate(path: Path, *, startup_timeout: float, teardown_timeout: float):
        calls.append((path, startup_timeout, teardown_timeout))
        return {"status": "passed"}

    monkeypatch.setattr(validator, "validate_store_runtime", validate)
    assert (
        validator.main([
            "--smoke-result",
            "/nix/store/smoke",
            "--startup-timeout",
            "2.5",
            "--teardown-timeout",
            "3.5",
        ])
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {"status": "passed"}
    assert calls == [(Path("/nix/store/smoke"), 2.5, 3.5)]

    def fail(*_args, **_kwargs):
        raise validator.ValidationError("blocked")

    monkeypatch.setattr(validator, "validate_store_runtime", fail)
    with pytest.raises(SystemExit, match="validation failed: blocked"):
        validator.main(["--smoke-result", "/nix/store/smoke"])


def test_unsloth_frontend_install_check_is_release_agnostic() -> None:
    """A successful source build must yield a nonempty frontend entrypoint."""
    frontend = _derivation_arguments("frontend.nix")
    install_check = expect_instance(
        expect_binding(frontend.values, "installCheckPhase").value,
        IndentedString,
    )
    commands = command_texts(parse_shell(indented_string_body(install_check.rebuild())))

    assert commands == [
        "runHook preInstallCheck",
        'test -s "$out/dist/index.html"',
        "runHook postInstallCheck",
    ]


def test_unsloth_desktop_rejects_every_runtime_installer_from_app_bundle() -> None:
    """The artifact gate must reject root, Studio, and Python installers."""
    desktop = _derivation_arguments("desktop.nix")
    install_check = expect_instance(
        expect_binding(desktop.values, "installCheckPhase").value,
        IndentedString,
    )
    shell = parse_shell(indented_string_body(install_check.rebuild()))
    installer_finds = [
        command
        for command in command_texts(shell, "find")
        if 'find "$app" -type f' in command
    ]

    assert len(installer_finds) == 1
    assert all(
        f"-name {installer}" in installer_finds[0]
        for installer in (
            "install.sh",
            "install.ps1",
            "setup.sh",
            "setup.ps1",
            "install_python_stack.py",
        )
    )


def test_unsloth_desktop_uses_store_strings_for_backend_identity() -> None:
    """The artifact gate must not initialize mutable Xcode state as a build user."""
    desktop = _derivation_arguments("desktop.nix")
    install_check = expect_instance(
        expect_binding(desktop.values, "installCheckPhase").value,
        IndentedString,
    )
    shell = parse_shell(indented_string_body(install_check.rebuild()))

    assert [
        command for command in command_texts(shell) if "/bin/strings" in command
    ] == ['__NIX_INTERP__/bin/strings -a "$executable"']


def test_unsloth_desktop_install_check_tracks_package_version() -> None:
    """The bundle gate must validate the release selected by sources.json."""
    desktop = _derivation_arguments("desktop.nix")
    install_check = expect_instance(
        expect_binding(desktop.values, "installCheckPhase").value,
        IndentedString,
    )
    shell = parse_shell(indented_string_body(install_check.rebuild()))

    for heredoc in iter_nodes(shell.tree.root_node, "heredoc_body"):
        source = textwrap.dedent("    " + node_text(heredoc, shell.sanitized))
        for statement in ast.parse(source).body:
            if not isinstance(statement, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "expected"
                for target in statement.targets
            ):
                continue
            expected = ast.literal_eval(statement.value)
            if isinstance(expected, dict) and "CFBundleShortVersionString" in expected:
                assert expected["CFBundleShortVersionString"] == "__NIX_INTERP__"
                return
    raise AssertionError("desktop install check has no bundle metadata contract")


def test_unsloth_llama_cpp_installs_conversion_script_from_absolute_source_root() -> (
    None
):
    """CMake's build-dir phase must address the relative sourceRoot absolutely."""
    llama = _derivation_arguments("llama-cpp.nix")
    install_phase = expect_instance(
        expect_binding(llama.values, "installPhase").value,
        IndentedString,
    )
    install_commands = command_texts(
        parse_shell(indented_string_body(install_phase.rebuild())),
        "install",
    )

    assert install_commands[-1] == (
        'install -m0644 "$NIX_BUILD_TOP/$sourceRoot/convert_hf_to_gguf.py" \\\n'
        '      "$out/convert_hf_to_gguf.py"'
    )


def test_unsloth_desktop_installs_studio_agpl_license() -> None:
    """The public desktop artifact must ship its declared AGPL license."""
    desktop = _derivation_arguments("desktop.nix")
    post_install = expect_instance(
        expect_binding(desktop.values, "postInstall").value,
        IndentedString,
    )
    install_commands = command_texts(
        parse_shell(indented_string_body(post_install.rebuild())),
        "install",
    )

    assert install_commands == [
        'install -m0644 "$NIX_BUILD_TOP/$sourceRoot/studio/LICENSE.AGPL-3.0" \\\n'
        '      "$out/share/licenses/unsloth-desktop/LICENSE"'
    ]
    assert_nix_ast_equal(
        expect_binding(
            expect_instance(
                expect_binding(desktop.values, "meta").value, AttributeSet
            ).values,
            "license",
        ).value,
        "lib.licenses.agpl3Only",
    )


def test_unsloth_native_helper_flags_match_the_audited_offline_closure() -> None:
    """Each helper must retain its exact arm64, static, and offline CMake policy."""
    llama = _derivation_arguments("llama-cpp.nix")
    assert_nix_ast_equal(
        expect_binding(llama.values, "cmakeFlags").value,
        """[
          (lib.cmakeBool "BUILD_SHARED_LIBS" false)
          (lib.cmakeBool "CMAKE_BUILD_WITH_INSTALL_RPATH" true)
          (lib.cmakeFeature "CMAKE_INSTALL_RPATH" "@loader_path")
          (lib.cmakeFeature "CMAKE_OSX_ARCHITECTURES" "arm64")
          (lib.cmakeFeature "CMAKE_OSX_DEPLOYMENT_TARGET" "13.3")
          (lib.cmakeBool "GGML_BACKEND_DL" false)
          (lib.cmakeBool "GGML_METAL" true)
          (lib.cmakeBool "GGML_METAL_EMBED_LIBRARY" true)
          (lib.cmakeBool "GGML_METAL_USE_BF16" true)
          (lib.cmakeBool "GGML_NATIVE" false)
          (lib.cmakeBool "LLAMA_BUILD_EXAMPLES" true)
          (lib.cmakeBool "LLAMA_BUILD_SERVER" true)
          (lib.cmakeBool "LLAMA_BUILD_TESTS" false)
          (lib.cmakeBool "LLAMA_BUILD_UI" false)
          (lib.cmakeBool "LLAMA_OPENSSL" false)
          (lib.cmakeBool "LLAMA_USE_PREBUILT_UI" false)
          (lib.cmakeBool "FETCHCONTENT_FULLY_DISCONNECTED" true)
        ]""",
    )
    llama_install_check = expect_instance(
        expect_binding(llama.values, "installCheckPhase").value,
        IndentedString,
    )
    llama_install_commands = command_texts(
        parse_shell(indented_string_body(llama_install_check.rebuild()))
    )
    quantize_help = llama_install_commands.index('"$out/bin/llama-quantize" --help')
    assert llama_install_commands[quantize_help - 1 : quantize_help + 4] == [
        "set +e",
        '"$out/bin/llama-quantize" --help',
        "set -e",
        'test "$quantizeStatus" -eq 1',
        '[[ "$quantizeHelp" == *usage:* ]]',
    ]
    whisper = _derivation_arguments("whisper-cpp.nix")
    assert_nix_ast_equal(
        expect_binding(whisper.values, "cmakeFlags").value,
        """[
          (lib.cmakeBool "BUILD_SHARED_LIBS" false)
          (lib.cmakeFeature "CMAKE_OSX_ARCHITECTURES" "arm64")
          (lib.cmakeFeature "CMAKE_OSX_DEPLOYMENT_TARGET" "13.3")
          (lib.cmakeBool "GGML_BACKEND_DL" false)
          (lib.cmakeBool "GGML_METAL" true)
          (lib.cmakeBool "GGML_METAL_EMBED_LIBRARY" true)
          (lib.cmakeBool "GGML_NATIVE" false)
          (lib.cmakeBool "WHISPER_BUILD_EXAMPLES" true)
          (lib.cmakeBool "WHISPER_BUILD_SERVER" true)
          (lib.cmakeBool "WHISPER_BUILD_TESTS" false)
          (lib.cmakeBool "WHISPER_CURL" false)
          (lib.cmakeBool "FETCHCONTENT_FULLY_DISCONNECTED" true)
        ]""",
    )
    stable_diffusion = _derivation_arguments("stable-diffusion-cpp.nix")
    assert_nix_ast_equal(
        expect_binding(stable_diffusion.values, "cmakeFlags").value,
        """[
          (lib.cmakeFeature "CMAKE_OSX_ARCHITECTURES" "arm64")
          (lib.cmakeFeature "CMAKE_OSX_DEPLOYMENT_TARGET" "13.3")
          (lib.cmakeBool "GGML_BACKEND_DL" false)
          (lib.cmakeBool "GGML_METAL_EMBED_LIBRARY" true)
          (lib.cmakeBool "GGML_NATIVE" false)
          (lib.cmakeBool "SD_BUILD_EXAMPLES" true)
          (lib.cmakeBool "SD_BUILD_SHARED_LIBS" false)
          (lib.cmakeBool "SD_BUILD_SHARED_GGML_LIB" false)
          (lib.cmakeBool "SD_METAL" true)
          (lib.cmakeBool "SD_SERVER_BUILD_FRONTEND" false)
          (lib.cmakeBool "SD_WEBM" true)
          (lib.cmakeBool "SD_WEBP" true)
          (lib.cmakeBool "FETCHCONTENT_FULLY_DISCONNECTED" true)
        ]""",
    )


def test_unsloth_recorded_hashes_and_artifact_gate_are_explicit() -> None:
    """Recorded FOD hashes and real-artifact validation remain distinct gates."""
    closure_hashes = _json("closure-hashes.json")
    assert isinstance(closure_hashes, dict)
    assert set(closure_hashes) == {
        "cargoHash",
        "frontendNpmDepsHash",
        "oxcNpmDepsHash",
    }
    assert all(
        isinstance(value, str) and value.startswith("sha256-")
        for value in closure_hashes.values()
    )
    artifact_validation = _json("artifact-validation.json")
    assert isinstance(artifact_validation, dict)
    checks = artifact_validation["checks"]
    assert isinstance(checks, list)
    assert checks[0] in {"frontend-dist-manifest", "frontend-source-build"}
    assert checks[1:] == [
        "oxc-valid-and-invalid-programs",
        "python-import-and-cli",
        "native-helper-arm64-and-help",
        "app-plist-architecture-signature",
        "store-path-app-candidate-backend-smoke",
        "contained-direct-store-path-app-runtime",
        "no-updater-or-runtime-installer-endpoints",
    ]
    assert artifact_validation["runtimeEvidenceSchemaVersion"] == 3
    assert artifact_validation["status"] == "passed"
    runtime_evidence = artifact_validation["runtimeEvidence"]
    assert isinstance(runtime_evidence, dict)
    assert set(runtime_evidence) == {
        "appCandidate",
        "backendExecutable",
        "backendRuntimeEntrypoint",
        "health",
        "listenerOwnership",
        "sandbox",
        "schemaVersion",
        "status",
        "studioRootIdentity",
        "teardown",
    }
    assert runtime_evidence["schemaVersion"] == 3


def test_unsloth_export_gate_requires_persisted_runtime_and_closure_evidence() -> None:
    """Hashes and a status word alone must never expose the desktop package."""
    scope = _package_scope()
    assert_nix_ast_equal(
        expect_binding(scope, "smokeEvidenceComplete").value,
        "(artifactValidation.storePathAppCandidateSmokeOutput or null) "
        '== "${storePathAppCandidateSmoke}"',
    )
    assert_nix_ast_equal(
        expect_binding(scope, "runtimeEvidence").value,
        "artifactValidation.runtimeEvidence or null",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "runtimeEvidenceComplete").value,
        '''builtins.isAttrs runtimeEvidence
          && (artifactValidation.runtimeEvidenceSchemaVersion or null) == 3
          && (runtimeEvidence.schemaVersion or null) == 3
          && (runtimeEvidence.status or null) == "passed"
          && (runtimeEvidence.teardown or null) == "passed"
          && (runtimeEvidence.sandbox or null) == "passed"
          && (runtimeEvidence.listenerOwnership or null) == "passed"
          && (runtimeEvidence.appCandidate or null) == "${appCandidate}"
          && (runtimeEvidence.backendExecutable or null) == "${backend}/bin/unsloth"
          && (runtimeEvidence.backendRuntimeEntrypoint or null)
            == "${backend.venv}/bin/unsloth"
          && builtins.isAttrs (runtimeEvidence.health or null)
          && builtins.attrNames runtimeEvidence.health == [ "service" "status" ]
          && (runtimeEvidence.health.service or null) == "Unsloth UI Backend"
          && (runtimeEvidence.health.status or null) == "healthy"
          && (runtimeEvidence.studioRootIdentity or null) == "passed"''',
    )
    assert_nix_ast_equal(
        expect_binding(scope, "closureIdentityComplete").value,
        """(closurePlan.app.version or null) == version
          && (closurePlan.app.tag or null) == "v${version}"
          && (closurePlan.app.commit or null) == source.commit
          && (closurePlan.app.sourceHash or null) == desktopSourceHash
          && (closurePlan.backend.version or null) == backendVersion
          && (closurePlan.backend.sdistHash or null) == backendSourceHash
          && (closurePlan.releaseManifest.version or null) == version
          && (closurePlan.releaseManifest.pypiVersion or null) == backendVersion
          && (closurePlan.releaseManifest.hash or null)
            == (hashEntryFor "sha256" source.urls.releaseManifest).hash""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "closureStateAllowsExport").value,
        """(closurePlan.status == "ready-for-promotion"
            && closurePlan.packageExported == false)
          || (closurePlan.status == "exported-and-validated"
            && closurePlan.packageExported == true)""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "unresolvedBuildGates").value,
        '''lib.optional (closureHashes.oxcNpmDepsHash == null) "oxcNpmDepsHash"
          ++ lib.optional (closureHashes.frontendNpmDepsHash == null) "frontendNpmDepsHash"
          ++ lib.optional (closureHashes.cargoHash == null) "cargoHash"
          ++ lib.optional (artifactValidation.status != "passed") "artifact-validation"
          ++ lib.optional (!smokeEvidenceComplete) "store-path-smoke-evidence"
          ++ lib.optional (!runtimeEvidenceComplete) "runtime-evidence"
          ++ lib.optional (!closureIdentityComplete) "closure-plan-identity"
          ++ lib.optional (closurePlan.blockers != [ ]) "closure-plan-blockers"
          ++ lib.optional (!closureStateAllowsExport) "closure-plan-status"''',
    )
