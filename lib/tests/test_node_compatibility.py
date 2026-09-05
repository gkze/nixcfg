"""Behavioral tests for standards-based Node.js toolchain selection."""

from types import SimpleNamespace

import pytest

from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import run_async
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


def test_nodejs_attribute_enumeration_sorts_and_deduplicates_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Candidate order follows numeric majors and ignores duplicate JSON entries."""
    calls: list[tuple[list[str], float, bool]] = []

    async def _run_nix(
        args: list[str],
        *,
        command_timeout: float,
        check: bool,
    ) -> SimpleNamespace:
        calls.append((args, command_timeout, check))
        return SimpleNamespace(
            returncode=0,
            stdout='["nodejs_24", "nodejs_20", "nodejs_24", "nodejs_22"]',
            stderr="",
        )

    monkeypatch.setattr(node_compatibility, "run_nix", _run_nix)

    assert run_async(
        node_compatibility._evaluate_nodejs_attributes(
            "aarch64-darwin",
            command_timeout=29,
            source_name="Fixture",
        )
    ) == ("nodejs_20", "nodejs_22", "nodejs_24")
    assert len(calls) == 1
    args, timeout, check = calls[0]
    assert args[:5] == [
        "nix",
        "eval",
        "--impure",
        "--json",
        "--expr",
    ]
    assert_nix_ast_equal(
        args[5],
        node_compatibility._nixpkgs_package_set_expr("aarch64-darwin"),
    )
    assert args[6] == "--apply"
    assert_nix_ast_equal(
        args[-1], node_compatibility._nodejs_attribute_names_apply_expr()
    )
    assert timeout == 29
    assert check is False


@pytest.mark.parametrize(
    ("result", "error_type", "message"),
    [
        (
            SimpleNamespace(returncode=1, stdout="", stderr="attribute lookup failed"),
            RuntimeError,
            "attribute lookup failed",
        ),
        (
            SimpleNamespace(returncode=1, stdout="lookup output", stderr=""),
            RuntimeError,
            "lookup output",
        ),
        (
            SimpleNamespace(returncode=1, stdout="", stderr=""),
            RuntimeError,
            "nix eval failed",
        ),
        (
            SimpleNamespace(returncode=0, stdout="not-json", stderr=""),
            RuntimeError,
            "invalid JSON",
        ),
        (
            SimpleNamespace(returncode=0, stdout='{"nodejs_24": "24.19.0"}', stderr=""),
            TypeError,
            "expected a JSON list",
        ),
        (
            SimpleNamespace(returncode=0, stdout='["nodejs_latest"]', stderr=""),
            RuntimeError,
            "unexpected attribute 'nodejs_latest'",
        ),
        (
            SimpleNamespace(returncode=0, stdout="[24]", stderr=""),
            RuntimeError,
            "unexpected attribute 24",
        ),
    ],
)
def test_nodejs_attribute_enumeration_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    result: SimpleNamespace,
    error_type: type[Exception],
    message: str,
) -> None:
    """Evaluation failures and malformed discovery payloads cannot select a runtime."""

    async def _run_nix(
        _args: list[str],
        *,
        command_timeout: float,
        check: bool,
    ) -> SimpleNamespace:
        assert command_timeout == 37
        assert check is False
        return result

    monkeypatch.setattr(node_compatibility, "run_nix", _run_nix)

    with pytest.raises(error_type, match=message):
        run_async(
            node_compatibility._evaluate_nodejs_attributes(
                "x86_64-linux",
                command_timeout=37,
                source_name="Fixture",
            )
        )


def test_nodejs_resolution_selects_first_satisfying_available_major(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed older candidate does not hide the next satisfying runtime."""
    calls: list[tuple[str, str, float, str]] = []
    monkeypatch.setattr(
        node_compatibility.update_nix,
        "get_current_nix_platform",
        lambda: "aarch64-darwin",
    )

    async def _attributes(
        platform: str,
        *,
        command_timeout: float,
        source_name: str,
    ) -> tuple[str, ...]:
        assert (platform, command_timeout, source_name) == (
            "aarch64-darwin",
            41,
            "Fixture",
        )
        return ("nodejs_20", "nodejs_22", "nodejs_24")

    async def _version(
        package_attr: str,
        *,
        platform: str,
        command_timeout: float,
        source_name: str,
    ) -> str:
        calls.append((package_attr, platform, command_timeout, source_name))
        if package_attr == "nodejs_20":
            raise RuntimeError("unsupported on this platform")
        return {
            "nodejs_22": "22.19.0",
            "nodejs_24": "24.19.0",
        }[package_attr]

    monkeypatch.setattr(node_compatibility, "_evaluate_nodejs_attributes", _attributes)
    monkeypatch.setattr(
        node_compatibility,
        "_resolve_nixpkgs_package_version_for_platform",
        _version,
    )

    assert run_async(
        node_compatibility.resolve_nixpkgs_nodejs_for_engine(
            ">=24 <25",
            command_timeout=41,
            source_name="Fixture",
        )
    ) == node_compatibility.NodejsSelection(
        engine=">=24 <25",
        attribute="nodejs_24",
        version="24.19.0",
    )
    assert calls == [
        ("nodejs_20", "aarch64-darwin", 41, "Fixture"),
        ("nodejs_22", "aarch64-darwin", 41, "Fixture"),
        ("nodejs_24", "aarch64-darwin", 41, "Fixture"),
    ]


def test_nodejs_resolution_reports_available_and_invalid_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No-match diagnostics distinguish usable versions from malformed ones."""
    monkeypatch.setattr(
        node_compatibility.update_nix,
        "get_current_nix_platform",
        lambda: "x86_64-linux",
    )

    async def _attributes(
        _platform: str,
        *,
        command_timeout: float,
        source_name: str,
    ) -> tuple[str, ...]:
        assert command_timeout == 43
        assert source_name == "Fixture"
        return ("nodejs_20", "nodejs_22")

    async def _version(
        package_attr: str,
        *,
        platform: str,
        command_timeout: float,
        source_name: str,
    ) -> str:
        assert (platform, command_timeout, source_name) == (
            "x86_64-linux",
            43,
            "Fixture",
        )
        return "20.19.0" if package_attr == "nodejs_20" else "22.19"

    monkeypatch.setattr(node_compatibility, "_evaluate_nodejs_attributes", _attributes)
    monkeypatch.setattr(
        node_compatibility,
        "_resolve_nixpkgs_package_version_for_platform",
        _version,
    )

    with pytest.raises(RuntimeError) as exc_info:
        run_async(
            node_compatibility.resolve_nixpkgs_nodejs_for_engine(
                ">=24",
                command_timeout=43,
                source_name="Fixture",
            )
        )

    message = str(exc_info.value)
    assert "available versions: nodejs_20=20.19.0" in message
    assert "nodejs_22" in message
    assert "exact semantic version" in message


def test_nodejs_resolution_rejects_an_empty_candidate_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pinned package set without versioned Node.js attributes fails explicitly."""
    monkeypatch.setattr(
        node_compatibility.update_nix,
        "get_current_nix_platform",
        lambda: "aarch64-darwin",
    )

    async def _attributes(
        _platform: str,
        *,
        command_timeout: float,
        source_name: str,
    ) -> tuple[str, ...]:
        assert command_timeout == 47
        assert source_name == "Fixture"
        return ()

    monkeypatch.setattr(node_compatibility, "_evaluate_nodejs_attributes", _attributes)

    with pytest.raises(RuntimeError, match="available versions: none"):
        run_async(
            node_compatibility.resolve_nixpkgs_nodejs_for_engine(
                ">=24",
                command_timeout=47,
                source_name="Fixture",
            )
        )


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
