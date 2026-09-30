"""Behavioral tests for target-aware update derivation validation."""

import json
import subprocess
import threading
import time
from contextlib import nullcontext
from pathlib import Path

import pytest
from pydantic import ValidationError

from lib.tests._nix_ast import assert_nix_ast_equal
from lib.update import derivation_validation as validation
from lib.update.derivation_validation import (
    DerivationValidation,
    DerivationValidationFailure,
    DerivationValidationRequest,
)
from lib.update.paths import get_repo_root
from lib.update.updaters import Crate2NixArtifactsMixin, Updater


class _DarwinAndLinuxUpdater:
    derivation_validations = (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}.drvPath",
            systems=("aarch64-darwin", "x86_64-linux"),
        ),
    )


class _LinuxOnlyUpdater:
    derivation_validations = (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}.drvPath",
            systems=("x86_64-linux",),
        ),
    )


class _PortableUpdater:
    derivation_validations = (DerivationValidation(installable=".#portable.drvPath"),)


class _BuildPortableUpdater:
    derivation_validations = (
        DerivationValidation(installable=".#portable", mode="build"),
    )


class _DuplicateUpdater:
    derivation_validations = (
        DerivationValidation(
            installable=".#duplicate.drvPath",
            systems=("aarch64-darwin", "x86_64-linux"),
        ),
    )


class _NoValidationUpdater:
    pass


class _GooseCrate2NixUpdater(Crate2NixArtifactsMixin):
    name = "goose-cli"


class _UnknownCrate2NixUpdater(Crate2NixArtifactsMixin):
    name = "unknown"


@pytest.mark.parametrize(
    ("entry_type", "payload"),
    [
        (
            validation.RootClosureManifestIdentity,
            {"kind": "darwin", "name": "argus"},
        ),
        (
            validation.RootClosureManifestRoot,
            {"kind": "darwin", "name": "argus", "system": "aarch64-darwin"},
        ),
    ],
)
def test_root_manifest_entries_preserve_payload_and_immutable_identity(
    entry_type: type[
        validation.RootClosureManifestIdentity | validation.RootClosureManifestRoot
    ],
    payload: dict[str, str],
) -> None:
    """Root identity survives serialization and cannot change after validation."""
    entry = entry_type.model_validate(payload)

    assert entry.model_dump(mode="json") == payload
    with pytest.raises(ValidationError, match="frozen"):
        # Exercise runtime immutability, which static typing also rejects.
        entry.name = "another-host"  # ty: ignore[invalid-assignment]


@pytest.mark.parametrize(
    "entry_type",
    [validation.RootClosureManifestIdentity, validation.RootClosureManifestRoot],
)
@pytest.mark.parametrize(
    "invalid_identity",
    [
        {"kind": "darwin"},
        {"kind": "darwin", "name": ""},
        {"kind": "unknown", "name": "argus"},
        {"kind": "darwin", "name": "argus", "unexpected": "value"},
    ],
)
def test_root_manifest_entries_reject_invalid_identity(
    entry_type: type[
        validation.RootClosureManifestIdentity | validation.RootClosureManifestRoot
    ],
    invalid_identity: dict[str, str],
) -> None:
    """Both entry kinds reject missing, invalid, and unrecognized identity data."""
    payload = dict(invalid_identity)
    if entry_type is validation.RootClosureManifestRoot:
        payload["system"] = "aarch64-darwin"

    with pytest.raises(ValidationError):
        entry_type.model_validate(payload)


@pytest.mark.parametrize(
    ("entry_type", "payload"),
    [
        (
            validation.RootClosureManifestIdentity,
            {"kind": "darwin", "name": "argus", "system": "aarch64-darwin"},
        ),
        (validation.RootClosureManifestRoot, {"kind": "darwin", "name": "argus"}),
        (
            validation.RootClosureManifestRoot,
            {"kind": "darwin", "name": "argus", "system": ""},
        ),
    ],
)
def test_root_manifest_entries_keep_distinct_system_contracts(
    entry_type: type[
        validation.RootClosureManifestIdentity | validation.RootClosureManifestRoot
    ],
    payload: dict[str, str],
) -> None:
    """Only configured roots carry a system, and that system is required."""
    with pytest.raises(ValidationError):
        entry_type.model_validate(payload)


def test_root_manifest_entries_keep_distinct_model_roles() -> None:
    """Configured roots and required identities cannot substitute for each other."""
    identity = validation.RootClosureManifestIdentity(kind="darwin", name="argus")
    root = validation.RootClosureManifestRoot(
        kind="darwin",
        name="argus",
        system="aarch64-darwin",
    )

    with pytest.raises(ValidationError):
        validation.RootClosureManifestIdentity.model_validate(root)
    with pytest.raises(ValidationError):
        validation.RootClosureManifestRoot.model_validate(identity)


def test_resolve_derivation_validations_honors_target_platforms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve only current-system checks locally and every declared check in CI."""
    monkeypatch.setattr(
        validation,
        "get_current_nix_platform",
        lambda: "aarch64-darwin",
    )
    updaters = {
        "demo": _DarwinAndLinuxUpdater,
        "linux-only": _LinuxOnlyUpdater,
        "portable": _PortableUpdater,
        "duplicate": _DuplicateUpdater,
        "plain": _NoValidationUpdater,
        "goose-cli": _GooseCrate2NixUpdater,
    }
    selected = [
        "missing",
        "demo",
        "linux-only",
        "portable",
        "duplicate",
        "plain",
        "goose-cli",
    ]

    assert validation.resolve_derivation_validations(
        selected,
        updaters=updaters,
    ) == (
        DerivationValidationRequest(
            source="demo",
            installable=".#pkgs.aarch64-darwin.demo.drvPath",
        ),
        DerivationValidationRequest(
            source="portable",
            installable=".#portable.drvPath",
        ),
        DerivationValidationRequest(
            source="duplicate",
            installable=".#duplicate.drvPath",
        ),
        DerivationValidationRequest(
            source="goose-cli",
            installable=".#pkgs.aarch64-darwin.goose-cli.drvPath",
        ),
    )
    assert validation.resolve_derivation_validations(
        selected,
        updaters=updaters,
        all_declared_systems=True,
    ) == (
        DerivationValidationRequest(
            source="demo",
            installable=".#pkgs.aarch64-darwin.demo.drvPath",
        ),
        DerivationValidationRequest(
            source="demo",
            installable=".#pkgs.x86_64-linux.demo.drvPath",
        ),
        DerivationValidationRequest(
            source="linux-only",
            installable=".#pkgs.x86_64-linux.linux-only.drvPath",
        ),
        DerivationValidationRequest(
            source="portable",
            installable=".#portable.drvPath",
        ),
        DerivationValidationRequest(
            source="duplicate",
            installable=".#duplicate.drvPath",
        ),
        DerivationValidationRequest(
            source="goose-cli",
            installable=".#pkgs.aarch64-darwin.goose-cli.drvPath",
        ),
        DerivationValidationRequest(
            source="goose-cli",
            installable=".#pkgs.x86_64-linux.goose-cli.drvPath",
        ),
    )


def test_updater_validation_metadata_defaults_and_unknown_crate2nix_target() -> None:
    """Keep validation opt-in and skip unregistered crate2nix mixin users."""
    assert Updater.get_derivation_validations() == ()
    assert _UnknownCrate2NixUpdater.get_derivation_validations() == ()


def test_validate_derivations_runs_nix_eval_from_repo_root() -> None:
    """Evaluate a resolved drvPath without building the package."""
    calls: list[tuple[list[str], dict[str, object]]] = []

    def _run(
        args: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args, 0, stdout="/nix/store/demo.drv", stderr=""
        )

    failures = validation.validate_derivations(
        ["portable"],
        updaters={"portable": _PortableUpdater},
        timeout=42,
        run=_run,
    )

    assert failures == ()
    assert calls == [
        (
            [
                "nix",
                "eval",
                "--no-update-lock-file",
                "--option",
                "allow-import-from-derivation",
                "false",
                "--raw",
                "path:.#portable.drvPath",
            ],
            {
                "cwd": get_repo_root(),
                "text": True,
                "capture_output": True,
                "check": False,
                "timeout": 42,
            },
        )
    ]


def test_validate_derivations_can_build_an_installable() -> None:
    """Build-mode validation should realize the package without creating a result link."""
    calls: list[list[str]] = []

    def _run(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    failures = validation.validate_derivations(
        ["portable"],
        updaters={"portable": _BuildPortableUpdater},
        run=_run,
    )

    assert failures == ()
    assert calls == [
        [
            "nix",
            "build",
            "--no-update-lock-file",
            "--no-link",
            "--keep-going",
            "path:.#portable",
        ]
    ]


def test_validate_derivation_requests_preserves_external_installables() -> None:
    """Only rewrite shorthand references to the local candidate flake."""
    calls: list[list[str]] = []

    def _run(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    assert (
        validation.validate_derivation_requests(
            (
                DerivationValidationRequest(
                    source="external",
                    installable="github:example/project#package.drvPath",
                ),
            ),
            run=_run,
        )
        == ()
    )
    assert calls == [
        [
            "nix",
            "eval",
            "--option",
            "allow-import-from-derivation",
            "false",
            "--raw",
            "github:example/project#package.drvPath",
        ]
    ]


@pytest.mark.parametrize(
    ("timeout", "expected_timeout"),
    [
        (None, validation.ROOT_CLOSURE_VALIDATION_TIMEOUT_SECONDS),
        (0, 0),
        (42, 42),
        (30000, 30000),
    ],
)
@pytest.mark.parametrize("systems", [None, ("aarch64-darwin",), ("aarch64-linux",)])
def test_validate_root_closures_builds_flake_owned_aggregate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    timeout: float | None,
    expected_timeout: float,
    systems: tuple[str, ...] | None,
) -> None:
    """Discover and build only nonempty systems from the candidate manifest."""
    calls: list[tuple[list[str], dict[str, object]]] = []
    snapshot_root = tmp_path / "candidate"

    def _run(
        args: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        if args[:2] == ["nix", "eval"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="""
                {
                  "schemaVersion": 2,
                  "requiredKinds": ["darwin", "home"],
                  "requiredRoots": [],
                  "roots": [
                    {"kind": "darwin", "name": "argus", "system": "aarch64-darwin"},
                    {"kind": "home", "name": "george", "system": "aarch64-darwin"},
                    {"kind": "nixos", "name": "server", "system": "x86_64-linux"}
                  ]
                }
                """,
                stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    def _snapshot(root: Path) -> nullcontext[Path]:
        assert root == get_repo_root()
        return nullcontext(snapshot_root)

    monkeypatch.setattr(
        validation.update_persistence,
        "visible_source_snapshot",
        _snapshot,
    )

    assert (
        validation.validate_root_closures(timeout=timeout, run=_run, systems=systems)
        == ()
    )
    expected_kwargs = {
        "cwd": snapshot_root,
        "text": True,
        "capture_output": True,
        "check": False,
        "timeout": expected_timeout,
    }
    assert calls == [
        (
            [
                "nix",
                "eval",
                "--no-update-lock-file",
                "--json",
                f"path:{snapshot_root}#lib.rootClosureManifest",
            ],
            expected_kwargs,
        ),
    ] + (
        [
            (
                [
                    "nix",
                    "build",
                    "--no-update-lock-file",
                    "--no-link",
                    "--keep-going",
                    *(
                        f"path:{snapshot_root}#checks.{system}.root-closures"
                        for system in ("aarch64-darwin", "x86_64-linux")
                        if systems is None or system in systems
                    ),
                ],
                expected_kwargs,
            ),
        ]
        if systems != ("aarch64-linux",)
        else []
    )


@pytest.fixture
def native_root_graph(tmp_path) -> tuple:
    """Model the actual Darwin -> Linux VM boundary at the Nix process seam."""

    def node(system, *dependencies):
        return {
            "version": 4,
            "system": system,
            "inputs": {
                "drvs": {
                    path: {"outputs": ["out"], "dynamicOutputs": {}}
                    for path in dependencies
                }
            },
        }

    graph = {
        "version": 4,
        "derivations": {
            "root.drv": node("aarch64-darwin", "middle.drv", "vm.drv"),
            "middle.drv": node("aarch64-darwin", "vm.drv"),
            "vm.drv": node("aarch64-linux", "linux-leaf.drv", "fetch.drv"),
            "linux-leaf.drv": node("aarch64-linux"),
            "fetch.drv": node("builtin"),
        },
    }
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert kwargs["cwd"] == tmp_path
        assert kwargs["timeout"] == 42
        stdout = ""
        if args[1] == "eval":
            stdout = json.dumps({
                "schemaVersion": 2,
                "requiredKinds": ["darwin", "home"],
                "requiredRoots": [],
                "roots": [
                    {"kind": "darwin", "name": "argus", "system": "aarch64-darwin"},
                    {"kind": "home", "name": "george", "system": "aarch64-darwin"},
                ],
            })
        if args[1:3] == ["derivation", "show"]:
            stdout = json.dumps(graph)
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    return graph, calls, run


@pytest.mark.parametrize("system", ["aarch64-linux", "x86_64-linux", "aarch64-darwin"])
def test_native_validator_builds_the_foreign_root_dependency_boundary(
    native_root_graph,
    tmp_path,
    system,
) -> None:
    """An ARM Linux runner must build the VM even though every root is Darwin."""
    _, calls, run = native_root_graph
    assert (
        validation.validate_root_closures(
            flake_root=tmp_path,
            systems=(system,),
            include_dependencies=True,
            timeout=42,
            run=run,
        )
        == ()
    )
    graph_command = next(args for args in calls if args[1] == "derivation")
    assert graph_command == [
        "nix",
        "derivation",
        "show",
        "--recursive",
        "--no-update-lock-file",
        "--option",
        "allow-import-from-derivation",
        "false",
        f"path:{tmp_path}#checks.aarch64-darwin.root-closures",
    ]
    builds = [args[-1] for args in calls if args[1] == "build"]
    assert (
        builds
        == {
            "aarch64-linux": ["/nix/store/vm.drv^*"],
            "aarch64-darwin": [f"path:{tmp_path}#checks.aarch64-darwin.root-closures"],
            "x86_64-linux": [],
        }[system]
    )


@pytest.mark.parametrize(
    "failure",
    ["missing", "version", "json", "command", "empty", "os", "timeout", "build"],
)
def test_native_dependency_failure_never_issues_success(
    native_root_graph,
    tmp_path,
    failure,
) -> None:
    graph, calls, run = native_root_graph
    if failure == "missing":
        del graph["derivations"]["vm.drv"]
    elif failure == "version":
        graph["version"] = 5

    def fail(args, **kwargs):
        result = run(args, **kwargs)
        if args[1] == "build" and failure == "build":
            return subprocess.CompletedProcess(
                args, 1, stdout="", stderr="VM build failed"
            )
        if args[1] == "derivation":
            if failure == "os":
                raise OSError("Nix unavailable")
            if failure == "timeout":
                raise subprocess.TimeoutExpired(args, 42)
            if failure == "json":
                return subprocess.CompletedProcess(args, 0, stdout="invalid", stderr="")
            if failure in {"command", "empty"}:
                return subprocess.CompletedProcess(
                    args,
                    1,
                    stdout="",
                    stderr="graph unavailable" if failure == "command" else "",
                )
        return result

    if failure in {"os", "timeout"}:
        with pytest.raises(validation.ValidationIncompleteError):
            validation.validate_root_closures(
                flake_root=tmp_path,
                systems=("aarch64-linux",),
                include_dependencies=True,
                timeout=42,
                run=fail,
            )
        assert not any(args[1] == "build" for args in calls)
        return

    failures = validation.validate_root_closures(
        flake_root=tmp_path,
        systems=("aarch64-linux",),
        include_dependencies=True,
        timeout=42,
        run=fail,
    )
    assert len(failures) == 1
    assert failures[0].source == "root-closures"
    assert failures[0].message
    if failure == "build":
        assert failures[0].installable == "/nix/store/vm.drv^*"
        assert failures[0].message == "VM build failed"
    else:
        assert failures[0].installable == "path:.#checks.aarch64-darwin.root-closures"
        assert not any(args[1] == "build" for args in calls)


def test_validate_root_closures_rejects_an_empty_candidate_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Repository root requirements cannot silently disappear."""
    calls: list[list[str]] = []

    def _run(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=(
                '{"schemaVersion": 2, "requiredKinds": '
                '["darwin", "home"], "requiredRoots": [], "roots": []}'
            ),
            stderr="",
        )

    monkeypatch.setattr(
        validation.update_persistence,
        "visible_source_snapshot",
        lambda _root: nullcontext(tmp_path),
    )

    failures = validation.validate_root_closures(run=_run)

    assert len(failures) == 1
    assert "required root kinds have no configured roots" in failures[0].message
    assert calls == [
        [
            "nix",
            "eval",
            "--no-update-lock-file",
            "--json",
            f"path:{tmp_path}#lib.rootClosureManifest",
        ]
    ]


def test_validate_root_closures_requires_every_source_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Output wiring cannot silently drop one host while its source remains."""
    (tmp_path / "darwin").mkdir()
    (tmp_path / "darwin" / "argus.nix").write_text("{}\n", encoding="utf-8")

    def _run(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="""
            {
              "schemaVersion": 2,
              "requiredKinds": ["darwin", "home"],
              "requiredRoots": [],
              "roots": [
                {"kind": "darwin", "name": "argus", "system": "aarch64-darwin"},
                {"kind": "home", "name": "george", "system": "aarch64-darwin"}
              ]
            }
            """,
            stderr="",
        )

    monkeypatch.setattr(
        validation.update_persistence,
        "visible_source_snapshot",
        lambda _root: nullcontext(tmp_path),
    )

    failures = validation.validate_root_closures(run=_run)

    assert len(failures) == 1
    assert "requiredRoots does not match source entrypoints" in failures[0].message


def test_source_required_roots_follows_configuration_entrypoint_conventions(
    tmp_path: Path,
) -> None:
    """Discover host files and standalone Home directories without name lists."""
    (tmp_path / "darwin").mkdir()
    (tmp_path / "darwin" / "argus.nix").write_text("{}\n", encoding="utf-8")
    (tmp_path / "darwin" / "README.md").write_text("ignored\n", encoding="utf-8")
    (tmp_path / "nixos").mkdir()
    (tmp_path / "nixos" / "server.nix").write_text("{}\n", encoding="utf-8")
    (tmp_path / "home" / "alice").mkdir(parents=True)
    (tmp_path / "home" / "alice" / "default.nix").write_text(
        "{}\n",
        encoding="utf-8",
    )
    (tmp_path / "home" / "incomplete").mkdir()

    assert validation._source_required_roots(tmp_path) == (
        validation.RootClosureManifestIdentity(kind="darwin", name="argus"),
        validation.RootClosureManifestIdentity(kind="nixos", name="server"),
        validation.RootClosureManifestIdentity(kind="home", name="alice"),
    )


@pytest.mark.parametrize(
    "manifest",
    [
        ('{"schemaVersion": 3, "requiredKinds": [], "requiredRoots": [], "roots": []}'),
        '{"schemaVersion": 2, "roots": []}',
        (
            '{"schemaVersion": 2, "requiredKinds": ["darwin", "home"], '
            '"requiredRoots": [], "roots": [], "unexpected": true}'
        ),
        """
        {
          "schemaVersion": 2,
          "requiredKinds": ["darwin", "home"],
          "requiredRoots": [],
          "roots": [{"kind": "unknown", "name": "host", "system": "test-system"}]
        }
        """,
        """
        {
          "schemaVersion": 2,
          "requiredKinds": ["darwin"],
          "requiredRoots": [],
          "roots": [{"kind": "darwin", "name": "host", "system": "test-system"}]
        }
        """,
        """
        {
          "schemaVersion": 2,
          "requiredKinds": ["darwin", "home"],
          "requiredRoots": [{"kind": "darwin", "name": "missing"}],
          "roots": [
            {"kind": "darwin", "name": "host", "system": "test-system"},
            {"kind": "home", "name": "person", "system": "test-system"}
          ]
        }
        """,
    ],
)
def test_validate_root_closures_rejects_invalid_candidate_manifests(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    manifest: str,
) -> None:
    """Fail closed when the candidate speaks an unsupported manifest protocol."""
    calls: list[list[str]] = []

    def _run(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=manifest, stderr="")

    monkeypatch.setattr(
        validation.update_persistence,
        "visible_source_snapshot",
        lambda _root: nullcontext(tmp_path),
    )

    failures = validation.validate_root_closures(run=_run)

    assert len(failures) == 1
    assert failures[0].source == "root-closures"
    assert failures[0].installable == "path:.#lib.rootClosureManifest"
    assert "invalid root closure manifest" in failures[0].message
    assert len(calls) == 1


def test_validate_root_closures_reports_manifest_evaluation_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Do not build guessed systems when candidate discovery cannot evaluate."""

    def _run(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr="candidate manifest failed",
        )

    monkeypatch.setattr(
        validation.update_persistence,
        "visible_source_snapshot",
        lambda _root: nullcontext(tmp_path),
    )

    assert validation.validate_root_closures(run=_run) == (
        DerivationValidationFailure(
            source="root-closures",
            installable="path:.#lib.rootClosureManifest",
            message="candidate manifest failed",
        ),
    )


def test_validate_root_closures_reports_manifest_process_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Convert failure to start the candidate manifest evaluation into a result."""

    def _run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("nix unavailable")

    monkeypatch.setattr(
        validation.update_persistence,
        "visible_source_snapshot",
        lambda _root: nullcontext(tmp_path),
    )

    with pytest.raises(validation.ValidationIncompleteError, match="nix unavailable"):
        validation.validate_root_closures(run=_run)


def test_validate_derivations_applies_timeout_to_each_request() -> None:
    """Give every derivation its own subprocess timeout regardless of ordering."""

    class _FourSystemUpdater:
        derivation_validations = (
            DerivationValidation(
                installable=".#pkgs.{system}.demo.drvPath",
                systems=("system-a", "system-b", "system-c", "system-d"),
            ),
        )

    timeouts: list[object] = []

    def _run(
        args: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        timeouts.append(kwargs["timeout"])
        return subprocess.CompletedProcess(args, 0, stdout="/nix/store/demo.drv")

    failures = validation.validate_derivations(
        ["demo"],
        updaters={"demo": _FourSystemUpdater},
        timeout=5,
        all_declared_systems=True,
        run=_run,
    )

    assert timeouts == [5, 5, 5, 5]
    assert failures == ()


@pytest.mark.parametrize(
    "message",
    [
        "HTTP/2 stream was reset while querying the substituter",
        "Failure when receiving data from the peer",
        "Operation too slow. Less than 1 bytes/sec transferred the last 5 seconds",
    ],
)
def test_validate_derivations_retries_transient_failure_then_succeeds(
    message: str,
) -> None:
    """Retry a classified Nix transport failure without reporting a false failure."""
    attempts: list[list[str]] = []
    sleep_delays: list[float] = []

    def _run(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        attempts.append(args)
        if len(attempts) == 1:
            return subprocess.CompletedProcess(
                args,
                1,
                stdout="",
                stderr=message,
            )
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="/nix/store/demo.drv",
            stderr="",
        )

    assert (
        validation.validate_derivations(
            ["portable"],
            updaters={"portable": _PortableUpdater},
            run=_run,
            sleep=sleep_delays.append,
        )
        == ()
    )
    assert len(attempts) == 2
    assert sleep_delays == [1.0]


@pytest.mark.parametrize(
    "message",
    [
        "error: attribute 'portable' missing",
        "error: package test timed out waiting for a child process",
        "error: Fail extracting tarball from a malformed fixture",
    ],
)
def test_validate_derivations_does_not_retry_deterministic_failure(
    message: str,
) -> None:
    """Package, evaluation, and generic timeout failures remain one attempt."""
    attempts: list[list[str]] = []
    sleep_delays: list[float] = []

    def _run(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        attempts.append(args)
        return subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr=message,
        )

    failures = validation.validate_derivations(
        ["portable"],
        updaters={"portable": _PortableUpdater},
        run=_run,
        sleep=sleep_delays.append,
    )

    assert len(failures) == 1
    assert len(attempts) == 1
    assert sleep_delays == []


def test_validate_derivations_caps_transient_retries() -> None:
    """Bound substituter retries and preserve the timeout for every attempt."""
    timeouts: list[object] = []
    sleep_delays: list[float] = []

    def _run(
        args: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        timeouts.append(kwargs["timeout"])
        return subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr="Temporary failure in name resolution",
        )

    failures = validation.validate_derivations(
        ["portable"],
        updaters={"portable": _PortableUpdater},
        timeout=17,
        run=_run,
        sleep=sleep_delays.append,
    )

    assert len(failures) == 1
    assert failures[0].message == "Temporary failure in name resolution"
    assert timeouts == [17, 17, 17]
    assert sleep_delays == [1.0, 2.0]


@pytest.mark.parametrize(
    ("stdout", "stderr", "expected"),
    [
        ("", "stderr details", "stderr details"),
        ("stdout details", "", "stdout details"),
        ("", "", "nix eval failed"),
    ],
)
def test_validate_derivations_reports_failed_command_output(
    stdout: str,
    stderr: str,
    expected: str,
) -> None:
    """Prefer stderr, then stdout, then a stable fallback for failed evals."""

    def _run(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout=stdout, stderr=stderr)

    assert validation.validate_derivations(
        ["portable"],
        updaters={"portable": _PortableUpdater},
        run=_run,
    ) == (
        DerivationValidationFailure(
            source="portable",
            installable=".#portable.drvPath",
            message=expected,
        ),
    )


def test_validate_derivations_labels_an_empty_build_failure() -> None:
    """Use the validation mode when a failed command produces no diagnostics."""

    def _run(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="")

    assert validation.validate_derivations(
        ["portable"],
        updaters={"portable": _BuildPortableUpdater},
        run=_run,
    ) == (
        DerivationValidationFailure(
            source="portable",
            installable=".#portable",
            message="nix build failed",
        ),
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (OSError("nix unavailable"), "nix unavailable"),
        (
            subprocess.TimeoutExpired(["nix", "eval"], 5),
            "timed out after 5 seconds",
        ),
    ],
)
def test_validate_derivations_reports_process_errors(
    error: OSError | subprocess.TimeoutExpired,
    expected: str,
) -> None:
    """Incomplete execution cannot attribute a failure to a package."""

    def _run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise error

    with pytest.raises(validation.ValidationIncompleteError, match=expected) as raised:
        validation.validate_derivations(
            ["portable"],
            updaters={"portable": _PortableUpdater},
            run=_run,
        )
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_snapshot_evaluations_share_one_flake_output(tmp_path: Path) -> None:
    """Force every selected platform's derivation with one pure, locked eval."""
    calls: list[tuple[list[str], dict[str, object]]] = []

    def _run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout="paths", stderr="")

    assert (
        validation.validate_derivations(
            ["demo", "second"],
            updaters={"demo": _DarwinAndLinuxUpdater, "second": _DarwinAndLinuxUpdater},
            all_declared_systems=True,
            flake_root=tmp_path,
            timeout=42,
            run=_run,
        )
        == ()
    )
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:-1] == [
        "nix",
        "eval",
        "--no-update-lock-file",
        "--option",
        "allow-import-from-derivation",
        "false",
        "--raw",
        f"path:{tmp_path}#pkgs",
        "--apply",
    ]
    assert_nix_ast_equal(
        args[-1],
        """
        root: builtins.concatStringsSep "" [
            root.aarch64-darwin.demo.drvPath
            root.x86_64-linux.demo.drvPath
            root.aarch64-darwin.second.drvPath
            root.x86_64-linux.second.drvPath
        ]
    """,
    )
    assert kwargs == {
        "cwd": tmp_path,
        "text": True,
        "capture_output": True,
        "check": False,
        "timeout": 42,
    }


def test_failed_batch_rechecks_each_target_with_original_retry_policy(
    tmp_path: Path,
) -> None:
    """A batch failure neither blames healthy peers nor loses per-target retries."""
    requests = [
        DerivationValidationRequest("healthy", ".#pkgs.system.healthy.drvPath"),
        DerivationValidationRequest("broken", "path:.#pkgs.system.broken.drvPath"),
        DerivationValidationRequest("network", ".#pkgs.system.network.drvPath"),
    ]
    attempts: list[list[str]] = []
    sleeps: list[float] = []
    network_attempts = 0

    def _run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal network_attempts
        attempts.append(args)
        assert kwargs["timeout"] == 7
        if "--apply" in args:
            return subprocess.CompletedProcess(
                args, 1, stdout="", stderr="batch failed"
            )
        if args[-1].endswith(".broken.drvPath"):
            return subprocess.CompletedProcess(
                args, 1, stdout="", stderr="broken member"
            )
        if args[-1].endswith(".network.drvPath"):
            network_attempts += 1
            if network_attempts == 1:
                return subprocess.CompletedProcess(
                    args,
                    1,
                    stdout="",
                    stderr="error: unable to download 'https://cache.nixos.org/example': HTTP error 503",
                )
        return subprocess.CompletedProcess(args, 0, stdout="path", stderr="")

    assert validation.validate_derivation_requests(
        requests,
        flake_root=tmp_path,
        timeout=7,
        run=_run,
        sleep=sleeps.append,
    ) == (
        DerivationValidationFailure("broken", requests[1].installable, "broken member"),
    )
    assert len(attempts) == 5
    assert network_attempts == 2
    assert sleeps == [1]


def test_snapshot_validation_preserves_mixed_modes_and_failure_order(
    tmp_path: Path,
) -> None:
    """Unsupported installables remain individual and failure ownership stays ordered."""
    requests = [
        DerivationValidationRequest("a", ".#pkgs.system.a.drvPath"),
        DerivationValidationRequest("external", "github:owner/repo#thing"),
        DerivationValidationRequest("b", ".#pkgs.system.b.drvPath"),
        DerivationValidationRequest("quoted", '.#pkgs."quoted.name".drvPath'),
        DerivationValidationRequest("build", ".#pkgs.system.a", mode="build"),
        DerivationValidationRequest("other", ".#checks.system.a.drvPath"),
        DerivationValidationRequest("raw-path", ".#pkgs.system.a.outPath"),
        DerivationValidationRequest("raw-value", ".#pkgs.system.b.version"),
    ]
    calls: list[list[str]] = []

    def _run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="invalid target")

    failures = validation.validate_derivation_requests(
        requests, flake_root=tmp_path, run=_run
    )
    assert [failure.source for failure in failures] == [
        request.source for request in requests
    ]
    assert len(calls) == 9
    assert len([args for args in calls if "--apply" in args]) == 1
    external = next(args for args in calls if args[-1] == "github:owner/repo#thing")
    assert "--no-update-lock-file" not in external
    assert [args[1] for args in calls].count("build") == 1


@pytest.mark.parametrize("mode", ["eval", "build"])
@pytest.mark.parametrize("size", [1, 3, 24])
@pytest.mark.parametrize("failure", ["timeout", "launch", "signal"])
def test_incomplete_validation_never_subdivides_or_blames_targets(
    tmp_path, mode, size, failure
) -> None:
    """A large timeout and a singleton launch error both abort without retry."""
    requests = [
        DerivationValidationRequest(
            str(i),
            f".#pkgs.system.target{i}" + (".drvPath" if mode == "eval" else ""),
            mode=mode,
        )
        for i in range(size)
    ]
    calls = []

    def run(args, **_kwargs):
        calls.append(args)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(
                args, 3, output=b"build output", stderr="diagnostic"
            )
        if failure == "signal":
            return subprocess.CompletedProcess(
                args, -9, stdout="", stderr="error: unable to download: HTTP error 503"
            )
        raise OSError("Nix unavailable")

    with pytest.raises(validation.ValidationIncompleteError) as raised:
        validation.validate_derivation_requests(
            requests,
            flake_root=tmp_path,
            timeout=3,
            run=run,
            sleep=lambda _: pytest.fail("incomplete validation must not retry"),
        )
    assert len(calls) == 1
    if failure == "timeout":
        assert "build output" in str(raised.value)
        assert "diagnostic" in str(raised.value)


@pytest.mark.parametrize("failed_index", [0, 25, None])
def test_sparse_validation_failure_avoids_full_individual_fallback(
    tmp_path: Path, failed_index: int | None
) -> None:
    """Subdivision preserves attribution while skipping healthy large subsets."""
    requests = [
        DerivationValidationRequest(
            str(index), f".#checks.system.target{index}", mode="build"
        )
        for index in range(26)
    ]
    calls: list[tuple[str, ...]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        targets = tuple(arg.split("#", 1)[1] for arg in args if "#" in arg)
        calls.append(targets)
        broken = f"checks.system.target{failed_index}" in targets
        # A transient aggregate failure may disappear when both halves pass.
        failed = broken or len(calls) == 1
        return subprocess.CompletedProcess(
            args, int(failed), stdout="", stderr="broken member" if failed else ""
        )

    failures = validation.validate_derivation_requests(
        requests, flake_root=tmp_path, run=run
    )
    assert [failure.source for failure in failures] == (
        [] if failed_index is None else [str(failed_index)]
    )
    assert len(calls) <= 11
    assert set().union(*map(set, calls[1:])) == {
        f"checks.system.target{i}" for i in range(26)
    }


def test_systemic_validation_failure_bounds_subdivision(tmp_path: Path) -> None:
    """Widespread failure stops subdivision and retains every target diagnostic."""
    requests = [
        DerivationValidationRequest(
            str(index), f".#checks.system.target{index}", mode="build"
        )
        for index in range(26)
    ]
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="invalid dependency"
        )

    failures = validation.validate_derivation_requests(
        requests, flake_root=tmp_path, run=run
    )
    assert [failure.source for failure in failures] == [
        str(index) for index in range(26)
    ]
    assert len(calls) == 29


def test_concurrent_eval_groups_serialize_progress_and_attribute_failures(
    tmp_path: Path,
) -> None:
    """Threaded group execution overlaps groups without racing the progress channel.

    The CLI's default budget runs eval groups concurrently; no test exercised
    that path, so this pins failure attribution, per-command events, and the
    caller-progress serialization the group lock promises. The barrier makes a
    serialized execution fail by construction instead of relying on timing.
    """
    requests = [
        DerivationValidationRequest("a", ".#pkgs.system.a.outPath"),
        DerivationValidationRequest("b", ".#pkgs.system.b.version"),
    ]
    barrier = threading.Barrier(2, timeout=10)
    progress_lock = threading.Lock()
    events: list[validation.ValidationProgressEvent] = []
    concurrent_entries = 0

    def progress(event: validation.ValidationProgressEvent) -> None:
        nonlocal concurrent_entries
        with progress_lock:
            concurrent_entries += 1
            assert concurrent_entries == 1
        events.append(event)
        time.sleep(0.005)
        with progress_lock:
            concurrent_entries -= 1

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        barrier.wait()
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr=f"boom {args[-1]}"
        )

    failures = validation.validate_derivation_requests(
        requests,
        flake_root=tmp_path,
        run=run,
        progress=progress,
        max_eval_workers=2,
    )
    assert [failure.source for failure in failures] == ["a", "b"]
    for failure, request in zip(failures, requests, strict=True):
        assert failure.message.startswith("boom path:")
        assert failure.message.endswith(f"#{request.installable.removeprefix('.#')}")
    started = [
        event.command
        for event in events
        if isinstance(event, validation.ValidationCommandStarted)
    ]
    finished = sorted(
        (event.command, event.succeeded)
        for event in events
        if isinstance(event, validation.ValidationCommandFinished)
    )
    assert len(started) == 2
    assert len(set(started)) == 2
    assert finished == [(started[0], False), (started[1], False)]


def test_native_builder_still_evaluates_every_declared_platform(monkeypatch) -> None:
    """CI splits native builds without erasing foreign evaluation contracts."""

    class MultiPlatform:
        derivation_validations = (
            DerivationValidation(
                installable=".#pkgs.{system}.{name}",
                mode="build",
                systems=("aarch64-darwin", "x86_64-linux"),
            ),
            DerivationValidation(
                installable=".#pkgs.{system}.{name}.drvPath",
                systems=("aarch64-darwin", "x86_64-darwin", "x86_64-linux"),
            ),
        )

    monkeypatch.setattr(
        validation, "get_current_nix_platform", lambda: "aarch64-darwin"
    )
    requests = validation.resolve_derivation_validations(
        ("example",),
        updaters={"example": MultiPlatform},
        all_declared_systems=True,
        native_builds_only=True,
    )
    assert [(request.mode, request.installable) for request in requests] == [
        ("build", ".#pkgs.aarch64-darwin.example"),
        ("eval", ".#pkgs.aarch64-darwin.example.drvPath"),
        ("eval", ".#pkgs.x86_64-darwin.example.drvPath"),
        ("eval", ".#pkgs.x86_64-linux.example.drvPath"),
    ]


@pytest.mark.parametrize("mode", ["eval", "build"])
def test_parallel_groups_continue_after_completed_target_failures(
    tmp_path, mode
) -> None:
    """Completion-order scheduling replenishes work and preserves request ordering."""
    requests = [
        DerivationValidationRequest(str(i), f"github:owner/repo#target{i}", mode=mode)
        for i in range(5)
    ]
    calls = []
    lock = threading.Lock()

    def run(args, **_kwargs):
        with lock:
            calls.append(args[-1])
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="invalid target")

    failures = validation.validate_derivation_requests(
        requests,
        flake_root=tmp_path,
        run=run,
        max_eval_workers=2,
        max_build_workers=2,
    )
    assert [failure.source for failure in failures] == [str(i) for i in range(5)]
    assert sorted(calls) == [request.installable for request in requests]
