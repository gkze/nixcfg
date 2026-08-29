"""Tests for nix-manipulator-backed Nix expression builders."""

import json

import pytest
from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.inherit import Inherit
from nix_manipulator.expressions.let import LetExpression
from nix_manipulator.expressions.operator import Operator
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.path import NixPath
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.flake_lock import FlakeLockNode, LockedRef
from lib.nix.models.sources import HashEntry, SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._nix_source import nix_source_fragment_expr
from lib.update.flake import (
    flake_fetch_expr,
    flake_fetch_expression,
    nixpkgs_expression,
)
from lib.update.nix import (
    _build_fetch_from_github_call,
    _build_fetch_from_github_expr,
    _build_fetch_pnpm_deps_expr,
    _build_fetchgit_call,
    _build_fetchgit_expr,
    _build_flake_attr_expr,
    _build_nix_expr,
    _build_overlay_attr_expr,
    _build_overlay_expr,
    _build_overlay_expression,
    _build_package_path_attr_expr,
    _build_pnpm_10_nodejs_22_expr,
    _build_repo_package_attr_expr,
    _contextual_overlay_bindings,
)
from lib.update.nix_expr import identifier_attr_path
from lib.update.paths import REPO_ROOT
from lib.update.sources import nix_source_names


def test_flake_fetch_expr_builds_parseable_fetch_tree() -> None:
    """flake_fetch_expr should emit valid fetchTree Nix."""
    node = FlakeLockNode(
        locked=LockedRef(
            type="github",
            owner="NixOS",
            repo="nixpkgs",
            rev="abc123",
            narHash="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        ),
    )

    expr = flake_fetch_expr(node)

    assert_nix_ast_equal(expr, flake_fetch_expression(node))


def test_flake_fetch_expr_builds_git_fetch_tree_with_submodules() -> None:
    """Generic git flake inputs should retain submodule fetch metadata."""
    node = FlakeLockNode(
        locked=LockedRef.model_validate({
            "type": "git",
            "url": "https://github.com/desktop/desktop.git",
            "rev": "abc123",
            "narHash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            "submodules": True,
        }),
    )

    assert_nix_ast_equal(
        flake_fetch_expr(node),
        FunctionCall(
            name=identifier_attr_path("builtins", "fetchTree"),
            argument=AttributeSet(
                values=[
                    Binding(name="type", value="git"),
                    Binding(name="url", value="https://github.com/desktop/desktop.git"),
                    Binding(name="rev", value="abc123"),
                    Binding(
                        name="narHash",
                        value="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                    ),
                    Binding(name="submodules", value=Primitive(value=True)),
                ]
            ),
        ),
    )


def test_flake_fetch_expr_builds_git_fetch_tree_without_submodules() -> None:
    """Generic git flake inputs should not invent submodule metadata."""
    node = FlakeLockNode(
        locked=LockedRef.model_validate({
            "type": "git",
            "url": "https://github.com/desktop/desktop.git",
            "rev": "abc123",
            "narHash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        }),
    )

    assert_nix_ast_equal(
        flake_fetch_expr(node),
        FunctionCall(
            name=identifier_attr_path("builtins", "fetchTree"),
            argument=AttributeSet(
                values=[
                    Binding(name="type", value="git"),
                    Binding(name="url", value="https://github.com/desktop/desktop.git"),
                    Binding(name="rev", value="abc123"),
                    Binding(
                        name="narHash",
                        value="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                    ),
                ]
            ),
        ),
    )


def test_flake_fetch_expr_rejects_incomplete_git_locked_ref() -> None:
    """Generic git fetchTree expressions need both a repository URL and revision."""
    with pytest.raises(ValueError, match="missing url/rev"):
        flake_fetch_expr(
            FlakeLockNode(
                locked=LockedRef.model_validate({
                    "type": "git",
                    "url": "https://github.com/desktop/desktop.git",
                    "narHash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                })
            )
        )


def test_nixpkgs_expression_uses_pinned_flake_input_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """nixpkgs_expression should default to the pinned nixpkgs flake input."""
    node = FlakeLockNode(
        locked=LockedRef(
            type="github",
            owner="NixOS",
            repo="nixpkgs",
            rev="abc123",
            narHash="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        ),
    )
    monkeypatch.delenv("NIXCFG_NIXPKGS_PATH", raising=False)
    monkeypatch.setattr("lib.update.flake.get_root_input_name", lambda _name: "nixpkgs")
    monkeypatch.setattr("lib.update.flake.get_flake_input_node", lambda _name: node)

    assert_nix_ast_equal(
        nixpkgs_expression(),
        FunctionCall(
            name=FunctionCall(
                name=Identifier(name="import"),
                argument=Parenthesis(value=flake_fetch_expression(node)),
            ),
            argument=AttributeSet.from_dict({
                "system": identifier_attr_path("builtins", "currentSystem")
            }),
        ),
    )


def test_nixpkgs_expression_honors_local_path_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """nixpkgs_expression should bypass flake lookups when a local path is set."""

    def _unexpected(*_args: object, **_kwargs: object) -> object:
        msg = "flake input lookup should not be used when NIXCFG_NIXPKGS_PATH is set"
        raise AssertionError(msg)

    monkeypatch.setenv("NIXCFG_NIXPKGS_PATH", "/tmp/nixpkgs")
    monkeypatch.setattr("lib.update.flake.get_root_input_name", _unexpected)
    monkeypatch.setattr("lib.update.flake.get_flake_input_node", _unexpected)

    assert_nix_ast_equal(
        nixpkgs_expression(),
        FunctionCall(
            name=FunctionCall(
                name=Identifier(name="import"),
                argument=NixPath(path="/tmp/nixpkgs"),
            ),
            argument=AttributeSet.from_dict({
                "system": identifier_attr_path("builtins", "currentSystem")
            }),
        ),
    )


def test_build_nix_expr_wraps_body_with_pkgs_binding() -> None:
    """_build_nix_expr should construct a parseable let-expression."""
    expr = _build_nix_expr("pkgs.hello")

    assert_nix_ast_equal(
        expr,
        LetExpression(
            local_variables=[Binding(name="pkgs", value=nixpkgs_expression())],
            value=identifier_attr_path("pkgs", "hello"),
        ),
    )


def test_build_nix_expr_can_isolate_fixed_output_probe_by_drv_hash() -> None:
    """Opt-in hash probes must not share fake-hash outputs across derivations."""
    expr = _build_nix_expr("pkgs.hello", isolate_by_drv_hash=True)

    assert_nix_ast_equal(
        expr,
        LetExpression(
            local_variables=[
                Binding(name="pkgs", value=nixpkgs_expression()),
                Binding(
                    name="nixcfgFixedOutputProbe",
                    value=identifier_attr_path("pkgs", "hello"),
                ),
            ],
            value=FunctionCall(
                name=FunctionCall(
                    name=identifier_attr_path(
                        "pkgs",
                        "testers",
                        "invalidateFetcherByDrvHash",
                    ),
                    argument=Parenthesis(
                        value=FunctionDefinition(
                            argument_set=Identifier(name="_"),
                            output=Identifier(name="nixcfgFixedOutputProbe"),
                        )
                    ),
                ),
                argument=AttributeSet(
                    values=[
                        Binding(
                            name="name",
                            value="nixcfg-fixed-output-probe",
                        )
                    ]
                ),
            ),
        ),
    )


def test_build_overlay_expr_supports_explicit_system() -> None:
    """_build_overlay_expr should produce parseable Nix for explicit systems."""
    expr = _build_overlay_expr("code-cursor", system="x86_64-linux")

    assert_nix_ast_equal(
        expr, _build_overlay_expression("code-cursor", system="x86_64-linux")
    )


def test_build_overlay_expr_imports_contextual_library_explicitly() -> None:
    """Source override probes retain real source hashes without ambient env."""
    override = SourceEntry(
        version="2.0.0",
        hashes=[HashEntry.create("srcHash", "sha256-source")],
    )

    expression = _build_overlay_expression(
        "demo",
        system="x86_64-linux",
        source_overrides={"demo": override},
    )

    assert isinstance(expression, LetExpression)
    contextual_import = expect_binding(expression.local_variables, "flake").value
    assert isinstance(contextual_import, FunctionCall)
    arguments = contextual_import.argument
    assert isinstance(arguments, AttributeSet)
    evaluation_context = expect_binding(arguments.values, "evaluationContext").value
    assert isinstance(evaluation_context, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(evaluation_context.values, "fakeHashes").value,
        Primitive(value=False),
    )
    overrides = expect_binding(evaluation_context.values, "sourceOverrides").value
    assert isinstance(overrides, FunctionCall)
    payload = overrides.argument
    assert isinstance(payload, StringPrimitive)
    assert json.loads(payload.value) == {"demo": override.to_dict()}

    fake_hash_expression = _build_overlay_expression(
        "demo",
        system="x86_64-linux",
    )
    assert isinstance(fake_hash_expression, LetExpression)
    fake_hash_import = expect_binding(
        fake_hash_expression.local_variables, "flake"
    ).value
    assert isinstance(fake_hash_import, FunctionCall)
    fake_hash_arguments = fake_hash_import.argument
    assert isinstance(fake_hash_arguments, AttributeSet)
    fake_hash_context = expect_binding(
        fake_hash_arguments.values, "evaluationContext"
    ).value
    assert isinstance(fake_hash_context, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(fake_hash_context.values, "fakeHashes").value,
        Primitive(value=True),
    )


def test_build_fetch_from_github_expr_is_parseable() -> None:
    """FetchFromGitHub helper should emit valid Nix via nix-manipulator."""
    expr = _build_fetch_from_github_expr(
        "element-hq",
        "element-desktop",
        rev="v1.11.0",
    )

    assert_nix_ast_equal(
        expr,
        _build_fetch_from_github_call(
            "element-hq",
            "element-desktop",
            rev="v1.11.0",
        ),
    )


def test_build_fetch_from_github_expr_supports_tag_post_fetch_and_expr_hash() -> None:
    """FetchFromGitHub helper should handle non-default optional fields."""
    expr = _build_fetch_from_github_expr(
        "getsentry",
        "sentry-cli",
        tag="v9.9.9",
        hash_value=StringPrimitive(
            value="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        ),
        post_fetch="rm -rf $out/*.xcarchive",
    )

    assert_nix_ast_equal(
        expr,
        _build_fetch_from_github_call(
            "getsentry",
            "sentry-cli",
            tag="v9.9.9",
            hash_value=StringPrimitive(
                value="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
            ),
            post_fetch="rm -rf $out/*.xcarchive",
        ),
    )


def test_build_fetch_from_github_expr_can_skip_submodules() -> None:
    """FetchFromGitHub helper should explicitly disable submodule fetching."""
    expr = _build_fetch_from_github_expr(
        "oxc-project",
        "tsgolint",
        tag="v0.23.0",
        fetch_submodules=False,
    )

    assert_nix_ast_equal(
        expr,
        _build_fetch_from_github_call(
            "oxc-project",
            "tsgolint",
            tag="v0.23.0",
            fetch_submodules=False,
        ),
    )
    fetcher = parse_nix_expr(expr)
    assert isinstance(fetcher, FunctionCall)
    argument = fetcher.argument
    assert isinstance(argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(argument.values, "fetchSubmodules").value,
        Primitive(value=False),
    )


def test_build_fetch_from_github_call_requires_exactly_one_selector() -> None:
    """Exactly one of rev or tag must be provided."""
    with pytest.raises(ValueError, match="Expected exactly one of rev or tag"):
        _build_fetch_from_github_call("element-hq", "element-desktop")

    with pytest.raises(ValueError, match="Expected exactly one of rev or tag"):
        _build_fetch_from_github_call(
            "element-hq",
            "element-desktop",
            rev="v1.11.0",
            tag="v1.11.0",
        )


def test_build_flake_attr_expr_quotes_dynamic_segments() -> None:
    """Quoted attribute selections should remain parseable for hyphenated keys."""
    expr = _build_flake_attr_expr(
        "path:/tmp/repo",
        "pkgs",
        "x86_64-linux",
        "deno",
        "version",
        quoted_indices=(1,),
    )

    assert_nix_ast_equal(
        expr,
        LetExpression(
            local_variables=[
                Binding(
                    name="flake",
                    value=FunctionCall(
                        name=identifier_attr_path("builtins", "getFlake"),
                        argument=StringPrimitive(value="path:/tmp/repo"),
                    ),
                ),
            ],
            value=identifier_attr_path(
                "flake",
                "pkgs",
                '"x86_64-linux"',
                "deno",
                "version",
            ),
        ),
    )


def test_build_fetch_pnpm_deps_expr_is_parseable() -> None:
    """FetchPnpmDeps helper should build the dependency derivation via the Nix AST."""
    src_call = _build_fetch_from_github_call(
        "element-hq",
        "element-web",
        tag="v1.12.14",
        hash_value="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    )
    expr = _build_fetch_pnpm_deps_expr(
        src_call,
        pname="element",
        version="1.12.14",
        fetcher_version=3,
    )

    assert_nix_ast_equal(
        expr,
        LetExpression(
            local_variables=[
                Binding(
                    name="src",
                    value=src_call,
                ),
            ],
            value=FunctionCall(
                name=identifier_attr_path("pkgs", "fetchPnpmDeps"),
                argument=AttributeSet(
                    values=[
                        Binding(name="pname", value=StringPrimitive(value="element")),
                        Binding(name="version", value=StringPrimitive(value="1.12.14")),
                        Inherit(names=[Identifier(name="src")]),
                        Binding(name="fetcherVersion", value=Primitive(value=3)),
                        Binding(
                            name="hash",
                            value=identifier_attr_path("pkgs", "lib", "fakeHash"),
                        ),
                    ],
                ),
            ),
        ),
    )


def test_build_fetch_pnpm_deps_expr_accepts_explicit_pnpm_toolchain() -> None:
    """FetchPnpmDeps helper should pin pnpm when callers need lockfile stability."""
    src_call = _build_fetch_from_github_call(
        "element-hq",
        "element-web",
        tag="v1.12.14",
        hash_value="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    )
    pnpm_expr = _build_pnpm_10_nodejs_22_expr()

    expr = _build_fetch_pnpm_deps_expr(
        src_call,
        pname="element",
        version="1.12.14",
        fetcher_version=3,
        pnpm=pnpm_expr,
    )

    assert_nix_ast_equal(
        expr,
        LetExpression(
            local_variables=[
                Binding(
                    name="src",
                    value=src_call,
                ),
            ],
            value=FunctionCall(
                name=identifier_attr_path("pkgs", "fetchPnpmDeps"),
                argument=AttributeSet(
                    values=[
                        Binding(name="pname", value=StringPrimitive(value="element")),
                        Binding(name="version", value=StringPrimitive(value="1.12.14")),
                        Inherit(names=[Identifier(name="src")]),
                        Binding(name="pnpm", value=pnpm_expr),
                        Binding(name="fetcherVersion", value=Primitive(value=3)),
                        Binding(
                            name="hash",
                            value=identifier_attr_path("pkgs", "lib", "fakeHash"),
                        ),
                    ],
                ),
            ),
        ),
    )


def test_pnpm_toolchain_builder_uses_supported_nodejs_override() -> None:
    """Updater pnpm expressions should use nixpkgs' supported Node override."""
    pnpm_override = _build_pnpm_10_nodejs_22_expr()
    override_args = expect_instance(pnpm_override.argument, AttributeSet)

    assert_nix_ast_equal(
        expect_binding(override_args.values, "nodejs-slim").value,
        identifier_attr_path("pkgs", "nodejs_22"),
    )


@pytest.mark.parametrize(
    "package_path",
    [
        "packages/emdash/default.nix",
        "packages/gitbutler/default.nix",
        "packages/goose-desktop/default.nix",
    ],
)
def test_pnpm_packages_use_supported_nodejs_override(package_path: str) -> None:
    """Local pnpm packages should use nixpkgs' supported Node override."""
    pnpm_override = expect_instance(
        nix_source_fragment_expr(package_path, "  pnpm = ", ";\n"),
        FunctionCall,
    )
    override_args = expect_instance(pnpm_override.argument, AttributeSet)

    nodejs = expect_binding(override_args.values, "nodejs-slim").value
    assert expect_instance(nodejs, Identifier).name == "nodejs"


def test_build_fetchgit_expr_is_parseable() -> None:
    """Fetchgit helper should emit valid Nix via nix-manipulator."""
    expr = _build_fetchgit_expr(
        "https://example.com/demo.git",
        "deadbeef",
    )

    assert_nix_ast_equal(
        expr,
        _build_fetchgit_call(
            "https://example.com/demo.git",
            "deadbeef",
        ),
    )


def test_build_fetchgit_expr_can_skip_submodules() -> None:
    """Fetchgit helper should explicitly disable submodule fetching when requested."""
    expr = _build_fetchgit_expr(
        "https://example.com/demo.git",
        "deadbeef",
        fetch_submodules=False,
    )

    assert_nix_ast_equal(
        expr,
        _build_fetchgit_call(
            "https://example.com/demo.git",
            "deadbeef",
            fetch_submodules=False,
        ),
    )
    fetchgit = parse_nix_expr(expr)
    assert isinstance(fetchgit, FunctionCall)
    argument = fetchgit.argument
    assert isinstance(argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(argument.values, "fetchSubmodules").value,
        Primitive(value=False),
    )


def test_build_overlay_attr_expr_wraps_selection_target() -> None:
    """Overlay attribute path helper should select attrs via the parsed AST."""
    expr = _build_overlay_attr_expr(
        "gemini-cli",
        ".node_modules",
        system="x86_64-linux",
    )

    assert_nix_ast_equal(
        expr,
        Select(
            expression=Parenthesis(
                value=_build_overlay_expression("gemini-cli", system="x86_64-linux"),
            ),
            attribute="node_modules",
        ),
    )


def test_build_overlay_attr_expr_skips_empty_attr_segments() -> None:
    """Overlay attr helper should tolerate redundant dots in attribute paths."""
    expr = _build_overlay_attr_expr(
        "gemini-cli",
        ".passthru..denoDeps",
        system="x86_64-linux",
    )

    assert_nix_ast_equal(
        expr,
        Select(
            expression=Select(
                expression=Parenthesis(
                    value=_build_overlay_expression(
                        "gemini-cli",
                        system="x86_64-linux",
                    ),
                ),
                attribute="passthru",
            ),
            attribute="denoDeps",
        ),
    )


def test_build_package_path_attr_expr_uses_shared_package_materialization() -> None:
    """Package probes should retain normal package dependency injection."""
    expr = _build_package_path_attr_expr(
        "anthropic-cli",
        "",
        system="aarch64-darwin",
        repo_root=str(REPO_ROOT),
    )

    package_materialization = FunctionCall(
        name=FunctionCall(
            name=Identifier(name="import"),
            argument=Parenthesis(
                value=BinaryExpression(
                    operator=Operator(name="+"),
                    left=identifier_attr_path("rootFlake", "outPath"),
                    right=StringPrimitive(value="/lib/package-materialization.nix"),
                )
            ),
        ),
        argument=AttributeSet.from_dict({
            "src": identifier_attr_path("rootFlake", "outPath"),
            "lib": identifier_attr_path("pkgs", "lib"),
            "outputs": Identifier(name="flake"),
        }),
    )
    package_function = Select(
        expression=Parenthesis(
            value=FunctionCall(
                name=identifier_attr_path(
                    "packageMaterialization",
                    "packageFunctionsForSystem",
                ),
                argument=Identifier(name="system"),
            )
        ),
        attribute='"anthropic-cli"',
    )
    package_expr = FunctionCall(
        name=FunctionCall(
            name=FunctionCall(
                name=identifier_attr_path("pkgs", "lib", "callPackageWith"),
                argument=Identifier(name="applied"),
            ),
            argument=package_function,
        ),
        argument=AttributeSet(
            values=[
                Binding(
                    name="inputs",
                    value=identifier_attr_path("rootFlake", "inputs"),
                ),
                Binding(name="outputs", value=Identifier(name="flake")),
            ]
        ),
    )
    expected = LetExpression(
        local_variables=[
            *_contextual_overlay_bindings(
                system="aarch64-darwin",
                repo_root=str(REPO_ROOT),
                source_overrides=None,
            ),
            Binding(
                name="packageMaterialization",
                value=package_materialization,
            ),
        ],
        value=package_expr,
    )

    assert_nix_ast_equal(expr, expected)

    attr_expr = _build_package_path_attr_expr(
        "anthropic-cli",
        ".passthru.node_modules",
        system="aarch64-darwin",
        repo_root=str(REPO_ROOT),
    )
    assert_nix_ast_equal(
        attr_expr,
        LetExpression(
            local_variables=expected.local_variables,
            value=Select(
                expression=Select(
                    expression=Parenthesis(value=package_expr),
                    attribute="passthru",
                ),
                attribute="node_modules",
            ),
        ),
    )

    override_expr = _build_package_path_attr_expr(
        "anthropic-cli",
        "",
        system="aarch64-darwin",
        repo_root=str(REPO_ROOT),
        package_args={"dependency": Identifier(name="replacement")},
    )
    assert_nix_ast_equal(
        override_expr,
        LetExpression(
            local_variables=expected.local_variables,
            value=FunctionCall(
                name=package_expr.name,
                argument=AttributeSet(
                    values=[
                        Binding(
                            name="inputs",
                            value=identifier_attr_path("rootFlake", "inputs"),
                        ),
                        Binding(name="outputs", value=Identifier(name="flake")),
                        Binding(
                            name="dependency",
                            value=Identifier(name="replacement"),
                        ),
                    ]
                ),
            ),
        ),
    )


def test_build_repo_package_attr_expr_selects_nested_attributes() -> None:
    """Internal package probes should select nested attrs from the package call."""
    expr = parse_nix_expr(
        _build_repo_package_attr_expr(
            "packages/example/package.nix",
            ".passthru.node_modules",
            system="aarch64-darwin",
            repo_root=str(REPO_ROOT),
        )
    )
    selection = expect_instance(expr, Select)
    package_call = expect_instance(selection.expression, Parenthesis)

    assert selection.attribute == "passthru.node_modules"
    assert isinstance(package_call.value, FunctionCall)


def test_gogcli_package_declares_its_overlay_dependency() -> None:
    """Direct package evaluation must expose gogcli to callPackageWith."""
    assert_nix_ast_equal(
        (REPO_ROOT / "packages/gogcli/default.nix").read_text(encoding="utf-8"),
        """
        { gogcli, ... }:
        gogcli
        """,
    )


def test_nix_source_names_uses_parseable_ast_expression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """nix_source_names should evaluate a valid expression generated via AST."""
    captured: dict[str, str] = {}

    async def _fake_run_nix(args: list[str], **_: object) -> object:
        captured["expr"] = args[-1]

        class _Result:
            returncode = 0
            stdout = '["foo", "bar"]'
            stderr = ""

        return _Result()

    monkeypatch.setattr("lib.update.sources.shutil.which", lambda _tool: "/usr/bin/nix")
    monkeypatch.setattr("lib.update.sources.run_nix", _fake_run_nix)

    names = nix_source_names()

    assert names == {"foo", "bar"}
    assert_nix_ast_equal(
        captured["expr"],
        LetExpression(
            local_variables=[
                Binding(
                    name="flake",
                    value=FunctionCall(
                        name=identifier_attr_path("builtins", "getFlake"),
                        argument=StringPrimitive(
                            value=f"git+file://{REPO_ROOT}?dirty=1"
                        ),
                    ),
                ),
            ],
            value=FunctionCall(
                name=identifier_attr_path("builtins", "attrNames"),
                argument=identifier_attr_path("flake", "outputs", "lib", "sources"),
            ),
        ),
    )
