# ruff: noqa: INP001 -- Actions runs this file as a script, not a package
"""Decide which PR outputs to realize and require them on gkze.cachix.org.

The gate compares drv paths, not file names. A workflow or Python edit that
leaves every package output and ``root-closure-*`` drv unchanged is an empty
plan and does not build. Realization uses the same ``nix build`` flags as
derivation validation, then the Update job's Cachix flush. Output paths come
from that build, or from the instantiated drv when every output is already
on gkze: an import-from-derivation-disabled ``.outPath`` eval is not a
receipt.

This module stays on the runner's Python 3.12 and the standard library.
Actions invokes it before the devshell exists.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import pty
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

# Actions runs this file as a script. The repo root is not a package path
# until it is inserted, and the Update helpers are imported only by realize.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Same budget as Update's Darwin closure shards: the reusable job is 360
# minutes, and flush needs the remainder after this build stops.
BUILD_BUDGET_SECONDS = 18000
# A build that prints nothing for a minute is still a running build.
# 45562201170's packages slice spent the rest of the 6h job inside one
# nix build whose captured output never reached the log.
_BUILD_HEARTBEAT_SECONDS = 60.0
_INVENTORY_EVAL_TIMEOUT_SECONDS = 50 * 60
_INSTANTIATE_TIMEOUT_SECONDS = 50 * 60
_PUSH_TIMEOUT_SECONDS = 1200.0
_PUSH_ATTEMPTS = 3
_PUSH_CHUNK = 1000
_NARINFO_TIMEOUT_SECONDS = 30.0
_NARINFO_ATTEMPTS = 3
_HTTP_OK = 200
_HTTP_MISSING = 404
_BINARY_CACHE = "gkze"
_GKZE_NARINFO = "https://gkze.cachix.org"
# Cachix answers 403 to urllib's default User-Agent.
_NARINFO_USER_AGENT = "nixcfg-cachix-flush"
_STORE_PATH_RE = re.compile(r"/nix/store/[0-9a-z]{32}-[^\s/]+")
_STORE_BASENAME_RE = re.compile(r"^[0-9a-z]{32}-[^\s/]+$")
_SYSTEM_RE = re.compile(r"^(?:aarch64|x86_64)-(?:darwin|linux)$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._+-]*$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_KIND_PREFIX = {"package": "packages", "root": "checks"}

Presence = Callable[[str], str]
Sleeper = Callable[[float], None]


class GateError(RuntimeError):
    """The closure gate cannot prove the PR's changed outputs."""


@dataclass(frozen=True)
class ChangedOutput:
    """One package output or root closure whose drv differs from the merge base."""

    kind: str
    name: str
    attr: str
    drv: str

    def installable(self) -> str:
        """Return the local flake installable validation builds."""
        return f".#{self.attr}"


@dataclass(frozen=True)
class Plan:
    """Changed outputs for one system at one pair of Git trees."""

    system: str
    merge_base: str
    head: str
    eval_seconds: float
    changed: tuple[ChangedOutput, ...]

    def to_json(self) -> str:
        """Serialize the plan the realize job downloads."""
        payload = {
            "system": self.system,
            "mergeBase": self.merge_base,
            "head": self.head,
            "evalSeconds": self.eval_seconds,
            "changed": [
                {
                    "kind": item.kind,
                    "name": item.name,
                    "attr": item.attr,
                    "drv": item.drv,
                }
                for item in self.changed
            ],
        }
        return json.dumps(payload, indent=2) + "\n"


@dataclass(frozen=True)
class RealizeResult:
    """Outputs already on gkze and outputs this job had to push."""

    pushed: tuple[str, ...]
    cached: tuple[str, ...]
    installables: tuple[str, ...]

    def to_json(self, system: str, *, ok: bool) -> str:
        """Serialize the evidence the aggregating check reads."""
        payload = {
            "system": system,
            "ok": ok,
            "pushed": list(self.pushed),
            "cached": list(self.cached),
            "installables": list(self.installables),
        }
        return json.dumps(payload, indent=2) + "\n"


class _Runner:
    """Nix and Cachix operations the realize decision calls."""

    def instantiate(self, attr: str) -> str:
        """Return the drv path ``nix build`` will use for *attr*."""
        raise NotImplementedError

    def outputs_of(self, drv: str) -> tuple[str, ...]:
        """Return the output paths declared by an instantiated *drv*."""
        raise NotImplementedError

    def presence(self, path: str) -> str:
        """Return ``present``, ``absent``, or ``unknown`` for a gkze narinfo."""
        raise NotImplementedError

    def build(self, installables: tuple[str, ...]) -> tuple[str, ...]:
        """Realize *installables* and return their output store paths."""
        raise NotImplementedError

    def settle(self, paths: tuple[str, ...]) -> None:
        """Keep *paths* and drop the build directory before the next one."""
        raise NotImplementedError

    def push(self, paths: tuple[str, ...]) -> None:
        """Upload *paths* to gkze or raise when the upload does not finish."""
        raise NotImplementedError


def required_systems(policy_text: str) -> tuple[str, ...]:
    """Return the policy's system names, in file order.

    The workflow matrix and this tuple are the same inventory. A plan that
    omits one, including aarch64-darwin, is not an empty success.
    """
    try:
        payload = json.loads(policy_text)
    except json.JSONDecodeError as error:
        msg = "system policy is not JSON"
        raise GateError(msg) from error
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        msg = "system policy schemaVersion must be 1"
        raise GateError(msg)
    systems = payload.get("systems")
    if not isinstance(systems, dict) or not systems:
        msg = "system policy has no systems"
        raise GateError(msg)
    names = tuple(systems)
    for name in names:
        if not isinstance(name, str) or _SYSTEM_RE.fullmatch(name) is None:
            msg = f"unsupported system in policy: {name!r}"
            raise GateError(msg)
    return names


def _store_path(value: object, *, derivation: bool) -> str:
    if not isinstance(value, str) or _STORE_PATH_RE.fullmatch(value) is None:
        msg = f"not a store path: {value!r}"
        raise GateError(msg)
    is_drv = value.endswith(".drv")
    if derivation != is_drv:
        msg = f"store path has the wrong kind: {value}"
        raise GateError(msg)
    return value


def _attribute(system: str, kind: str, name: str) -> str:
    prefix = _KIND_PREFIX.get(kind)
    if prefix is None or _NAME_RE.fullmatch(name) is None:
        msg = f"unsupported output identity: {kind} {name}"
        raise GateError(msg)
    if _SYSTEM_RE.fullmatch(system) is None:
        msg = f"unsupported system: {system}"
        raise GateError(msg)
    return f"{prefix}.{system}.{name}"


def parse_inventory(payload: object) -> dict[tuple[str, str], str]:
    """Return ``(kind, name) -> drv`` from one inventory eval."""
    if not isinstance(payload, dict):
        msg = "inventory is not an object"
        raise GateError(msg)
    parsed: dict[tuple[str, str], str] = {}
    for kind, field in (("package", "packages"), ("root", "roots")):
        entries = payload.get(field)
        if not isinstance(entries, dict):
            msg = f"inventory {field} is not an object"
            raise GateError(msg)
        for name, drv in entries.items():
            if not isinstance(name, str):
                msg = f"inventory {field} has a non-string name"
                raise GateError(msg)
            _attribute("x86_64-linux", kind, name)
            parsed[kind, name] = _store_path(drv, derivation=True)
    return parsed


def stable_inventory(
    first: Mapping[tuple[str, str], str],
    second: Mapping[tuple[str, str], str],
) -> None:
    """Reject a tree whose drv paths change between two evaluations.

    ``builtins.path`` of a store path on Nix 2.35 allocates a new hash on
    every eval. A diff against that noise would rebuild the output on every
    pull request, so the plan fails closed instead of building it.
    """
    if set(first) != set(second):
        msg = "inventory identity changed between two evals of one tree"
        raise GateError(msg)
    unstable = [
        f"{kind} {name}"
        for (kind, name), drv in sorted(first.items())
        if second[kind, name] != drv
    ]
    if unstable:
        msg = "drv is not a pure function of the tree: " + ", ".join(unstable)
        raise GateError(msg)


def diff_inventories(
    base: Mapping[tuple[str, str], str],
    head: Mapping[tuple[str, str], str],
    *,
    system: str,
) -> tuple[ChangedOutput, ...]:
    """Return head outputs whose drv is new or different.

    An output present only at the merge base is not built: the PR removed
    it. Equal drv paths are unchanged even if the Git tree differs.
    """
    if _SYSTEM_RE.fullmatch(system) is None:
        msg = f"unsupported system: {system}"
        raise GateError(msg)
    changed: list[ChangedOutput] = []
    for key in sorted(head):
        drv = head[key]
        if base.get(key) == drv:
            continue
        kind, name = key
        changed.append(
            ChangedOutput(
                kind=kind,
                name=name,
                attr=_attribute(system, kind, name),
                drv=drv,
            )
        )
    return tuple(changed)


def _nix_string(value: str) -> str:
    """Quote *value* as a Nix string literal."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("${", "\\${")
    return f'"{escaped}"'


def inventory_eval_args(inventory: Path, flake_path: Path, system: str) -> list[str]:
    """Eval one tree's drv map without importing derivation outputs.

    ``nix eval --file`` does not apply ``--argstr``; the file stays a
    function and ``--json`` fails. Apply the arguments in ``--expr``.
    """
    if _SYSTEM_RE.fullmatch(system) is None:
        msg = f"unsupported system: {system}"
        raise GateError(msg)
    expr = (
        f"import {_nix_string(str(inventory))} {{ "
        f"flakePath = {_nix_string(str(flake_path))}; "
        f"system = {_nix_string(system)}; }}"
    )
    return [
        "nix",
        "eval",
        "--impure",
        "--json",
        "--no-update-lock-file",
        "--option",
        "allow-import-from-derivation",
        "false",
        "--expr",
        expr,
    ]


def instantiate_args(attr: str) -> list[str]:
    """Instantiate the drv ``nix build`` will realize.

    Import-from-derivation stays at Nix's default. A Darwin system drv
    differs from the plan's disabled-IFD drv; the build uses this one.
    """
    return [
        "nix",
        "eval",
        "--raw",
        "--no-update-lock-file",
        f".#{attr}.drvPath",
    ]


def derivation_show_args(drv: str) -> list[str]:
    """Read output paths from an instantiated drv file."""
    _store_path(drv, derivation=True)
    return ["nix", "derivation", "show", drv]


def build_args(installables: tuple[str, ...]) -> list[str]:
    """Return the validation build plus ``--print-out-paths``.

    The flag prefix matches ``_batch_validation_args`` for ``mode="build"``
    except ``--keep-going``. The slice builds one installable, and that flag
    kept compiling after the first failure. Run 44888080350 then recorded
    home-george as a ``Cannot build`` cascade. ``--print-out-paths`` is the
    receipt flush already uses for roots; ``--no-link`` keeps the runner from
    retaining a result symlink.
    """
    if not installables:
        msg = "build requires at least one installable"
        raise GateError(msg)
    normalized = tuple(_normalize_installable(item) for item in installables)
    return [
        "nix",
        "build",
        "--no-update-lock-file",
        "--no-link",
        "--fallback",
        "--max-jobs",
        "1",
        "--print-build-logs",
        "--print-out-paths",
        *normalized,
    ]


def _normalize_installable(installable: str) -> str:
    if not installable.startswith(".#"):
        msg = f"installable is not a local flake output: {installable}"
        raise GateError(msg)
    fragment = installable.removeprefix(".#")
    if fragment.startswith(".") or ".." in fragment.split("."):
        msg = f"installable attribute is not local: {installable}"
        raise GateError(msg)
    return f"path:.#{fragment}"


def output_paths(show_payload: object, drv: str) -> tuple[str, ...]:
    """Return the output paths ``nix derivation show`` declared for *drv*.

    Nix 2.35 returns derivation-show version 4: the drv is keyed by its
    store basename, and each output path omits ``/nix/store/``. Older
    responses key the full drv path and use absolute output paths.
    """
    if not isinstance(show_payload, dict):
        msg = "derivation show did not return an object"
        raise GateError(msg)
    described = _described_derivation(show_payload, drv)
    outputs = described.get("outputs")
    if not isinstance(outputs, dict) or not outputs:
        msg = f"derivation {drv} declares no outputs"
        raise GateError(msg)
    paths: list[str] = []
    for name in sorted(outputs):
        described_output = outputs[name]
        if not isinstance(described_output, dict):
            msg = f"derivation {drv} output {name} is not an object"
            raise GateError(msg)
        paths.append(_absolute_output_path(described_output.get("path")))
    return tuple(paths)


def _described_derivation(
    show_payload: dict[str, object], drv: str
) -> dict[str, object]:
    direct = show_payload.get(drv)
    if isinstance(direct, dict):
        return direct
    derivations = show_payload.get("derivations")
    basename = drv.removeprefix("/nix/store/")
    described = derivations.get(basename) if isinstance(derivations, dict) else None
    if isinstance(described, dict):
        return described
    msg = f"derivation show has no entry for {drv}"
    raise GateError(msg)


def _absolute_output_path(value: object) -> str:
    if isinstance(value, str) and _STORE_BASENAME_RE.fullmatch(value):
        value = f"/nix/store/{value}"
    return _store_path(value, derivation=False)


def narinfo_url(store_path: str) -> str:
    """Return the gkze narinfo URL for one store path."""
    path = _store_path(store_path, derivation=False)
    digest = path.removeprefix("/nix/store/").split("-", 1)[0]
    return f"{_GKZE_NARINFO}/{digest}.narinfo"


def classify_narinfo(status: int) -> str:
    """Map one HTTP status to present, absent, or unknown."""
    if status == _HTTP_OK:
        return "present"
    if status == _HTTP_MISSING:
        return "absent"
    return "unknown"


def gkze_presence(
    store_path: str,
    status: Callable[[str, float], int],
    sleep: Sleeper,
) -> str:
    """Query gkze until it proves presence or absence.

    Only 200 is presence and only 404 is absence. Any other status is
    retried and then unknown, so a skip cannot treat a 403 or a timeout as
    a cache hit.
    """
    url = narinfo_url(store_path)
    last = "unknown"
    for attempt in range(1, _NARINFO_ATTEMPTS + 1):
        try:
            code = status(url, _NARINFO_TIMEOUT_SECONDS)
        except OSError:
            code = 0
        last = classify_narinfo(code)
        if last != "unknown" or attempt == _NARINFO_ATTEMPTS:
            return last
        sleep(float(attempt))
    return last


def diff_plan(
    *,
    system: str,
    merge_base: str,
    head: str,
    eval_seconds: float,
    base_inventory: object,
    head_inventory: object,
) -> Plan:
    """Build the plan for one system's two inventory documents."""
    if _SHA_RE.fullmatch(merge_base) is None or _SHA_RE.fullmatch(head) is None:
        msg = "merge base and head must be 40-character Git SHAs"
        raise GateError(msg)
    changed = diff_inventories(
        parse_inventory(base_inventory),
        parse_inventory(head_inventory),
        system=system,
    )
    return Plan(
        system=system,
        merge_base=merge_base,
        head=head,
        eval_seconds=eval_seconds,
        changed=changed,
    )


def load_plan(text: str) -> Plan:
    """Parse a plan artifact, rejecting a document that is not this system's."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        msg = "closure-gate plan is not JSON"
        raise GateError(msg) from error
    if not isinstance(payload, dict):
        msg = "closure-gate plan is not an object"
        raise GateError(msg)
    if set(payload) != {"system", "mergeBase", "head", "evalSeconds", "changed"}:
        msg = "closure-gate plan has an unexpected shape"
        raise GateError(msg)
    system = payload.get("system")
    merge_base = payload.get("mergeBase")
    head = payload.get("head")
    eval_seconds = payload.get("evalSeconds")
    raw_changed = payload.get("changed")
    if (
        not isinstance(system, str)
        or not isinstance(merge_base, str)
        or not isinstance(head, str)
        or not isinstance(eval_seconds, (int, float))
        or isinstance(eval_seconds, bool)
        or not isinstance(raw_changed, list)
    ):
        msg = "closure-gate plan is missing system, commits, or changed outputs"
        raise GateError(msg)
    changed: list[ChangedOutput] = []
    for item in raw_changed:
        if not isinstance(item, dict):
            msg = "closure-gate plan changed entry is not an object"
            raise GateError(msg)
        if set(item) != {"kind", "name", "attr", "drv"}:
            msg = "closure-gate plan changed entry has an unexpected shape"
            raise GateError(msg)
        kind = item.get("kind")
        name = item.get("name")
        attr = item.get("attr")
        drv = item.get("drv")
        if (
            not isinstance(kind, str)
            or not isinstance(name, str)
            or not isinstance(attr, str)
        ):
            msg = "closure-gate plan changed entry is incomplete"
            raise GateError(msg)
        expected = _attribute(system, kind, name)
        if attr != expected:
            msg = f"closure-gate plan attr {attr} does not match {expected}"
            raise GateError(msg)
        changed.append(
            ChangedOutput(
                kind=kind,
                name=name,
                attr=attr,
                drv=_store_path(drv, derivation=True),
            )
        )
    return diff_plan(
        system=system,
        merge_base=merge_base,
        head=head,
        eval_seconds=float(eval_seconds),
        base_inventory={"packages": {}, "roots": {}},
        head_inventory=_inventory_from_changed(changed),
    )


def _inventory_from_changed(
    changed: Sequence[ChangedOutput],
) -> dict[str, dict[str, str]]:
    packages: dict[str, str] = {}
    roots: dict[str, str] = {}
    for item in changed:
        target = packages if item.kind == "package" else roots
        if item.name in target:
            msg = f"closure-gate plan repeats {item.kind} {item.name}"
            raise GateError(msg)
        target[item.name] = item.drv
    return {"packages": packages, "roots": roots}


def retain_store_paths(
    paths: tuple[str, ...],
    root_dir: Path,
    run: Callable[[list[str]], object],
) -> None:
    """Register *paths* as GC roots so the next ``nix store gc`` keeps them."""
    root_dir.mkdir(parents=True, exist_ok=True)
    for path in paths:
        link = root_dir / Path(path).name
        run(["nix-store", "--realise", "--add-root", str(link), path])


_STORE_BUILD_DIR = Path("/nix/var/nix/builds")


def clear_nix_build_directories(root: Path) -> int:
    """Delete leftover ``nix-*`` build directories under *root*.

    Nix's deleter uses ``std::filesystem::remove``. On APFS that reports
    ``Illegal byte sequence`` and then ``Directory not empty``, and a second
    build deleting at the same time becomes an ``IOStorageFamily`` storm
    (44158690416). This walk unlinks the names ``os.scandir`` returns before
    the next installable starts. The directory stays the Nix store build
    directory: Determinate Nix rejects ``/private/tmp`` as world-writable
    (44569736549).
    """
    if not root.is_dir():
        return 0
    removed = 0
    for child in list(root.iterdir()):
        if not child.name.startswith("nix-"):
            continue
        _delete_build_tree(child)
        removed += 1
    return removed


def _delete_build_tree(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        path.unlink(missing_ok=True)
        return
    for _attempt in (1, 2):
        if not path.exists():
            return
        for dirpath, dirnames, filenames in os.walk(
            path, topdown=False, followlinks=False
        ):
            directory = Path(dirpath)
            for name in filenames:
                _unlink_build_entry(directory / name)
            for name in dirnames:
                child = directory / name
                if child.is_symlink():
                    _unlink_build_entry(child)
                else:
                    _rmdir_build_entry(child)
        try:
            path.rmdir()
        except FileNotFoundError:
            return
        except OSError:
            continue
        return
    sys.stdout.write(f"closure-gate left build directory {path}\n")
    sys.stdout.flush()


def _unlink_build_entry(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        sys.stdout.write(f"closure-gate could not unlink {path}: {error}\n")


def _rmdir_build_entry(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        return


def select_slice(
    changed: Sequence[ChangedOutput], slice_name: str
) -> tuple[ChangedOutput, ...]:
    """Return the plan entries one Darwin VM realizes.

    An empty slice is the whole plan, which is what Linux still builds.
    ``packages`` is every changed package output. A ``root-closure-*`` name
    is that one root, or nothing when the root's drv did not change. One
    hosted macOS job cannot keep every changed Darwin root, and a runner
    that dies before the final push leaves the finished outputs unpublished.
    """
    if slice_name == "":
        return tuple(changed)
    if slice_name == "packages":
        return tuple(item for item in changed if item.kind == "package")
    if slice_name.startswith("root-closure-"):
        return tuple(
            item for item in changed if item.kind == "root" and item.name == slice_name
        )
    msg = f"unknown closure-gate slice: {slice_name}"
    raise GateError(msg)


def realize_changed(changed: Sequence[ChangedOutput], runner: _Runner) -> RealizeResult:
    """Build the selected installables and push paths gkze does not have.

    ``nix build --print-out-paths`` is the receipt. Derivation show of a
    Darwin system names a different output than that build realizes, so it
    cannot choose the push set. A path already on gkze is substituted and
    is not pushed again. Each installable is pushed before the next build,
    so a later runner loss keeps the outputs that already finished. A push
    that does not finish raises.
    """
    for item in changed:
        instantiated = runner.instantiate(item.attr)
        if instantiated != item.drv:
            sys.stdout.write(
                f"closure-gate realize {item.attr} "
                f"plan_drv={item.drv} build_drv={instantiated}\n"
            )
            sys.stdout.flush()
    pushed: list[str] = []
    cached: list[str] = []
    seen: set[str] = set()
    for item in changed:
        # 44713152415 built every Darwin output in one nix invocation.
        # The first runner disappeared, and the retry was SIGTERM'd.
        # 44158690416 faulted disk2s7 while two vendor trees were unlinked
        # together. One installable, then its build directory, at a time.
        # 44744612180 and 44776913311 lost both the first VM and the retry
        # before the single push, so none of the finished outputs reached gkze.
        realized = runner.build((item.installable(),))
        if not realized:
            msg = f"nix build returned 0 paths for {item.installable()}"
            raise GateError(msg)
        runner.settle(realized)
        to_push: list[str] = []
        for path in realized:
            if path in seen:
                continue
            seen.add(path)
            if runner.presence(path) == "present":
                cached.append(path)
            else:
                to_push.append(path)
        if to_push:
            runner.push(tuple(to_push))
            pushed.extend(to_push)
    return RealizeResult(
        pushed=tuple(pushed),
        cached=tuple(cached),
        installables=tuple(item.installable() for item in changed),
    )


def gate_problems(
    *,
    plan: str,
    route: str,
    realize: Mapping[str, str],
    changed: Mapping[str, int],
    results: Mapping[str, Mapping[str, object]],
    required: Sequence[str],
) -> tuple[str, ...]:
    """Return why the aggregating check is red. Empty means it is green.

    *realize* is one job result per policy system. A system with a changed
    drv must be ``success``. A system with an empty diff may be ``skipped``.
    """
    problems: list[str] = []
    if plan != "success":
        problems.append(f"plan {plan}")
    if route != "success":
        problems.append(f"route {route}")
    if tuple(changed) != tuple(required) or set(changed) != set(required):
        problems.append(
            f"changed systems {tuple(changed)} are not the policy {tuple(required)}"
        )
    for system in required:
        status = realize.get(system, "")
        count = changed.get(system, 0)
        if count > 0 and status != "success":
            problems.append(f"realize {status} for {system}")
        elif count == 0 and status not in {"skipped", "success"}:
            problems.append(f"realize {status} for an empty {system} diff")
    for system in required:
        count = changed.get(system, 0)
        result = results.get(system)
        if count == 0:
            if result not in (None, {}):
                problems.append(f"{system} realized outputs when its diff was empty")
            continue
        problems.extend(_result_problems(system, count, result))
    return tuple(problems)


def combine_realize_payloads(
    system: str, parts: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    """Join one result per Darwin slice into the aggregate's single result.

    Slices partition the plan. An unchanged root contributes no result file.
    """
    pushed: list[str] = []
    cached: list[str] = []
    installables: list[str] = []
    for part in parts:
        if part.get("system") != system or part.get("ok") is not True:
            msg = f"{system} slice result is not an ok result for that system"
            raise GateError(msg)
        for key, sink in (
            ("pushed", pushed),
            ("cached", cached),
            ("installables", installables),
        ):
            values = part.get(key)
            if not isinstance(values, list) or not all(
                isinstance(value, str) for value in values
            ):
                msg = f"{system} slice result {key} is not a list of strings"
                raise GateError(msg)
            sink.extend(values)
    if len(installables) != len(set(installables)):
        msg = f"{system} slice results repeat an installable"
        raise GateError(msg)
    return {
        "system": system,
        "ok": True,
        "pushed": pushed,
        "cached": cached,
        "installables": installables,
    }


def _result_problems(
    system: str,
    count: int,
    result: Mapping[str, object] | None,
) -> tuple[str, ...]:
    if result is None:
        return (f"{system} changed {count} outputs but did not record a result",)
    if result.get("ok") is not True:
        return (f"{system} result is not ok",)
    installables = result.get("installables")
    pushed = result.get("pushed")
    cached = result.get("cached")
    if (
        not isinstance(installables, list)
        or len(installables) != count
        or not isinstance(pushed, list)
        or not isinstance(cached, list)
    ):
        return (f"{system} result does not list {count} installables",)
    try:
        for path in (*pushed, *cached):
            _store_path(path, derivation=False)
    except GateError as error:
        return (f"{system} result has a bad store path: {error}",)
    return ()


def render_summary(
    *,
    problems: Sequence[str],
    plans: Sequence[Plan],
    results: Sequence[RealizeResult],
    systems: Sequence[str],
) -> str:
    """Render the check summary a reviewer can compare to gkze."""
    lines = ["# Closure gate", ""]
    if problems:
        lines.append("The gate is red:")
        lines.extend(f"- {problem}" for problem in problems)
        lines.append("")
    else:
        lines.append("Every changed output was realized and is on gkze.cachix.org.")
        lines.append("")
    by_system = {plan.system: plan for plan in plans}
    lines.append("| system | changed | eval seconds |")
    lines.append("| --- | --- | --- |")
    for system in systems:
        plan = by_system.get(system)
        if plan is None:
            lines.append(f"| {system} | missing |  |")
        else:
            lines.append(
                f"| {system} | {len(plan.changed)} | {plan.eval_seconds:.1f} |"
            )
    lines.extend(["", "## Pushed store paths"])
    pushed = [path for result in results for path in result.pushed]
    cached = [path for result in results for path in result.cached]
    lines.extend(f"- `{path}`" for path in pushed)
    if not pushed:
        lines.append("- none")
    lines.extend(["", "## Already on gkze"])
    lines.extend(f"- `{path}`" for path in cached)
    if not cached:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def route_counts(plans: Mapping[str, int], required: Sequence[str]) -> dict[str, int]:
    """Return one changed-count per policy system, or refuse a partial matrix."""
    if set(plans) != set(required):
        missing = [system for system in required if system not in plans]
        extra = [system for system in plans if system not in required]
        msg = f"plan systems missing={missing} extra={extra}"
        raise GateError(msg)
    return {system: plans[system] for system in required}


def resolve_merge_base(repo: Path, base: str, head: str) -> str:
    """Return the merge base of two commits that are already local."""
    sha = _git(repo, "merge-base", base, head).strip()
    if _SHA_RE.fullmatch(sha) is None:
        msg = f"git merge-base returned {sha!r}"
        raise GateError(msg)
    return sha


def add_worktree(repo: Path, dest: Path, sha: str) -> None:
    """Check *sha* out in a detached worktree."""
    _git(repo, "worktree", "add", "--detach", str(dest), sha)


def _executable(name: str) -> str:
    """Resolve a runner tool to an absolute path."""
    found = shutil.which(name)
    if found is None:
        msg = f"{name} is not on PATH"
        raise GateError(msg)
    return found


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(  # noqa: S603 -- fixed git argv, never a shell
        [_executable("git"), *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        msg = f"git {' '.join(args)} failed: {detail}"
        raise GateError(msg)
    return result.stdout


_ERROR_MARKERS = ("error:", "illegal byte sequence", "hash mismatch")


def failure_excerpt(detail: str) -> str:
    """Keep the first builder error when later lines are only dependents.

    ``--keep-going`` used to run on after the first failure. Run 44888080350
    then recorded home-george as twenty ``Cannot build`` lines and dropped
    the builder that actually failed.
    """
    lines = detail.splitlines()
    marked = [
        line
        for line in lines
        if any(marker in line.lower() for marker in _ERROR_MARKERS)
    ]
    chosen: list[str] = []
    seen: set[str] = set()
    for line in (*marked[:12], *lines[-20:]):
        if line in seen:
            continue
        seen.add(line)
        chosen.append(line)
    return "\n".join(chosen)


def _bounded_text(detail: str, *, limit: int = 200_000) -> str:
    if len(detail) <= limit:
        return detail
    half = limit // 2
    return f"{detail[:half]}\n...\n{detail[-half:]}"


def _slice_fault_log(path: Path) -> bool:
    return (path / "device-fault.log").is_file() or (
        path / "runner-fault.log"
    ).is_file()


def finished_slice_attempt(prior: Path) -> bool:
    """Return whether a prior artifact finished the slice without a fault.

    A one-artifact download extracts into ``prior`` itself. 45673035217
    treated ``cachix-daemon/`` in that tree as a finished slice, so every
    Darwin retry skipped. A ``closure-gate-*`` child is the multi-artifact
    layout. A fault suffix or a fault log is unfinished.
    """
    if not prior.is_dir():
        return False
    if _slice_fault_log(prior):
        return False
    if (prior / "closure-gate-result.json").is_file():
        return True
    return any(
        child.is_dir()
        and child.name.startswith("closure-gate-")
        and "device-fault" not in child.name
        and "runner-fault" not in child.name
        and not _slice_fault_log(child)
        for child in prior.iterdir()
    )


def _retain_process_failure(detail: str) -> None:
    """Keep a nix failure in the artifact uploaded before the action post.

    44388423654's Darwin retry lost its job log: the realize step had already
    failed, then the action post hung until the job was cancelled. The
    artifact upload happens before that post.
    """
    temp = os.environ.get("RUNNER_TEMP", "").strip()
    if not temp:
        return
    directory = Path(temp) / "update-artifacts"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "realize-error.txt").write_text(detail, encoding="utf-8")


def _process_text(value: bytes | str | None) -> str:
    """Decode process output without dropping a non-UTF-8 byte.

    Nix on Darwin can write a path that is not valid UTF-8. The hosted
    Python then raises UnicodeDecodeError inside ``subprocess.run`` and the
    gate never records the build result. Run 44817523361's home-george slice
    died that way twice, on byte 0xdd.
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def _write_build_log(chunk: bytes) -> None:
    text = _process_text(chunk).replace("\r\n", "\n").replace("\r", "\n")
    sys.stdout.write(text)
    sys.stdout.flush()


def _read_build_stderr(master: int, chunks: list[bytes]) -> None:
    while True:
        try:
            chunk = os.read(master, 4096)
        except OSError:
            return
        if not chunk:
            return
        chunks.append(chunk)
        _write_build_log(chunk)


def _read_build_stdout(process: subprocess.Popen[bytes], chunks: list[bytes]) -> None:
    stdout = process.stdout
    if stdout is None:
        msg = "build stdout was not captured"
        raise GateError(msg)
    chunks.append(stdout.read())


def _wait_for_build(
    process: subprocess.Popen[bytes],
    *,
    timeout: float,
    label: str,
) -> bool:
    """Return whether the build exceeded ``timeout``. Print a heartbeat until then."""
    started = time.monotonic()
    deadline = started + timeout
    while process.poll() is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            process.kill()
            process.wait(timeout=30)
            return True
        try:
            process.wait(timeout=min(_BUILD_HEARTBEAT_SECONDS, remaining))
        except subprocess.TimeoutExpired:
            if time.monotonic() >= deadline:
                continue
            elapsed = time.monotonic() - started
            sys.stdout.write(
                f"closure-gate building {label} still running after {elapsed:.0f}s\n"
            )
            sys.stdout.flush()
    return False


def _join_build_logs(
    process: subprocess.Popen[bytes],
    *,
    master: int,
    stdout_thread: threading.Thread,
    stderr_thread: threading.Thread,
) -> None:
    if process.poll() is None:  # pragma: no cover -- the wait loop owns the timeout
        process.kill()
        process.wait(timeout=30)
    stdout_thread.join(timeout=5)
    if stderr_thread.is_alive():
        os.close(master)
        stderr_thread.join(timeout=5)
        return
    os.close(master)


def _stream_build(args: list[str], *, timeout: float, label: str) -> str:
    """Run one build and copy its log to the Actions log while it is running.

    ``subprocess.run(capture_output=True)`` keeps both pipes until the process
    exits. A multi-hour ``nix build`` then looks idle, and the 6h job kills it
    before ``communicate`` can return a log. Nix block-buffers stderr on a
    pipe, so the builder log is a pty. A heartbeat covers a compile that
    prints nothing. The job timeout and ``BUILD_BUDGET_SECONDS`` stay put.
    """
    argv = [_executable(args[0]), *args[1:]]
    sys.stdout.write(f"closure-gate building {label}\n")
    sys.stdout.flush()
    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(  # noqa: S603 -- nix argv, never a shell
            argv,
            stdout=subprocess.PIPE,
            stderr=slave,
        )
    except Exception:
        os.close(master)
        raise
    finally:
        os.close(slave)

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_thread = threading.Thread(
        target=_read_build_stdout, args=(process, stdout_chunks)
    )
    stderr_thread = threading.Thread(
        target=_read_build_stderr, args=(master, stderr_chunks)
    )
    stdout_thread.start()
    stderr_thread.start()
    try:
        timed_out = _wait_for_build(process, timeout=timeout, label=label)
    finally:
        _join_build_logs(
            process,
            master=master,
            stdout_thread=stdout_thread,
            stderr_thread=stderr_thread,
        )
    stdout = _process_text(b"".join(stdout_chunks))
    stderr = (
        _process_text(b"".join(stderr_chunks)).replace("\r\n", "\n").replace("\r", "\n")
    )
    if timed_out:
        detail = stderr or stdout
        _retain_process_failure(_bounded_text(detail))
        tail = failure_excerpt(detail)
        msg = f"closure-gate building {label} timed out after {timeout:.0f}s"
        if tail:
            msg = f"{msg}: {tail}"
        raise GateError(msg)
    if process.returncode != 0:
        detail = (stderr or stdout).strip()
        _retain_process_failure(_bounded_text(detail))
        msg = f"{' '.join(args[:4])} failed: {failure_excerpt(detail)}"
        raise GateError(msg)
    return stdout


def _run(args: list[str], *, timeout: float, cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(  # noqa: S603 -- nix or cachix argv, never a shell
            [_executable(args[0]), *args[1:]],
            cwd=cwd,
            check=False,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        text = _process_text(error.stderr) or _process_text(error.stdout)
        _retain_process_failure(_bounded_text(text))
        tail = failure_excerpt(text)
        msg = f"{args[0]} timed out after {timeout:.0f}s"
        if tail:
            msg = f"{msg}: {tail}"
        raise GateError(msg) from error
    stdout = _process_text(result.stdout)
    if result.returncode != 0:
        detail = (_process_text(result.stderr) or stdout).strip()
        _retain_process_failure(_bounded_text(detail))
        msg = f"{' '.join(args[:4])} failed: {failure_excerpt(detail)}"
        raise GateError(msg)
    return stdout


def _eval_inventory(inventory: Path, flake_path: Path, system: str) -> object:
    stdout = _run(
        inventory_eval_args(inventory, flake_path, system),
        timeout=_INVENTORY_EVAL_TIMEOUT_SECONDS,
    )
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as error:
        msg = "inventory eval did not return JSON"
        raise GateError(msg) from error


def _http_status(url: str, timeout: float) -> int:
    if not url.startswith(f"{_GKZE_NARINFO}/"):
        msg = f"narinfo URL is not gkze: {url}"
        raise GateError(msg)
    request = urllib.request.Request(  # noqa: S310 -- fixed https://gkze.cachix.org narinfo
        url,
        method="HEAD",
        headers={"User-Agent": _NARINFO_USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 -- fixed https://gkze.cachix.org narinfo
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)


def _push_once(paths: tuple[str, ...], timeout: float) -> int:
    result = subprocess.run(  # noqa: S603 -- fixed cachix argv, never a shell
        [_executable("cachix"), "push", _BINARY_CACHE, *paths],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        sys.stdout.write(f"cachix push failed: {detail}\n")
        sys.stdout.flush()
    return result.returncode


class _ProcessRunner(_Runner):
    def instantiate(self, attr: str) -> str:
        return _store_path(
            _run(instantiate_args(attr), timeout=_INSTANTIATE_TIMEOUT_SECONDS).strip(),
            derivation=True,
        )

    def outputs_of(self, drv: str) -> tuple[str, ...]:
        stdout = _run(derivation_show_args(drv), timeout=_INSTANTIATE_TIMEOUT_SECONDS)
        return output_paths(json.loads(stdout), drv)

    def presence(self, path: str) -> str:
        return gkze_presence(path, _http_status, time.sleep)

    def build(self, installables: tuple[str, ...]) -> tuple[str, ...]:
        stdout = _stream_build(
            build_args(installables),
            timeout=BUILD_BUDGET_SECONDS,
            label=" ".join(installables),
        )
        paths = tuple(
            line.strip()
            for line in stdout.splitlines()
            if _STORE_PATH_RE.fullmatch(line.strip())
        )
        return tuple(_store_path(path, derivation=False) for path in paths)

    def settle(self, paths: tuple[str, ...]) -> None:
        temp = os.environ.get("RUNNER_TEMP")
        if not temp:
            msg = "RUNNER_TEMP is unset"
            raise GateError(msg)
        retain_store_paths(
            paths,
            Path(temp) / "closure-gate-gcroots",
            lambda args: _run(args, timeout=_INSTANTIATE_TIMEOUT_SECONDS),
        )
        removed = clear_nix_build_directories(_STORE_BUILD_DIR)
        sys.stdout.write(f"closure-gate cleared {removed} leftover build directories\n")
        sys.stdout.flush()
        _load_jobs().reclaim_hosted_store()

    def push(self, paths: tuple[str, ...]) -> None:
        for start in range(0, len(paths), _PUSH_CHUNK):
            chunk = paths[start : start + _PUSH_CHUNK]
            for attempt in range(1, _PUSH_ATTEMPTS + 1):
                try:
                    code = _push_once(chunk, _PUSH_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    code = 1
                if code == 0:
                    break
                if attempt == _PUSH_ATTEMPTS:
                    msg = f"cachix push failed for {len(chunk)} paths"
                    raise GateError(msg)
                time.sleep(float(attempt))
        for path in paths:
            if gkze_presence(path, _http_status, time.sleep) != "present":
                msg = f"{path} is not on gkze after cachix push"
                raise GateError(msg)


_JOBS: dict[str, ModuleType] = {}


def _load_jobs() -> ModuleType:
    """Load ``jobs.py`` as a script.

    ``lib.update.ci`` imports typer. The hosted runner's Python does not
    have it, and Actions invokes ``jobs.py`` directly for that reason.
    """
    cached = _JOBS.get("jobs")
    if cached is not None:
        return cached
    path = _REPO_ROOT / "lib" / "update" / "ci" / "jobs.py"
    spec = importlib.util.spec_from_file_location("_nixcfg_closure_gate_jobs", path)
    if spec is None or spec.loader is None:
        msg = "cannot load the update job entrypoint"
        raise GateError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_nixcfg_closure_gate_jobs"] = module
    spec.loader.exec_module(module)
    _JOBS["jobs"] = module
    return module


def _remember(paths: tuple[str, ...]) -> None:
    if not paths:
        return
    _load_jobs().remember_realized_store_paths(paths)


def _write_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as summary:
        summary.write(text)


def _write_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT", "")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as output:
        output.write(f"{name}={value}\n")


def _command_prior_slice() -> None:
    """Skip a retry whose first VM already finished this slice."""
    raw = os.environ.get("PRIOR_SLICE", "").strip()
    skip = bool(raw) and finished_slice_attempt(Path(raw))
    value = "true" if skip else "false"
    output = os.environ.get("GITHUB_OUTPUT", "")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"skip={value}\n")
    sys.stdout.write(f"prior-slice skip={value}\n")


def _command_plan() -> None:
    system = os.environ["SYSTEM"]
    base_sha = os.environ["BASE_SHA"]
    head_sha = os.environ["HEAD_SHA"]
    repo = Path.cwd()
    # Checkout is the PR head. The base tip may be ahead of that history,
    # so fetch it before computing the merge base. The repo is public;
    # this fetch does not use a credential.
    _git(repo, "fetch", "--no-tags", "origin", base_sha)
    for sha in (base_sha, head_sha):
        _git(repo, "cat-file", "-e", f"{sha}^{{commit}}")
    merge_base = resolve_merge_base(repo, base_sha, head_sha)
    temp = Path(os.environ["RUNNER_TEMP"])
    base_tree = temp / "closure-gate-base"
    head_tree = temp / "closure-gate-head"
    add_worktree(repo, base_tree, merge_base)
    add_worktree(repo, head_tree, head_sha)
    inventory = repo / ".github/closure-gate/inventory.nix"
    started = time.monotonic()
    base_doc = _eval_inventory(inventory, base_tree, system)
    head_doc = _eval_inventory(inventory, head_tree, system)
    head_again = _eval_inventory(inventory, head_tree, system)
    stable_inventory(parse_inventory(head_doc), parse_inventory(head_again))
    plan = diff_plan(
        system=system,
        merge_base=merge_base,
        head=head_sha,
        eval_seconds=time.monotonic() - started,
        base_inventory=base_doc,
        head_inventory=head_doc,
    )
    out = Path(os.environ["RUNNER_TEMP"]) / "closure-gate"
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.json").write_text(plan.to_json(), encoding="utf-8")
    _write_output("changed", str(len(plan.changed)))
    sys.stdout.write(
        f"closure-gate plan system={system} changed={len(plan.changed)} "
        f"eval_seconds={plan.eval_seconds:.1f} merge_base={merge_base}\n"
    )
    sys.stdout.flush()


def _command_route() -> None:
    root = Path(os.environ["RUNNER_TEMP"]) / "plans"
    counts: dict[str, int] = {}
    for path in sorted(root.glob("*/plan.json")):
        plan = load_plan(path.read_text(encoding="utf-8"))
        if plan.system in counts:
            msg = f"duplicate plan for {plan.system}"
            raise GateError(msg)
        counts[plan.system] = len(plan.changed)
    policy = Path("lib/system-policy.json").read_text(encoding="utf-8")
    routed = route_counts(counts, required_systems(policy))
    _write_output("changed", json.dumps(routed, separators=(",", ":")))


def _command_realize() -> None:
    plan = load_plan(
        Path(os.environ["NIXCFG_CLOSURE_GATE_PLAN"]).read_text(encoding="utf-8")
    )
    system = os.environ["SYSTEM"]
    if plan.system != system:
        msg = f"plan system {plan.system} does not match {system}"
        raise GateError(msg)
    if not plan.changed:
        msg = f"{system} has an empty plan; the realize job should have been skipped"
        raise GateError(msg)
    selected = select_slice(
        plan.changed, os.environ.get("NIXCFG_CLOSURE_GATE_SLICE", "")
    )
    if not selected:
        result = RealizeResult(pushed=(), cached=(), installables=())
    else:
        if any(item.kind == "root" for item in selected):
            _load_jobs().reclaim_hosted_store()
        result = realize_changed(selected, _ProcessRunner())
    _remember(result.pushed)
    artifact = Path(os.environ["RUNNER_TEMP"]) / "update-artifacts"
    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / "closure-gate-result.json").write_text(
        result.to_json(system, ok=True),
        encoding="utf-8",
    )
    _write_summary(
        render_summary(
            problems=(),
            plans=(plan,),
            results=(result,),
            systems=(system,),
        )
    )


def _load_result(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"{path} is not a result object"
        raise GateError(msg)
    return payload


def _fault_artifact(path: Path) -> bool:
    return "device-fault" in path.parent.name or "runner-fault" in path.parent.name


def load_system_result(results_root: Path, system: str) -> dict[str, object] | None:
    """Read one system's result, joining Darwin slice artifacts when present."""
    direct = _load_result(results_root / system / "closure-gate-result.json")
    if direct is not None:
        return direct
    parts: list[dict[str, object]] = []
    system_root = results_root / system
    if not system_root.is_dir():
        return None
    for path in sorted(system_root.glob("*/closure-gate-result.json")):
        if _fault_artifact(path):
            continue
        payload = _load_result(path)
        if payload is not None:
            parts.append(payload)
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return combine_realize_payloads(system, parts)


def _command_aggregate() -> None:
    policy = Path("lib/system-policy.json").read_text(encoding="utf-8")
    required = required_systems(policy)
    raw_changed = os.environ.get("CHANGED", "") or "{}"
    try:
        changed_payload = json.loads(raw_changed)
    except json.JSONDecodeError as error:
        msg = "changed map is not JSON"
        raise GateError(msg) from error
    if not isinstance(changed_payload, dict):
        msg = "changed map is not an object"
        raise GateError(msg)
    changed = {
        str(system): int(count)
        for system, count in changed_payload.items()
        if isinstance(count, int) and not isinstance(count, bool)
    }
    results_root = Path(os.environ.get("CLOSURE_GATE_RESULTS", "results"))
    results: dict[str, Mapping[str, object]] = {}
    loaded: list[RealizeResult] = []
    for system in required:
        payload = load_system_result(results_root, system)
        if payload is None:
            continue
        results[system] = payload
        pushed = payload.get("pushed")
        cached = payload.get("cached")
        installables = payload.get("installables")
        if (
            isinstance(pushed, list)
            and isinstance(cached, list)
            and isinstance(installables, list)
        ):
            loaded.append(
                RealizeResult(
                    pushed=tuple(str(path) for path in pushed),
                    cached=tuple(str(path) for path in cached),
                    installables=tuple(str(item) for item in installables),
                )
            )
    plans: list[Plan] = []
    plans_root = Path(os.environ.get("CLOSURE_GATE_PLANS", ""))
    if plans_root.is_dir():
        plans.extend(
            load_plan(path.read_text(encoding="utf-8"))
            for path in sorted(plans_root.glob("*/plan.json"))
        )
    raw_realize = os.environ.get("REALIZE_RESULTS", "") or "{}"
    try:
        realize_payload = json.loads(raw_realize)
    except json.JSONDecodeError as error:
        msg = "realize results are not JSON"
        raise GateError(msg) from error
    if not isinstance(realize_payload, dict) or not all(
        isinstance(status, str) for status in realize_payload.values()
    ):
        msg = "realize results are not a system to job-result map"
        raise GateError(msg)
    problems = gate_problems(
        plan=os.environ.get("PLAN_RESULT", ""),
        route=os.environ.get("ROUTE_RESULT", ""),
        realize={str(system): status for system, status in realize_payload.items()},
        changed=changed,
        results=results,
        required=required,
    )
    _write_summary(
        render_summary(
            problems=problems,
            plans=tuple(plans),
            results=tuple(loaded),
            systems=required,
        )
    )
    if problems:
        for problem in problems:
            sys.stdout.write(f"::error::{problem}\n")
        sys.stdout.flush()
        raise SystemExit(1)


def main() -> None:
    """Run the command selected by ``NIXCFG_CLOSURE_GATE_COMMAND``."""
    command = os.environ.get("NIXCFG_CLOSURE_GATE_COMMAND", "")
    commands = {
        "prior-slice": _command_prior_slice,
        "plan": _command_plan,
        "route": _command_route,
        "realize": _command_realize,
        "aggregate": _command_aggregate,
    }
    function = commands.get(command)
    if function is None:
        msg = f"unknown closure-gate command: {command}"
        raise SystemExit(msg)
    try:
        function()
    except GateError as error:
        sys.stdout.write(f"::error::{error}\n")
        sys.stdout.flush()
        raise SystemExit(1) from error


if __name__ == "__main__":
    ast.parse(Path(__file__).read_text(encoding="utf-8"), feature_version=(3, 12))
    main()
