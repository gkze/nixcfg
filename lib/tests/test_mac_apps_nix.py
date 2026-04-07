"""Regression checks for macOS application bundle management."""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from functools import cache
from pathlib import Path

import pytest
from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.expression import NixExpression
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.inherit import Inherit
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.operator import Operator
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    expect_scope_binding,
    parse_nix_expr,
)
from lib.tests._nix_eval import nix_attrset, nix_eval_raw, nix_import, nix_let, nix_list
from lib.update.flake import nixpkgs_expression
from lib.update.nix_expr import identifier_attr_path
from lib.update.paths import REPO_ROOT


@cache
def _module_output(relative_path: str) -> AttributeSet:
    """Parse one Nix module and return its top-level output attrset."""
    expr = expect_instance(
        parse_nix_expr(Path(REPO_ROOT / relative_path).read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    return expect_instance(expr.output, AttributeSet)


@cache
def _rsync_path() -> str:
    """Return a real rsync path for shell-script smoke tests."""
    return shutil.which("rsync") or "/usr/bin/rsync"


def _mac_apps_eval(expr: NixExpression) -> str:
    """Evaluate one expression in a lightweight ``lib/mac-apps.nix`` context."""
    lib_expr = BinaryExpression(
        left=identifier_attr_path("nixpkgs", "lib"),
        operator=Operator(name="//"),
        right=nix_attrset({
            "getExe": FunctionDefinition(
                argument_set=Identifier(name="value"),
                output=FunctionCall(
                    name=identifier_attr_path("builtins", "toString"),
                    argument=Identifier(name="value"),
                ),
            )
        }),
    )
    wrapped_expr = nix_let(
        {
            "nixpkgs": nixpkgs_expression(),
            "pkgs": nix_attrset({"rsync": _rsync_path()}),
            "lib": lib_expr,
            "macApps": FunctionCall(
                name=nix_import(REPO_ROOT / "lib/mac-apps.nix"),
                argument=nix_attrset({
                    "lib": Identifier(name="lib"),
                    "pkgs": Identifier(name="pkgs"),
                }),
            ),
        },
        expr,
    )
    return nix_eval_raw(wrapped_expr)


def _fake_mac_app_package(
    pname: str,
    out_path: str,
    bundle_name: str,
    *,
    bundle_rel_path: str | None = None,
    install_mode: str | None = None,
) -> AttributeSet:
    """Build one fake package carrying ``passthru.macApp`` metadata."""
    mac_app: dict[str, str] = {
        "bundleName": bundle_name,
        "bundleRelPath": bundle_rel_path or f"Applications/{bundle_name}",
    }
    if install_mode is not None:
        mac_app["installMode"] = install_mode
    return nix_attrset({
        "pname": pname,
        "outPath": out_path,
        "passthru.macApp": mac_app,
    })


@cache
def _mac_apps_script(
    mode: str,
    *,
    writable: bool,
    package_out_path: str = "/nix/store/fake-app",
    bundle_name: str = "Fake.app",
    bundle_rel_path: str = "Applications/Fake.app",
    state_directory: str = "/Applications/.nixcfg-mac-apps",
    state_name: str = "test-manager",
    target_directory: str = "/Applications",
) -> str:
    """Evaluate ``systemApplicationsScript`` for a fake macOS app entry."""
    expression = nix_let(
        {
            "fakePkg": _fake_mac_app_package(
                "fake-app",
                package_out_path,
                bundle_name,
                bundle_rel_path=bundle_rel_path,
                install_mode=mode,
            )
        },
        FunctionCall(
            name=identifier_attr_path("macApps", "systemApplicationsScript"),
            argument=nix_attrset({
                "entries": nix_list([
                    nix_attrset({
                        "package": Identifier(name="fakePkg"),
                        "bundleName": bundle_name,
                        "mode": mode,
                    })
                ]),
                "stateDirectory": state_directory,
                "stateName": state_name,
                "targetDirectory": target_directory,
                "writable": writable,
            }),
        ),
    )
    return _mac_apps_eval(expression)


@cache
def _profile_bundle_leak_audit_script(
    managed_bundle_names: tuple[str, ...],
    package_paths: tuple[str, ...],
) -> str:
    """Evaluate ``profileBundleLeakAuditScript`` for fake Home Manager packages."""
    return _mac_apps_eval(
        FunctionCall(
            name=identifier_attr_path("macApps", "profileBundleLeakAuditScript"),
            argument=nix_attrset({
                "managedBundleNames": nix_list(list(managed_bundle_names)),
                "packagePaths": nix_list(list(package_paths)),
                "label": "home.packages",
            }),
        )
    )


def _managed_app_overlap_assertion_result(
    package_lists: list[AttributeSet],
) -> dict[str, object]:
    """Evaluate ``managedAppsNotInPackageListsAssertion`` and decode its JSON."""
    expression = nix_let(
        {
            "managedPkg": _fake_mac_app_package(
                "cursor",
                "/nix/store/fake-cursor",
                "Cursor.app",
            )
        },
        FunctionCall(
            name=identifier_attr_path("builtins", "toJSON"),
            argument=Parenthesis(
                value=FunctionCall(
                    name=identifier_attr_path(
                        "macApps", "managedAppsNotInPackageListsAssertion"
                    ),
                    argument=nix_attrset({
                        "entries": nix_list([
                            nix_attrset({
                                "package": Identifier(name="managedPkg"),
                                "bundleName": "Cursor.app",
                                "mode": "copy",
                            })
                        ]),
                        "packageLists": nix_list(package_lists),
                    }),
                )
            ),
        ),
    )
    payload = json.loads(_mac_apps_eval(expression))
    assert isinstance(payload, dict)
    return payload


@cache
def _mac_apps_source_fragment(start_marker: str, end_marker: str) -> IndentedString:
    """Return one source fragment from ``lib/mac-apps.nix`` as an indented string."""
    mac_apps = (REPO_ROOT / "lib/mac-apps.nix").read_text(encoding="utf-8")
    start = mac_apps.index(start_marker)
    end = mac_apps.index(end_marker, start)
    fragment = textwrap.dedent(mac_apps[start:end]).rstrip()
    parsed = parse_nix_expr(f"''\n{fragment}\n''")
    return expect_instance(parsed, IndentedString)


def _curried_call(
    name: NixExpression,
    first_arg: NixExpression,
    second_arg: NixExpression,
) -> FunctionCall:
    """Build one curried function application with stable precedence."""
    rendered_first_arg: NixExpression = first_arg
    if not isinstance(
        first_arg,
        Identifier | NixList | Parenthesis | Primitive | Select | StringPrimitive,
    ):
        rendered_first_arg = Parenthesis(value=first_arg)
    return FunctionCall(
        name=FunctionCall(name=name, argument=rendered_first_arg),
        argument=Parenthesis(value=second_arg),
    )


def _system_applications_script_expr(
    entries: NixExpression,
    *,
    state_name: str,
    writable: bool,
) -> FunctionCall:
    """Build the expected ``macApps.systemApplicationsScript`` invocation."""
    return FunctionCall(
        name=identifier_attr_path("macApps", "systemApplicationsScript"),
        argument=nix_attrset({
            "entries": entries,
            "stateDirectory": "/Applications/.nixcfg-mac-apps",
            "stateName": state_name,
            "writable": writable,
        }),
    )


def _mac_app_metadata_attrset(
    bundle_name: object,
    bundle_rel_path: object,
    install_mode: str,
) -> AttributeSet:
    """Build the expected ``passthru.macApp`` metadata attrset."""
    return nix_attrset({
        "macApp": {
            "bundleName": bundle_name,
            "bundleRelPath": bundle_rel_path,
            "installMode": install_mode,
        }
    })


def test_copy_mode_replaces_symlinked_application_destinations() -> None:
    """Copy-mode installs should replace old symlink targets before rsync."""
    fragment = _mac_apps_source_fragment(
        '              if [ -L "$dst" ] || { [ -e "$dst" ] && [ ! -d "$dst" ]; }; then\n',
        '              mkdir -p "$dst"\n',
    )

    assert fragment.value == (
        "\n"
        'if [ -L "$dst" ] || { [ -e "$dst" ] && [ ! -d "$dst" ]; }; then\n'
        "  rm -rf -- \"''${dst:?}\"\n"
        "fi\n"
    )


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_manifest_cleanup_checks_other_mac_app_managers_first(tmp_path: Path) -> None:
    """Stale cleanup should not delete apps still claimed by another manifest."""
    target_directory = tmp_path / "Applications"
    state_directory = tmp_path / ".nixcfg-mac-apps"
    stale_app = target_directory / "Cursor.app"
    fake_package = tmp_path / "fake-package"
    fake_bundle = fake_package / "Applications" / "Fake.app"

    stale_app.mkdir(parents=True)
    fake_bundle.mkdir(parents=True)
    state_directory.mkdir()
    (state_directory / "test-manager.txt").write_text("Cursor.app\n", encoding="utf-8")
    (state_directory / "other-manager.txt").write_text("Cursor.app\n", encoding="utf-8")

    script = _mac_apps_script(
        "symlink",
        writable=False,
        package_out_path=str(fake_package),
        state_directory=str(state_directory),
        target_directory=str(target_directory),
    )
    result = subprocess.run(  # noqa: S603
        ["/bin/bash", "-c", f"set -euo pipefail\n{script}"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert stale_app.is_dir()
    assert (target_directory / "Fake.app").is_symlink()
    assert (target_directory / "Fake.app").resolve() == fake_bundle.resolve()
    assert (state_directory / "test-manager.txt").read_text(
        encoding="utf-8"
    ) == "Fake.app\n"
    assert (state_directory / "other-manager.txt").read_text(encoding="utf-8") == (
        "Cursor.app\n"
    )


def test_embedded_home_manager_defers_system_app_management_to_darwin() -> None:
    """Integrated nix-darwin should keep one /Applications owner and replay cleanup."""
    darwin_config = expect_instance(
        expect_binding(
            _module_output("modules/darwin/base.nix").values, "config"
        ).value,
        AttributeSet,
    )
    darwin_system = expect_instance(
        expect_binding(darwin_config.values, "system").value,
        AttributeSet,
    )
    darwin_activation_scripts = expect_instance(
        expect_binding(darwin_system.values, "activationScripts").value,
        AttributeSet,
    )
    darwin_applications = expect_instance(
        expect_binding(darwin_activation_scripts.values, "applications").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(darwin_applications.values, "text").value,
        FunctionCall(
            name=identifier_attr_path("lib", "mkAfter"),
            argument=Parenthesis(
                value=_system_applications_script_expr(
                    Identifier(name="activeMacAppEntries"),
                    state_name="darwin-system",
                    writable=False,
                )
            ),
        ),
    )

    home_config = expect_instance(
        expect_binding(
            _module_output("modules/home/darwin.nix").values, "config"
        ).value,
        AttributeSet,
    )
    home_binding = expect_instance(
        expect_binding(home_config.values, "home").value,
        AttributeSet,
    )
    home_activation = expect_instance(
        expect_binding(home_binding.values, "activation").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(home_activation.values, "nixcfgSystemApplications").value,
        _curried_call(
            identifier_attr_path("lib", "mkIf"),
            Identifier(name="standaloneActivation"),
            _curried_call(
                identifier_attr_path("lib", "hm", "dag", "entryAfter"),
                nix_list(["installPackages"]),
                _system_applications_script_expr(
                    identifier_attr_path("cfg", "systemApplications"),
                    state_name="home-manager",
                    writable=True,
                ),
            ),
        ),
    )


def test_home_manager_mac_app_module_asserts_managed_apps_stay_out_of_home_packages() -> (
    None
):
    """Managed macOS app bundles should not also be installed via ``home.packages``."""
    home_config = expect_instance(
        expect_binding(
            _module_output("modules/home/darwin.nix").values, "config"
        ).value,
        AttributeSet,
    )
    assertions = expect_instance(
        expect_binding(home_config.values, "assertions").value,
        FunctionCall,
    )
    optionals_call = expect_instance(assertions.name, FunctionCall)
    assert_nix_ast_equal(optionals_call.name, identifier_attr_path("lib", "optionals"))
    assert_nix_ast_equal(
        optionals_call.argument,
        BinaryExpression(
            left=identifier_attr_path("cfg", "systemApplications"),
            operator=Operator(name="!="),
            right=nix_list([]),
        ),
    )

    assertion_list = expect_instance(assertions.argument, NixList).value
    assert len(assertion_list) == 2

    unique_call = expect_instance(
        expect_instance(assertion_list[0], Parenthesis).value,
        FunctionCall,
    )
    assert_nix_ast_equal(
        unique_call.name,
        identifier_attr_path("macApps", "uniqueBundleNamesAssertion"),
    )
    assert_nix_ast_equal(
        unique_call.argument, identifier_attr_path("cfg", "systemApplications")
    )

    overlap_call = expect_instance(
        expect_instance(assertion_list[1], Parenthesis).value,
        FunctionCall,
    )
    assert_nix_ast_equal(
        overlap_call.name,
        identifier_attr_path("macApps", "managedAppsNotInPackageListsAssertion"),
    )
    overlap_args = expect_instance(overlap_call.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(overlap_args.values, "entries").value,
        identifier_attr_path("cfg", "systemApplications"),
    )

    package_lists = expect_instance(
        expect_binding(overlap_args.values, "packageLists").value,
        NixList,
    )
    assert len(package_lists.value) == 1
    package_list = expect_instance(package_lists.value[0], AttributeSet)
    label = expect_instance(
        expect_binding(package_list.values, "label").value,
        StringPrimitive,
    )
    assert label.value == "home.packages"
    assert_nix_ast_equal(
        package_list,
        AttributeSet(
            values=[
                Binding(name="label", value=StringPrimitive(value="home.packages")),
                Inherit(
                    from_expression=identifier_attr_path("config", "home"),
                    names=[Identifier(name="packages")],
                ),
            ]
        ),
    )


def test_home_manager_mac_app_module_audits_profile_bundle_leaks() -> None:
    """Home Manager should audit profile package outputs for managed app bundles."""
    home_config = expect_instance(
        expect_binding(
            _module_output("modules/home/darwin.nix").values, "config"
        ).value,
        AttributeSet,
    )
    home_binding = expect_instance(
        expect_binding(home_config.values, "home").value, AttributeSet
    )
    home_activation = expect_instance(
        expect_binding(home_binding.values, "activation").value,
        AttributeSet,
    )
    audit = expect_instance(
        expect_binding(home_activation.values, "nixcfgProfileAppBundleAudit").value,
        FunctionCall,
    )
    mk_if = expect_instance(audit.name, FunctionCall)
    assert_nix_ast_equal(mk_if.name, identifier_attr_path("lib", "mkIf"))
    assert_nix_ast_equal(
        mk_if.argument,
        BinaryExpression(
            left=identifier_attr_path("cfg", "systemApplications"),
            operator=Operator(name="!="),
            right=nix_list([]),
        ),
    )

    entry_after = expect_instance(
        expect_instance(audit.argument, Parenthesis).value, FunctionCall
    )
    entry_after_name = expect_instance(entry_after.name, FunctionCall)
    assert_nix_ast_equal(
        entry_after_name.name,
        identifier_attr_path("lib", "hm", "dag", "entryAfter"),
    )
    assert_nix_ast_equal(entry_after_name.argument, nix_list(["installPackages"]))

    leak_audit = expect_instance(
        expect_instance(entry_after.argument, Parenthesis).value,
        FunctionCall,
    )
    assert_nix_ast_equal(
        leak_audit.name,
        identifier_attr_path("macApps", "profileBundleLeakAuditScript"),
    )
    leak_audit_args = expect_instance(leak_audit.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(leak_audit_args.values, "packagePaths").value,
        FunctionCall(
            name=FunctionCall(
                name=Identifier(name="map"),
                argument=Identifier(name="toString"),
            ),
            argument=identifier_attr_path("config", "home.packages"),
        ),
    )
    assert_nix_ast_equal(
        expect_binding(leak_audit_args.values, "managedBundleNames").value,
        FunctionCall(
            name=FunctionCall(
                name=Identifier(name="map"),
                argument=Parenthesis(
                    value=FunctionDefinition(
                        argument_set=Identifier(name="entry"),
                        output=identifier_attr_path("entry", "bundleName"),
                    )
                ),
            ),
            argument=identifier_attr_path("cfg", "systemApplications"),
        ),
    )
    label = expect_instance(
        expect_binding(leak_audit_args.values, "label").value,
        StringPrimitive,
    )
    assert label.value == "home.packages"


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_profile_bundle_leak_audit_script_reports_managed_bundle_exposure(
    tmp_path: Path,
) -> None:
    """Activation audit should fail if managed bundles leak through package outputs."""
    managed_package = tmp_path / "cursor-package"
    (managed_package / "Applications" / "Cursor.app").mkdir(parents=True)

    script = _profile_bundle_leak_audit_script(
        managed_bundle_names=("Cursor.app",),
        package_paths=(str(managed_package),),
    )
    result = subprocess.run(  # noqa: S603
        ["/bin/bash", "-c", f"set -euo pipefail\n{script}"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert (
        "Managed macOS app bundles must not be exposed through home.packages."
        in result.stderr
    )
    assert f" - Cursor.app <= {managed_package}" in result.stderr


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_profile_bundle_leak_audit_script_ignores_unmanaged_bundle_exposure(
    tmp_path: Path,
) -> None:
    """Activation audit should ignore unrelated GUI bundles in package outputs."""
    unrelated_package = tmp_path / "spotify-package"
    (unrelated_package / "Applications" / "Spotify.app").mkdir(parents=True)

    script = _profile_bundle_leak_audit_script(
        managed_bundle_names=("Cursor.app",),
        package_paths=(str(unrelated_package),),
    )
    result = subprocess.run(  # noqa: S603
        ["/bin/bash", "-c", f"set -euo pipefail\n{script}"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_managed_app_overlap_assertion_reports_conflicting_package_lists() -> None:
    """Managed app packages should not overlap with installed package lists."""
    result = _managed_app_overlap_assertion_result([
        nix_attrset({
            "label": "home.packages",
            "packages": nix_list([
                _fake_mac_app_package(
                    "cursor-wrapper",
                    "/nix/store/fake-wrapper",
                    "Cursor.app",
                )
            ]),
        })
    ])

    assert result == {
        "assertion": False,
        "message": (
            "nixcfg.macApps.systemApplications packages must not also appear in other "
            "installed package lists.\n"
            "- Cursor.app (cursor) also appears in home.packages as cursor-wrapper."
        ),
    }


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_managed_app_overlap_assertion_allows_distinct_package_lists() -> None:
    """Distinct package lists should not trip the managed app overlap assertion."""
    result = _managed_app_overlap_assertion_result([
        nix_attrset({
            "label": "home.packages",
            "packages": nix_list([
                _fake_mac_app_package(
                    "spotify",
                    "/nix/store/fake-spotify",
                    "Spotify.app",
                )
            ]),
        })
    ])

    assert result == {
        "assertion": True,
        "message": (
            "nixcfg.macApps.systemApplications packages must not also appear in other "
            "installed package lists."
        ),
    }


def test_zoom_overlay_threads_self_source_version_and_copy_mode_mac_app_metadata() -> (
    None
):
    """The Zoom overlay should keep its local source wiring and copy-mode app contract."""
    overlay = _module_output("overlays/zoom-us.nix")

    zoom = expect_instance(
        expect_binding(overlay.values, "zoom-us").value, IfExpression
    )
    assert_nix_ast_equal(
        zoom.condition,
        identifier_attr_path("prev", "stdenv", "hostPlatform", "isDarwin"),
    )
    assert_nix_ast_equal(zoom.alternative, identifier_attr_path("prev", "zoom-us"))

    override_call = expect_instance(zoom.consequence, FunctionCall)
    assert_nix_ast_equal(
        override_call.name,
        identifier_attr_path("prev", "zoom-us", "overrideAttrs"),
    )
    override_fn = expect_instance(
        expect_instance(override_call.argument, Parenthesis).value,
        FunctionDefinition,
    )
    assert_nix_ast_equal(override_fn.argument_set, Identifier(name="old"))
    override_attrs = expect_instance(override_fn.output, AttributeSet)

    version_inherit = next(
        value for value in override_attrs.values if isinstance(value, Inherit)
    )
    assert_nix_ast_equal(version_inherit.from_expression, Identifier(name="selfSource"))
    assert [name.rebuild() for name in version_inherit.names] == ["version"]

    src_call = expect_instance(
        expect_binding(override_attrs.values, "src").value, FunctionCall
    )
    assert_nix_ast_equal(src_call.name, identifier_attr_path("prev", "fetchurl"))
    src_args = expect_instance(src_call.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(src_args.values, "url").value,
        identifier_attr_path("selfSource", "urls", "${system}"),
    )
    assert_nix_ast_equal(
        expect_binding(src_args.values, "hash").value,
        identifier_attr_path("selfSource", "hashes", "${system}"),
    )

    passthru = expect_instance(
        expect_binding(override_attrs.values, "passthru").value,
        BinaryExpression,
    )
    assert passthru.operator.name == "//"
    assert_nix_ast_equal(
        passthru.right,
        _mac_app_metadata_attrset(
            "zoom.us.app",
            "Applications/zoom.us.app",
            "copy",
        ),
    )


def test_netnewswire_package_exposes_copy_mode_mac_app_metadata() -> None:
    """The NetNewsWire package should expose copy-mode macApp metadata."""
    sources = json.loads(
        (REPO_ROOT / "packages/netnewswire/sources.json").read_text(encoding="utf-8")
    )
    package_source = Path(REPO_ROOT / "packages/netnewswire/default.nix").read_text(
        encoding="utf-8"
    )
    package = expect_instance(parse_nix_expr(package_source), FunctionDefinition)
    derivation = expect_instance(package.output, FunctionCall)
    derivation_args = expect_instance(derivation.argument, AttributeSet)

    assert sources["version"] == "7.0.4"
    version_inherit = next(
        value for value in derivation_args.values if isinstance(value, Inherit)
    )
    assert_nix_ast_equal(version_inherit.from_expression, Identifier(name="selfSource"))
    assert [name.rebuild() for name in version_inherit.names] == ["version"]
    assert_nix_ast_equal(
        derivation.name,
        identifier_attr_path("stdenvNoCC", "mkDerivation"),
    )
    assert_nix_ast_equal(
        expect_binding(derivation_args.values, "passthru").value,
        _mac_app_metadata_attrset(
            StringPrimitive(value="${appName}.app"),
            StringPrimitive(value="Applications/${appName}.app"),
            "copy",
        ),
    )


def test_george_config_manages_mutable_gui_apps_via_system_applications() -> None:
    """George's config should route the known-problem mutable app bundles via /Applications."""
    root = _module_output("home/george/configuration.nix")
    nixcfg = expect_instance(expect_binding(root.values, "nixcfg").value, AttributeSet)

    package_sets = expect_instance(
        expect_binding(nixcfg.values, "packageSets").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(package_sets.values, "excludePackagesByName").value,
        NixList(
            value=[
                StringPrimitive(value="chatgpt"),
                StringPrimitive(value="cursor"),
                StringPrimitive(value="datagrip"),
                StringPrimitive(value="wispr-flow"),
            ]
        ),
    )

    mac_apps = expect_instance(
        expect_binding(nixcfg.values, "macApps").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(mac_apps.values, "systemApplications").value,
        NixList(
            value=[
                AttributeSet(
                    values=[
                        Binding(
                            name="package",
                            value=identifier_attr_path("pkgs", "chatgpt"),
                        ),
                        Binding(name="mode", value=StringPrimitive(value="copy")),
                    ]
                ),
                AttributeSet(
                    values=[
                        Binding(
                            name="package",
                            value=identifier_attr_path("pkgs", "code-cursor"),
                        ),
                        Binding(name="mode", value=StringPrimitive(value="copy")),
                    ]
                ),
                AttributeSet(
                    values=[
                        Binding(
                            name="package",
                            value=identifier_attr_path("pkgs", "jetbrains", "datagrip"),
                        ),
                        Binding(name="mode", value=StringPrimitive(value="copy")),
                    ]
                ),
                AttributeSet(
                    values=[
                        Binding(
                            name="package",
                            value=identifier_attr_path("pkgs", "vscode-insiders"),
                        ),
                        Binding(name="mode", value=StringPrimitive(value="copy")),
                    ]
                ),
                AttributeSet(
                    values=[
                        Binding(
                            name="package",
                            value=identifier_attr_path("pkgs", "netnewswire"),
                        )
                    ]
                ),
                AttributeSet(
                    values=[
                        Binding(
                            name="package",
                            value=identifier_attr_path("pkgs", "wispr-flow"),
                        )
                    ]
                ),
                AttributeSet(
                    values=[
                        Binding(
                            name="package",
                            value=identifier_attr_path("pkgs", "zoom-us"),
                        )
                    ]
                ),
            ]
        ),
    )

    programs = expect_instance(
        expect_binding(root.values, "programs").value, AttributeSet
    )
    vscode = expect_instance(
        expect_binding(programs.values, "vscode").value, AttributeSet
    )
    assert_nix_ast_equal(
        expect_binding(vscode.values, "package").value,
        Primitive(value=None),
    )
    assert_nix_ast_equal(
        expect_binding(vscode.values, "pname").value,
        StringPrimitive(value="vscode-insiders"),
    )


def test_managed_gui_app_tiny_overlays_keep_copy_mode_metadata_contracts() -> None:
    """The shared overlay should keep copy-mode macApp metadata on the targeted apps."""
    overlay_root = _module_output("overlays/default.nix")
    default_overlay = expect_instance(
        expect_binding(overlay_root.values, "default").value,
        FunctionDefinition,
    )
    overlay_fn = expect_instance(default_overlay.output, FunctionDefinition)
    tiny_overlays = expect_instance(
        expect_scope_binding(overlay_fn.output, "tinyOverlays").value,
        AttributeSet,
    )

    assert_nix_ast_equal(
        expect_binding(tiny_overlays.values, "chatgpt").value,
        _curried_call(
            Identifier(name="withManagedMacApp"),
            FunctionCall(
                name=FunctionCall(
                    name=identifier_attr_path("final", "mkSourceOverride"),
                    argument=StringPrimitive(value="chatgpt", raw_string=True),
                ),
                argument=identifier_attr_path("prev", "chatgpt"),
            ),
            StringPrimitive(value="ChatGPT.app", raw_string=True),
        ),
    )
    assert_nix_ast_equal(
        expect_binding(tiny_overlays.values, "code-cursor").value,
        _curried_call(
            Identifier(name="withManagedMacApp"),
            FunctionCall(
                name=FunctionCall(
                    name=identifier_attr_path("final", "mkSourceOverride"),
                    argument=StringPrimitive(value="code-cursor", raw_string=True),
                ),
                argument=identifier_attr_path("prev", "code-cursor"),
            ),
            StringPrimitive(value="Cursor.app", raw_string=True),
        ),
    )
    jetbrains = expect_instance(
        expect_binding(tiny_overlays.values, "jetbrains").value,
        BinaryExpression,
    )
    assert_nix_ast_equal(jetbrains.left, identifier_attr_path("prev", "jetbrains"))
    assert jetbrains.operator.name == "//"
    jetbrains_overrides = expect_instance(jetbrains.right, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(jetbrains_overrides.values, "datagrip").value,
        _curried_call(
            Identifier(name="withManagedMacApp"),
            FunctionCall(
                name=FunctionCall(
                    name=identifier_attr_path("final", "mkSourceOverride"),
                    argument=StringPrimitive(value="datagrip", raw_string=True),
                ),
                argument=identifier_attr_path("prev", "jetbrains", "datagrip"),
            ),
            StringPrimitive(value="DataGrip.app", raw_string=True),
        ),
    )


def test_vscode_insiders_overlay_keeps_copy_mode_mac_app_metadata_contract() -> None:
    """The VS Code Insiders overlay should keep its copy-mode app metadata contract."""
    overlay = _module_output("overlays/vscode-insiders/default.nix")

    vscode_overlay = expect_instance(
        expect_binding(overlay.values, "vscode-insiders").value,
        FunctionCall,
    )

    assert_nix_ast_equal(
        expect_scope_binding(vscode_overlay, "info").value,
        Identifier(name="selfSource"),
    )
    version_inherit = next(
        value for value in vscode_overlay.scope if isinstance(value, Inherit)
    )
    assert_nix_ast_equal(version_inherit.from_expression, Identifier(name="info"))
    assert [name.rebuild() for name in version_inherit.names] == ["version"]
    assert_nix_ast_equal(
        expect_scope_binding(vscode_overlay, "hash").value,
        identifier_attr_path("info", "hashes", "${system}"),
    )
    assert_nix_ast_equal(
        expect_scope_binding(vscode_overlay, "plat").value,
        Select(
            expression=nix_attrset({
                "aarch64-darwin": "darwin-arm64",
                "aarch64-linux": "linux-arm64",
                "x86_64-linux": "linux-x64",
            }),
            attribute="${system}",
        ),
    )
    assert_nix_ast_equal(
        expect_scope_binding(vscode_overlay, "archive_fmt").value,
        IfExpression(
            condition=identifier_attr_path(
                "prev", "stdenv", "hostPlatform", "isDarwin"
            ),
            consequence=StringPrimitive(value="zip"),
            alternative=StringPrimitive(value="tar.gz"),
        ),
    )

    assert_nix_ast_equal(
        vscode_overlay.name,
        Select(
            expression=Parenthesis(
                value=FunctionCall(
                    name=identifier_attr_path("prev", "vscode", "override"),
                    argument=nix_attrset({"isInsiders": True}),
                )
            ),
            attribute="overrideAttrs",
        ),
    )
    override_fn = expect_instance(
        expect_instance(vscode_overlay.argument, Parenthesis).value,
        FunctionDefinition,
    )
    assert_nix_ast_equal(override_fn.argument_set, Identifier(name="old"))
    override_attrs = expect_instance(override_fn.output, AttributeSet)

    version_inherit = next(
        value for value in override_attrs.values if isinstance(value, Inherit)
    )
    assert version_inherit.from_expression is None
    assert [name.rebuild() for name in version_inherit.names] == ["version"]

    src_call = expect_instance(
        expect_binding(override_attrs.values, "src").value, FunctionCall
    )
    assert_nix_ast_equal(src_call.name, identifier_attr_path("prev", "fetchurl"))
    src_args = expect_instance(src_call.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(src_args.values, "name").value,
        StringPrimitive(
            value="VSCode-insiders-${version}-${plat}.${archive_fmt}",
            raw_string=True,
        ),
    )
    assert_nix_ast_equal(
        expect_binding(src_args.values, "url").value,
        identifier_attr_path("info", "urls", "${system}"),
    )
    src_hash_inherit = next(
        value for value in src_args.values if isinstance(value, Inherit)
    )
    assert src_hash_inherit.from_expression is None
    assert [name.rebuild() for name in src_hash_inherit.names] == ["hash"]

    meta = expect_instance(
        expect_binding(override_attrs.values, "meta").value, BinaryExpression
    )
    assert meta.operator.name == "//"
    assert_nix_ast_equal(
        meta.right,
        nix_attrset({
            "platforms": FunctionCall(
                name=identifier_attr_path("builtins", "attrNames"),
                argument=identifier_attr_path("info", "urls"),
            )
        }),
    )

    passthru = expect_instance(
        expect_binding(override_attrs.values, "passthru").value,
        BinaryExpression,
    )
    assert passthru.operator.name == "//"
    assert_nix_ast_equal(
        passthru.right,
        _mac_app_metadata_attrset(
            "Visual Studio Code - Insiders.app",
            "Applications/Visual Studio Code - Insiders.app",
            "copy",
        ),
    )


def test_dock_configs_keep_the_targeted_gc_mitigation_scope_explicit() -> None:
    """Dock modules should keep the targeted /Applications policy explicit for managed bundles."""
    george_dock = (REPO_ROOT / "modules/darwin/george/dock-apps.nix").read_text(
        encoding="utf-8"
    )
    town_dock = (REPO_ROOT / "modules/darwin/george/town-dock-apps.nix").read_text(
        encoding="utf-8"
    )

    for rendered in (george_dock, town_dock):
        assert "intentionally left profile-managed" in rendered
        assert (
            "/Users/${primaryUser}/Applications/Home Manager Apps/ChatGPT.app"
            not in rendered
        )
        assert (
            "/Users/${primaryUser}/Applications/Home Manager Apps/DataGrip.app"
            not in rendered
        )

    assert '"/Applications/ChatGPT.app"' in george_dock
    assert '"/Applications/DataGrip.app"' in george_dock

    assert '"/Applications/ChatGPT.app"' in town_dock
    assert '"/Applications/Cursor.app"' in town_dock
    assert '"/Applications/Visual Studio Code - Insiders.app"' in town_dock
    assert '"/Applications/DataGrip.app"' in town_dock
    assert (
        "/Users/${primaryUser}/Applications/Home Manager Apps/Cursor.app"
        not in town_dock
    )
    assert (
        "/Users/${primaryUser}/Applications/Home Manager Apps/Visual Studio Code - Insiders.app"
        not in town_dock
    )


def test_george_bin_wrappers_target_only_managed_app_copies() -> None:
    """CLI wrappers should only target the managed /Applications bundles."""
    cursor_wrapper = (REPO_ROOT / "home/george/bin/cursor").read_text(encoding="utf-8")
    code_insiders_wrapper = (REPO_ROOT / "home/george/bin/code-insiders").read_text(
        encoding="utf-8"
    )

    assert (
        '"/Applications/Cursor.app/Contents/Resources/app/bin/cursor"' in cursor_wrapper
    )
    assert "$HOME/Applications/Home Manager Apps/Cursor.app" not in cursor_wrapper
    assert 'exec "$candidate" "$@"' in cursor_wrapper
    assert (
        '"/Applications/Visual Studio Code - Insiders.app/Contents/Resources/app/bin/code"'
        in code_insiders_wrapper
    )
    assert (
        "$HOME/Applications/Home Manager Apps/Visual Studio Code - Insiders.app"
        not in code_insiders_wrapper
    )
    assert 'exec "$candidate" "$@"' in code_insiders_wrapper
