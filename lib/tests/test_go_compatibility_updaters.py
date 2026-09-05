"""Behavioral tests for locked-release Go compatibility validation."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.let import LetExpression
from nix_manipulator.expressions.primitive import StringPrimitive

from lib.nix.models.flake_lock import FlakeLockNode
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import collect_events, load_repo_module, run_async
from lib.update import locked_source
from lib.update.config import resolve_config
from lib.update.flake import flake_source_path_expression
from lib.update.nix_expr import identifier_attr_path
from lib.update.updaters.go_compatibility import (
    _MAX_GO_MOD_BYTES,
    GoModCompatibilityUpdater,
    _extract_go_requirement,
    _go_version_triplet,
    _locked_github_source,
)

_COMMIT = "a" * 40
_REF = "v1.2.3"
_UPDATERS = (
    pytest.param(
        "packages/axiom-cli/updater.py",
        "AxiomCliUpdater",
        "axiom-cli",
        "axiomhq",
        "cli",
        id="axiom-cli",
    ),
    pytest.param(
        "packages/gogcli/updater.py",
        "GogcliUpdater",
        "gogcli",
        "steipete",
        "gogcli",
        id="gogcli",
    ),
)


def _updater(
    path: str = "packages/axiom-cli/updater.py",
    class_name: str = "AxiomCliUpdater",
    *,
    suffix: str,
) -> GoModCompatibilityUpdater:
    module = load_repo_module(path, f"go_compatibility_{suffix}")
    updater_class = getattr(module, class_name)
    instance = updater_class()
    assert isinstance(instance, GoModCompatibilityUpdater)
    return instance


def _flake_node(
    *,
    owner: str = "axiomhq",
    repo: str = "cli",
    commit: str = _COMMIT,
    source_type: str = "github",
) -> FlakeLockNode:
    return FlakeLockNode.model_validate({
        "locked": {
            "type": source_type,
            "owner": owner,
            "repo": repo,
            "rev": commit,
            "narHash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        },
        "original": {
            "type": source_type,
            "owner": owner,
            "repo": repo,
            "ref": _REF,
        },
    })


@pytest.mark.parametrize(
    ("version", "expected"),
    [("1.27", (1, 27, 0)), ("1.27.3", (1, 27, 3))],
)
def test_go_version_parser_accepts_go_release_forms(
    version: str,
    expected: tuple[int, int, int],
) -> None:
    assert _go_version_triplet(version, context="fixture") == expected


@pytest.mark.parametrize("version", ["1", "01.27", "1.twenty", "1.27.0.1"])
def test_go_version_parser_rejects_non_release_forms(version: str) -> None:
    with pytest.raises(RuntimeError, match="must be an exact Go version"):
        _go_version_triplet(version, context="fixture")


def test_go_mod_requirement_is_single_exact_utf8_directive() -> None:
    go_mod = b"module example.test/tool\r\n\r\n  go 1.27.2 // release floor\r\n"

    assert _extract_go_requirement(go_mod, source_name="fixture") == "1.27.2"


@pytest.mark.parametrize(
    "toolchain",
    [
        "go1.27.1",
        "default",
    ],
)
def test_go_mod_requirement_uses_go_floor_not_toolchain_suggestion(
    toolchain: str,
) -> None:
    go_mod = (
        "module example.test/tool\r\n\r\n"
        "go 1.26.0\r\n"
        f"  toolchain {toolchain} // preferred compiler\r\n"
    ).encode()

    assert _extract_go_requirement(go_mod, source_name="fixture") == "1.26.0"


@pytest.mark.parametrize(
    "dependency",
    [
        "toolchain",
        "toolchain.example/dependency",
        "go",
    ],
)
def test_directive_named_dependencies_in_require_block_are_not_top_level(
    dependency: str,
) -> None:
    go_mod = f"""module example.test/tool

go 1.26.0

require (
    {dependency} v1.2.3
)
""".encode()

    assert _extract_go_requirement(go_mod, source_name="fixture") == "1.26.0"


@pytest.mark.parametrize(
    "directive",
    [
        "toolchain",
        "toolchain=go1.27.3",
        "toolchain default extra",
        "toolchain go1.27.3 extra",
        "toolchain go01.27.3",
        "toolchain other1.27.3",
    ],
)
def test_go_mod_requirement_rejects_malformed_toolchain_directive(
    directive: str,
) -> None:
    go_mod = f"module example.test/tool\n\ngo 1.27.2\n{directive}\n".encode()

    with pytest.raises(RuntimeError, match="must name"):
        _extract_go_requirement(go_mod, source_name="fixture")


def test_go_mod_requirement_rejects_duplicate_toolchain_directives() -> None:
    go_mod = (
        b"module example.test/tool\n\n"
        b"go 1.27.2\n"
        b"toolchain go1.27.3\n"
        b"toolchain default\n"
    )

    with pytest.raises(RuntimeError, match="at most one Go toolchain, found 2"):
        _extract_go_requirement(go_mod, source_name="fixture")


def test_comment_parenthesis_does_not_hide_toolchain_directives() -> None:
    go_mod = (
        b"module example.test/tool\n\n"
        b"go 1.27.2\n"
        b"// ( this is only a comment\n"
        b"toolchain default\n"
        b"toolchain go1.27.3\n"
    )

    with pytest.raises(RuntimeError, match="at most one Go toolchain, found 2"):
        _extract_go_requirement(go_mod, source_name="fixture")


@pytest.mark.parametrize(
    ("go_mod", "message"),
    [
        (b"module example.test/tool\n", "exactly one Go version, found 0"),
        (b"go 1.26\ngo 1.27\n", "exactly one Go version, found 2"),
        (b"go 1.27rc1\n", "must be an exact Go version"),
        (b"go 1.27 extra\n", "exactly one Go version, found 0"),
        (b"\xff", "not valid UTF-8"),
    ],
)
def test_go_mod_requirement_rejects_ambiguous_or_invalid_input(
    go_mod: bytes,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        _extract_go_requirement(go_mod, source_name="fixture")


def test_locked_source_requires_expected_immutable_github_repository() -> None:
    node = _flake_node()

    assert _locked_github_source(
        node,
        source_name="axiom-cli",
        expected_owner="axiomhq",
        expected_repo="cli",
    ) == ("axiomhq", "cli", _COMMIT)


@pytest.mark.parametrize(
    ("node", "message"),
    [
        (FlakeLockNode(), "complete GitHub source"),
        (_flake_node(source_type="gitlab"), "complete GitHub source"),
        (_flake_node(owner="other"), "must resolve to axiomhq/cli"),
        (_flake_node(commit="main"), "immutable commit"),
    ],
)
def test_locked_source_rejects_mutable_or_unexpected_identity(
    node: FlakeLockNode,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        _locked_github_source(
            node,
            source_name="axiom-cli",
            expected_owner="axiomhq",
            expected_repo="cli",
        )


@pytest.mark.parametrize(
    ("path", "class_name", "source_name", "owner", "repo"),
    _UPDATERS,
)
def test_fetch_latest_validates_locked_go_mod_against_exact_selected_nix_go(
    path: str,
    class_name: str,
    source_name: str,
    owner: str,
    repo: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    updater = _updater(path, class_name, suffix=f"{source_name}_fetch")
    updater.config = resolve_config(subprocess_timeout=137)
    node = _flake_node(owner=owner, repo=repo)
    (tmp_path / "go.mod").write_bytes(
        b"module example.test/tool\n\ngo 1.26.0\ntoolchain go1.27.1\n"
    )
    source_calls: list[tuple[str, float]] = []
    nix_calls: list[list[str]] = []
    session = object()

    monkeypatch.setattr(
        "lib.update.flake.get_flake_input_node",
        lambda input_name: node if input_name == source_name else None,
    )
    monkeypatch.setattr(
        "lib.update.updaters.go_compatibility.local_flake_url",
        lambda: "git+file:///fixture?dirty=1",
    )
    monkeypatch.setattr(
        "lib.update.updaters.go_compatibility.update_nix.get_current_nix_platform",
        lambda: "aarch64-darwin",
    )

    async def _nix_eval_raw(expr: str, *, command_timeout: float) -> str:
        source_calls.append((expr, command_timeout))
        return str(tmp_path)

    async def _run_nix(
        args: list[str],
        *,
        check: bool,
        command_timeout: float,
    ):
        assert check is False
        assert command_timeout == 137
        nix_calls.append(args)
        return SimpleNamespace(returncode=0, stdout="1.27.0\n", stderr="")

    monkeypatch.setattr(locked_source, "nix_eval_raw", _nix_eval_raw)
    monkeypatch.setattr(
        "lib.update.updaters.go_compatibility.run_nix",
        _run_nix,
    )

    info = run_async(updater.fetch_latest(session))

    assert info.version == _REF
    assert info.commit == _COMMIT
    assert len(source_calls) == 1
    assert source_calls[0][1] == 137
    assert_nix_ast_equal(
        source_calls[0][0],
        flake_source_path_expression(node),
    )
    assert len(nix_calls) == 1
    assert nix_calls[0][:-1] == ["nix", "eval", "--impure", "--raw", "--expr"]
    assert_nix_ast_equal(
        nix_calls[0][-1],
        LetExpression(
            local_variables=[
                Binding(
                    name="flake",
                    value=FunctionCall(
                        name=identifier_attr_path("builtins", "getFlake"),
                        argument=StringPrimitive(
                            value="git+file:///fixture?dirty=1",
                        ),
                    ),
                ),
            ],
            value=identifier_attr_path(
                "flake",
                "pkgs",
                '"aarch64-darwin"',
                f'"{source_name}"',
                "passthru",
                "goVersion",
            ),
        ),
    )


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (
            SimpleNamespace(returncode=1, stdout="", stderr="lookup failed"),
            "lookup failed",
        ),
        (
            SimpleNamespace(returncode=1, stdout="lookup output", stderr=""),
            "lookup output",
        ),
        (
            SimpleNamespace(returncode=0, stdout="\n", stderr=""),
            "nix eval failed",
        ),
        (
            SimpleNamespace(returncode=0, stdout="1.27rc1\n", stderr=""),
            "must be an exact Go version",
        ),
    ],
)
def test_selected_go_resolution_fails_closed(
    result: SimpleNamespace,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = _updater(suffix=f"bad_eval_{message}")
    monkeypatch.setattr(
        "lib.update.updaters.go_compatibility.update_nix.get_current_nix_platform",
        lambda: "x86_64-linux",
    )

    async def _run_nix(
        _args: list[str],
        *,
        check: bool,
        command_timeout: float,
    ):
        assert check is False
        assert command_timeout == updater.config.default_subprocess_timeout
        return result

    monkeypatch.setattr(
        "lib.update.updaters.go_compatibility.run_nix",
        _run_nix,
    )

    with pytest.raises(RuntimeError, match=message):
        run_async(updater._resolve_selected_go_version())


def test_fetch_latest_rejects_oversized_locked_go_mod_before_go_evaluation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    updater = _updater(suffix="oversized_locked_go_mod")
    node = _flake_node()
    (tmp_path / "go.mod").write_bytes(b" " * (_MAX_GO_MOD_BYTES + 1))

    monkeypatch.setattr(
        "lib.update.flake.get_flake_input_node",
        lambda input_name: node if input_name == "axiom-cli" else None,
    )

    async def _nix_eval_raw(_expr: str, *, command_timeout: float) -> str:
        assert command_timeout == updater.config.default_subprocess_timeout
        return str(tmp_path)

    async def _unexpected_selected_go() -> str:
        pytest.fail("oversized go.mod reached Go toolchain evaluation")

    monkeypatch.setattr(locked_source, "nix_eval_raw", _nix_eval_raw)
    monkeypatch.setattr(
        updater,
        "_resolve_selected_go_version",
        _unexpected_selected_go,
    )

    with pytest.raises(RuntimeError, match="go.mod exceeds 1048576 bytes"):
        run_async(updater.fetch_latest(object()))


def test_selected_go_resolution_accepts_future_exact_package_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Nix package, rather than a duplicated Python series, owns policy."""
    updater = _updater(suffix="future_package_contract")
    monkeypatch.setattr(
        "lib.update.updaters.go_compatibility.update_nix.get_current_nix_platform",
        lambda: "x86_64-linux",
    )

    async def _run_nix(
        _args: list[str],
        *,
        check: bool,
        command_timeout: float,
    ):
        assert check is False
        assert command_timeout == updater.config.default_subprocess_timeout
        return SimpleNamespace(returncode=0, stdout="1.28.0\n", stderr="")

    monkeypatch.setattr(
        "lib.update.updaters.go_compatibility.run_nix",
        _run_nix,
    )

    assert run_async(updater._resolve_selected_go_version()) == "1.28.0"


@pytest.mark.parametrize("required", ["1.26.9", "1.27.4"])
def test_compatibility_accepts_satisfied_release_floor(required: str) -> None:
    updater = _updater(suffix=f"compatible_{required}")

    updater._validate_go_requirement(required=required, selected="1.27.4")


def test_update_stops_before_fingerprint_or_hash_for_newer_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    updater = _updater(suffix="incompatible_pipeline")
    node = _flake_node()
    (tmp_path / "go.mod").write_bytes(
        b"module example.test/tool\n\ngo 1.27.5\ntoolchain go1.27.6\n"
    )

    monkeypatch.setattr(
        "lib.update.flake.get_flake_input_node",
        lambda input_name: node if input_name == "axiom-cli" else None,
    )

    async def _nix_eval_raw(_expr: str, *, command_timeout: float) -> str:
        assert command_timeout == updater.config.default_subprocess_timeout
        return str(tmp_path)

    async def _selected_go() -> str:
        return "1.27.4"

    async def _unexpected_latest(*_args, **_kwargs):
        pytest.fail("incompatible release reached derivation fingerprinting")

    async def _unexpected_hashes(*_args, **_kwargs):
        pytest.fail("incompatible release reached vendor hashing")
        yield

    monkeypatch.setattr(locked_source, "nix_eval_raw", _nix_eval_raw)
    monkeypatch.setattr(updater, "_resolve_selected_go_version", _selected_go)
    monkeypatch.setattr(updater, "_is_latest", _unexpected_latest)
    monkeypatch.setattr(updater, "fetch_hashes", _unexpected_hashes)

    with pytest.raises(RuntimeError, match="requires Go 1.27.5, newer than"):
        run_async(collect_events(updater.update_stream(None, object())))
