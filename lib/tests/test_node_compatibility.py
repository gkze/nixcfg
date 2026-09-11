"""Behavioral tests for standards-based Node.js toolchain selection."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import run_async
from lib.update import runtime
from lib.update.config import default_config
from lib.update.nix import _build_flake_attr_expr
from lib.update.updaters import node_compatibility


@pytest.mark.parametrize(
    ("engine", "version"),
    [
        (">=22 <25", "24.19.0"),
        ("^22.0.0 || >=24.0.0", "24.19.0"),
        ("24.x", "24.19.0"),
        ("22.0.0 - 24.19.0", "24.19.0"),
        (">=24.19.0-rc.1 <24.19.0", "24.19.0-rc.2"),
    ],
)
def test_node_engine_accepts_standard_ranges_satisfied_by_selected_version(
    engine: str,
    version: str,
) -> None:
    """Node engine checks use npm's comparator, OR, x, hyphen, and prerelease rules."""
    assert (
        node_compatibility.require_supported_node_engine(
            engine,
            selected_attr="fixture.passthru.nodejsVersion",
            selected_version=version,
            source_name="Fixture",
        )
        == engine
    )


def test_node_engine_rejects_newer_patch_within_selected_major() -> None:
    """A shared major does not imply that the selected runtime satisfies the range."""
    with pytest.raises(
        RuntimeError, match=r"does not satisfy Node engine '>=24\.20\.0'"
    ):
        node_compatibility.require_supported_node_engine(
            ">=24.20.0",
            selected_attr="fixture.passthru.nodejsVersion",
            selected_version="24.19.0",
            source_name="Fixture",
        )


def test_node_engine_error_names_manifest_derived_attribute() -> None:
    """Dynamic toolchain diagnostics identify the package attribute in use."""
    with pytest.raises(RuntimeError, match=r"package-selected nodejs_26 '26\.0\.0'"):
        node_compatibility.require_supported_node_engine(
            ">=26.1.0",
            selected_attr="nodejs_26",
            selected_version="26.0.0",
            source_name="Fixture",
        )


@pytest.mark.parametrize(
    ("engine", "error_type", "message"),
    [
        (None, TypeError, "Node engine is missing"),
        ("", TypeError, "Node engine is missing"),
        ("workspace:*", RuntimeError, "valid npm semantic-version range"),
    ],
)
def test_node_engine_rejects_missing_or_invalid_constraints(
    engine: object,
    error_type: type[Exception],
    message: str,
) -> None:
    """Missing and non-semver engine constraints fail closed."""
    with pytest.raises(error_type, match=message):
        node_compatibility.require_supported_node_engine(
            engine,
            selected_attr="fixture.passthru.nodejsVersion",
            selected_version="24.19.0",
            source_name="Fixture",
        )


def test_node_engine_rejects_non_exact_selected_version() -> None:
    """The evaluated Nix toolchain must identify one exact runtime."""
    with pytest.raises(RuntimeError, match="exact semantic version"):
        node_compatibility.require_supported_node_engine(
            ">=24",
            selected_attr="fixture.passthru.nodejsVersion",
            selected_version="24.19",
            source_name="Fixture",
        )


def test_resolve_package_passthru_version_evaluates_package_owned_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolution reads the package's exact selected toolchain contract."""
    calls: list[tuple[list[str], float, bool]] = []
    flake_url = "git+file:///fixture?dirty=1"
    monkeypatch.setattr(node_compatibility, "local_flake_url", lambda: flake_url)
    monkeypatch.setattr(
        node_compatibility.update_nix,
        "get_current_nix_platform",
        lambda: "aarch64-darwin",
    )

    async def _run_nix(
        args: list[str],
        *,
        command_timeout: float,
        check: bool,
    ) -> SimpleNamespace:
        calls.append((args, command_timeout, check))
        return SimpleNamespace(returncode=0, stdout="24.19.0\n", stderr="")

    monkeypatch.setattr(node_compatibility, "run_nix", _run_nix)

    assert (
        run_async(
            node_compatibility.resolve_package_passthru_version(
                "gooeypi",
                "nodejsVersion",
                command_timeout=17,
                source_name="Fixture",
            )
        )
        == "24.19.0"
    )
    assert calls == [
        (
            [
                "nix",
                "eval",
                "--impure",
                "--raw",
                "--expr",
                _build_flake_attr_expr(
                    flake_url,
                    "pkgs",
                    "aarch64-darwin",
                    "gooeypi",
                    "passthru",
                    "nodejsVersion",
                    quoted_indices=(1, 2, 4),
                ),
            ],
            17,
            False,
        )
    ]


def test_resolve_nixpkgs_package_version_supports_updater_derived_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dynamic pnpm majors use the same exact flake package boundary."""
    calls: list[tuple[list[str], float]] = []
    monkeypatch.setattr(
        node_compatibility.update_nix,
        "get_current_nix_platform",
        lambda: "x86_64-linux",
    )

    async def _run_nix(
        args: list[str],
        *,
        command_timeout: float,
        check: bool,
    ) -> SimpleNamespace:
        assert check is False
        calls.append((args, command_timeout))
        return SimpleNamespace(returncode=0, stdout="10.34.5\n", stderr="")

    monkeypatch.setattr(node_compatibility, "run_nix", _run_nix)

    assert (
        run_async(
            node_compatibility.resolve_nixpkgs_package_version(
                "pnpm_10",
                command_timeout=23,
                source_name="Fixture",
            )
        )
        == "10.34.5"
    )
    assert calls == [
        (
            [
                "nix",
                "eval",
                "--impure",
                "--raw",
                "--expr",
                node_compatibility._nixpkgs_package_version_expr(
                    "x86_64-linux",
                    "pnpm_10",
                ),
            ],
            23,
        )
    ]


def test_nodejs_enumeration_expressions_target_the_pinned_package_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Node discovery is derived from the current flake instead of a fixed major."""
    flake_url = "git+file:///fixture?dirty=1"
    monkeypatch.setattr(node_compatibility, "local_flake_url", lambda: flake_url)

    assert_nix_ast_equal(
        node_compatibility._nodejs_attribute_names_apply_expr(),
        'pkgs: builtins.filter (name: builtins.match "nodejs_[0-9]+" name != null) '
        "(builtins.attrNames pkgs)",
    )
    assert_nix_ast_equal(
        node_compatibility._nixpkgs_package_set_expr("aarch64-darwin"),
        _build_flake_attr_expr(
            flake_url,
            "pkgs",
            "aarch64-darwin",
            quoted_indices=(1,),
        ),
    )
    assert_nix_ast_equal(
        node_compatibility._nixpkgs_package_version_expr(
            "aarch64-darwin",
            "nodejs_24",
        ),
        _build_flake_attr_expr(
            flake_url,
            "pkgs",
            "aarch64-darwin",
            "nodejs_24",
            "version",
            quoted_indices=(1,),
        ),
    )


def test_nodejs_inventory_expression_isolates_candidate_failures() -> None:
    """One Nix evaluation catches unsupported aliases without forcing packages."""
    assert_nix_ast_equal(
        node_compatibility._nodejs_inventory_apply_expr(),
        """pkgs: builtins.listToAttrs (builtins.map (name: {
          name = name;
          value = builtins.tryEval (let package = builtins.getAttr name pkgs;
            version = if builtins.isAttrs package then package.version or null else null;
            in if builtins.isString version then version
              else throw "Node.js version is not a string");
        }) ((pkgs: builtins.filter
          (name: builtins.match "nodejs_[0-9]+" name != null)
          (builtins.attrNames pkgs)) pkgs))""",
    )


def test_nodejs_inventory_selects_lowest_compatible_major_with_one_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed aliases remain isolated and JSON key order cannot select a newer major."""
    calls: list[list[str]] = []

    async def _run_nix(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(args)
        assert kwargs == {"command_timeout": 29, "check": False}
        return SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps({
                "nodejs_24": {"success": True, "value": "24.19.0"},
                "nodejs_18": {"success": False, "value": False},
                "nodejs_22": {"success": True, "value": "22.19.0"},
            }),
        )

    monkeypatch.setattr(node_compatibility, "run_nix", _run_nix)
    selection = run_async(
        node_compatibility.resolve_nixpkgs_nodejs_for_engine(
            ">=20", command_timeout=29, source_name="Fixture"
        )
    )
    assert selection == node_compatibility.NodejsSelection(
        engine=">=20", attribute="nodejs_22", version="22.19.0"
    )
    assert len(calls) == 1
    args = calls[0]
    assert args[:5] == ["nix", "eval", "--impure", "--json", "--expr"]
    assert args[6] == "--apply"
    assert_nix_ast_equal(args[7], node_compatibility._nodejs_inventory_apply_expr())


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
        (SimpleNamespace(returncode=1, stdout="", stderr=""), "nix eval failed"),
        (
            SimpleNamespace(returncode=0, stdout="not-json", stderr=""),
            "invalid JSON inventory",
        ),
        (
            SimpleNamespace(returncode=0, stdout="[]", stderr=""),
            "invalid JSON inventory",
        ),
        (
            SimpleNamespace(
                returncode=0,
                stdout='{"nodejs_24":{"success":true,"value":24}}',
                stderr="",
            ),
            "invalid JSON inventory",
        ),
        (
            SimpleNamespace(
                returncode=0,
                stdout='{"nodejs_24":{"success":false,"value":"24.0.0"}}',
                stderr="",
            ),
            "invalid JSON inventory",
        ),
        (
            SimpleNamespace(
                returncode=0,
                stdout='{"nodejs_latest":{"success":true,"value":"24.0.0"}}',
                stderr="",
            ),
            "Unexpected nixpkgs Node.js attribute",
        ),
    ],
)
def test_nodejs_inventory_fails_closed(
    monkeypatch: pytest.MonkeyPatch, result: SimpleNamespace, message: str
) -> None:
    """Malformed candidates and a failed batch cannot produce a runtime selection."""

    async def _run_nix(_args: list[str], **_kwargs: object) -> SimpleNamespace:
        return result

    monkeypatch.setattr(node_compatibility, "run_nix", _run_nix)
    with pytest.raises(RuntimeError, match=message):
        run_async(
            node_compatibility.resolve_nixpkgs_nodejs_for_engine(
                ">=20", command_timeout=29, source_name="Fixture"
            )
        )


@pytest.mark.parametrize(
    ("inventory", "messages"),
    [
        ({}, ("available versions: none",)),
        (
            {
                "nodejs_18": {"success": False, "value": False},
                "nodejs_20": {"success": True, "value": "20.19.0"},
                "nodejs_22": {"success": True, "value": "22.19"},
            },
            (
                "nodejs_20=20.19.0",
                "nodejs_18: version evaluation failed",
                "exact semantic version",
            ),
        ),
    ],
)
def test_nodejs_inventory_no_match_reports_usable_versions_and_failures(
    monkeypatch: pytest.MonkeyPatch,
    inventory: dict[str, dict[str, str | bool]],
    messages: tuple[str, ...],
) -> None:
    """Unsupported candidates, invalid semver, and absence remain distinguishable."""

    async def _run_nix(_args: list[str], **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=json.dumps(inventory), stderr="")

    monkeypatch.setattr(node_compatibility, "run_nix", _run_nix)
    with pytest.raises(RuntimeError) as error:
        run_async(
            node_compatibility.resolve_nixpkgs_nodejs_for_engine(
                ">=24", command_timeout=29, source_name="Fixture"
            )
        )
    assert all(message in str(error.value) for message in messages)


def test_nodejs_inventory_does_not_cache_mutable_package_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later package overlay change within the run must remain visible."""
    versions = iter(["24.0.0", "24.1.0"])

    async def _run_nix(_args: list[str], **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps({
                "nodejs_24": {"success": True, "value": next(versions)}
            }),
        )

    monkeypatch.setattr(node_compatibility, "run_nix", _run_nix)

    async def _run() -> tuple[str, str]:
        async with runtime.runtime_scope(default_config()):
            first = await node_compatibility.resolve_nixpkgs_nodejs_for_engine(
                ">=24", command_timeout=29, source_name="Fixture"
            )
            second = await node_compatibility.resolve_nixpkgs_nodejs_for_engine(
                ">=24", command_timeout=29, source_name="Fixture"
            )
            return first.version, second.version

    assert run_async(_run()) == ("24.0.0", "24.1.0")


@pytest.mark.parametrize("inventory", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_node_evaluation_respects_workspace_and_resource_budgets(
    monkeypatch: pytest.MonkeyPatch, *, inventory: bool, failed: bool
) -> None:
    """Direct version lookups wait for stable files and an available evaluator."""
    calls: list[list[str]] = []
    stdout = json.dumps({"nodejs_24": {"success": True, "value": "24.0.0"}})
    if not inventory:
        stdout = "24.0.0\n"
    stderr = "échec" if failed else ""

    async def evaluate(args: list[str], **_kwargs: object) -> SimpleNamespace:
        owner = runtime.active_runtime()
        assert owner is not None
        assert asyncio.current_task() in owner.workspace_reader_owners
        assert owner.slots["eval"].locked()
        calls.append(args)
        return SimpleNamespace(returncode=int(failed), stdout=stdout, stderr=stderr)

    async def invoke() -> object:
        if inventory:
            return await node_compatibility._evaluate_nodejs_inventory(
                "aarch64-darwin", command_timeout=10, source_name="Fixture"
            )
        return await node_compatibility._evaluate_version(
            '"24.0.0"', command_timeout=10, selection="nodejs", source_name="Fixture"
        )

    monkeypatch.setattr(node_compatibility, "run_nix", evaluate)

    async def run() -> None:
        config = default_config()
        async with runtime.runtime_scope(config) as owner:
            async with runtime.resource_slot("eval", source="owner", config=config):
                async with runtime.workspace_access(write=True):
                    pending = asyncio.create_task(invoke())
                    await asyncio.sleep(0)
                    assert not calls
                await asyncio.sleep(0)
                assert not calls
            if failed:
                with pytest.raises(RuntimeError, match="échec"):
                    await pending
            else:
                await pending
            assert len(calls) == 1
            timing = owner.timing("Fixture", "eval")
            assert timing.stdout_bytes == len(stdout.encode())
            assert timing.stderr_bytes == len(stderr.encode())
            assert timing.nonzero_exits == int(failed)

    run_async(run())


def test_resolve_nixpkgs_package_version_rejects_invalid_attribute() -> None:
    """Manifest-derived attributes cannot inject arbitrary Nix expressions."""
    with pytest.raises(RuntimeError, match="Invalid nixpkgs package attribute"):
        run_async(
            node_compatibility.resolve_nixpkgs_package_version(
                'pnpm_10; builtins.abort "unexpected"',
                command_timeout=1,
                source_name="Fixture",
            )
        )


@pytest.mark.parametrize(
    ("package_attr", "passthru_attr", "message"),
    [
        ('gooeypi; builtins.abort "unexpected"', "nodejsVersion", "flake package"),
        ("gooeypi", 'nodejsVersion; builtins.abort "unexpected"', "passthru"),
    ],
)
def test_resolve_package_passthru_version_rejects_invalid_attributes(
    package_attr: str,
    passthru_attr: str,
    message: str,
) -> None:
    """Dynamic attribute segments cannot inject arbitrary Nix expressions."""
    with pytest.raises(RuntimeError, match=message):
        run_async(
            node_compatibility.resolve_package_passthru_version(
                package_attr,
                passthru_attr,
                command_timeout=1,
                source_name="Fixture",
            )
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
            SimpleNamespace(returncode=0, stdout="24.19\n", stderr=""),
            "exact semantic version",
        ),
    ],
)
def test_resolve_package_passthru_version_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    result: SimpleNamespace,
    message: str,
) -> None:
    """Evaluator failures and non-exact outputs cannot pass compatibility checks."""

    async def _run_nix(
        _args: list[str],
        *,
        command_timeout: float,
        check: bool,
    ) -> SimpleNamespace:
        assert check is False
        assert command_timeout == 31
        return result

    monkeypatch.setattr(node_compatibility, "run_nix", _run_nix)

    with pytest.raises(RuntimeError, match=message):
        run_async(
            node_compatibility.resolve_package_passthru_version(
                "gooeypi",
                "nodejsVersion",
                command_timeout=31,
                source_name="Fixture",
            )
        )


def test_nodejs_inventory_nix_failure_isolation() -> None:
    """A tiny evaluation proves tryEval/laziness semantics that AST equality cannot."""
    from pathlib import Path

    from nix_manipulator.expressions.function.call import FunctionCall
    from nix_manipulator.expressions.identifier import Identifier
    from nix_manipulator.expressions.parenthesis import Parenthesis
    from nix_manipulator.expressions.path import NixPath
    from nix_manipulator.parser import parse

    fixture = Path(__file__).parents[2] / "tests/nix/node-inventory.nix"
    expression = FunctionCall(
        name=Parenthesis(
            value=FunctionCall(
                name=Identifier(name="import"), argument=NixPath(path=str(fixture))
            )
        ),
        argument=Parenthesis(
            value=parse(node_compatibility._nodejs_inventory_apply_expr()).expr
        ),
    )
    result = run_async(
        node_compatibility.run_nix(
            ["nix", "eval", "--impure", "--json", "--expr", expression.rebuild()],
            command_timeout=10,
        )
    )
    assert json.loads(result.stdout) == {
        "nodejs_18": {"success": False, "value": False},
        "nodejs_20": {"success": False, "value": False},
        "nodejs_22": {"success": True, "value": "22.19.0"},
        "nodejs_24": {"success": False, "value": False},
        "nodejs_26": {"success": False, "value": False},
    }
