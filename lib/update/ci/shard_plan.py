"""One generated Darwin closure-shard plan from the root manifest.

The root inventory is ``lib.rootClosureManifest``. This module is the only
place that turns that inventory plus measured relative costs into a shard
list. GitHub Actions consumes the matrix JSON; it does not hand-maintain
job blocks per host.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from lib.update.derivation_validation import (
    RootClosureManifest,
    RootClosureManifestRoot,
)
from lib.update.derivation_validation import (
    composed_root_name as compose_kind_name,
)
from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_INSTALLED_COSTS_PATH = Path(__file__).with_name("shard_costs.json")
_REPO_COSTS_PATH = REPO_ROOT / "lib" / "update" / "ci" / "shard_costs.json"
_DARWIN_SYSTEM = "aarch64-darwin"
_DEFAULT_WEIGHT = 1
# Provisional width for this kick, not a 2-VMs-per-host cap. Nothing
# proves four shards land on two Apple hosts. After structural warmup
# cuts local builds to roughly the packages job, measure 4-wide versus
# 2-wide by bytes written and update-runtime and revisit this number.
# Public macos-15 cap is 5; packages occupies one slot while the shared
# missing drv set builds, then root shards use the remaining slots.
MAX_PARALLEL_DARWIN_ROOT_SHARDS = 2


class ShardCosts(BaseModel):
    """Measured relative costs for packing Darwin roots into shards."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(alias="schemaVersion")
    measured_from: str = Field(alias="measuredFrom")
    notes: str
    shared_packages: tuple[str, ...] = Field(alias="sharedPackages")
    root_weights: dict[str, int] = Field(alias="rootWeights")


class ClosureShardReceipt(BaseModel):
    """Evidence that one always-run Darwin root shard realized its roots."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tree: str
    system: str
    shard: str
    roots: tuple[str, ...]
    failures: tuple[str, ...] = ()
    store_paths: dict[str, str] = Field(default_factory=dict)


class ClosureShard(BaseModel):
    """One always-run Darwin closure shard."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    shard: str
    system: str
    roots: tuple[str, ...]

    @property
    def check_attrs(self) -> tuple[str, ...]:
        """Return flake check names for this shard's roots."""
        return tuple(f"root-closure-{name}" for name in self.roots)

    @property
    def installables(self) -> tuple[str, ...]:
        """Return ``path:.#checks.<system>.<attr>`` installables."""
        return tuple(f"path:.#checks.{self.system}.{attr}" for attr in self.check_attrs)


def composed_root_name(root: RootClosureManifestRoot) -> str:
    """Return the ``forSystem`` root name ``<kind>-<name>``."""
    return compose_kind_name(root.kind, root.name)


def load_shard_costs(path: Path | None = None) -> ShardCosts:
    """Load the committed relative-cost table."""
    table = path or default_costs_path()
    try:
        return ShardCosts.model_validate_json(table.read_bytes())
    except FileNotFoundError as error:
        msg = f"shard cost table is missing: {table}"
        raise FileNotFoundError(msg) from error


def costs_for_tree(tree: Path) -> ShardCosts:
    """Prefer the candidate tree's cost table; otherwise the packaged copy.

    Hosted plan-shards applies the candidate into a snapshot. That snapshot
    is the authority when it ships ``shard_costs.json``. Test workspaces and
    an unpackaged venv fall back to ``default_costs_path``.
    """
    candidate_table = tree / "lib" / "update" / "ci" / "shard_costs.json"
    if candidate_table.is_file():
        return load_shard_costs(candidate_table)
    return load_shard_costs()


def darwin_roots(manifest: RootClosureManifest) -> tuple[RootClosureManifestRoot, ...]:
    """Return configured aarch64-darwin roots in stable manifest order."""
    return tuple(root for root in manifest.roots if root.system == _DARWIN_SYSTEM)


def plan_darwin_closure_shards(
    manifest: RootClosureManifest,
    *,
    costs: ShardCosts | None = None,
    max_shards: int = MAX_PARALLEL_DARWIN_ROOT_SHARDS,
) -> tuple[ClosureShard, ...]:
    """Pack every Darwin root into always-run shards.

    One root per shard while that fits ``max_shards``. Extra roots bin-pack
    onto the current lightest shard by measured relative weight. An empty
    Darwin inventory is a hard error: requiredKinds include darwin and home.
    """
    if max_shards < 1:
        msg = "max_shards must be at least 1"
        raise ValueError(msg)
    roots = darwin_roots(manifest)
    if not roots:
        msg = (
            "Darwin root-closure plan is empty; required darwin/home roots are missing"
        )
        raise ValueError(msg)
    table = costs if costs is not None else load_shard_costs()
    names = tuple(composed_root_name(root) for root in roots)
    if len(names) <= max_shards:
        return tuple(
            ClosureShard(shard=name, system=_DARWIN_SYSTEM, roots=(name,))
            for name in names
        )
    packed: list[list[str]] = [[] for _ in range(max_shards)]
    packed_weight = [0] * max_shards
    ordered = sorted(
        names,
        key=lambda name: (-table.root_weights.get(name, _DEFAULT_WEIGHT), name),
    )
    for name in ordered:
        index = min(range(max_shards), key=lambda i: (packed_weight[i], i))
        packed[index].append(name)
        packed_weight[index] += table.root_weights.get(name, _DEFAULT_WEIGHT)
    shards: list[ClosureShard] = []
    for members in packed:
        members.sort()
        shards.append(
            ClosureShard(
                shard="+".join(members),
                system=_DARWIN_SYSTEM,
                roots=tuple(members),
            )
        )
    return tuple(shards)


def github_actions_matrix(shards: tuple[ClosureShard, ...]) -> dict[str, object]:
    """Return a ``strategy.matrix`` object for reusable native jobs."""
    if not shards:
        msg = "refusing an empty GitHub Actions closure matrix"
        raise ValueError(msg)
    return {
        "include": [
            {
                "shard": shard.shard,
                "roots": " ".join(shard.roots),
                "system": shard.system,
            }
            for shard in shards
        ]
    }


def plan_from_manifest_json(
    payload: Mapping[str, object] | bytes | str,
    *,
    costs: ShardCosts | None = None,
    max_shards: int = MAX_PARALLEL_DARWIN_ROOT_SHARDS,
) -> tuple[ClosureShard, ...]:
    """Parse a manifest JSON document and return the Darwin shard plan."""
    manifest = (
        RootClosureManifest.model_validate_json(payload)
        if isinstance(payload, (bytes, str))
        else RootClosureManifest.model_validate(payload)
    )
    return plan_darwin_closure_shards(manifest, costs=costs, max_shards=max_shards)


def write_github_actions_output(shards: tuple[ClosureShard, ...], output: Path) -> None:
    """Write ``matrix=<json>`` for ``GITHUB_OUTPUT``."""
    matrix = github_actions_matrix(shards)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as handle:
        handle.write(
            f"darwin_closure_shards={json.dumps(matrix, separators=(',', ':'))}\n"
        )


def default_costs_path() -> Path:
    """Return the packaged cost table, or the checkout copy if unpackaged.

    Hosted ``nixcfg`` runs from site-packages. The cost table must be in
    ``[tool.setuptools.package-data]`` or this falls back to the checkout
    ``REPO_ROOT``. Missing both is a hard error: there is no second authority.
    """
    if _INSTALLED_COSTS_PATH.is_file():
        return _INSTALLED_COSTS_PATH
    if _REPO_COSTS_PATH.is_file():
        return _REPO_COSTS_PATH
    msg = (
        "shard_costs.json is missing from the installed nixcfg package "
        f"({_INSTALLED_COSTS_PATH}) and from the checkout ({_REPO_COSTS_PATH})"
    )
    raise FileNotFoundError(msg)


def repo_root() -> Path:
    """Return the repository root the planner was imported from."""
    return REPO_ROOT


def eval_root_closure_manifest(
    flake_root: Path | None = None,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> RootClosureManifest:
    """Evaluate ``lib.rootClosureManifest`` from *flake_root* or this checkout."""
    root = flake_root or REPO_ROOT
    runner = subprocess.run if run is None else run
    result = runner(
        [
            "nix",
            "eval",
            "--json",
            "--no-write-lock-file",
            f"path:{root}#lib.rootClosureManifest",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "nix eval failed"
        msg = f"root closure manifest eval failed: {detail}"
        raise ValueError(msg)
    return RootClosureManifest.model_validate_json(result.stdout)


def main(argv: list[str] | None = None) -> int:
    """Write the Darwin closure matrix for Actions or stdout."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--nix-eval", action="store_true")
    parser.add_argument("--flake-root", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument(
        "--max-shards", type=int, default=MAX_PARALLEL_DARWIN_ROOT_SHARDS
    )
    args = parser.parse_args(argv)
    if args.manifest is not None and args.nix_eval:
        msg = "choose one of --manifest or --nix-eval"
        raise ValueError(msg)
    if args.nix_eval:
        manifest = eval_root_closure_manifest(args.flake_root)
        shards = plan_darwin_closure_shards(manifest, max_shards=args.max_shards)
    elif args.manifest is not None:
        shards = plan_from_manifest_json(
            args.manifest.read_bytes(), max_shards=args.max_shards
        )
    else:
        msg = "a --manifest file or --nix-eval is required"
        raise ValueError(msg)
    matrix = github_actions_matrix(shards)
    if args.github_output is not None:
        write_github_actions_output(shards, args.github_output)
    else:
        sys.stdout.write(json.dumps(matrix, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover -- CLI delegates to tested main()
    raise SystemExit(main())
