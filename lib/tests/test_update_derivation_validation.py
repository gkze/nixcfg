"""Behavioral tests for target-aware update derivation validation."""

import subprocess
from contextlib import nullcontext
from pathlib import Path

import pytest
from pydantic import ValidationError

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
def test_validate_root_closures_builds_flake_owned_aggregate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    timeout: float | None,
    expected_timeout: float,
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

    assert validation.validate_root_closures(timeout=timeout, run=_run) == ()
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
        (
            [
                "nix",
                "build",
                "--no-update-lock-file",
                "--no-link",
                f"path:{snapshot_root}#checks.aarch64-darwin.root-closures",
            ],
            expected_kwargs,
        ),
        (
            [
                "nix",
                "build",
                "--no-update-lock-file",
                "--no-link",
                f"path:{snapshot_root}#checks.x86_64-linux.root-closures",
            ],
            expected_kwargs,
        ),
    ]


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

    assert validation.validate_root_closures(run=_run) == (
        DerivationValidationFailure(
            source="root-closures",
            installable="path:.#lib.rootClosureManifest",
            message="nix unavailable",
        ),
    )


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
    """Convert process startup and timeout errors into target failures."""

    def _run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise error

    failures = validation.validate_derivations(
        ["portable"],
        updaters={"portable": _PortableUpdater},
        run=_run,
    )

    assert len(failures) == 1
    assert failures[0].source == "portable"
    assert expected in failures[0].message
