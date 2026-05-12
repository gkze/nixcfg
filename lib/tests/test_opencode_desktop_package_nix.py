"""AST- and eval-level tests for OpenCode Desktop packaging."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from functools import cache
from pathlib import Path

from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    expect_scope_binding,
    parse_nix_expr,
)
from lib.update.paths import REPO_ROOT

_SUPPORTED_PLATFORMS = [
    "aarch64-darwin",
    "x86_64-darwin",
    "aarch64-linux",
    "x86_64-linux",
]
_BASH = shutil.which("bash")


@cache
def _package_assertion() -> Assertion:
    """Parse the package and return the outer top-level assertion."""
    root = expect_instance(
        parse_nix_expr(
            Path(REPO_ROOT / "packages/opencode-desktop/default.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )
    return expect_instance(root.output, Assertion)


@cache
def _derivation() -> FunctionCall:
    """Return the final ``stdenv.mkDerivation`` call under the assertions."""
    inner_assertion = expect_instance(_package_assertion().body, Assertion)
    return expect_instance(inner_assertion.body, FunctionCall)


@cache
def _derivation_args() -> AttributeSet:
    """Return the attribute set passed to ``stdenv.mkDerivation``."""
    return expect_instance(_derivation().argument, AttributeSet)


@cache
def _sources_payload() -> dict[str, object]:
    """Load the package's persisted source metadata."""
    return json.loads(
        Path(REPO_ROOT / "packages/opencode-desktop/sources.json").read_text(
            encoding="utf-8"
        )
    )


def _install_fake_substitute_in_place(bin_dir: Path) -> None:
    """Install a tiny test double for Nix's substituteInPlace helper."""
    bin_dir.mkdir()
    command = bin_dir / "substituteInPlace"
    command.write_text(
        f"""#!{sys.executable}
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
path = Path(args.pop(0))
text = path.read_text(encoding="utf-8")
while args:
    if len(args) < 3:
        raise SystemExit("incomplete replacement arguments")
    flag, old, new, *args = args
    if flag != "--replace-fail":
        raise SystemExit(f"unsupported flag: {{flag}}")
    if old not in text:
        raise SystemExit(f"missing replacement target: {{old}}")
    text = text.replace(old, new, 1)
    if log_path := os.environ.get("NIXCFG_SUBSTITUTE_LOG"):
        with Path(log_path).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({{"path": path.as_posix(), "old": old, "new": new}}))
            handle.write("\\n")
path.write_text(text, encoding="utf-8")
""",
        encoding="utf-8",
    )
    command.chmod(0o755)


def test_opencode_desktop_top_level_derivation_keeps_both_guards() -> None:
    """The package should stay wrapped in both version assertions."""
    outer_assertion = _package_assertion()
    inner_assertion = expect_instance(outer_assertion.body, Assertion)

    assert_nix_ast_equal(outer_assertion.expression, "desktopPackageVersionCheck")
    assert_nix_ast_equal(inner_assertion.expression, "electronRuntimeVersionCheck")
    assert_nix_ast_equal(_derivation().name, "stdenv.mkDerivation")


def test_opencode_desktop_uses_exact_nixcfg_electron_runtime() -> None:
    """All supported platforms should source Electron from the exact shared runtime."""
    package = _package_assertion()

    assert_nix_ast_equal(
        expect_scope_binding(package, "electronRuntime").value,
        "nixcfgElectron.runtimeFor electronVersion",
    )
    assert_nix_ast_equal(
        expect_scope_binding(package, "electronRuntimeVersion").value,
        "electronRuntime.version",
    )
    assert_nix_ast_equal(
        expect_scope_binding(package, "electronDist").value,
        "electronRuntime.passthru.dist",
    )


def test_opencode_desktop_detects_current_desktop_workspace_path() -> None:
    """The package should follow upstream's desktop workspace rename."""
    package = _package_assertion()

    assert_nix_ast_equal(
        expect_scope_binding(package, "desktopPackagePath").value,
        """
        if pathExists (src + "/packages/desktop/package.json") then
          "packages/desktop"
        else
          "packages/desktop-electron"
        """,
    )
    assert_nix_ast_equal(
        expect_scope_binding(package, "desktopPackageJson").value,
        'fromJSON (readFile (src + "/${desktopPackagePath}/package.json"))',
    )


def test_opencode_desktop_node_modules_derivation_tracks_platform_hashes() -> None:
    """The FOD should stay platform-specific and keep the package-side hash lookup."""
    package = _package_assertion()
    node_modules = expect_instance(
        expect_scope_binding(package, "node_modules").value,
        FunctionCall,
    )
    override = expect_instance(
        expect_instance(node_modules.argument, Parenthesis).value,
        FunctionDefinition,
    )
    override_args = expect_instance(override.output, AttributeSet)

    assert_nix_ast_equal(
        expect_scope_binding(package, "bunTarget").value,
        """
        {
          aarch64-darwin = {
            cpu = "arm64";
            os = "darwin";
          };
          x86_64-darwin = {
            cpu = "x64";
            os = "darwin";
          };
          aarch64-linux = {
            cpu = "arm64";
            os = "linux";
          };
          x86_64-linux = {
            cpu = "x64";
            os = "linux";
          };
        }
        .${system} or (throw "Unsupported system ${system} for ${pname}")
        """,
    )
    assert_nix_ast_equal(node_modules.name, "opencode.node_modules.overrideAttrs")
    assert_nix_ast_equal(
        expect_binding(override_args.values, "outputHash").value,
        'slib.sourceHashForPlatform sourceHashPackageName "nodeModulesHash" system',
    )

    build_phase = expect_instance(
        expect_binding(override_args.values, "buildPhase").value,
        IndentedString,
    )
    install_phase = expect_instance(
        expect_binding(override_args.values, "installPhase").value,
        IndentedString,
    )
    assert "--filter './packages/core'" in build_phase.value
    assert "--filter './${desktopPackagePath}'" in build_phase.value
    assert 'cp -R node_modules "$out/node_modules"' in install_phase.value
    assert "${desktopPackagePath}" in install_phase.value
    assert "packages/core" in install_phase.value
    assert "packages/sdk/js" in install_phase.value
    assert "packages/script" in install_phase.value
    assert 'cp -R --parents "$workspace/node_modules" "$out"' in install_phase.value
    assert "find . -type d -name node_modules" not in install_phase.value


def test_opencode_desktop_linux_desktop_item_is_structured() -> None:
    """Linux desktop metadata should be declared as data, not heredoc text."""
    desktop_item = expect_instance(
        expect_scope_binding(_package_assertion(), "linuxDesktopItem").value,
        FunctionCall,
    )
    desktop_item_args = expect_instance(desktop_item.argument, AttributeSet)

    assert_nix_ast_equal(desktop_item.name, "makeDesktopItem")
    assert_nix_ast_equal(
        expect_binding(desktop_item_args.values, "name").value, "pname"
    )
    assert_nix_ast_equal(
        expect_binding(desktop_item_args.values, "desktopName").value,
        "appName",
    )
    assert_nix_ast_equal(
        expect_binding(desktop_item_args.values, "exec").value,
        '"${pname} %U"',
    )
    assert_nix_ast_equal(
        expect_binding(desktop_item_args.values, "mimeTypes").value,
        '[ "x-scheme-handler/${appProtocolScheme}" ]',
    )


def test_opencode_desktop_derivation_keeps_platform_branches() -> None:
    """Build, install, and install-check phases should stay platform-conditional."""
    derivation_args = _derivation_args()

    for phase_name in ("buildPhase", "installPhase", "installCheckPhase"):
        phase = expect_instance(
            expect_binding(derivation_args.values, phase_name).value,
            IfExpression,
        )
        assert_nix_ast_equal(phase.condition, "stdenv.hostPlatform.isDarwin")
        assert isinstance(phase.consequence, IndentedString)
        assert isinstance(phase.alternative, IndentedString)


def test_opencode_desktop_sources_platforms_match_supported_matrix() -> None:
    """Persisted source hashes should advertise the full supported platform matrix."""
    hashes = _sources_payload().get("hashes")

    assert isinstance(hashes, list)
    assert sorted(entry["platform"] for entry in hashes) == sorted(_SUPPORTED_PLATFORMS)


def test_opencode_desktop_env_exports_runtime_identity_overrides() -> None:
    """The derivation should export the runtime identity overrides used by the Electron patch."""
    env = expect_instance(
        expect_binding(_derivation_args().values, "env").value, AttributeSet
    )

    assert_nix_ast_equal(expect_binding(env.values, "OPENCODE_APP_ID").value, "appId")
    assert_nix_ast_equal(
        expect_binding(env.values, "OPENCODE_APP_NAME").value, "appName"
    )
    assert_nix_ast_equal(
        expect_binding(env.values, "OPENCODE_PROTOCOL_NAME").value, "appProtocolName"
    )
    assert_nix_ast_equal(
        expect_binding(env.values, "OPENCODE_PROTOCOL_SCHEME").value,
        "appProtocolScheme",
    )


def test_opencode_desktop_uses_source_patch_helper() -> None:
    """Source rewrites should live in the helper script instead of inline Nix."""
    package = _package_assertion()
    post_patch = expect_instance(
        expect_binding(_derivation_args().values, "postPatch").value,
        IndentedString,
    )

    assert_nix_ast_equal(
        expect_scope_binding(package, "patchDesktopSource").value,
        "./patch_desktop_source.sh",
    )
    assert ". ${patchDesktopSource} . ${desktopPackagePath}" in post_patch.value
    assert (
        "substituteInPlace ${desktopPackagePath}/src/main/index.ts"
        not in post_patch.value
    )
    assert "OPENCODE_EXPERIMENTAL_ICON_DISCOVERY" not in post_patch.value


def test_opencode_desktop_patch_helper_rewrites_required_sources(
    tmp_path: Path,
) -> None:
    """Run the helper against fixture files to protect required rewrites."""
    assert _BASH is not None
    fake_bin = tmp_path / "bin"
    substitute_log = tmp_path / "substitute-calls.jsonl"
    _install_fake_substitute_in_place(fake_bin)

    repo = tmp_path / "repo"
    desktop = repo / "packages/desktop"
    opencode_script = repo / "packages/opencode/script"
    (desktop / "scripts").mkdir(parents=True)
    (desktop / "src/main").mkdir(parents=True)
    opencode_script.mkdir(parents=True)

    (desktop / "scripts/prepare.ts").write_text(
        'import { Script } from "@opencode-ai/script"\nScript.version\n',
        encoding="utf-8",
    )
    (opencode_script / "build-node.ts").write_text(
        'import { Script } from "@opencode-ai/script"\nScript.channel\n',
        encoding="utf-8",
    )
    (desktop / "src/main/server.ts").write_text(
        'const env = {\n    OPENCODE_EXPERIMENTAL_ICON_DISCOVERY: "true",\n}\n',
        encoding="utf-8",
    )
    (desktop / "src/main/index.ts").write_text(
        "\n".join([
            'const appId = app.isPackaged ? APP_IDS[CHANNEL] : "ai.opencode.desktop.dev"',
            'app.setName(app.isPackaged ? APP_NAMES[CHANNEL] : "OpenCode Dev")',
            'const urls = argv.filter((arg: string) => arg.startsWith("opencode://"))',
            '    app.setAsDefaultProtocolClient("opencode")',
        ])
        + "\n",
        encoding="utf-8",
    )
    (desktop / "electron-builder.config.ts").write_text(
        "\n".join([
            "protocols: {",
            '    name: "OpenCode",',
            '    schemes: ["opencode"],',
            "}",
        ])
        + "\n",
        encoding="utf-8",
    )

    env = os.environ | {
        "NIXCFG_SUBSTITUTE_LOG": str(substitute_log),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
    }
    subprocess.run(  # noqa: S603
        [
            _BASH,
            str(REPO_ROOT / "packages/opencode-desktop/patch_desktop_source.sh"),
            str(repo),
            "packages/desktop",
        ],
        capture_output=True,
        check=True,
        env=env,
        text=True,
    )

    assert [
        json.loads(line)
        for line in substitute_log.read_text(encoding="utf-8").splitlines()
    ] == [
        {
            "path": "packages/desktop/scripts/prepare.ts",
            "old": 'import { Script } from "@opencode-ai/script"',
            "new": 'const Script = { version: process.env.OPENCODE_VERSION ?? "0.0.0" }',
        },
        {
            "path": "packages/opencode/script/build-node.ts",
            "old": 'import { Script } from "@opencode-ai/script"',
            "new": 'const Script = { channel: process.env.OPENCODE_CHANNEL ?? "dev" }',
        },
        {
            "path": "packages/desktop/src/main/server.ts",
            "old": '    OPENCODE_EXPERIMENTAL_ICON_DISCOVERY: "true",',
            "new": '    OPENCODE_EXPERIMENTAL_ICON_DISCOVERY: "false",',
        },
        {
            "path": "packages/desktop/src/main/index.ts",
            "old": (
                "const appId = app.isPackaged ? APP_IDS[CHANNEL] : "
                '"ai.opencode.desktop.dev"'
            ),
            "new": (
                "const appId = process.env.OPENCODE_APP_ID ?? "
                '(app.isPackaged ? APP_IDS[CHANNEL] : "ai.opencode.desktop.dev")'
            ),
        },
        {
            "path": "packages/desktop/src/main/index.ts",
            "old": (
                'app.setName(app.isPackaged ? APP_NAMES[CHANNEL] : "OpenCode Dev")'
            ),
            "new": (
                "app.setName(process.env.OPENCODE_APP_NAME ?? "
                '(app.isPackaged ? APP_NAMES[CHANNEL] : "OpenCode Dev"))'
            ),
        },
        {
            "path": "packages/desktop/src/main/index.ts",
            "old": (
                "const urls = argv.filter((arg: string) => "
                'arg.startsWith("opencode://"))'
            ),
            "new": (
                "const urls = argv.filter((arg: string) => "
                'arg.startsWith((process.env.OPENCODE_PROTOCOL_SCHEME ?? "opencode") '
                '+ "://"))'
            ),
        },
        {
            "path": "packages/desktop/src/main/index.ts",
            "old": 'app.setAsDefaultProtocolClient("opencode")',
            "new": (
                "app.setAsDefaultProtocolClient("
                'process.env.OPENCODE_PROTOCOL_SCHEME ?? "opencode")'
            ),
        },
        {
            "path": "packages/desktop/electron-builder.config.ts",
            "old": '    name: "OpenCode",',
            "new": '    name: process.env.OPENCODE_PROTOCOL_NAME ?? "OpenCode",',
        },
        {
            "path": "packages/desktop/electron-builder.config.ts",
            "old": '    schemes: ["opencode"],',
            "new": (
                '    schemes: [process.env.OPENCODE_PROTOCOL_SCHEME ?? "opencode"],'
            ),
        },
    ]
    assert (desktop / "native").is_dir()


def test_opencode_desktop_exposes_copy_mode_mac_app_metadata() -> None:
    """The package should expose shared mac-app metadata for /Applications routing."""
    passthru = expect_instance(
        expect_binding(_derivation_args().values, "passthru").value,
        AttributeSet,
    )
    mac_app = expect_instance(
        expect_binding(passthru.values, "macApp").value, AttributeSet
    )

    assert_nix_ast_equal(
        expect_binding(mac_app.values, "bundleName").value, "appBundleName"
    )
    assert_nix_ast_equal(
        expect_binding(mac_app.values, "bundleRelPath").value,
        '"Applications/${appBundleName}"',
    )
    assert_nix_ast_equal(expect_binding(mac_app.values, "installMode").value, '"copy"')
