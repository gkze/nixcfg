"""Behavioral checks for Traycer's generated Bun dependency graph."""

import json
import re
from dataclasses import fields
from functools import cache
from hashlib import sha256
from pathlib import Path
from types import ModuleType

import pytest
from nix_manipulator.expressions.expression import NixExpression
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_file_expr
from lib.tests._updater_helpers import load_repo_module
from lib.update.paths import REPO_ROOT

_PACKAGE_DIR = Path(REPO_ROOT) / "packages/traycer"
_ARCHIVE_ENTRY = """
  "dependency@1.2.3" = fetchurl {
    url = "https://registry.npmjs.org/dependency/-/dependency-1.2.3.tgz";
    hash = "sha512-dependency";
  };
"""
_WORKSPACE_ENTRY = """
  "@traycer-clients/desktop" = copyPathToStore ./clients/desktop;
"""
_EMPTY_GENERATED_GRAPH = "{ copyPathToStore, fetchurl, ... }: { }\n"


@cache
def _validator() -> ModuleType:
    return load_repo_module(
        "packages/traycer/validate_bun_graph.py",
        "traycer_validate_bun_graph_test",
    )


def _checked_in_lock() -> dict[str, object]:
    text = (_PACKAGE_DIR / "bun.lock").read_text(encoding="utf-8")
    return json.loads(re.sub(r",(?=\s*[}\]])", "", text))


def _write_fixture(tmp_path: Path, *, generated_entries: str) -> tuple[Path, Path]:
    return _write_raw_fixture(
        tmp_path,
        {
            "lockfileVersion": 1,
            "configVersion": 1,
            "workspaces": {
                "": {"name": "traycer"},
                "clients/desktop": {"name": "@traycer-clients/desktop"},
            },
            "packages": {
                "dependency": [
                    "dependency@1.2.3",
                    "",
                    {},
                    "sha512-dependency",
                ],
                "@traycer-clients/desktop": [
                    "@traycer-clients/desktop@workspace:clients/desktop"
                ],
            },
        },
        """
{ copyPathToStore, fetchurl, ... }:
{
"""
        + generated_entries
        + """
}
""",
    )


def _write_raw_fixture(
    tmp_path: Path,
    lock: object,
    nix_source: str,
) -> tuple[Path, Path]:
    lock_path = tmp_path / "bun.lock"
    nix_path = tmp_path / "bun.nix"
    normalized_lock = (
        {"lockfileVersion": 1, "configVersion": 1, **lock}
        if isinstance(lock, dict)
        else lock
    )
    lock_path.write_text(json.dumps(normalized_lock), encoding="utf-8")
    nix_path.write_text(nix_source, encoding="utf-8")
    return lock_path, nix_path


def _walk_nix(value: object, seen: set[int] | None = None):
    if seen is None:
        seen = set()
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, NixExpression):
        yield value
        for field in fields(value):
            if field.name in {"before", "after", "scope_state", "source_path"}:
                continue
            yield from _walk_nix(getattr(value, field.name), seen)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_nix(item, seen)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_nix(item, seen)


def test_checked_in_graph_matches_every_unique_lock_resolution() -> None:
    """Validate the real graph and derive its counts from the lock itself."""
    lock = _checked_in_lock()
    packages = lock["packages"]
    workspaces = lock["workspaces"]
    assert isinstance(packages, dict)
    assert isinstance(workspaces, dict)

    package_ids = {value[0] for value in packages.values()}
    workspace_count = len(workspaces) - 1
    summary = _validator().validate_bun_graph(
        _PACKAGE_DIR / "bun.lock",
        _PACKAGE_DIR / "bun.nix",
    )

    assert summary.raw_package_count == len(packages)
    assert summary.unique_package_count == len(package_ids)
    assert summary.workspace_count == workspace_count
    assert summary.archive_count == len(package_ids) - workspace_count
    assert summary.unique_package_count == (
        summary.archive_count + summary.workspace_count
    )
    assert summary.raw_package_count > summary.unique_package_count


def test_generated_graph_rejects_a_missing_lock_resolution(tmp_path: Path) -> None:
    lock_path, nix_path = _write_fixture(
        tmp_path,
        generated_entries=_WORKSPACE_ENTRY,
    )

    with pytest.raises(
        _validator().BunGraphValidationError,
        match="missing=.*dependency@1.2.3",
    ):
        _validator().validate_bun_graph(lock_path, nix_path)


@pytest.mark.parametrize(
    ("generated_entries", "message"),
    [
        (
            _ARCHIVE_ENTRY.replace("sha512-dependency", "sha512-altered")
            + _WORKSPACE_ENTRY,
            "entries differ:.*dependency@1.2.3",
        ),
        (
            _ARCHIVE_ENTRY
            + _WORKSPACE_ENTRY
            + '  "extra@9.9.9" = fetchurl {\n'
            + '    url = "https://registry.npmjs.org/extra/-/extra-9.9.9.tgz";\n'
            + '    hash = "sha512-extra";\n'
            + "  };\n",
            "extra=.*extra@9.9.9",
        ),
        (
            _ARCHIVE_ENTRY
            + _WORKSPACE_ENTRY.replace("./clients/desktop", "./clients/altered"),
            "entries differ:.*@traycer-clients/desktop",
        ),
    ],
    ids=["altered-archive", "extra-entry", "altered-workspace"],
)
def test_generated_graph_rejects_semantic_drift(
    tmp_path: Path,
    generated_entries: str,
    message: str,
) -> None:
    lock_path, nix_path = _write_fixture(
        tmp_path,
        generated_entries=generated_entries,
    )

    with pytest.raises(_validator().BunGraphValidationError, match=message):
        _validator().validate_bun_graph(lock_path, nix_path)


def test_validator_preserves_explicit_registry_identity(tmp_path: Path) -> None:
    """An explicit registry URL and filename must survive lock generation exactly."""
    lock = {
        "packages": {
            "@private/dependency": [
                "@private/dependency@1.2.3",
                "https://npm.example.test/@private/dependency/-/dependency-1.2.3.tgz",
                {},
                "sha512-private",
            ],
        },
        "workspaces": {},
    }
    correct_entry = """
  "@private/dependency@1.2.3" = fetchurl {
    url = "https://npm.example.test/@private/dependency/-/dependency-1.2.3.tgz";
    hash = "sha512-private";
    name = "dependency-1.2.3.tgz";
  };
"""
    lock_path, nix_path = _write_raw_fixture(
        tmp_path,
        lock,
        "{ fetchurl, ... }: {" + correct_entry + "}\n",
    )

    summary = _validator().validate_bun_graph(lock_path, nix_path)
    assert summary.archive_count == 1

    nix_path.write_text(
        "{ fetchurl, ... }: {"
        + correct_entry.replace(
            "https://npm.example.test/@private/dependency/-/dependency-1.2.3.tgz",
            "https://registry.npmjs.org/@private/dependency/-/dependency-1.2.3.tgz",
        )
        + "}\n",
        encoding="utf-8",
    )
    with pytest.raises(
        _validator().BunGraphValidationError,
        match="entries differ:.*@private/dependency@1.2.3",
    ):
        _validator().validate_bun_graph(lock_path, nix_path)


def test_validator_accepts_jsonc_and_rejects_duplicate_keys(tmp_path: Path) -> None:
    """Match Bun's commented lock syntax without silently last-winning keys."""
    lock_path = tmp_path / "bun.lock"
    nix_path = tmp_path / "bun.nix"
    lock_path.write_text(
        """
        {
          // Bun lock schema is part of the generated-graph contract.
          "lockfileVersion": 1,
          "configVersion": 1,
          "workspaces": {},
          "packages": {
            /* This trailing comma is valid JSONC. */
            "dependency": ["dependency@1.2.3", "", {}, "sha512-dependency"],
          },
        }
        """,
        encoding="utf-8",
    )
    nix_path.write_text(
        "{ fetchurl, ... }: {" + _ARCHIVE_ENTRY + "}\n",
        encoding="utf-8",
    )
    assert _validator().validate_bun_graph(lock_path, nix_path).archive_count == 1

    lock_path.write_text(
        '{"lockfileVersion":1,"configVersion":1,"workspaces":{},'
        '"packages":{},"packages":{"dependency":['
        '"dependency@1.2.3","",{},"sha512-dependency"]}}',
        encoding="utf-8",
    )
    with pytest.raises(
        _validator().BunGraphValidationError,
        match="duplicate JSON object key: 'packages'",
    ):
        _validator().validate_bun_graph(lock_path, nix_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [("lockfileVersion", 2), ("configVersion", 2)],
)
def test_validator_rejects_lock_schema_drift(
    tmp_path: Path,
    field: str,
    value: int,
) -> None:
    """Only the exact Bun lock schema audited for Traycer may be promoted."""
    lock = {
        "lockfileVersion": 1,
        "configVersion": 1,
        "workspaces": {},
        "packages": {},
        field: value,
    }
    lock_path, nix_path = _write_raw_fixture(tmp_path, lock, _EMPTY_GENERATED_GRAPH)
    with pytest.raises(
        _validator().BunGraphValidationError,
        match=rf"{field} must be 1",
    ):
        _validator().validate_bun_graph(lock_path, nix_path)


@pytest.mark.parametrize(
    ("lock", "message"),
    [
        ([], "top-level object"),
        ({"packages": [], "workspaces": {}}, "field 'packages'"),
        (
            {"packages": {}, "workspaces": {"clients/desktop": {}}},
            "must declare a name",
        ),
        (
            {"packages": {}, "workspaces": {"clients/desktop": 1}},
            "must declare a name",
        ),
        (
            {
                "packages": {},
                "workspaces": {
                    "one": {"name": "duplicate"},
                    "two": {"name": "duplicate"},
                },
            },
            "duplicate workspace name",
        ),
        (
            {"packages": {"bad": []}, "workspaces": {}},
            "invalid lock entry",
        ),
        (
            {"packages": {"bad": [42, "", {}, "sha512-value"]}, "workspaces": {}},
            "has no package identity",
        ),
        (
            {
                "packages": {"wrong-key": ["workspace-name@workspace:clients/desktop"]},
                "workspaces": {"clients/desktop": {"name": "workspace-name"}},
            },
            "invalid workspace lock entry",
        ),
        (
            {"packages": {"bad": ["bad@1.0.0"]}, "workspaces": {}},
            "unsupported archive lock entry",
        ),
        (
            {
                "packages": {"bad": ["unversioned", "", {}, "sha512-value"]},
                "workspaces": {},
            },
            "invalid registry package identity",
        ),
        (
            {
                "packages": {"bad": ["bad@1.0.0", "", {}, "sha256-value"]},
                "workspaces": {},
            },
            "invalid integrity",
        ),
        (
            {
                "packages": {"bad": ["bad@1.0.0", 42, {}, "sha512-value"]},
                "workspaces": {},
            },
            "invalid tarball URL",
        ),
        (
            {
                "packages": {"bad": ["bad@1.0.0", "", [], "sha512-value"]},
                "workspaces": {},
            },
            "invalid registry metadata",
        ),
        (
            {
                "packages": {
                    "first": ["same@1.0.0", "", {}, "sha512-first"],
                    "second": ["same@1.0.0", "", {}, "sha512-second"],
                },
                "workspaces": {},
            },
            "conflicting archive records",
        ),
        (
            {
                "packages": {},
                "workspaces": {"clients/desktop": {"name": "workspace-name"}},
            },
            "workspace records differ",
        ),
    ],
    ids=[
        "top-level",
        "object-field",
        "workspace-name",
        "workspace-metadata",
        "duplicate-workspace",
        "lock-entry",
        "package-identity-type",
        "workspace-record",
        "archive-shape",
        "registry-identity",
        "integrity",
        "tarball-url",
        "registry-metadata",
        "conflicting-archive",
        "workspace-correspondence",
    ],
)
def test_validator_rejects_malformed_lock_graphs(
    tmp_path: Path,
    lock: object,
    message: str,
) -> None:
    lock_path, nix_path = _write_raw_fixture(
        tmp_path,
        lock,
        _EMPTY_GENERATED_GRAPH,
    )

    with pytest.raises(_validator().BunGraphValidationError, match=message):
        _validator().validate_bun_graph(lock_path, nix_path)


@pytest.mark.parametrize(
    ("nix_source", "message"),
    [
        ("", "not a single valid Nix expression"),
        ("{ }", "must generate an attribute set function"),
        ("{ ... }: 1", "must generate an attribute set function"),
        (
            "{ copyPathToStore, fetchurl, ... }: { inherit fetchurl; }",
            "non-binding graph entry",
        ),
        (
            '{ fetchurl, ... }: { dependency = fetchurl { url = "x"; hash = "y"; }; }',
            "not a quoted string",
        ),
        (
            '{ fetchurl, ... }: { null = fetchurl { url = "x"; hash = "y"; }; }',
            "package name is not a string",
        ),
        (
            '{ fetchurl, ... }: { "dependency@1.2.3" = ./dependency; }',
            "must be a function call",
        ),
        (
            "{ fetchurl, ... }: { "
            '"dependency@1.2.3" = fetchurl { url = "x"; hash = "y"; }; '
            '"dependency@1.2.3" = fetchurl { url = "x"; hash = "y"; }; }',
            "duplicate generated package",
        ),
        (
            '{ fetchurl, ... }: { "dependency@1.2.3" = fetchurl { '
            'url = "x"; hash = "y"; extra = true; }; }',
            "unexpected fields",
        ),
        (
            '{ fetchurl, ... }: { "dependency@1.2.3" = fetchurl { '
            'url = 1; hash = "sha512-dependency"; }; }',
            "must have one string 'url'",
        ),
        (
            '{ copyPathToStore, ... }: { "@traycer-clients/desktop" = '
            "copyPathToStore /clients/desktop; }",
            "must use a relative path",
        ),
        (
            '{ copyPathToStore, ... }: { "@traycer-clients/desktop" = '
            "copyPathToStore ./../desktop; }",
            "has an unsafe path",
        ),
        (
            '{ fetchgit, ... }: { "dependency@1.2.3" = fetchgit { url = "x"; }; }',
            "unsupported constructor 'fetchgit'",
        ),
    ],
    ids=[
        "empty",
        "not-function",
        "not-attrset",
        "inherit",
        "unquoted-name",
        "non-string-name",
        "not-call",
        "duplicate",
        "archive-fields",
        "archive-value",
        "absolute-workspace",
        "unsafe-workspace",
        "constructor",
    ],
)
def test_validator_rejects_malformed_generated_graphs(
    tmp_path: Path,
    nix_source: str,
    message: str,
) -> None:
    lock_path, nix_path = _write_fixture(tmp_path, generated_entries="")
    nix_path.write_text(nix_source, encoding="utf-8")

    with pytest.raises(_validator().BunGraphValidationError, match=message):
        _validator().validate_bun_graph(lock_path, nix_path)


def test_validator_rejects_unreadable_inputs(tmp_path: Path) -> None:
    lock_path, nix_path = _write_fixture(tmp_path, generated_entries="")
    missing_lock = tmp_path / "missing.lock"
    missing_nix = tmp_path / "missing.nix"

    with pytest.raises(_validator().BunGraphValidationError, match="unable to parse"):
        _validator().validate_bun_graph(missing_lock, nix_path)
    lock_path.write_text("not JSON", encoding="utf-8")
    with pytest.raises(_validator().BunGraphValidationError, match="unable to parse"):
        _validator().validate_bun_graph(lock_path, nix_path)

    lock_path.write_text(
        '{"lockfileVersion":1,"configVersion":1,"workspaces":{},'
        '"packages":{},"note":"escaped \\" slash https://example.test"}',
        encoding="utf-8",
    )
    assert _validator().validate_bun_graph(lock_path, nix_path).archive_count == 0

    lock_path.write_text(
        '{"lockfileVersion":1,"configVersion":1,"workspaces":{},'
        '"packages":{},"note":/invalid}',
        encoding="utf-8",
    )
    with pytest.raises(_validator().BunGraphValidationError, match="unable to parse"):
        _validator().validate_bun_graph(lock_path, nix_path)

    lock_path.write_text("{/* unterminated", encoding="utf-8")
    with pytest.raises(
        _validator().BunGraphValidationError,
        match="unterminated JSONC block comment",
    ):
        _validator().validate_bun_graph(lock_path, nix_path)

    valid_lock, _ = _write_fixture(tmp_path, generated_entries="")
    with pytest.raises(_validator().BunGraphValidationError, match="unable to read"):
        _validator().validate_bun_graph(valid_lock, missing_nix)


def test_validator_cli_reports_the_proven_counts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lock_path, nix_path = _write_fixture(
        tmp_path,
        generated_entries=_ARCHIVE_ENTRY + _WORKSPACE_ENTRY,
    )

    assert _validator().main([str(lock_path), str(nix_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "archive_count": 1,
        "raw_package_count": 2,
        "unique_package_count": 2,
        "workspace_count": 1,
    }


def test_bun_cache_uses_build_time_workspaces_and_hash_prefix_shards() -> None:
    """Keep source realization out of evaluation and cap the shard namespace."""
    module = expect_instance(
        nix_file_expr("packages/traycer/bun-cache.nix"),
        FunctionDefinition,
    )
    body = expect_instance(module.output, FunctionCall)
    callees = {
        node.name.rebuild()
        for node in _walk_nix(list(body.scope))
        if isinstance(node, FunctionCall)
    }

    assert callees.isdisjoint({
        "builtins.filterSource",
        "builtins.path",
        "lib.filterAttrs",
        "lib.isStorePath",
        "lib.cleanSourceWith",
        "pkgs.copyPathToStore",
    })
    assert_nix_ast_equal(
        expect_binding(body.scope, "bunPackages").value,
        """
builtins.addErrorContext invalidBunNixError (
  (import ./bun.nix) {
    copyPathToStore = copyBunWorkspacePathToStore;
    inherit (pkgs) fetchFromGitHub fetchgit fetchurl;
  }
)
""",
    )
    assert_nix_ast_equal(
        expect_binding(body.scope, "bunPackageShards").value,
        """
lib.groupBy (
  entry: builtins.substring 0 2 (builtins.hashString "sha256" entry.name)
) bunPackageEntries
""",
    )
    assert_nix_ast_equal(
        expect_binding(body.scope, "workspacePaths").value,
        """
{
  "clients/desktop" = "desktop";
  "clients/gui-app" = "gui-app";
  "clients/shared" = "shared";
  "clients/traycer-cli" = "traycer-cli";
  protocol = "protocol";
}
""",
    )

    workspace_package = expect_instance(
        expect_binding(body.scope, "workspacePackage").value,
        FunctionDefinition,
    )
    workspace_derivation = expect_instance(workspace_package.output, FunctionCall)
    assert_nix_ast_equal(workspace_derivation.name, "stdenvNoCC.mkDerivation")
    workspace_arguments = expect_instance(workspace_derivation.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(workspace_arguments.values, "src").value,
        "traycerSource",
    )
    assert_nix_ast_equal(
        expect_binding(workspace_arguments.values, "dontUnpack").value,
        "true",
    )
    assert_nix_ast_equal(
        expect_binding(workspace_arguments.values, "dontFixup").value,
        "true",
    )

    cache_arguments = expect_instance(body.argument, AttributeSet)
    passthru = expect_instance(
        expect_binding(cache_arguments.values, "passthru").value,
        AttributeSet,
    )
    metadata = expect_instance(
        expect_binding(passthru.values, "nixcfg").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(metadata.values, "packageCount").value,
        "builtins.length bunPackageEntries",
    )
    assert_nix_ast_equal(
        expect_binding(metadata.values, "shardCount").value,
        "builtins.length shardSizes",
    )
    assert_nix_ast_equal(
        expect_binding(metadata.values, "maxShardSize").value,
        "builtins.foldl' lib.max 0 shardSizes",
    )
    assert_nix_ast_equal(
        expect_binding(metadata.values, "patchShebangs").value,
        "false",
    )


def test_bun_cache_join_reuses_substitutable_shard_outputs() -> None:
    """Expose the exact cache roots once and keep the aggregate substitutable."""
    module = expect_instance(
        nix_file_expr("packages/traycer/bun-cache.nix"),
        FunctionDefinition,
    )
    body = expect_instance(module.output, FunctionCall)
    assert_nix_ast_equal(
        expect_binding(body.scope, "shardOutputs").value,
        "builtins.attrValues (builtins.mapAttrs buildBunShard bunPackageShards)",
    )

    cache_arguments = expect_instance(body.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(cache_arguments.values, "paths").value,
        "shardOutputs",
    )
    assert_nix_ast_equal(
        expect_binding(
            cache_arguments.values,
            "allowSubstitutes",
        ).value,
        "true",
    )
    assert_nix_ast_equal(
        expect_binding(
            cache_arguments.values,
            "preferLocalBuild",
        ).value,
        "false",
    )

    passthru = expect_instance(
        expect_binding(cache_arguments.values, "passthru").value,
        AttributeSet,
    )
    metadata = expect_instance(
        expect_binding(passthru.values, "nixcfg").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(metadata.values, "shardOutputs").value,
        "shardOutputs",
    )


def test_checked_in_graph_fits_the_two_digit_shard_namespace() -> None:
    """Derive the real distribution without invoking Nix evaluation."""
    generated = expect_instance(
        nix_file_expr("packages/traycer/bun.nix"),
        FunctionDefinition,
    )
    graph = expect_instance(generated.output, AttributeSet)
    package_names = [
        json.loads(binding.name) for binding in graph.values if hasattr(binding, "name")
    ]
    shard_sizes: dict[str, int] = {}
    for package_name in package_names:
        shard = sha256(package_name.encode()).hexdigest()[:2]
        shard_sizes[shard] = shard_sizes.get(shard, 0) + 1

    summary = _validator().validate_bun_graph(
        _PACKAGE_DIR / "bun.lock",
        _PACKAGE_DIR / "bun.nix",
    )
    assert sum(shard_sizes.values()) == summary.unique_package_count
    assert len(shard_sizes) <= 16**2
    assert len(shard_sizes) < summary.unique_package_count
    assert max(shard_sizes.values()) <= 32
