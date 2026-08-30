"""Semantic contracts for Buzz's source-built llama.cpp runtime."""

from functools import cache
from textwrap import dedent
from typing import TYPE_CHECKING

from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    nix_attrset_call,
    parse_nix_expr,
)
from lib.tests._shell_ast import (
    ParsedShell,
    command_name,
    command_texts,
    indented_string_body,
    iter_nodes,
    node_text,
    parse_shell,
)
from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from nix_manipulator.expressions.scope import Scope

_LLAMA_CPP_PATH = REPO_ROOT / "packages/buzz/native/llama-cpp.nix"


@cache
def _llama_cpp_package() -> tuple[FunctionDefinition, FunctionCall]:
    package = expect_instance(
        parse_nix_expr(_LLAMA_CPP_PATH.read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    output = package.output
    while isinstance(output, Assertion):
        output = output.body
    return package, expect_instance(output, FunctionCall)


def _derivation_arguments() -> AttributeSet:
    _package, derivation = _llama_cpp_package()
    return expect_instance(derivation.argument, AttributeSet)


def _package_scope() -> Scope:
    package, derivation = _llama_cpp_package()
    output = package.output
    while isinstance(output, Assertion):
        if output.scope:
            return output.scope
        output = output.body
    return derivation.scope


def _assertion_conditions() -> list[object]:
    package, _derivation = _llama_cpp_package()
    conditions: list[object] = []
    output = package.output
    while isinstance(output, Assertion):
        conditions.append(output.expression)
        output = output.body
    return conditions


def _phase_shell(name: str) -> ParsedShell:
    script = expect_instance(
        expect_binding(_derivation_arguments().values, name).value,
        IndentedString,
    )
    return parse_shell(dedent(indented_string_body(script.rebuild())))


def test_llama_cpp_fetch_is_exact_and_does_not_fetch_submodules() -> None:
    """The runtime must build only the audited pristine llama.cpp revision."""
    package, derivation = _llama_cpp_package()
    scope = _package_scope()

    assert {
        expect_instance(argument, Identifier).name for argument in package.argument_set
    } == {
        "cctools",
        "cmake",
        "fetchFromGitHub",
        "gitMinimal",
        "lib",
        "meshSrcHash",
        "nativeLock",
        "ninja",
        "srcHash",
        "stdenv",
    }
    assert_nix_ast_equal(
        expect_binding(scope, "commit").value,
        "nativeLock.llamaCpp.commit or null",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "meshCommit").value,
        "nativeLock.meshLlm.commit or null",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "src").value,
        nix_attrset_call(
            Identifier(name="fetchFromGitHub"),
            owner="ggml-org",
            repo="llama.cpp",
            rev=Identifier(name="commit"),
            hash=Identifier(name="srcHash"),
            fetchSubmodules=False,
        ),
    )
    assert_nix_ast_equal(
        expect_binding(scope, "meshSource").value,
        nix_attrset_call(
            Identifier(name="fetchFromGitHub"),
            owner="Mesh-LLM",
            repo="mesh-llm",
            rev=Identifier(name="meshCommit"),
            hash=Identifier(name="meshSrcHash"),
            fetchSubmodules=False,
        ),
    )
    assert_nix_ast_equal(derivation.name, "stdenv.mkDerivation")


def test_llama_cpp_requires_darwin_without_caller_attested_source_bytes() -> None:
    """The patch queue comes from the internal fixed-revision source fetch."""
    _package, _derivation = _llama_cpp_package()
    conditions = _assertion_conditions()

    assert len(conditions) == 3
    assert_nix_ast_equal(
        conditions[0],
        'stdenv.hostPlatform.system == "aarch64-darwin"',
    )
    assert_nix_ast_equal(
        conditions[1],
        'builtins.isString commit && builtins.match "[0-9a-f]{40}" commit != null',
    )
    assert_nix_ast_equal(
        conditions[2],
        'builtins.isString meshCommit && builtins.match "[0-9a-f]{40}" meshCommit != null',
    )


def test_llama_cpp_builds_only_the_arm64_metal_shared_runtime() -> None:
    """The build must be Release-only, deterministic, and fully offline."""
    attrs = _derivation_arguments()
    assert_nix_ast_equal(
        expect_binding(attrs.values, "nativeBuildInputs").value,
        "[ cmake gitMinimal ninja ]",
    )
    assert_nix_ast_equal(expect_binding(attrs.values, "strictDeps").value, "true")
    assert_nix_ast_equal(
        expect_binding(attrs.values, "cmakeBuildType").value,
        '"Release"',
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "cmakeFlags").value,
        """[
          (lib.cmakeFeature "CMAKE_OSX_ARCHITECTURES" "arm64")
          (lib.cmakeFeature "CMAKE_OSX_DEPLOYMENT_TARGET" "14.0")
          (lib.cmakeFeature "CMAKE_INSTALL_BINDIR" "bin")
          (lib.cmakeFeature "CMAKE_INSTALL_INCLUDEDIR" "include")
          (lib.cmakeFeature "CMAKE_INSTALL_LIBDIR" "lib")
          (lib.cmakeFeature "CMAKE_INSTALL_NAME_DIR" "@rpath")
          (lib.cmakeBool "BUILD_SHARED_LIBS" true)
          (lib.cmakeBool "GGML_NATIVE" false)
          (lib.cmakeBool "GGML_METAL" true)
          (lib.cmakeBool "LLAMA_BUILD_APP" false)
          (lib.cmakeBool "LLAMA_BUILD_EXAMPLES" false)
          (lib.cmakeBool "LLAMA_BUILD_SERVER" false)
          (lib.cmakeBool "LLAMA_BUILD_TESTS" false)
          (lib.cmakeBool "LLAMA_CURL" false)
          (lib.cmakeBool "LLAMA_OPENSSL" false)
          (lib.cmakeBool "FETCHCONTENT_FULLY_DISCONNECTED" true)
        ]""",
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "buildPhase").value,
        """''
          runHook preBuild
          cmake --build . --config Release
          runHook postBuild
        ''""",
    )
    assert_nix_ast_equal(expect_binding(attrs.values, "doCheck").value, "false")


def test_llama_cpp_applies_the_complete_mesh_patch_queue_in_bytewise_order() -> None:
    """Patch drift, an empty queue, or preparation-script reuse must stop the build."""
    shell = _phase_shell("patchPhase")

    find_commands = command_texts(shell, "find")
    assert any(
        "-mindepth 1 -maxdepth 1 ! -type f -print -quit" in command
        for command in find_commands
    )
    assert any(
        "-mindepth 1 -maxdepth 1 -type f ! -name '*.patch' -print -quit" in command
        for command in find_commands
    )
    assert any(
        "-mindepth 1 -maxdepth 1 -type f -name '*.patch' -print0" in command
        for command in find_commands
    )
    assert command_texts(shell, "sort") == ["LC_ALL=C sort -z"]
    assert command_texts(shell, "git") == [
        'git apply --check "$meshPatch"',
        'git apply "$meshPatch"',
    ]
    assert command_texts(shell, "patch") == []
    assert (
        sum(node.type == "while_statement" for node in iter_nodes(shell.tree.root_node))
        == 1
    )

    forbidden_commands = {"curl", "patch", "sh", "wget"}
    invoked_commands = {
        name
        for node in iter_nodes(shell.tree.root_node)
        if (name := command_name(node, shell.sanitized)) is not None
    }
    assert invoked_commands.isdisjoint(forbidden_commands)


def test_llama_cpp_normalizes_compiler_paths_before_building() -> None:
    """Ephemeral source paths must not survive in runtime library bytes."""
    shell = _phase_shell("preConfigure")

    assignments = [
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "variable_assignment")
    ]
    assert any(
        "-ffile-prefix-map=$NIX_BUILD_TOP=/build" in assignment
        for assignment in assignments
    )
    export_commands = command_texts(shell, "export")
    assert len(export_commands) == 2
    assert any("NIX_CFLAGS_COMPILE=" in command for command in export_commands)
    assert any("NIX_CXXFLAGS_COMPILE=" in command for command in export_commands)


def test_llama_cpp_stages_only_cmake_dylibs_and_rejects_unproven_resources() -> None:
    """Runtime inventory must come from the build, with no guessed file names."""
    attrs = _derivation_arguments()
    shell = _phase_shell("installPhase")

    assert command_texts(shell, "cmake") == [
        'cmake --install . --config Release --prefix "$runtimeStage"'
    ]
    find_commands = command_texts(shell, "find")
    assert any(
        '"$runtimeStage" -type f' in command
        and "-name '*.dylib'" in command
        and "-name '*.dylib.*'" in command
        and "-print0" in command
        for command in find_commands
    )
    assert any(
        '"$runtimeStage" -type l' in command
        and "-name '*.dylib'" in command
        and "-name '*.dylib.*'" in command
        and "-print0" in command
        for command in find_commands
    )
    assert any(
        '"$runtimeStage" -type f' in command
        and "-name '*.metal'" in command
        and "-name '*.metallib'" in command
        and "-print0" in command
        for command in find_commands
    )
    assert command_texts(shell, "sort") == [
        "LC_ALL=C sort -z",
        "LC_ALL=C sort -z",
        "LC_ALL=C sort -z",
        "LC_ALL=C sort -z",
    ]
    assert command_texts(shell, "install") == [
        'install -m 755 "$sourceLibrary" "$destinationLibrary"',
    ]
    assert command_texts(shell, "ln") == ['ln -s "$linkTarget" "$destinationLink"']
    assert_nix_ast_equal(expect_binding(attrs.values, "dontFixup").value, "true")
    assert any(command == '[ -s "$resourceQueue" ]' for command in command_texts(shell))

    install_check = _phase_shell("installCheckPhase")
    top_level_checks = [
        command
        for command in command_texts(install_check, "find")
        if '"$out" -mindepth 1 -maxdepth 1' in command
    ]
    assert len(top_level_checks) == 1
    assert "! -name lib" in top_level_checks[0]
    assert "share" not in top_level_checks[0]

    semantic_words = {
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "word")
    }
    assert not {
        word
        for word in semantic_words
        if word.startswith(("libcommon", "libllama", "libmtmd"))
    }


def test_llama_cpp_rewrites_only_resolved_local_dylib_dependencies() -> None:
    """Every non-system load command must resolve inside the staged closure."""
    shell = _phase_shell("installPhase")

    assert command_texts(shell, "__NIX_INTERP__/bin/install_name_tool") == [
        '__NIX_INTERP__/bin/install_name_tool -id "@rpath/$libraryName" "$library"',
        '__NIX_INTERP__/bin/install_name_tool -change "$dependency" '
        '"@loader_path/$dependencyName" "$library"',
    ]
    assert command_texts(shell, "__NIX_INTERP__/bin/otool") == [
        '__NIX_INTERP__/bin/otool -L "$library"'
    ]

    case_statements = [
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "case_statement")
    ]
    dependency_case = next(
        statement
        for statement in case_statements
        if 'case "$dependency" in' in statement
    )
    assert '"/usr/lib/"* | "/System/Library/"*' in dependency_case
    assert '"/nix/store/"* | "/opt/homebrew/"* | "/usr/local/"*' in dependency_case
    assert '"@rpath/"* | "@loader_path/"*' in dependency_case
    assert '"$NIX_BUILD_TOP/"* | "$runtimeStage/"*' in dependency_case


def test_llama_cpp_signs_only_after_all_install_name_rewrites() -> None:
    """Ad-hoc signatures must cover the final relocated Mach-O bytes."""
    install_shell = _phase_shell("installPhase")
    command_nodes = [
        node
        for node in iter_nodes(install_shell.tree.root_node, "command")
        if command_name(node, install_shell.sanitized) is not None
    ]
    rewrite_nodes = [
        node
        for node in command_nodes
        if command_name(node, install_shell.sanitized)
        == "__NIX_INTERP__/bin/install_name_tool"
    ]
    signing_nodes = [
        node
        for node in command_nodes
        if command_name(node, install_shell.sanitized) == "/usr/bin/codesign"
    ]

    assert len(rewrite_nodes) == 2
    assert [node_text(node, install_shell.sanitized) for node in signing_nodes] == [
        '/usr/bin/codesign --force --sign - "$library"'
    ]
    post_install_nodes = [
        node
        for node in command_nodes
        if node_text(node, install_shell.sanitized) == "runHook postInstall"
    ]
    assert len(post_install_nodes) == 1
    assert post_install_nodes[0].end_byte < min(
        node.start_byte for node in rewrite_nodes
    )
    assert max(node.end_byte for node in rewrite_nodes) < signing_nodes[0].start_byte


def test_llama_cpp_install_check_enforces_relocatable_arm64_macos_14_closure() -> None:
    """Every shipped dylib must be signed, local, arm64-only, and runnable on 14.0."""
    attrs = _derivation_arguments()
    shell = _phase_shell("installCheckPhase")

    assert_nix_ast_equal(expect_binding(attrs.values, "doInstallCheck").value, "true")
    assert command_texts(shell, "__NIX_INTERP__/bin/lipo") == [
        '__NIX_INTERP__/bin/lipo -archs "$library"'
    ]
    assert command_texts(shell, "__NIX_INTERP__/bin/otool") == [
        '__NIX_INTERP__/bin/otool -D "$library"',
        '__NIX_INTERP__/bin/otool -l "$library"',
        '__NIX_INTERP__/bin/otool -L "$library"',
    ]
    assert command_texts(shell, "/usr/bin/codesign") == [
        '/usr/bin/codesign --verify --strict "$library"'
    ]
    assert command_texts(shell, "readlink") == ['readlink "$libraryLink"']
    assert command_texts(shell, "__NIX_INTERP__/bin/strings") == [
        '__NIX_INTERP__/bin/strings -a "$library"'
    ]

    test_commands = command_texts(shell)
    assert any('"$architectures" != "arm64"' in command for command in test_commands)
    assert any(
        '"$installId" != "@rpath/$libraryName"' in command for command in test_commands
    )
    version_checks = command_texts(shell, "awk")
    assert any(
        "version !~" in command and "major < 14" in command
        for command in version_checks
    )

    dependency_cases = [
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "case_statement")
        if 'case "$dependency" in' in node_text(node, shell.sanitized)
    ]
    assert len(dependency_cases) == 1
    assert '"/usr/lib/"* | "/System/Library/"*' in dependency_cases[0]
    assert '"@loader_path/"*' in dependency_cases[0]
    assert '"@rpath/"* | "@executable_path/"*' in dependency_cases[0]

    link_target_cases = [
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "case_statement")
        if 'case "$linkTarget" in' in node_text(node, shell.sanitized)
    ]
    assert len(link_target_cases) == 1
    assert '"" | /* | */*' in link_target_cases[0]

    grep_commands = command_texts(shell, "grep")
    assert any('"$NIX_BUILD_TOP/"' in command for command in grep_commands)
    for forbidden_root in (
        "/nix/store/",
        "/nix/var/nix/builds/",
        "/opt/homebrew/",
        "/usr/local/",
    ):
        assert any(forbidden_root in command for command in grep_commands)


def test_llama_cpp_passthru_exactly_matches_the_package_and_bundle_seams() -> None:
    """Consumers get the audited policy identity without inventing runtime names."""
    passthru = expect_instance(
        expect_binding(_derivation_arguments().values, "passthru").value,
        AttributeSet,
    )

    assert_nix_ast_equal(
        expect_binding(passthru.values, "libSubdir").value,
        '"lib"',
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "resourceSubpaths").value,
        "[ ]",
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "buzzNativeContract").value,
        """{
          kind = "llama.cpp";
          inherit commit;
          target = "aarch64-apple-darwin";
          backend = "metal";
          linkMode = "dynamic";
          buildType = "Release";
          ggmlNative = false;
          cmakeOptions = {
            BUILD_SHARED_LIBS = true;
            GGML_METAL = true;
            LLAMA_BUILD_APP = false;
            LLAMA_BUILD_EXAMPLES = false;
            LLAMA_BUILD_SERVER = false;
            LLAMA_BUILD_TESTS = false;
            LLAMA_CURL = false;
            LLAMA_OPENSSL = false;
          };
        }""",
    )
