"""Structural Darwin root warmup: realize the missing shared drv set.

Packages inventory (zed) is not the roots' rust/stdenv graph. Hydra's
cache.nixos.org still serves the pre-CVE Darwin coreutils, while this
nixpkgs revision patches coreutils (CVE-2026-56391/56392) in
``pkgs/by-name/co/coreutils/package.nix``. That is a nixpkgs source
patch, not a repo overlay: ``overlays/local-build-fixes.nix`` does not
touch coreutils, and ``modules/home/packages.nix`` only adds the
``coreutils-full`` leaf. ``lib/package-overlays.nix`` applies the same
overlay list to packages, darwinConfigurations, and homeConfigurations,
so the patched stdenv is consistent across those instantiations. Scoping
or dropping the CVE patches is a nixpkgs-pin decision, not a warmup
shortcut.

Until hydra publishes that stdenv, every downstream drv whose hash
depends on it misses cache.nixos.org. Warmup therefore realizes the
outputs shared by two or more Darwin roots that are on neither
cache.nixos.org nor gkze: rust_* plus safe FODs on the rust-warmup
slots, and the remaining shared non-rust cascade as sharded
per-derivation work pushed to gkze. The planner fails closed if a
shard would still compile more than a packages-scale *host-unique*
remainder locally. 38130420607 left that cascade in the root shards
(remaining=1724/1733, rust_*=0) because the rust-only filter peeled
only rust_* / cargo-package. 38146388927 then left remaining=829/836
because all-root intersection skipped darwin-system-only shared
outputs (argus∩rocinante∩zeus, not home-george).
"""

import json
import re
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from lib.update.ci.coverage import (
    check_path_in_cachix,
    check_path_on_nixos,
    nixos_cache_store,
    paths_on_store,
)
from lib.update.ci.jobs import remember_realized_store_paths
from lib.update.ci.shard_plan import (
    ClosureShard,
    composed_root_name,
    darwin_roots,
)
from lib.update.derivation_validation import (
    DerivationValidationFailure,
    DerivationValidationRequest,
    RootClosureManifest,
    RootClosureManifestRoot,
    root_closure_installable,
    validate_derivation_requests,
)
from lib.update.io import atomic_write_text
from lib.update.nix import get_current_nix_platform, is_retryable_nix_network_failure

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from lib.update.derivation_validation import ValidationProgress

type _StoreRun = Callable[
    ..., subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]
]

WARMUP_PLAN_NAME = "warmup-plan.json"
WARMUP_DRVS_NAME = "warmup-drvs"
WARMUP_DRVS_CACHE_INFO = "nix-cache-info"
WARMUP_DRVS_ROOTS = "gcroots"
_DARWIN_SYSTEM = "aarch64-darwin"
# Hosted packages on 37740898487 realized 132 local zed drvs. Home-george
# compiled 1534 rust_* because those hashes were not the packages graph.
# After intersection warmup, a shard should only compile host-unique leaves.
# 400 is packages-scale plus slack; 1534 rust_* fails this gate.
MAX_SHARD_LOCAL_BUILDS = 400
# Public macos-15 cap is 5. rust_* warmup owns those slots, partitioned by
# crate2nix dependency layers so later crates can substitute earlier ones.
RUST_WARMUP_SLOTS = 5
# Shared non-rust outputs present in two or more Darwin roots
# (#38130420607: ~1300 after rust_* peel; #38146388927: all-root
# intersection left remaining=829/836 because darwin-only shared
# stayed in both 2-wide shards). Five slots stripe each layer.
CASCADE_WARMUP_SLOTS = 5
# Two-plus, not all-roots. home-george is a fourth root and does not
# contain the darwin-system graph; requiring every root hid that set.
SHARED_MISSING_MIN_ROOTS = 2
# 38867194218: rust-warmup / cascade went green with warmupOutputs=[]
# (remaining_rust_crates=0) because path-info marked the crate2nix
# graph substitutable. Darwin machine roots then compiled rust_gitbutler-*
# / rust_but-* / rust_sqlx-* / rust_gix-* from source until the 5h
# budget. home-george finished; argus/rocinante/zeus did not. Peel this
# family from packages.aarch64-darwin.gitbutler even when two-plus is
# empty or path-info says the outputs are already in gkze.
DARWIN_RUST_FAMILY_PACKAGES = ("gitbutler",)
_GITBUTLER_FAMILY_PREFIXES = (
    "rust_gitbutler-",
    "rust_but-",
    "rust_sqlx-",
    "rust_gix-",
)
# 38137325241 and 38142145131: 4-wide `nix derivation show --recursive`
# of Darwin roots on ubuntu-24.04 (16 GiB) got runner SIGTERM at
# ~800s with ~100G disk free. One graph at a time.
DARWIN_GRAPH_EVAL_WORKERS = 1


def parse_canary_slots(raw: str) -> tuple[int, ...]:
    """Parse dispatch ``canary_slots`` (comma or space separated 0-4)."""
    parts = tuple(part for part in raw.replace(",", " ").split() if part)
    if not parts:
        return ()
    slots: list[int] = []
    for part in parts:
        try:
            slot = int(part)
        except ValueError:
            msg = f"canary slot {part!r} is not an integer"
            raise WarmupError(msg) from None
        if not 0 <= slot < RUST_WARMUP_SLOTS:
            msg = f"canary slot must be in 0..{RUST_WARMUP_SLOTS - 1}, got {slot}"
            raise WarmupError(msg)
        slots.append(slot)
    return tuple(dict.fromkeys(slots))


def parse_canary_crates(raw: str) -> tuple[str, ...]:
    """Parse dispatch ``canary_crates`` (space or comma separated crate names)."""
    return tuple(dict.fromkeys(part for part in raw.replace(",", " ").split() if part))


_KICK_CANARY_SLOTS = re.compile(r"(?m)^canary-slots:\s*(.+?)\s*$")
_KICK_CANARY_CRATES = re.compile(r"(?m)^canary-crates:\s*(.+?)\s*$")


def parse_kick_canary_slots(text: str) -> tuple[int, ...]:
    """Parse ``canary-slots: 3,4`` from an update-kick file.

    Kick-file pushes cannot set ``workflow_dispatch`` inputs (App token
    403). #1268 skipped canary-warm-rust for that reason; a kick line is
    the push-path equivalent of ``inputs.canary_slots``.
    """
    match = _KICK_CANARY_SLOTS.search(text)
    if not match:
        return ()
    return parse_canary_slots(match.group(1))


def parse_kick_canary_crates(text: str) -> tuple[str, ...]:
    """Parse ``canary-crates: settings settings_content`` from an update-kick file.

    Push-path canary cannot set ``inputs.canary_crates``. #1269's
    slot-only canary never reached rust_settings (2h20m, still
    in_progress) so the settings SVH mix landed on validate slot 3.
    """
    match = _KICK_CANARY_CRATES.search(text)
    if not match:
        return ()
    return parse_canary_crates(match.group(1))


def canary_crate_warmup_paths(
    layers: Sequence[Sequence[str]],
    crates: Sequence[str],
) -> tuple[str, ...]:
    """Return rust_* outputs for *crates* from every warmup layer.

    Slot-stripe filtering misses a crate that lives on another slot
    (settings on slot 3, canary on slot 0). Named-crate canary is a
    family probe, not a slot probe.
    """
    wanted = tuple(dict.fromkeys(crates))
    if not wanted:
        return ()
    return _warmup_outputs_matching(
        layers,
        lambda path: any(
            is_named_rust_crate_store_path(path, crate) for crate in wanted
        ),
    )


_WARMUP_REALIZE_CHUNK = 128
# Crane builds crates.io deps before the workspace. crate2nix already has
# per-crate drvs; realize those first in small batches so force-local SVH
# family finds them in the store instead of rebuilding under --no-substitute.
# macos-15 is 7 GiB; 128-wide rustc would OOM a cold stripe.
CRANE_DEP_CHUNK = 8
_STORE_COPY_CHUNK = 128
_SUBSTITUTER_WORKERS = 16
_NIXOS_CACHE = "https://cache.nixos.org"
_GKZE_CACHE = "https://gkze.cachix.org"
_STORE_PREFIX = "/nix/store/"
# Derivation-v4 JSON omits the store dir; older `nix derivation show`
# responses used absolute paths. Accept both so warmup does not collect
# zero Darwin outputs on Nix 2.35.
_STORE_BASENAME = re.compile(r"^[0-9a-z]{32}-.+$")
# rust_* family is a handful of crates. #1263 compiled 406 bootstrap
# drvs; fail the hosted job on the dry-run line instead of --keep-going.
WARMUP_FATAL_BUILD_LIMIT = 32
# 38137325241: Linux validate root-closures realized
# nixos-disk-image.drv (linux-builder / Rosetta VM). That rebuild is
# the native boundary of Darwin roots when hydra has not published the
# CVE-patched stdenv. The 32-limit is for rust-warmup bootstrap, not
# this image; keep_going lifts the cap to MAX_SHARD_LOCAL_BUILDS.
_LINUX_BUILDER_IMAGE = "nixos-disk-image"
_WILL_BE_BUILT = re.compile(r"these (\d+) derivations? will be built", re.IGNORECASE)
_THIS_WILL_BE_BUILT = re.compile(r"this derivation will be built", re.IGNORECASE)


def is_linux_builder_image_command(command: str) -> bool:
    """Return whether this hosted command realizes the linux-builder VM."""
    return _LINUX_BUILDER_IMAGE in command


def is_linux_builder_output_store_path(path: str) -> bool:
    """Return whether *path* is the linux-builder / Rosetta VM image output."""
    return _LINUX_BUILDER_IMAGE in path


def linux_builder_substitute_miss_reason(line: str) -> str | None:
    """Return why Darwin ``--max-jobs 0`` cannot recover a missing VM image.

    39006154683: Linux cache-root-deps substituted
    ``wy15yizd…-nixos-disk-image`` from gkze (post-build hook silent) and
    finished 0. Darwin closures then
    ``nix build --max-jobs 0 nixos-disk-image.drv^*``, streamed 50 wbb,
    copied inners from cache.nixos.org, and Cannot-build'd ``etc-fstab``
    (platform mismatch). Keep-going then spent 5h on the host closures.
    Any local rebuild here is a binary-cache miss, not a native compile.
    """
    if "Cannot build" in line:
        return "linux-builder image missing from binary cache"
    if _WILL_BE_BUILT.search(line) or _THIS_WILL_BE_BUILT.search(line):
        return "linux-builder image missing from binary cache"
    return None


class WarmupError(ValueError):
    """The planner could not produce a safe Darwin warmup set."""


class WarmupFatalError(WarmupError):
    """A streamed warmup/root line that must fail the hosted job immediately."""


_FIXED_OUTPUT_HASH_MISMATCH = "hash mismatch in fixed-output derivation"
# 38739788947: leftover-contaminated rematerialize pinned Wap+; two
# independent Darwin cascade hosts then produced wb2d. Realize these
# FODs on two macos-15 runners before the 5h rust-warmup fan-out.
_DARWIN_FOD_CANARY_ATTRS = (
    "bb.pnpmDeps",
    "emdash.pnpmDeps",
    "comet.src",
)


def is_fixed_output_hash_mismatch(text: str) -> bool:
    """Return whether Nix streamed a FOD content-hash disagreement."""
    return _FIXED_OUTPUT_HASH_MISMATCH in text.casefold()


def darwin_fod_canary_installables(
    system: str = _DARWIN_SYSTEM,
) -> tuple[str, ...]:
    """Return flake attrs for Darwin warmup-path FODs (pnpm-deps, DMGs)."""
    return tuple(f"path:.#pkgs.{system}.{attr}" for attr in _DARWIN_FOD_CANARY_ATTRS)


def warmup_fatal_line(
    line: str,
    *,
    substitute_only: bool = False,
    keep_going: bool = False,
    source_fetch_miss: bool = False,
    apply_wbb_limit: bool = True,
) -> str | None:
    """Return why a live warmup/root log line must fail the job."""
    if is_fixed_output_hash_mismatch(line):
        # 38739788947: cascade --keep-going spent 19m after bb-pnpm-deps
        # mismatch. A FOD pin error cannot recover by building siblings.
        # 39234076105 slot 4: rusty_v8 fetchgit HTTP 503 then hashed a
        # truncated Chromium tree. Retry that source FOD; do not treat a
        # transport-tainted hash as a pin change (and never skip
        # bb-pnpm-deps). Fall through to the terminal None when skipped.
        drv = command_store_drv(line)
        if not (source_fetch_miss and drv and is_source_fetch_store_path(drv)):
            return "fixed-output hash mismatch"
    if "error[E04" in line:
        # Zed-only keep_going collects every crate error in one pass.
        return None if keep_going else "rustc SVH/crate mismatch"
    if "Cannot build" in line:
        # --max-jobs 0 cache miss on a 1-drv helper (patchutils/pbzx/cpio).
        # keep_going surfaces every broken Zed crate instead of one/run.
        # 38258066093 slots 0/1: source.drv / convert_case-*.tar.gz
        # Cannot-build after curl (6) on github.com / static.crates.io.
        # Stream-skip so realize_source_fetch_drvs can backoff, prove
        # DNS, and retry. rustc / rust_* / helpers stay fatal unless
        # the same nix invocation already missed a source FOD
        # (38359636482 slot 2: markup5ever.tar.gz then rust_markup5ever).
        skip = substitute_only or keep_going
        if not skip:
            drv = command_store_drv(line)
            skip = bool(drv and is_source_fetch_store_path(drv))
            if not skip and source_fetch_miss and drv and is_crate2nix_rust_output(drv):
                skip = True
        return None if skip else "cannot build"
    if "liveness" in line.lower():
        return "liveness"
    lowered = line.lower()
    if "cannot download" in lowered and "from any mirror" in lowered:
        # 38307411499 slots 2/4: Cannot-build on pin-project / libloading
        # was stream-skipped, then the builder tail "cannot download
        # *.tar.gz from any mirror" (curl 6 on static.crates.io) aborted
        # before DNS retry. bmake/rustc mirrors stay fatal.
        return (
            None
            if is_source_fetch_mirror_line(line)
            else "cannot download from any mirror"
        )
    match = _WILL_BE_BUILT.search(line)
    if match and apply_wbb_limit:
        # 32 catches #1263's 406-drv bootstrap on a real compile.
        # --max-jobs 0 rust_* misses (38126388469 canary: cranelift 40)
        # must reach _is_deferred_compiler_rust_miss, not abort here.
        # keep_going builds the whole rust_* family on one runner
        # (parent 38081612138 died at 37). 39217658040 slot 4 others
        # rust_but-agentlog 39 is that class; realize must pass
        # keep_going=True instead of raising at the 32 default.
        # Still fail closed at the packages-scale shard ceiling.
        # 38506525442: Darwin root-closure installables turn this off.
        # After rust+cascade the 2-wide rocinante+zeus shard streamed
        # 470 wbb (CVE-stdenv vs hydra / gkze miss). That is the host
        # closure, not rust-warmup bootstrap; --keep-going must finish.
        limit = (
            MAX_SHARD_LOCAL_BUILDS
            if keep_going or substitute_only
            else WARMUP_FATAL_BUILD_LIMIT
        )
        if int(match.group(1)) > limit:
            return f"unexpectedly large will-be-built count ({match.group(1)})"
    return None


def raise_if_warmup_fatal(
    line: str,
    *,
    substitute_only: bool = False,
    keep_going: bool = False,
    source_fetch_miss: bool = False,
    apply_wbb_limit: bool = True,
) -> None:
    """Abort as soon as a fatal warmup/root pattern is streamed."""
    if reason := warmup_fatal_line(
        line,
        substitute_only=substitute_only,
        keep_going=keep_going,
        source_fetch_miss=source_fetch_miss,
        apply_wbb_limit=apply_wbb_limit,
    ):
        msg = f"{reason}: {line}"
        raise WarmupFatalError(msg)


class RootWarmupStats(BaseModel):
    """Per-root Darwin output counts relative to substituters and warmup."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outputs: int
    missing: int
    warmup: int
    remaining: int


class ShardLocalBuildReport(BaseModel):
    """How many Darwin outputs a shard must still compile after warmup."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    shard: str
    roots: tuple[str, ...]
    remaining: int
    remaining_rust_crates: int


class WarmupPlan(BaseModel):
    """Intersection of missing Darwin root outputs rust-warmup realizes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(alias="schemaVersion")
    system: str
    substituters: tuple[str, ...]
    warmup_outputs: tuple[str, ...] = Field(alias="warmupOutputs")
    rust_layers: tuple[tuple[str, ...], ...] = Field(alias="rustLayers", default=())
    cascade_outputs: tuple[str, ...] = Field(alias="cascadeOutputs", default=())
    cascade_layers: tuple[tuple[str, ...], ...] = Field(
        alias="cascadeLayers", default=()
    )
    output_drvs: dict[str, str] = Field(alias="outputDrvs", default_factory=dict)
    per_root: dict[str, RootWarmupStats] = Field(alias="perRoot")
    shards: tuple[ShardLocalBuildReport, ...]
    notes: str


def is_crate2nix_rust_output(store_path: str) -> bool:
    """Return whether *store_path* is a crate2nix ``rust_*`` output."""
    name = store_path.rsplit("/", 1)[-1]
    _hash, separator, rest = name.partition("-")
    return bool(separator) and rest.startswith("rust_")


def is_gitbutler_family_store_path(path: str) -> bool:
    """Return whether *path* is a GitButler/but/sqlx/gix crate2nix rust_*."""
    rest = _store_output_rest(path)
    return rest.startswith(_GITBUTLER_FAMILY_PREFIXES)


def is_planned_rust_warmup_output(store_path: str) -> bool:
    """Return whether rust-warmup (not cascade) owns *store_path*."""
    return is_crate2nix_rust_output(store_path) or is_safe_rust_warmup_other(store_path)


def _absolute_store_path(path: str) -> str | None:
    """Return a full store path, or None if *path* is not a store output."""
    if path.startswith(_STORE_PREFIX) and path != _STORE_PREFIX:
        return path
    if _STORE_BASENAME.fullmatch(path):
        return f"{_STORE_PREFIX}{path}"
    return None


def darwin_output_paths(payload: Mapping[str, object]) -> frozenset[str]:
    """Collect aarch64-darwin output paths from ``nix derivation show`` JSON."""
    derivations = payload.get("derivations")
    if not isinstance(derivations, dict):
        msg = "derivation graph missing derivations"
        raise WarmupError(msg)
    paths: set[str] = set()
    for drv in derivations.values():
        if not isinstance(drv, dict) or drv.get("system") != _DARWIN_SYSTEM:
            continue
        outputs = drv.get("outputs")
        if not isinstance(outputs, dict):
            continue
        for output in outputs.values():
            if not isinstance(output, dict):
                continue
            raw = output.get("path")
            if not isinstance(raw, str):
                continue
            path = _absolute_store_path(raw)
            if path is not None:
                paths.add(path)
    return frozenset(paths)


def intersect_missing(
    per_root: Mapping[str, frozenset[str]],
    substitutable: frozenset[str],
    *,
    min_roots: int = SHARED_MISSING_MIN_ROOTS,
) -> frozenset[str]:
    """Return outputs at least *min_roots* roots need that caches lack.

    38146388927: all-root intersection left remaining=829/836 (rust_*=0)
    because darwin-system-only shared outputs (argus∩rocinante∩zeus, not
    home-george) stayed in both 2-wide shards. Two-plus is the cascade
    peel; one-root leaves still face the 400 gate.
    """
    if min_roots < 1:
        msg = "min_roots must be at least 1"
        raise WarmupError(msg)
    if not per_root:
        return frozenset()
    counts: dict[str, int] = {}
    for outputs in per_root.values():
        for path in outputs:
            counts[path] = counts.get(path, 0) + 1
    return frozenset(
        path
        for path, count in counts.items()
        if count >= min_roots and path not in substitutable
    )


def shard_remaining_outputs(
    shard: ClosureShard,
    per_root: Mapping[str, frozenset[str]],
    *,
    substitutable: frozenset[str],
    warmup: frozenset[str],
) -> frozenset[str]:
    """Return this shard's Darwin outputs that warmup and caches will not cover."""
    union: set[str] = set()
    for name in shard.roots:
        union.update(per_root.get(name, frozenset()))
    return frozenset(union - substitutable - warmup)


def assert_local_build_threshold(
    reports: Sequence[ShardLocalBuildReport],
    *,
    threshold: int = MAX_SHARD_LOCAL_BUILDS,
) -> None:
    """Fail closed if any shard would compile more than *threshold* locally."""
    if threshold < 1:
        msg = "local-build threshold must be at least 1"
        raise WarmupError(msg)
    offenders = [report for report in reports if report.remaining > threshold]
    if not offenders:
        return
    detail = ", ".join(
        f"{report.shard} remaining={report.remaining} "
        f"rust_*={report.remaining_rust_crates}"
        for report in offenders
    )
    msg = (
        "Darwin shard local-build budget exceeded before realization "
        f"(threshold={threshold}, packages-scale was 132 local drvs on "
        f"37740898487): {detail}"
    )
    raise WarmupError(msg)


def darwin_package_installable(name: str, *, flake_root: Path | None = None) -> str:
    """Return ``path:.#packages.aarch64-darwin.<name>`` for a Darwin package."""
    prefix = f"path:{flake_root}#" if flake_root is not None else "path:.#"
    return f"{prefix}packages.{_DARWIN_SYSTEM}.{name}"


def eval_darwin_installable_graph(
    installable: str,
    *,
    label: str,
    run: _StoreRun | None = None,
) -> dict[str, object]:
    """Instantiate one Darwin installable and return recursive derivation JSON."""
    runner = subprocess.run if run is None else run
    result = runner(
        [
            "nix",
            "derivation",
            "show",
            "--recursive",
            "--no-update-lock-file",
            "--option",
            "allow-import-from-derivation",
            "false",
            installable,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = (
            result.stderr.strip()
            or result.stdout.strip()
            or "nix derivation show failed"
        )
        msg = f"warmup graph for {label} failed: {detail}"
        raise WarmupError(msg)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        msg = f"warmup graph for {label} is not JSON"
        raise WarmupError(msg) from error
    if not isinstance(payload, dict):
        msg = f"warmup graph for {label} is not an object"
        raise WarmupError(msg)
    return payload


def eval_root_darwin_graph(
    flake_root: Path,
    root_name: str,
    *,
    run: _StoreRun | None = None,
) -> dict[str, object]:
    """Instantiate one Darwin root and return the recursive derivation JSON."""
    installable = root_closure_installable(_DARWIN_SYSTEM, root_name).replace(
        "path:.#", f"path:{flake_root}#"
    )
    return eval_darwin_installable_graph(installable, label=root_name, run=run)


def eval_darwin_package_graph(
    flake_root: Path,
    name: str,
    *,
    run: _StoreRun | None = None,
) -> dict[str, object]:
    """Instantiate one Darwin package and return the recursive derivation JSON."""
    return eval_darwin_installable_graph(
        darwin_package_installable(name, flake_root=flake_root),
        label=name,
        run=run,
    )


def eval_root_darwin_outputs(
    flake_root: Path,
    root_name: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> frozenset[str]:
    """Instantiate one Darwin root's drv graph and return Darwin output paths."""
    return darwin_output_paths(eval_root_darwin_graph(flake_root, root_name, run=run))


def _drv_basename(name: str) -> str:
    """Return the store basename of a derivation path or key."""
    return name.rsplit("/", 1)[-1]


def _input_drv_keys(drv: Mapping[str, object]) -> frozenset[str]:
    """Return input derivation basenames from v3 ``inputDrvs`` or v4 ``inputs``."""
    inputs = drv.get("inputs")
    if isinstance(inputs, dict):
        drvs = inputs.get("drvs")
        if isinstance(drvs, dict):
            return frozenset(_drv_basename(key) for key in drvs)
    raw = drv.get("inputDrvs")
    if isinstance(raw, dict):
        return frozenset(_drv_basename(key) for key in raw)
    return frozenset()


def output_dependency_layers(
    outputs: frozenset[str],
    payloads: Sequence[Mapping[str, object]],
    *,
    kind: str,
) -> tuple[tuple[str, ...], ...]:
    """Layer *outputs* by Darwin ``inputDrvs`` so later paths can substitute."""
    if not outputs:
        return ()
    path_to_drv: dict[str, str] = {}
    drv_outputs: dict[str, set[str]] = {}
    drv_inputs: dict[str, set[str]] = {}
    for payload in payloads:
        derivations = payload.get("derivations")
        if not isinstance(derivations, dict):
            msg = "derivation graph missing derivations"
            raise WarmupError(msg)
        for raw_name, drv in derivations.items():
            if not isinstance(raw_name, str) or not isinstance(drv, dict):
                continue
            if drv.get("system") != _DARWIN_SYSTEM:
                continue
            output_map = drv.get("outputs")
            if not isinstance(output_map, dict):
                continue
            key = _drv_basename(raw_name)
            for output in output_map.values():
                if not isinstance(output, dict):
                    continue
                raw = output.get("path")
                if not isinstance(raw, str):
                    continue
                path = _absolute_store_path(raw)
                if path is None or path not in outputs:
                    continue
                path_to_drv[path] = key
                drv_outputs.setdefault(key, set()).add(path)
            drv_inputs.setdefault(key, set()).update(_input_drv_keys(drv))
    missing = outputs - frozenset(path_to_drv)
    if missing:
        sample = ", ".join(sorted(missing)[:8])
        msg = f"{kind} warmup paths missing from Darwin graphs: {sample}"
        raise WarmupError(msg)
    owned = frozenset(drv_outputs)
    deps = {
        drv: frozenset(dep for dep in drv_inputs.get(drv, set()) if dep in owned)
        for drv in owned
    }
    remaining = set(owned)
    layers: list[tuple[str, ...]] = []
    while remaining:
        ready = sorted(drv for drv in remaining if not (deps[drv] & remaining))
        if not ready:
            sample = ", ".join(sorted(remaining)[:8])
            msg = f"{kind} warmup dependency cycle: {sample}"
            raise WarmupError(msg)
        layer_paths = tuple(path for drv in ready for path in sorted(drv_outputs[drv]))
        layers.append(layer_paths)
        remaining.difference_update(ready)
    return tuple(layers)


def rust_warmup_layers(
    warmup: frozenset[str],
    payloads: Sequence[Mapping[str, object]],
) -> tuple[tuple[str, ...], ...]:
    """Layer rust_* warmup outputs by crate2nix ``inputDrvs``; prelude is the rest."""
    rust_warmup = frozenset(path for path in warmup if is_crate2nix_rust_output(path))
    # #1268: goose-cli-1.51.0 / vendor-cargo-deps in the unfiltered
    # prelude exploded slots 3/4 (1759 / 521 will-be-built). rustc-1.98.1
    # was already substituting; only FOD/helpers belong here.
    prelude = tuple(
        sorted(
            path
            for path in warmup
            if path not in rust_warmup and is_safe_rust_warmup_other(path)
        )
    )
    layers = output_dependency_layers(rust_warmup, payloads, kind="rust_*")
    if prelude:
        return (prelude, *layers)
    return layers


def cascade_warmup_layers(
    cascade: frozenset[str],
    payloads: Sequence[Mapping[str, object]],
) -> tuple[tuple[str, ...], ...]:
    """Layer the shared non-rust intersection so umbrellas compile last."""
    return output_dependency_layers(cascade, payloads, kind="cascade")


def warmup_output_drvs(
    warmup: frozenset[str],
    payloads: Sequence[Mapping[str, object]],
) -> dict[str, str]:
    """Map each warmup output to the Darwin ``.drv`` that produces it."""
    mapping: dict[str, str] = {}
    for payload in payloads:
        derivations = payload.get("derivations")
        if not isinstance(derivations, dict):
            msg = "derivation graph missing derivations"
            raise WarmupError(msg)
        for raw_name, drv in derivations.items():
            if not isinstance(raw_name, str) or not isinstance(drv, dict):
                continue
            if drv.get("system") != _DARWIN_SYSTEM:
                continue
            drv_path = _absolute_store_path(raw_name)
            if drv_path is None or not drv_path.endswith(".drv"):
                continue
            outputs = drv.get("outputs")
            if not isinstance(outputs, dict):
                continue
            for output in outputs.values():
                if not isinstance(output, dict):
                    continue
                raw = output.get("path")
                if not isinstance(raw, str):
                    continue
                path = _absolute_store_path(raw)
                if path is None or path not in warmup:
                    continue
                mapping[path] = drv_path
    missing = warmup - frozenset(mapping)
    if missing:
        sample = ", ".join(sorted(missing)[:8])
        msg = f"warmup outputs missing Darwin .drv mapping: {sample}"
        raise WarmupError(msg)
    return mapping


def unique_drvs_for_outputs(
    outputs: Sequence[str],
    output_drvs: Mapping[str, str],
) -> tuple[str, ...]:
    """Return first-seen ``.drv`` paths for *outputs*, failing closed on gaps."""
    missing = [path for path in outputs if path not in output_drvs]
    if missing:
        sample = ", ".join(missing[:8])
        msg = f"warmup outputs missing Darwin .drv mapping: {sample}"
        raise WarmupError(msg)
    seen: dict[str, None] = {}
    for path in outputs:
        seen.setdefault(output_drvs[path], None)
    return tuple(seen)


def warmup_build_installable(path: str) -> str:
    """Return the installable that realizes warmup *outputs*, not the ``.drv`` file.

    ``nix build /nix/store/foo.drv`` only substitutes/realizes the derivation
    text. After a successful import that is a no-op and Cachix never sees
    rust_* outputs. ``37868270521`` then failed with ``don't know how to
    build these paths`` once auto-GC ate the unrooted import. ``foo.drv^*``
    is the output set; ``_batch_key`` already refuses to batch those nodes.
    """
    if path.endswith(".drv"):
        return f"{path}^*"
    return path


def _store_command_detail(
    result: subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes],
    *,
    fallback: str,
) -> str:
    """Return stderr/stdout from a nix process as text."""
    stderr = result.stderr
    stdout = result.stdout
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    if isinstance(stdout, bytes):
        stdout = stdout.decode(errors="replace")
    return stderr.strip() or stdout.strip() or fallback


def _file_store_uri(path: Path) -> str:
    """Return a ``file://`` URI for a local binary-cache directory."""
    return path.resolve().as_uri()


def _auto_gc_off_args() -> tuple[str, ...]:
    """Disable store auto-GC for copy/verify so unrooted imports are not eaten.

    Hosted ``update-runtime`` sets ``min-free = 32GiB``. macos-15 often has
    less free than that, so any store addition triggers GC. ``37868270521``
    ``nix-store --import`` exited 0, ``/nix`` stayed ~4.6Gi, and the first
    ``nix build`` could not see the just-imported ``.drv`` files.
    """
    return ("--option", "min-free", "0", "--option", "max-free", "0")


# ``nix-store --add-root`` is a modifier. Determinate Nix 3.22 on macos-15
# prints ``error: no operation specified`` unless an operation such as
# ``--realise`` is also present (``37879308953`` / #1256).
_NIX_STORE_OPERATIONS = frozenset({
    "--realise",
    "-r",
    "--query",
    "-q",
    "--add",
    "--delete",
    "--gc",
    "--dump",
    "--restore",
    "--export",
    "--import",
    "--verify",
    "--optimise",
    "--read-log",
    "-l",
    "--dump-db",
    "--load-db",
    "--print-env",
    "--serve",
})


def nix_store_argv_has_operation(args: Sequence[str]) -> bool:
    """Return whether a ``nix-store`` argv includes a real operation.

    The #1256 argv ``nix-store --add-root ROOT --indirect DRV`` does not.
    """
    return (
        bool(args)
        and args[0] == "nix-store"
        and bool(_NIX_STORE_OPERATIONS & set(args))
    )


def warmup_drv_root_args(root: Path, drv: str) -> list[str]:
    """Return argv that GC-roots an imported ``.drv`` file without building it.

    ``nix-store --realise --add-root`` on a ``.drv`` builds outputs. ``nix
    build --out-link`` realizes the already-copied ``.drv`` store path and
    registers the symlink as a GC root. That is the #1256-safe form.
    """
    return [
        "nix",
        "build",
        "--out-link",
        str(root),
        "--offline",
        *_auto_gc_off_args(),
        drv,
    ]


def _copy_derivation_closure(
    drv_paths: Sequence[str],
    store: Path,
    *,
    to_store: bool,
    run: _StoreRun | None = None,
) -> None:
    """Copy a derivation FS closure through a daemon-aware ``file://`` cache."""
    runner = subprocess.run if run is None else run
    uri = _file_store_uri(store)
    flag = "--to" if to_store else "--from"
    extra = () if to_store else ("--no-check-sigs",)
    ordered = tuple(dict.fromkeys(drv_paths))
    verb = "export" if to_store else "import"
    for start in range(0, len(ordered), _STORE_COPY_CHUNK):
        batch = ordered[start : start + _STORE_COPY_CHUNK]
        result = runner(
            [
                "nix",
                "copy",
                "--derivation",
                *extra,
                *_auto_gc_off_args(),
                flag,
                uri,
                *batch,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            detail = _store_command_detail(
                result, fallback="nix copy --derivation failed"
            )
            msg = f"failed to {verb} warmup .drv closure: {detail}"
            raise WarmupError(msg)


def _register_warmup_drv_roots(
    drv_paths: Sequence[str],
    roots_dir: Path,
    *,
    run: _StoreRun | None = None,
) -> None:
    """Pin imported ``.drv`` files so later ``min-free`` GC cannot drop them."""
    runner = subprocess.run if run is None else run
    roots_dir.mkdir(parents=True, exist_ok=True)
    for drv in dict.fromkeys(drv_paths):
        root = roots_dir / Path(drv).name
        result = runner(
            warmup_drv_root_args(root, drv),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            detail = _store_command_detail(
                result, fallback="nix build --out-link failed"
            )
            msg = f"failed to GC-root imported warmup .drv {drv}: {detail}"
            raise WarmupError(msg)


def _require_store_paths(
    paths: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> None:
    """Fail closed unless every imported ``.drv`` is a live store object."""
    runner = subprocess.run if run is None else run
    ordered = tuple(dict.fromkeys(paths))
    for start in range(0, len(ordered), _STORE_COPY_CHUNK):
        batch = ordered[start : start + _STORE_COPY_CHUNK]
        result = runner(
            ["nix", "path-info", *_auto_gc_off_args(), *batch],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            detail = _store_command_detail(result, fallback="nix path-info failed")
            msg = f"imported warmup .drv missing from store: {detail}"
            raise WarmupError(msg)


def export_warmup_drvs(
    drv_paths: Sequence[str],
    dest: Path,
    *,
    run: _StoreRun | None = None,
) -> None:
    """Copy the planner's warmup derivation closure into a ``file://`` cache.

    ``nix-store --add`` of a copied ``.drv`` content-addresses a new path
    (``37851246740``). A concatenated ``nix-store --export`` NAR imported via
    Python ``input=`` returned 0 on macos-15 but did not leave buildable store
    objects (``37868270521``): auto-GC under ``min-free = 32GiB`` collected
    the unrooted dump, and ``nix build foo.drv`` would only have realized the
    ``.drv`` file anyway. ``nix copy --derivation`` talks to the daemon, keeps
    original store paths, and includes the eval-closure references.
    """
    dest.mkdir(parents=True, exist_ok=True)
    ordered = tuple(dict.fromkeys(drv_paths))
    if not ordered:
        return
    for drv in ordered:
        if not Path(drv).is_file():
            msg = f"planner is missing warmup .drv {drv}"
            raise WarmupError(msg)
    _copy_derivation_closure(ordered, dest, to_store=True, run=run)


def import_warmup_drvs(
    drv_paths: Sequence[str],
    cache: Path,
    *,
    run: _StoreRun | None = None,
) -> None:
    """Register the exported derivation closure and GC-root each ``.drv``."""
    if not drv_paths:
        return
    if not cache.is_dir():
        msg = f"warmup drv cache missing: {cache}"
        raise WarmupError(msg)
    info = cache / WARMUP_DRVS_CACHE_INFO
    if not info.is_file():
        msg = f"warmup drv cache missing {WARMUP_DRVS_CACHE_INFO}"
        raise WarmupError(msg)
    ordered = tuple(dict.fromkeys(drv_paths))
    _copy_derivation_closure(ordered, cache, to_store=False, run=run)
    _register_warmup_drv_roots(ordered, cache / WARMUP_DRVS_ROOTS, run=run)
    _require_store_paths(ordered, run=run)


def stripe_layer_paths(
    layers: Sequence[Sequence[str]],
    slot: int,
    *,
    width: int,
    weight: Callable[[str], int] | None = None,
) -> tuple[str, ...]:
    """Assign each path to the current lightest slot, carrying load across layers.

    Rust warmup keeps per-layer round-robin so crate2nix dependents stay on
    the same slot as their inputs. Cascade uses this min-load assignment:
    39166099302 packed 123 minutes of compile into slots 16/46/37/13/11m
    because ``tuple(layer)[slot::width]`` stacked llvm/clang onto slot 1.
    """
    if width < 1:
        msg = "warmup width must be at least 1"
        raise WarmupError(msg)
    if not 0 <= slot < width:
        msg = f"warmup slot must be in 0..{width - 1}, got {slot}"
        raise WarmupError(msg)
    weigher = weight if weight is not None else (lambda _path: 1)
    assigned: list[str] = []
    loads = [0] * width
    for layer in layers:
        for path in layer:
            target = min(range(width), key=lambda index: (loads[index], index))
            loads[target] += max(int(weigher(path)), 1)
            if target == slot:
                assigned.append(path)
    return tuple(assigned)


def slot_warmup_paths(
    layers: Sequence[Sequence[str]],
    slot: int,
    *,
    width: int = RUST_WARMUP_SLOTS,
) -> tuple[str, ...]:
    """Return this matrix slot's stripe of each warmup layer, in layer order."""
    if width < 1:
        msg = "rust warmup width must be at least 1"
        raise WarmupError(msg)
    if not 0 <= slot < width:
        msg = f"rust warmup slot must be in 0..{width - 1}, got {slot}"
        raise WarmupError(msg)
    return tuple(path for layer in layers for path in tuple(layer)[slot::width])


# Relative compile cost for cascade min-load. Values are from 39166099302
# slot imbalance (46m vs 11m) plus known Darwin C++/toolchain archives;
# they are not minutes. Unlisted paths weigh 1.
_CASCADE_PATH_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("WebKit", 12),
    ("webkitgtk-", 12),
    ("webkit-", 12),
    ("qtwebengine-", 12),
    ("chromium-", 12),
    ("electron-", 10),
    ("onnxruntime-", 10),
    ("buzz-onnxruntime-", 10),
    ("llvm-", 8),
    ("clang-", 8),
    ("compiler-rt-", 6),
    ("swift-", 8),
    ("ghc-", 8),
    ("openjdk-", 6),
    ("ffmpeg-", 6),
    ("lld-", 4),
    ("bun-", 5),
    ("nodejs-", 4),
    ("postgresql-", 4),
)


def cascade_path_weight(path: str) -> int:
    """Return the relative cascade compile weight for min-load striping."""
    rest = _store_output_rest(path)
    for prefix, weight in _CASCADE_PATH_WEIGHTS:
        if rest.startswith(prefix):
            return weight
    return 1


def slot_cascade_paths(
    layers: Sequence[Sequence[str]],
    slot: int,
    *,
    width: int = CASCADE_WARMUP_SLOTS,
) -> tuple[str, ...]:
    """Return this cascade-warmup slot's min-load stripe of each cascade layer."""
    if width < 1:
        msg = "cascade warmup width must be at least 1"
        raise WarmupError(msg)
    if not 0 <= slot < width:
        msg = f"cascade warmup slot must be in 0..{width - 1}, got {slot}"
        raise WarmupError(msg)
    return stripe_layer_paths(layers, slot, width=width, weight=cascade_path_weight)


def is_cascade_canary_store_path(path: str) -> bool:
    """Return whether cascade canary must realize *path* before the 5-wide stripe.

    ``buzz-onnxruntime`` is a heavy Darwin-only C++ static archive. Hosted
    macos-15 APFS I/O-faulted ranlib mid-link (38394692516 slot 4) while
    five cascade slots plus packages oversubscribed the free-tier pool.
    Build it once on a dedicated runner, push via the Cachix
    post-build-hook, then stripe slots substitute.

    ``hq-<version>`` is a fail-closed authenticated-binary patcher. 38986213199
    cascade slot 1 died on HQ 0.11.13 arm64 automatic-update gate inventory
    (expected 1, got 2) after canary had already gone green. Realize HQ in
    canary so patcher drift fails before the 5-wide stripe starts.
    """
    rest = _store_output_rest(path)
    for prefix in ("buzz-onnxruntime-", "hq-"):
        if not rest.startswith(prefix):
            continue
        after = rest[len(prefix) :]
        return bool(after) and after[0].isdigit()
    return False


def cascade_canary_paths(layers: Sequence[Sequence[str]]) -> tuple[str, ...]:
    """Return planned cascade canary outputs, first-seen order."""
    return tuple(
        path for layer in layers for path in layer if is_cascade_canary_store_path(path)
    )


def skip_cached_warmup_paths(
    paths: Sequence[str],
    *,
    present: Callable[[str], bool] | None = None,
    run: Callable[..., object] | None = None,
) -> tuple[str, ...]:
    """Drop paths already in gkze so a later run resumes the same stripe.

    The default checker batches ``nix path-info --json --store gkze``. Tests
    and GitButler probes that pass a custom *present* stay per-path.
    """
    unique = tuple(dict.fromkeys(paths))
    if not unique:
        return ()
    if present is None or present is check_path_in_cachix:
        found = paths_on_store(unique, _GKZE_CACHE, run=run)
        return tuple(path for path in unique if path not in found)
    return tuple(path for path in unique if not present(path))


def rust_warmup_probe_paths(
    paths: Sequence[str],
    *,
    present: Callable[[str], bool],
) -> tuple[str, ...]:
    """Return GitButler-family paths path-info claims are already in gkze.

    38867194218: path-info lied. ``--max-jobs 0`` is the cheap oracle:
    substitute succeeds on a real NAR, then skip compile. Substitute
    failure falls back to a local realize.
    """
    return tuple(
        path
        for path in dict.fromkeys(paths)
        if is_gitbutler_family_store_path(path) and present(path)
    )


def rust_warmup_realize_paths(
    paths: Sequence[str],
    *,
    present: Callable[[str], bool],
) -> tuple[str, ...]:
    """Skip cached rust-warmup paths except the GitButler crate family.

    38867194218: ``nix path-info --store https://gkze.cachix.org`` treated
    rust_gitbutler-* / rust_but-* / rust_sqlx-* as present (plan
    warmupOutputs=[], rust-warmup no-op'd) while ``nix build`` of the
    machine roots compiled those exact .drv files from source. path-info
    is not the realize oracle for this family; ``nix build`` substitutes
    when the NAR is real. Prefer ``rust_warmup_probe_paths`` plus
    substitute-only before compiling the cached family.
    """
    missing = skip_cached_warmup_paths(paths, present=present)
    family = tuple(
        path for path in dict.fromkeys(paths) if is_gitbutler_family_store_path(path)
    )
    return tuple(dict.fromkeys((*missing, *family)))


def substitutable_paths(
    paths: Iterable[str],
    *,
    present: Callable[[str], bool] | None = None,
    run: Callable[..., object] | None = None,
) -> frozenset[str]:
    """Return the subset of *paths* a substituter already has.

    The default checker batches gkze, then cache.nixos.org for the remainder,
    so rust_* do not pay a hydra miss each. A custom *present* stays 16-wide.
    """
    unique = tuple(dict.fromkeys(paths))
    if not unique:
        return frozenset()
    if present is None:
        return default_cache_present_many(unique, run=run)
    found: set[str] = set()
    with ThreadPoolExecutor(max_workers=_SUBSTITUTER_WORKERS) as pool:
        for path, ok in zip(unique, pool.map(present, unique), strict=True):
            if ok:
                found.add(path)
    return frozenset(found)


def default_cache_present(
    store_path: str,
    *,
    run: Callable[..., object] | None = None,
) -> bool:
    """Return whether *store_path* is on cache.nixos.org or gkze.cachix.org."""
    return check_path_on_nixos(store_path, run=run) or check_path_in_cachix(
        store_path, run=run
    )


def default_cache_present_many(
    store_paths: Iterable[str],
    *,
    run: Callable[..., object] | None = None,
) -> frozenset[str]:
    """Return paths on gkze or hydra, gkze first, batched ``path-info --json``."""
    unique = tuple(dict.fromkeys(store_paths))
    if not unique:
        return frozenset()
    gkze = paths_on_store(unique, _GKZE_CACHE, run=run)
    rest = tuple(path for path in unique if path not in gkze)
    if not rest:
        return gkze
    return gkze | paths_on_store(rest, nixos_cache_store(), run=run)


def _eval_root_darwin_graphs(
    flake_root: Path,
    roots: Sequence[RootClosureManifestRoot],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> tuple[dict[str, dict[str, object]], dict[str, frozenset[str]]]:
    """Instantiate Darwin roots one at a time; graph eval is the plan-shards cost."""
    names = tuple(composed_root_name(root) for root in roots)

    def one(name: str) -> tuple[str, dict[str, object], frozenset[str]]:
        sys.stderr.write(f"plan-shards eval-graph start {name}\n")
        sys.stderr.flush()
        payload = eval_root_darwin_graph(flake_root, name, run=run)
        outputs = darwin_output_paths(payload)
        sys.stderr.write(f"plan-shards eval-graph done {name} outputs={len(outputs)}\n")
        sys.stderr.flush()
        return name, payload, outputs

    graphs: dict[str, dict[str, object]] = {}
    per_root: dict[str, frozenset[str]] = {}
    if not names:
        return graphs, per_root
    workers = min(DARWIN_GRAPH_EVAL_WORKERS, len(names))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for name, payload, outputs in pool.map(one, names):
            graphs[name] = payload
            per_root[name] = outputs
    return graphs, per_root


def _eval_darwin_package_graphs(
    flake_root: Path,
    names: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> dict[str, dict[str, object]]:
    """Instantiate Darwin rust-family packages one at a time."""
    graphs: dict[str, dict[str, object]] = {}
    for name in names:
        graphs[name] = eval_darwin_package_graph(flake_root, name, run=run)
    return graphs


def gitbutler_family_package_outputs(
    payloads: Sequence[Mapping[str, object]],
) -> frozenset[str]:
    """Return GitButler-family rust_* and safe FODs from package graphs."""
    paths: set[str] = set()
    for payload in payloads:
        for path in darwin_output_paths(payload):
            if is_gitbutler_family_store_path(path) or is_safe_rust_warmup_other(path):
                paths.add(path)
    return frozenset(paths)


def plan_darwin_warmup(
    flake_root: Path,
    *,
    manifest: RootClosureManifest,
    shards: tuple[ClosureShard, ...],
    run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    present: Callable[[str], bool] | None = None,
    threshold: int = MAX_SHARD_LOCAL_BUILDS,
    package_graphs: Mapping[str, Mapping[str, object]] | None = None,
) -> WarmupPlan:
    """Plan the missing shared Darwin outputs and fail closed on huge remainders."""
    graphs, per_root = _eval_root_darwin_graphs(
        flake_root, darwin_roots(manifest), run=run
    )
    all_outputs = frozenset().union(*per_root.values()) if per_root else frozenset()
    checker = present if present is not None else default_cache_present
    sys.stderr.write(f"plan-shards path-info start outputs={len(all_outputs)}\n")
    sys.stderr.flush()
    substitutable = substitutable_paths(all_outputs, present=present)
    sys.stderr.write(
        f"plan-shards path-info done present={len(substitutable)} "
        f"outputs={len(all_outputs)}\n"
    )
    sys.stderr.flush()
    missing_shared = intersect_missing(per_root, substitutable)
    warmup = frozenset(
        path for path in missing_shared if is_planned_rust_warmup_output(path)
    )
    # 38130420607: rust_* peel left 1724/1733 shared non-rust in both
    # root shards (rust_*=0). 38146388927: all-root intersection then
    # left remaining=829/836 because darwin-system-only shared (3 of 4
    # roots) stayed in both shards. Two-plus is the cascade; one-root
    # leaves stay in remaining and still face the 400 gate.
    cascade = missing_shared - warmup
    extra_payloads: tuple[Mapping[str, object], ...] = ()
    if per_root:
        extra_graphs = (
            package_graphs
            if package_graphs is not None
            else _eval_darwin_package_graphs(
                flake_root, DARWIN_RUST_FAMILY_PACKAGES, run=run
            )
        )
        extra_payloads = tuple(extra_graphs.values())
        family = gitbutler_family_package_outputs(extra_payloads)
        # Family rust_* stay in rust-warmup even when path-info says
        # they are cached (38867194218). Safe FODs stay cache-aware.
        family_rust = frozenset(
            path for path in family if is_gitbutler_family_store_path(path)
        )
        family_fods = frozenset(
            path
            for path in family
            if is_safe_rust_warmup_other(path) and not checker(path)
        )
        warmup = warmup | family_rust | family_fods
    payloads = (*graphs.values(), *extra_payloads)
    rust_layers = rust_warmup_layers(warmup, payloads)
    cascade_layers = cascade_warmup_layers(cascade, payloads)
    output_drvs = warmup_output_drvs(warmup | cascade, payloads)
    covered = warmup | cascade
    reports: list[ShardLocalBuildReport] = []
    leftover_by_shard: list[frozenset[str]] = []
    stats: dict[str, RootWarmupStats] = {}
    for name, outputs in per_root.items():
        missing = outputs - substitutable
        remaining = missing - covered
        stats[name] = RootWarmupStats(
            outputs=len(outputs),
            missing=len(missing),
            warmup=len(missing & warmup),
            remaining=len(remaining),
        )
    for shard in shards:
        remaining = shard_remaining_outputs(
            shard, per_root, substitutable=substitutable, warmup=covered
        )
        leftover_by_shard.append(remaining)
        reports.append(
            ShardLocalBuildReport(
                shard=shard.shard,
                roots=shard.roots,
                remaining=len(remaining),
                remaining_rust_crates=sum(
                    1 for path in remaining if is_crate2nix_rust_output(path)
                ),
            )
        )
    try:
        assert_local_build_threshold(reports, threshold=threshold)
    except WarmupError as error:
        leftover = next(
            (
                leftover_by_shard[index]
                for index, report in enumerate(reports)
                if report.remaining > threshold
            ),
            frozenset(),
        )
        sample = ", ".join(sorted(cascade)[:8])
        remaining_sample = ", ".join(sorted(leftover)[:8])
        msg = (
            f"{error} cascade={len(cascade)} rust_warmup={len(warmup)} "
            f"cascade_sample={sample} remaining_sample={remaining_sample}"
        )
        raise WarmupError(msg) from error
    ordered = tuple(sorted(warmup))
    ordered_cascade = tuple(sorted(cascade))
    return WarmupPlan(
        schemaVersion=1,
        system=_DARWIN_SYSTEM,
        substituters=(_NIXOS_CACHE, _GKZE_CACHE),
        warmupOutputs=ordered,
        rustLayers=rust_layers,
        cascadeOutputs=ordered_cascade,
        cascadeLayers=cascade_layers,
        outputDrvs=output_drvs,
        perRoot=stats,
        shards=tuple(reports),
        notes=(
            "Outputs shared by two or more Darwin roots and absent from "
            "cache.nixos.org and gkze.cachix.org. rust_* and safe FODs go "
            "to rust-warmup. The remaining shared non-rust cascade "
            "(38130420607 left the all-root set in the root shards at "
            "remaining=1724/1733; 38146388927 left darwin-only shared at "
            "remaining=829/836) is five macos-15 cascade-warmup slots, "
            "one drv at a time, skip-if-in-gkze, pushed to gkze. "
            "One-root leaves stay in remaining after both peels. "
            "38867194218: also peel rust_gitbutler-* / rust_but-* / "
            "rust_sqlx-* / rust_gix-* from packages.aarch64-darwin.gitbutler "
            "even when two-plus is empty or path-info says cached. "
            f"warmup={len(ordered)} rustLayers={len(rust_layers)} "
            f"cascade={len(ordered_cascade)} cascadeLayers={len(cascade_layers)} "
            f"slots={RUST_WARMUP_SLOTS} cascadeSlots={CASCADE_WARMUP_SLOTS} "
            f"threshold={threshold} "
            "(provisional 2-wide root shards; revisit 4-wide vs 2-wide by "
            "bytes written and update-runtime after this warmup lands)."
        ),
    )


def write_warmup_plan(path: Path, plan: WarmupPlan) -> None:
    """Write the packages-job warmup list next to the shard matrix."""
    atomic_write_text(
        path,
        plan.model_dump_json(by_alias=True, indent=2) + "\n",
        mkdir=True,
    )


def load_warmup_plan(path: Path) -> WarmupPlan:
    """Load a warmup plan or fail closed."""
    try:
        return WarmupPlan.model_validate_json(path.read_bytes())
    except (OSError, ValueError) as error:
        msg = f"invalid warmup plan: {path}"
        raise WarmupError(msg) from error


def _store_output_rest(path: str) -> str:
    """Return the store basename after the 32-char hash, or the basename."""
    name = path.rstrip("/").rsplit("/", 1)[-1]
    _digest, separator, rest = name.partition("-")
    return rest if separator else name


def is_named_rust_crate_store_path(path: str, crate: str) -> bool:
    """Return whether *path* is crate2nix ``rust_<crate>-<version>``, not a sibling.

    ``rust_language_models-`` does not match ``rust_language_models_cloud-``.
    ``rust_zed-1.`` does not match ``rust_zed-font-kit`` or ``rust_zed_actions``.
    """
    rest = _store_output_rest(path)
    prefix = f"rust_{crate}-"
    if not rest.startswith(prefix):
        return False
    after = rest[len(prefix) :]
    return bool(after) and after[0].isdigit()


def is_rust_agent_ui_store_path(path: str) -> bool:
    """Return whether *path* is a crate2nix ``rust_agent_ui`` output or drv."""
    return is_named_rust_crate_store_path(path, "agent_ui")


def is_rust_language_models_store_path(path: str) -> bool:
    """Return whether *path* is crate2nix ``rust_language_models``, not ``_cloud``."""
    return is_named_rust_crate_store_path(path, "language_models")


def is_rust_zed_store_path(path: str) -> bool:
    """Return whether *path* is crate2nix ``rust_zed-<version>``, not ``zed_*``."""
    return is_named_rust_crate_store_path(path, "zed")


def is_zed_editor_nightly_store_path(path: str) -> bool:
    """Return whether *path* is the Zed nightly package, not a crate ``-src``."""
    rest = _store_output_rest(path)
    return rest.startswith("zed-editor-nightly-") and "-src" not in rest


# Cargo.nix crates that depend on ``extension_host`` plus ``title_bar``
# (depends on ``recent_projects``; rust_zed --externs it). rust_zed is the
# leaf and is realized after this set is compiled on the same runner.
EXTENSION_HOST_MEMBER_CRATES = (
    "activity_indicator",
    "agent_ui",
    "extension_host",
    "extensions_ui",
    "feedback",
    "language_models",
    "recent_projects",
    "remote_server",
    "settings_ui",
    "title_bar",
)


def is_extension_host_family_store_path(path: str) -> bool:
    """Return whether *path* is an ``extension_host`` SVH-family rust_* crate."""
    return any(
        is_named_rust_crate_store_path(path, crate)
        for crate in EXTENSION_HOST_MEMBER_CRATES
    )


# #1269 slot 3: rust_settings compiled locally against Cachix
# rust_settings_content / _json / _macros and E0463'd (nixpkgs#482646).
SETTINGS_MEMBER_CRATES = (
    "settings",
    "settings_content",
    "settings_json",
    "settings_macros",
)

# rust_settings_json --externs these. Canary 114295702356 substituted
# rust_tree-sitter then E0463'd with the rlib on the rustc line.
# 38096149800: local tree-sitter E0463'd with Cachix
# rust_wasmtime-c-api-impl on the rustc --extern line.
# 38103781962: local extension_host E0463'd with Cachix
# rust_wasmtime-wasi on the rustc --extern line. wasmtime-wasi
# --externs wasmtime-wasi-io (Cargo.nix); force-local both.
TREE_SITTER_MEMBER_CRATES = (
    "tree-sitter",
    "tree-sitter-json",
    "wasmtime-c-api-impl",
    "wasmtime-c-api",
    "wasmtime",
    "wasmtime-wasi",
    "wasmtime-wasi-io",
)


def is_settings_family_store_path(path: str) -> bool:
    """Return whether *path* is a rust_settings SVH-family crate."""
    return any(
        is_named_rust_crate_store_path(path, crate) for crate in SETTINGS_MEMBER_CRATES
    )


def is_tree_sitter_family_store_path(path: str) -> bool:
    """Return whether *path* is rust_tree-sitter or its wasmtime intern.

    Includes wasmtime-wasi / wasmtime-wasi-io so extension_host and
    wasmtime-wasi do not compile locally against Cachix rlibs.
    """
    return any(
        is_named_rust_crate_store_path(path, crate)
        for crate in TREE_SITTER_MEMBER_CRATES
    )


def partition_settings_family_drvs(
    drvs: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split rust_settings* out of others so they force-local together."""
    others: list[str] = []
    settings: list[str] = []
    for drv in dict.fromkeys(drvs):
        if is_settings_family_store_path(drv):
            settings.append(drv)
        else:
            others.append(drv)
    return tuple(others), tuple(settings)


def is_crane_dep_store_path(path: str) -> bool:
    """Return whether *path* is a crates.io rust_* crane would vendor first.

    Workspace rust_*-0.1.0, settings, extension_host, and zed leaves stay
    in the SVH family. crates.io rust_* (serde-1.0.x, lsp-types-0.95.1)
    populate the store before force-local so --no-substitute does not
    rebuild them.
    """
    return is_crate2nix_rust_output(path) and not is_svh_sensitive_store_path(path)


def partition_crane_dep_drvs(
    drvs: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Peel crates.io rust_* out of others so they realize before SVH family."""
    others: list[str] = []
    deps: list[str] = []
    for drv in dict.fromkeys(drvs):
        if is_crane_dep_store_path(drv):
            deps.append(drv)
        else:
            others.append(drv)
    return tuple(others), tuple(deps)


def partition_svh_sensitive_drvs(
    drvs: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split SVH-sensitive rust_* out of others so they force-local first.

    38121661811 slots 1/2 ``nix build`` rust_feature_flags /
    rust_git_hosting_providers (workspace 0.1.0) which then compiled
    rust_settings against Cachix content/json and fatal'd Cannot build.
    """
    others: list[str] = []
    sensitive: list[str] = []
    for drv in dict.fromkeys(drvs):
        if is_svh_sensitive_store_path(drv):
            sensitive.append(drv)
        else:
            others.append(drv)
    return tuple(others), tuple(sensitive)


def settings_family_warmup_outputs(layers: Sequence[Sequence[str]]) -> tuple[str, ...]:
    """Return rust_settings-family outputs from every warmup layer."""
    return _warmup_outputs_matching(layers, is_settings_family_store_path)


def zed_family_warmup_outputs(layers: Sequence[Sequence[str]]) -> tuple[str, ...]:
    """Return the atomic Zed rust_* family from every warmup layer.

    rust_zed, zed-editor-nightly, settings*, tree-sitter,
    rust_*-0.1.0 workspace members, and extension_host* share one
    rustc intern / SVH generation. skip_cached on a subset is the mix
    that produced #1269 rust_settings E0463 against Cachix content/json
    and #1270 settings_json vs tree_sitter.
    """
    return _warmup_outputs_matching(layers, is_svh_sensitive_store_path)


def zed_family_cachix_presence(
    paths: Sequence[str],
    *,
    present: Callable[[str], bool],
) -> Literal["all", "none", "mixed"]:
    """Return whether gkze has the whole Zed family, none of it, or a mix."""
    unique = tuple(dict.fromkeys(paths))
    if not unique:
        return "none"
    hits = tuple(path for path in unique if present(path))
    if not hits:
        return "none"
    if len(hits) == len(unique):
        return "all"
    return "mixed"


def zed_family_realize_policy(
    paths: Sequence[str],
    *,
    present: Callable[[str], bool],
    rustc_ids: Sequence[str] = (),
) -> Literal["force-local", "substitute"]:
    """Choose one family policy: never substitute a Cachix subset.

    ``all`` plus one rustc generation substitutes. ``none`` or mixed
    Cachix presence force-locals the entire family. Distinct rustc
    hashes among family drvs fail closed.
    """
    ids = tuple(dict.fromkeys(rustc_ids))
    if len(ids) > 1:
        msg = f"zed family mixes rustc generations: {', '.join(ids)}"
        raise WarmupError(msg)
    presence = zed_family_cachix_presence(paths, present=present)
    if presence == "all":
        return "substitute"
    return "force-local"


def assert_zed_family_realize_set(
    family: Sequence[str], realize: Sequence[str]
) -> None:
    """Fail if a realize set would compile some family crates and substitute others."""
    wanted = tuple(dict.fromkeys(family))
    chosen = tuple(dict.fromkeys(realize))
    if not chosen or chosen == wanted:
        return
    msg = "zed family realize set mixes generations"
    raise WarmupError(msg)


def rustc_generation_ids(
    drvs: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return one rustc generation key per distinct compiler ``.drv``.

    Hosted crate2nix references both ``rustc-wrapper-1.98.1`` and
    ``rust-default-1.98.1``. Those are one toolchain (38086011709
    false-mixed them). Prefer wrapper, then rustc, then rust-default.
    Distinct wrapper hashes still fail closed.
    """
    wrappers: list[str] = []
    rustcs: list[str] = []
    defaults: list[str] = []
    for path in _query_drv_graph(drvs, "references", run=run):
        rest = _store_output_rest(path).removesuffix(".drv")
        if rest.startswith("rustc-wrapper-"):
            wrappers.append(path)
        elif rest.startswith("rustc-"):
            rustcs.append(path)
        elif rest.startswith("rust-default-"):
            defaults.append(path)
    return tuple(dict.fromkeys(wrappers or rustcs or defaults))


def is_rust_extension_host_store_path(path: str) -> bool:
    """Return whether *path* is crate2nix ``rust_extension_host``."""
    return is_named_rust_crate_store_path(path, "extension_host")


def partition_agent_ui_drvs(
    drvs: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split warmup drvs so ``rust_agent_ui`` can realize last with ``-L``."""
    others: list[str] = []
    agent_ui: list[str] = []
    for drv in dict.fromkeys(drvs):
        if is_rust_agent_ui_store_path(drv):
            agent_ui.append(drv)
        else:
            others.append(drv)
    return tuple(others), tuple(agent_ui)


def partition_rust_zed_drvs(
    drvs: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split warmup drvs so ``rust_zed`` realizes after its extension_host family."""
    others: list[str] = []
    rust_zed: list[str] = []
    for drv in dict.fromkeys(drvs):
        if is_rust_zed_store_path(drv):
            rust_zed.append(drv)
        else:
            others.append(drv)
    return tuple(others), tuple(rust_zed)


def partition_zed_editor_nightly_drvs(
    drvs: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split warmup drvs so the nightly package cannot build rust_zed early."""
    others: list[str] = []
    nightly: list[str] = []
    for drv in dict.fromkeys(drvs):
        if is_zed_editor_nightly_store_path(drv):
            nightly.append(drv)
        else:
            others.append(drv)
    return tuple(others), tuple(nightly)


def is_zed_workspace_crate_store_path(path: str) -> bool:
    """Return whether *path* is a Zed workspace ``rust_*-0.1.0`` crate.

    38086720924 put ``rust_git_ui_core`` in compiler substitutes. Its
    --max-jobs 0 closure was the 37 workspace members and tripped the
    32-drv gate before force-local. crates.io rust_* keep their
    published versions (serde-1.0.229, lsp-types-0.95.1). The digit
    guard keeps ``rust_foo-10.1.0`` from matching ``-0.1.0``.
    """
    rest = _store_output_rest(path).removesuffix(".drv")
    if not rest.startswith("rust_"):
        return False
    marker = "-0.1.0"
    index = rest.find(marker)
    if index <= 0 or rest[index - 1].isdigit():
        return False
    version = rest[index + len(marker) :]
    return version == "" or version.startswith("-")


def is_svh_sensitive_store_path(path: str) -> bool:
    """Return whether substituting *path* can mix extension_host-family SVHs."""
    return (
        is_extension_host_family_store_path(path)
        or is_settings_family_store_path(path)
        or is_tree_sitter_family_store_path(path)
        or is_zed_workspace_crate_store_path(path)
        or is_rust_zed_store_path(path)
        or is_zed_editor_nightly_store_path(path)
        or is_rust_agent_ui_store_path(path)
    )


def _warmup_outputs_matching(
    layers: Sequence[Sequence[str]],
    predicate: Callable[[str], bool],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(path for layer in layers for path in layer if predicate(path))
    )


def language_models_warmup_outputs(layers: Sequence[Sequence[str]]) -> tuple[str, ...]:
    """Return rust_language_models outputs from every warmup layer, first-seen."""
    return _warmup_outputs_matching(layers, is_rust_language_models_store_path)


def gitbutler_family_warmup_outputs(
    layers: Sequence[Sequence[str]],
) -> tuple[str, ...]:
    """Return GitButler-family rust_* from every warmup layer, first-seen."""
    return _warmup_outputs_matching(layers, is_gitbutler_family_store_path)


def gitbutler_family_slot_from_package(
    flake_root: Path,
    slot: int,
    *,
    run: _StoreRun | None = None,
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Stripe GitButler-family rust_* from the Darwin package graph.

    38867194218 rust-warmup no-op'd because the Linux two-plus plan had
    warmupOutputs=[]. Darwin instantiate of packages.gitbutler is the
    realize-time canary when the planner peel is empty.
    """
    payload = eval_darwin_package_graph(flake_root, "gitbutler", run=run)
    outputs = gitbutler_family_package_outputs((payload,))
    layers = rust_warmup_layers(outputs, (payload,))
    return slot_warmup_paths(layers, slot), warmup_output_drvs(outputs, (payload,))


def extension_host_family_warmup_outputs(
    layers: Sequence[Sequence[str]],
) -> tuple[str, ...]:
    """Return extension_host-family outputs from every warmup layer, first-seen."""
    return _warmup_outputs_matching(layers, is_extension_host_family_store_path)


def _query_drv_graph(
    parent_drvs: Sequence[str],
    operation: Literal["references", "requisites"],
    *,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return ``.drv`` ``--references`` or ``--requisites`` of *parent_drvs*."""
    runner = subprocess.run if run is None else run
    found: dict[str, None] = {}
    for drv in dict.fromkeys(parent_drvs):
        result = runner(
            ["nix-store", "--query", f"--{operation}", drv],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            detail = _store_command_detail(
                result, fallback=f"nix-store --query --{operation} failed"
            )
            msg = f"failed to query rust crate inputs of {drv}: {detail}"
            raise WarmupError(msg)
        stdout = result.stdout
        text = stdout.decode() if isinstance(stdout, bytes) else stdout
        for line in text.splitlines():
            path = line.strip()
            if path.endswith(".drv"):
                found.setdefault(path, None)
    return tuple(found)


def drv_input_references(
    parent_drvs: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return direct ``.drv`` references of *parent_drvs* after import."""
    return _query_drv_graph(parent_drvs, "references", run=run)


def drv_input_requisites(
    parent_drvs: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return the full ``.drv`` closure of *parent_drvs* after import."""
    return _query_drv_graph(parent_drvs, "requisites", run=run)


def rust_crate_input_drvs(
    parent_drvs: Sequence[str],
    crates: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return named crate2nix ``.drv`` inputs of *parent_drvs* after import."""
    wanted = tuple(crates)
    return tuple(
        path
        for path in drv_input_references(parent_drvs, run=run)
        if any(is_named_rust_crate_store_path(path, crate) for crate in wanted)
    )


def rust_svh_input_drvs(
    parent_drvs: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return SVH-sensitive ``.drv`` inputs of *parent_drvs* after import.

    38151215158 slot 1 compiled rust_zed locally against Cachix
    ``rust_collab_ui-0.1.0`` (skip_cached dropped it from the missing
    stripe; family merge only pulled ``EXTENSION_HOST_MEMBER_CRATES``)
    and E0463'd (nixpkgs#482646). Workspace ``rust_*-0.1.0`` inputs
    must force-local with rust_zed, not only named extension_host
    members. rust_zed / nightly stay out so the logged leaf realize
    owns them.
    """
    return tuple(
        path
        for path in drv_input_references(parent_drvs, run=run)
        if is_svh_sensitive_store_path(path)
        and not is_rust_zed_store_path(path)
        and not is_zed_editor_nightly_store_path(path)
    )


def rust_zed_member_warmup_outputs(
    layers: Sequence[Sequence[str]],
) -> tuple[str, ...]:
    """Return planned SVH-sensitive rust_* that must compile before rust_zed.

    Includes cached workspace crates still listed in ``rust_layers``.
    rust_zed / nightly are realized after this set.
    """
    return tuple(
        path
        for path in zed_family_warmup_outputs(layers)
        if not is_rust_zed_store_path(path)
        and not is_zed_editor_nightly_store_path(path)
    )


def rust_compile_input_drvs(
    parent_drvs: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return every crate2nix ``rust_*`` ``.drv`` the family intern's.

    #1270 canary 114295702356: ``--max-jobs 0`` substituted
    ``rust_tree-sitter`` as a compiler input, then force-local
    ``rust_settings_json`` E0463'd with that rlib on the rustc line
    (nixpkgs#482646). Workspace-only family is not enough.
    """
    return tuple(
        path
        for path in drv_input_references(parent_drvs, run=run)
        if is_crate2nix_rust_output(path)
    )


def compiler_input_drvs(
    parent_drvs: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return the non-rust ``.drv`` closure that must be substituted first.

    ``#1263`` used direct ``--references`` plus ``--fallback``, then
    ``--no-substitute`` on ``extension_host`` rebuilt 406 bootstrap
    drvs (bmake 404). Query ``--requisites`` and realize them with
    ``--max-jobs 0`` so only the SVH-sensitive rust_* compile.
    Excluding every rust_* (eab3998a) made extension_host --no-substitute
    will-be-built 682 on 38083709508. Third-party rust_* stay here;
    tree-sitter and rust_*-0.1.0 workspace crates are SVH-sensitive.
    """
    return tuple(
        path
        for path in drv_input_requisites(parent_drvs, run=run)
        if not is_svh_sensitive_store_path(path)
    )


_SOURCE_FETCH_ARCHIVES = (
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tar.zst",
    ".tgz",
    ".zip",
    ".crate",
)


def is_source_fetch_store_path(path: str) -> bool:
    """Return whether *path* is a fetchurl/FOD archive, not a compile.

    ``#1264`` ``--max-jobs 0`` refused ``coreaudio-rs-0.14.2.tar.gz.drv``
    because a cache miss on a crate tarball is a download, not bootstrap.
    39234076105 slot 4: ``rusty_v8-dbb64c2`` is fetchgit of Chromium
    submodules, not crate2nix ``rust_v8-goose``. Plan leftover and Darwin
    retry must treat it as a download.
    """
    name = _store_output_rest(path).removesuffix(".drv")
    if name.endswith(_SOURCE_FETCH_ARCHIVES):
        return True
    if name == "source" or name.endswith(("-src", "-source")):
        return True
    return name.startswith("rusty_v8-")


# Known 1-drv Unix helpers that miss cache.nixos.org on the CVE-patched
# stdenv. The tarball substitutes; the package drv compiles locally on
# the first pass. Unknown helpers still retry after ``--max-jobs 0``
# Cannot-build; rustc/stdenv stay substitute-only.
# #1270 canary: patchutils, then pbzx, then cpio (job 114291903959).
_COMPILER_LOCAL_HELPER_PREFIXES = (
    "patchutils-",
    "pbzx-",
    "xar-",
    "cpio-",
)
_COMPILER_MUST_SUBSTITUTE_PREFIXES = (
    "rustc-",
    "stdenv-",
    "clang-",
    "gcc-",
    "llvm-",
    "compiler-rt-",
    "bmake-",
)
_MIRROR_DOWNLOAD = re.compile(r"cannot download (\S+) from any mirror", re.IGNORECASE)


def _compiler_must_substitute_name(name: str) -> bool:
    """Return whether *name* is a rustc/clang/stdenv bootstrap, not clang-sys.

    38344741255 slot 1: ``clang-sys-1.9.1.tar.gz`` starts with ``clang-``
    so the prefix deny treated a crates.io FOD as a compiler tarball
    and WarmupFatalError'd the mirror line before DNS retry. pin-project
    and libloading had already substituted from gkze. Versioned compiler
    sources (``rustc-1.98.1``, ``clang-19.1.7.src``, ``bmake-20260313``)
    still match; crate suffixes after the prefix (``sys``, ``hash``)
    do not. ``stdenv-*`` has no crate collision and stays must-substitute.
    """
    for prefix in _COMPILER_MUST_SUBSTITUTE_PREFIXES:
        if not name.startswith(prefix):
            continue
        rest = name.removeprefix(prefix)
        if prefix == "stdenv-" or (rest and rest[0].isdigit()):
            return True
    return False


def is_source_fetch_transport_error(text: str) -> bool:
    """Return whether Nix/git streamed a retryable source-fetch HTTP failure.

    39234076105 slot 4: chromium.googlesource.com HTTP 503 during a
    rusty_v8 submodule clone, then hash mismatch on the tainted tree.
    Delegates to the shared updater/FOD classifier so git 503, HTTP 5xx,
    and Zig ``BadHttpStatus`` retry through one owner.
    """
    return is_retryable_nix_network_failure(stdout="", stderr=text)


def is_source_fetch_mirror_line(line: str) -> bool:
    """Return whether a mirror-fail line is a crate/source FOD.

    38307411499 slots 2/4 printed ``cannot download pin-project-1.1.13.tar.gz
    from any mirror`` after curl (6) on static.crates.io. That is the
    same source-FOD miss as Cannot-build, not a rustc/bmake bootstrap
    mirror failure. 38344741255 slot 1: ``clang-sys-1.9.1.tar.gz`` is
    the same class; a bare ``clang-`` prefix must not hide it.
    """
    match = _MIRROR_DOWNLOAD.search(line)
    if match is None:
        return False
    name = match.group(1)
    if _compiler_must_substitute_name(name):
        return False
    if name.endswith(_SOURCE_FETCH_ARCHIVES):
        return True
    stem = name.rsplit(".", 1)[0]
    return stem == "source" or stem.endswith(("-src", "-source"))


# rust-overlay rustc-1.98.1-aarch64-apple-darwin is a tarball unpack
# (38128207590 canary: zed.cachix.org fetched the tar.xz, then
# --max-jobs 0 refused the 1-drv unpack). rustc-1.98.1 without a
# target triple stays must-substitute so nixpkgs rustc never compiles.
_RUST_OVERLAY_TARGET = re.compile(
    r"-(?:aarch64|x86_64)-(?:apple-darwin|unknown-linux-gnu)$"
)


def is_compiler_local_helper_store_path(path: str) -> bool:
    """Return whether a compiler input may compile locally (1-drv helper)."""
    if is_source_fetch_store_path(path):
        return False
    rest = _store_output_rest(path).removesuffix(".drv")
    return rest.startswith(_COMPILER_LOCAL_HELPER_PREFIXES)


def is_rust_overlay_binary_component(path: str) -> bool:
    """Return whether *path* is a rust-overlay target tarball unpack."""
    rest = _store_output_rest(path).removesuffix(".drv")
    return bool(_RUST_OVERLAY_TARGET.search(rest))


def is_compiler_must_substitute_store_path(path: str) -> bool:
    """Return whether a compiler input must stay ``--max-jobs 0``."""
    if is_rust_overlay_binary_component(path):
        return False
    rest = _store_output_rest(path).removesuffix(".drv")
    return _compiler_must_substitute_name(rest)


# rust-warmup others may realize these without compiling a crate graph.
# #1268 slot 4: goose-cli-1.51.0 = 1759 will-be-built; crane-utils = 1.
# #1268 slot 3: vendor-cargo-deps = 521; cargo-package-* and but = 1.
# #1268 slot 0: goose-cli-v8-native = 1 (toolchain already in gkze).
_SAFE_WARMUP_OTHER_PREFIXES = (
    "cargo-package-",
    "cargo-src-",
    "crane-utils-",
    "goose-cli-v8-",
)


def is_safe_rust_warmup_other(path: str) -> bool:
    """Return whether rust-warmup may realize this non-rust_* path.

    rustc-1.98.1 substituted from cache.nixos.org on #1268. The explosion
    was realizing leaf/vendor umbrellas whose crate graphs were never in
    gkze, not a rust-overlay hash mismatch for those slots.
    """
    if is_source_fetch_store_path(path):
        return True
    rest = _store_output_rest(path).removesuffix(".drv")
    return rest.startswith(_SAFE_WARMUP_OTHER_PREFIXES)


def partition_rust_warmup_others(
    drvs: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split rust-warmup others into safe FODs/helpers vs deferred leaves."""
    safe: list[str] = []
    deferred: list[str] = []
    for drv in dict.fromkeys(drvs):
        if is_crate2nix_rust_output(drv) or is_safe_rust_warmup_other(drv):
            safe.append(drv)
        else:
            deferred.append(drv)
    return tuple(safe), tuple(deferred)


def classify_slot_warmup_will_be_built(
    paths: Sequence[str],
    *,
    will_be_built: Mapping[str, int],
) -> tuple[tuple[tuple[str, int], ...], tuple[tuple[str, int], ...]]:
    """Pair slot paths with dry-run will-be-built counts, split safe/deferred.

    Hosted Darwin logs are the count oracle: this Linux workspace cannot
    ``nix build`` aarch64-darwin closures. Tests pass the #1268 counts.
    """
    safe, deferred = partition_rust_warmup_others(paths)
    return (
        tuple((path, will_be_built[path]) for path in safe),
        tuple((path, will_be_built[path]) for path in deferred),
    )


def partition_compiler_input_drvs(
    drvs: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split compiler requisites into substitute-only vs local-ok helpers/FODs."""
    substitute: list[str] = []
    fetches: list[str] = []
    for drv in dict.fromkeys(drvs):
        if is_source_fetch_store_path(drv) or is_compiler_local_helper_store_path(drv):
            fetches.append(drv)
        else:
            substitute.append(drv)
    return tuple(substitute), tuple(fetches)


def is_deferred_compiler_rust_drv(path: str) -> bool:
    """Return whether a compiler-input rust_* may compile after rustc is local.

    rustc/bmake/stdenv stay ``--max-jobs 0``. crates.io rust_* (cranelift,
    serde, gix) substitute-or-compile under ``--keep-going --fallback``.
    """
    return is_crate2nix_rust_output(
        path
    ) and not is_compiler_must_substitute_store_path(path)


def partition_compiler_substitute_probe_drvs(
    drvs: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Peel crate2nix rust_* out of mixed ``--max-jobs 0`` compiler substitutes.

    ``validate_derivation_requests`` batches 128, then when both bisection
    halves fail it individually ``--max-jobs 0``s every drv. 39387963366
    rust canary spent 1069 one-drv probes and slot 2 spent 2441 for ~119
    rustc compiles, because cranelift/serde misses sat in the same window
    as rustc. Fail-closed-probe rustc/stdenv/helpers only; rust_* leaves
    keep-going after the toolchain is on the store.
    """
    probe: list[str] = []
    rust_leaves: list[str] = []
    for drv in dict.fromkeys(drvs):
        if is_deferred_compiler_rust_drv(drv):
            rust_leaves.append(drv)
        else:
            probe.append(drv)
    return tuple(probe), tuple(rust_leaves)


SOURCE_FETCH_DNS_HOSTS = ("github.com", "static.crates.io")
SOURCE_FETCH_RETRY_BACKOFF_SECONDS = 15.0


def rust_warmup_output_drvs(plan: WarmupPlan) -> tuple[str, ...]:
    """Return planned rust-warmup output ``.drv`` paths, first-seen order."""
    return tuple(
        drv
        for path, drv in plan.output_drvs.items()
        if is_planned_rust_warmup_output(path)
    )


def source_fetch_input_drvs(
    parent_drvs: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return source FODs in *parent_drvs*' non-SVH compiler requisites."""
    _substitute, fetches = partition_compiler_input_drvs(
        compiler_input_drvs(parent_drvs, run=run)
    )
    return tuple(drv for drv in fetches if is_source_fetch_store_path(drv))


def source_fetch_failure_drv(failure: DerivationValidationFailure) -> str:
    """Return the source-FOD ``.drv`` from a failure installable or message.

    39234076105 slot 4 realized ``rust_v8-goose``; the hash-mismatch
    line named ``rusty_v8-dbb64c2.drv``. Retry the FOD, not the crate.
    """
    for candidate in (
        failure.installable.removesuffix("^*"),
        command_store_drv(failure.message),
    ):
        if is_source_fetch_store_path(candidate):
            return candidate
    return ""


def source_fetch_retry_drvs(
    failures: Sequence[DerivationValidationFailure],
) -> tuple[str, ...]:
    """Return streamed FOD Cannot-build installables; refuse rustc / rust_*."""
    retry: list[str] = []
    for failure in failures:
        drv = source_fetch_failure_drv(failure)
        if not drv:
            continue
        if (
            "Cannot build" not in failure.message
            and "from any mirror" not in failure.message
            and not is_fixed_output_hash_mismatch(failure.message)
        ):
            continue
        retry.append(drv)
    return tuple(dict.fromkeys(retry))


def source_fetch_leftover_drvs(
    failures: Sequence[DerivationValidationFailure],
) -> tuple[str, ...]:
    """Return source-FOD installables that missed substitute-only publish."""
    leftover: list[str] = []
    for failure in failures:
        drv = source_fetch_failure_drv(failure)
        if drv:
            leftover.append(drv)
    return tuple(dict.fromkeys(leftover))


def source_fetch_network_ready(
    *,
    resolve: Callable[[str, int], object] | None = None,
) -> bool:
    """Return whether github.com and static.crates.io resolve."""
    lookup = socket.getaddrinfo if resolve is None else resolve
    try:
        for host in SOURCE_FETCH_DNS_HOSTS:
            lookup(host, 443)
    except OSError:
        return False
    return True


def wait_for_source_fetch_network(
    *,
    resolve: Callable[[str, int], object] | None = None,
    sleep: Callable[[float], None] | None = None,
    backoff: float = SOURCE_FETCH_RETRY_BACKOFF_SECONDS,
) -> bool:
    """Sleep, then prove source-fetch DNS before retrying a FOD."""
    waiter = time.sleep if sleep is None else sleep
    waiter(backoff)
    return source_fetch_network_ready(resolve=resolve)


def realize_source_fetch_drvs(
    drvs: Sequence[str],
    *,
    flake_root: Path,
    progress: ValidationProgress | None = None,
    resolve: Callable[[str, int], object] | None = None,
    sleep: Callable[[float], None] | None = None,
    realize: Callable[..., tuple[DerivationValidationFailure, ...]] | None = None,
) -> tuple[DerivationValidationFailure, ...]:
    """Realize source FODs; backoff and prove DNS once; fail closed after that.

    38258066093 slots 0/1 lost github.com / static.crates.io in the same
    minute after a 2h58m compile. An immediate retry hits that same
    dead network. A fetch that still fails must fail the slot so the
    parent rust_* never compiles against a missing source.
    """
    builder = realize_warmup_outputs if realize is None else realize
    ordered = tuple(dict.fromkeys(drvs))
    if not ordered:
        return ()
    failures = builder(ordered, flake_root=flake_root, progress=progress)
    retry = source_fetch_retry_drvs(failures)
    if not retry:
        return failures
    if not wait_for_source_fetch_network(resolve=resolve, sleep=sleep):
        return failures
    retried = builder(retry, flake_root=flake_root, progress=progress)
    stubborn = tuple(
        failure
        for failure in failures
        if failure.installable.removesuffix("^*") not in retry
    )
    return (*stubborn, *retried)


def source_fetch_publish_leftover_line(
    failures: Sequence[DerivationValidationFailure],
) -> str:
    """Return the planner leftover line; Darwin still fail-closes a needed miss."""
    sample = ", ".join(failure.installable for failure in failures[:4])
    return (
        f"source FOD publish leftover ({len(failures)}): {sample}. "
        "Darwin rust-warmup fetches misses after DNS-gated retry."
    )


_LOCAL_FETCH_SYSTEMS = frozenset({"builtin"})


def source_fetch_drv_systems(
    drvs: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> dict[str, str]:
    """Return each ``.drv``'s ``system`` from one ``nix derivation show``."""
    ordered = tuple(dict.fromkeys(drvs))
    if not ordered:
        return {}
    runner = subprocess.run if run is None else run
    result = runner(
        ["nix", "derivation", "show", *ordered],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = _store_command_detail(result, fallback="nix derivation show failed")
        msg = f"failed to read source FOD systems: {detail}"
        raise WarmupError(msg)
    stdout = result.stdout
    text = stdout.decode() if isinstance(stdout, bytes) else stdout
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        msg = "source FOD derivation show is not JSON"
        raise WarmupError(msg) from error
    derivations = payload.get("derivations") if isinstance(payload, dict) else None
    if not isinstance(derivations, dict):
        msg = "source FOD derivation show missing derivations"
        raise WarmupError(msg)
    systems: dict[str, str] = {}
    for key, drv in derivations.items():
        path = _absolute_store_path(key) if isinstance(key, str) else None
        system = drv.get("system") if isinstance(drv, dict) else None
        if path is not None and isinstance(system, str):
            systems[path] = system
    return systems


def unpublished_source_fetches(
    fods: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return source FODs with an output gkze does not already serve.

    39538011102 plan-shards spent ~31 min substituting 2284 FOD hits
    onto the planner; the flush then pushed 2225 paths in 2 s because
    gkze already had them. A gkze hit needs no publish. A path-info
    timeout is a miss, so it still substitutes as before.
    """
    ordered = tuple(dict.fromkeys(fods))
    if not ordered:
        return ()
    with ThreadPoolExecutor(max_workers=_SUBSTITUTER_WORKERS) as pool:
        outputs = dict(
            zip(
                ordered,
                pool.map(lambda drv: query_drv_outputs(drv, run=run), ordered),
                strict=True,
            )
        )
    published = paths_on_store(
        tuple(dict.fromkeys(path for paths in outputs.values() for path in paths)),
        _GKZE_CACHE,
        run=run,
    )
    return tuple(
        drv
        for drv, paths in outputs.items()
        if not paths or any(path not in published for path in paths)
    )


def locally_fetchable_source_drvs(
    drvs: Sequence[str],
    *,
    system: str,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return FODs this host can realize: ``builtin`` or *system*.

    39538011102: 2463 of 2464 rust-warmup FODs are ``aarch64-darwin``
    ``stdenv`` fetchurl. On x86_64-linux every one fails ``platform
    mismatch``; Darwin rust-warmup fetches them after its DNS gate.
    """
    ordered = tuple(dict.fromkeys(drvs))
    try:
        systems = source_fetch_drv_systems(ordered, run=run)
    except WarmupError as error:
        # Best-effort publish: an unreadable system keeps the old attempt.
        sys.stderr.write(f"source FOD systems unknown, fetching all: {error}\n")
        return ordered
    allowed = _LOCAL_FETCH_SYSTEMS | {system}
    return tuple(drv for drv in ordered if systems.get(drv) in allowed)


def _foreign_source_fetch_failure(drv: str, system: str) -> DerivationValidationFailure:
    return DerivationValidationFailure(
        source="root-warmup",
        installable=warmup_build_installable(drv),
        message=f"not fetched on {system}: derivation system cannot build here",
    )


def publish_warmup_source_fetches(
    plan: WarmupPlan,
    *,
    flake_root: Path,
    progress: ValidationProgress | None = None,
    run: _StoreRun | None = None,
    realize: Callable[..., tuple[DerivationValidationFailure, ...]] | None = None,
    system: str | None = None,
) -> tuple[DerivationValidationFailure, ...]:
    """Publish rust-warmup source FODs gkze lacks; never fail the plan.

    Compiled ``rust_*`` outputs do not retain source tarballs in their
    runtime closure. 38258066093 substituted ``rust_convert_case-0.11.0``
    from gkze, then had to fetch ``convert_case-0.11.0.tar.gz`` and
    sphinx ``source.drv`` from upstream. Push cache hits from the
    planner so warmup can substitute them from gkze.

    38296208698 then fail-closed plan-shards on 562 leftover crate2nix
    FODs after ~50 min of Linux fetch. ``--max-jobs 0 --keep-going``
    still pushes hits. Never fail-close the plan; Darwin still
    DNS-gates and fail-closes a needed miss.

    39538011102: FODs gkze already serves are skipped, and the local
    fetch pass only gets FODs this host can build (``builtin`` or the
    host system). Darwin-system misses are reported, not attempted.
    """
    fods = source_fetch_input_drvs(rust_warmup_output_drvs(plan), run=run)
    pending = unpublished_source_fetches(fods, run=run)
    sys.stderr.write(
        f"plan-shards source-fods total={len(fods)} unpublished={len(pending)}\n"
    )
    sys.stderr.flush()
    if not pending:
        return ()
    builder = realize_warmup_outputs if realize is None else realize
    failures = builder(
        pending,
        flake_root=flake_root,
        progress=progress,
        substitute_only=True,
        keep_going=True,
    )
    leftovers = source_fetch_leftover_drvs(failures)
    if leftovers:
        host = get_current_nix_platform() if system is None else system
        local = locally_fetchable_source_drvs(leftovers, system=host, run=run)
        foreign = tuple(
            _foreign_source_fetch_failure(drv, host)
            for drv in leftovers
            if drv not in local
        )
        fetched = (
            builder(local, flake_root=flake_root, progress=progress, keep_going=True)
            if local
            else ()
        )
        failures = (*fetched, *foreign)
    if failures:
        sys.stderr.write(source_fetch_publish_leftover_line(failures) + "\n")
        sys.stderr.flush()
    return failures


_DRV_PATH = re.compile(r"/nix/store/[0-9a-z]{32}-[^/\s']+\.drv")


def command_store_drv(command: str) -> str:
    """Return the first ``.drv`` store path in a Nix command, or empty."""
    match = _DRV_PATH.search(command)
    return match.group(0) if match else ""


def is_force_local_allowed_build(path: str) -> bool:
    """Return whether a ``--dry-run --no-substitute`` build may compile *path*."""
    if is_svh_sensitive_store_path(path):
        return True
    # 38092942461: deferred rust_lsp-types / rust_merman / rust_accesskit
    # compile as --no-substitute family deps after rustc already substituted.
    if is_crate2nix_rust_output(path) and not is_compiler_must_substitute_store_path(
        path
    ):
        return True
    rest = _store_output_rest(path)
    if "-src" not in rest:
        return False
    return (
        "zed-editor-nightly" in rest
        or "extension_host" in rest
        or any(crate in rest for crate in EXTENSION_HOST_MEMBER_CRATES)
    )


def force_local_dry_run_builds(
    drvs: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return ``.drv``s ``nix build --dry-run --no-substitute`` would compile."""
    runner = subprocess.run if run is None else run
    found: dict[str, None] = {}
    for drv in dict.fromkeys(drvs):
        result = runner(
            [
                "nix",
                "build",
                "--dry-run",
                "--no-link",
                "--no-substitute",
                warmup_build_installable(drv),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            detail = _store_command_detail(
                result, fallback="nix build --dry-run --no-substitute failed"
            )
            msg = f"failed to dry-run force-local {drv}: {detail}"
            raise WarmupError(msg)
        stdout = result.stdout
        stderr = result.stderr
        out = stdout.decode() if isinstance(stdout, bytes) else stdout
        err = stderr.decode() if isinstance(stderr, bytes) else stderr
        in_section = False
        for line in f"{out}\n{err}".splitlines():
            if "will be built:" in line:
                in_section = True
                continue
            if not in_section:
                continue
            match = _DRV_PATH.search(line)
            if match:
                found.setdefault(match.group(0), None)
                continue
            if line.strip() == "" or not line.lstrip().startswith("/nix/store/"):
                in_section = False
    return tuple(found)


def assert_force_local_dry_run(
    drvs: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> None:
    """Fail closed if force-local would compile stdenv or other non-family drvs."""
    extra = tuple(
        path
        for path in force_local_dry_run_builds(drvs, run=run)
        if not is_force_local_allowed_build(path)
    )
    if extra:
        sample = ", ".join(extra[:8])
        msg = f"force-local would compile non-family: {sample}"
        raise WarmupError(msg)


def language_models_input_drvs(
    agent_ui_drvs: Sequence[str],
    *,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return ``rust_language_models`` ``.drv`` inputs of *agent_ui_drvs*."""
    return rust_crate_input_drvs(agent_ui_drvs, ("language_models",), run=run)


def dry_run_will_be_built_count(
    drv: str,
    *,
    run: _StoreRun | None = None,
) -> int:
    """Return how many derivations ``nix build --dry-run`` would compile."""
    runner = subprocess.run if run is None else run
    result = runner(
        [
            "nix",
            "build",
            "--dry-run",
            "--no-link",
            warmup_build_installable(drv),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = _store_command_detail(result, fallback="nix build --dry-run failed")
        msg = f"failed to dry-run cascade {drv}: {detail}"
        raise WarmupError(msg)
    stdout = result.stdout
    stderr = result.stderr
    out = stdout.decode() if isinstance(stdout, bytes) else stdout
    err = stderr.decode() if isinstance(stderr, bytes) else stderr
    text = f"{out}\n{err}"
    match = _WILL_BE_BUILT.search(text)
    if match:
        return int(match.group(1))
    if _THIS_WILL_BE_BUILT.search(text):
        return 1
    return 0


def filter_cascade_realize_drvs(
    drvs: Sequence[str],
    *,
    run: _StoreRun | None = None,
    limit: int = MAX_SHARD_LOCAL_BUILDS,
) -> tuple[str, ...]:
    """Keep cascade items whose dry-run will-be-built stays under *limit*.

    38191092497 slot 0: ``linear-cli-jsr-manifest.tsv.drv^*`` printed
    1297 will-be-built after ~75m of 1-3 drv FODs. Fatal'ing the slot
    threw away the rest of the stripe. Root shards still own umbrellas.
    """
    if limit < 1:
        msg = "cascade will-be-built limit must be at least 1"
        raise WarmupError(msg)
    return tuple(
        drv
        for drv in dict.fromkeys(drvs)
        if dry_run_will_be_built_count(drv, run=run) <= limit
    )


def query_drv_outputs(
    drv: str,
    *,
    run: _StoreRun | None = None,
) -> tuple[str, ...]:
    """Return store output paths of one ``.drv``."""
    runner = subprocess.run if run is None else run
    result = runner(
        ["nix-store", "--query", "--outputs", drv],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = _store_command_detail(
            result, fallback="nix-store --query --outputs failed"
        )
        msg = f"failed to query outputs of {drv}: {detail}"
        raise WarmupError(msg)
    stdout = result.stdout
    text = stdout.decode() if isinstance(stdout, bytes) else stdout
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def realize_warmup_outputs(
    paths: Sequence[str],
    *,
    flake_root: Path,
    run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    progress: ValidationProgress | None = None,
    timeout: float | None = None,
    print_build_logs: bool = False,
    force_local: bool = False,
    substitute_only: bool = False,
    keep_going: bool = False,
    chunk: int = _WARMUP_REALIZE_CHUNK,
) -> tuple[DerivationValidationFailure, ...]:
    """Build the warmup outputs so Cachix's post-build-hook pushes each path.

    ``substitute_only`` is ``--max-jobs 0`` for the non-family closure.
    ``force_local`` is ``--no-substitute`` on SVH-sensitive rust_* only.
    Do not combine them: #1263 ``--no-substitute`` on ``extension_host``
    rebuilt 406 bootstrap drvs after a partial ``--fallback``.
    Cascade warmup uses ``chunk=1`` so each shared non-rust path is one
    drv (38130420607 / #1268: umbrella goose-cli was 1759 will-be-built).
    """
    if force_local and substitute_only:
        msg = "realize_warmup_outputs cannot be force_local and substitute_only"
        raise WarmupError(msg)
    if chunk < 1:
        msg = "realize_warmup_outputs chunk must be at least 1"
        raise WarmupError(msg)
    failures: list[DerivationValidationFailure] = []
    ordered = tuple(dict.fromkeys(paths))
    for start in range(0, len(ordered), chunk):
        window = ordered[start : start + chunk]
        requests = tuple(
            DerivationValidationRequest(
                source="root-warmup",
                installable=warmup_build_installable(path),
                mode="build",
                no_substitute=force_local,
                substitute_only=substitute_only,
                keep_going=keep_going,
            )
            for path in window
        )
        window_failures = validate_derivation_requests(
            requests,
            flake_root=flake_root,
            run=run,
            progress=progress,
            timeout=timeout,
            print_build_logs=print_build_logs,
        )
        failures.extend(window_failures)
        _remember_realized_warmup_outputs(window, window_failures, run=run)
        if any(
            is_fixed_output_hash_mismatch(failure.message)
            for failure in window_failures
        ):
            return tuple(failures)
    return tuple(failures)


def _remember_realized_warmup_outputs(
    window: Sequence[str],
    window_failures: Sequence[DerivationValidationFailure],
    *,
    run: _StoreRun | None,
) -> None:
    """Record successful warmup outputs so flush can ``cachix push`` after a fail.

    Cascade/rust-warmup jobs that later fail used to drain the daemon and
    clear ``CACHIX_DAEMON_DIR``, then the action post hook logged
    ``Cachix Daemon not started. Skipping push``. Receipts keep the
    already-built paths even when that skip happens.
    """
    failed = {failure.installable for failure in window_failures}
    recorded: list[str] = []
    for path in window:
        if warmup_build_installable(path) in failed:
            continue
        if path.endswith(".drv"):
            if not Path(path).exists():
                continue
            recorded.extend(query_drv_outputs(path, run=run))
        elif Path(path).exists():
            recorded.append(path)
    present = tuple(
        item
        for item in dict.fromkeys(recorded)
        if item.startswith("/nix/store/") and Path(item).exists()
    )
    if not present:
        return
    remember_realized_store_paths(present)
