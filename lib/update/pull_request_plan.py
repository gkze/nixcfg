"""Dependabot-style update pull requests.

One scheduled planner discovers upstream movement and opens or updates one
pull request per independent package. Companion edges, aggregate edges, and
a flake input that actually moved share one grouped pull request. Low-priority
routine bumps that need Darwin go into one batch pull request. The batch
bisects on the same branch when closure validation fails.

Nothing here quarantines a package or waives a platform. A failed closure-gate
check is healed on the same branch, up to a fixed attempt cap, and then the
pull request is labeled for a person.
"""

import hashlib
import json
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Literal, cast

from lib.update.planner import (
    dependency_clusters,
    source_dependency_order,
    source_prerequisites,
)
from lib.update.prepare_shard import DARWIN_PREPARE_SHARD_COUNT

# Public macos-15 pool size. One lane stays free for a person and for the
# nightly full Update. The four lanes are the priority order below.
DARWIN_RUNNER_COUNT = DARWIN_PREPARE_SHARD_COUNT
DARWIN_GATE_LIMIT = DARWIN_RUNNER_COUNT - 1
MAX_HEAL_ATTEMPTS = 3
MAX_BISECT_STEPS = 8
BATCH_BRANCH = "update/routine-batch"
LABEL_UPDATE = "nixcfg-update"
LABEL_NEEDS_HUMAN = "needs-human"
LABEL_HEALING = "healing"
LABEL_BISECTING = "bisecting"
CLOSURE_GATE = "closure-gate"
_MARKER_PREFIX = "<!-- nixcfg-update-plan "
_MARKER_SUFFIX = " -->"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_JOB_ID = re.compile(r"/job/(\d+)\b")
_OK_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})
PlanKind = Literal["package", "group", "batch"]
PlanAction = Literal["open", "update", "skip"]
FailureAction = Literal["ignore", "heal", "bisect", "needs-human"]


class UpdatePriority(StrEnum):
    """Merge and Darwin-lane order. Earlier values run first."""

    MAIN_FIX = "main-fix"
    SECURITY = "security"
    HEAL = "heal"
    ROUTINE = "routine"


# Branch slugs longer than this hash. A routine batch needs at least two members.
_BRANCH_SLUG_LIMIT = 60
_MIN_BATCH_SIZE = 2

PRIORITY_LABELS = frozenset(priority.value for priority in UpdatePriority)


@dataclass(frozen=True)
class UpdateOffer:
    """One upstream movement the planner can put on a pull request."""

    name: str
    kind: Literal["source", "input"]
    current: str
    latest: str
    security: bool = False
    main_fix: bool = False
    touches_darwin: bool = True


@dataclass(frozen=True)
class ProbeFailure:
    """A discovery probe that did not produce an offer."""

    name: str
    error: str


@dataclass(frozen=True)
class BatchState:
    """Binary search over one routine batch, kept on the batch branch."""

    pending: tuple[str, ...]
    known_good: tuple[str, ...]
    culprits: tuple[str, ...]
    probe: tuple[str, ...]
    steps: int


@dataclass(frozen=True)
class PlanMarker:
    """The pull-request body record the next run and the healer read back."""

    branch: str
    kind: PlanKind
    priority: UpdatePriority
    members: tuple[str, ...]
    heal_attempts: int = 0
    batch: BatchState | None = None
    touches_darwin: bool = True

    def to_json(self) -> str:
        """Return the marker payload."""
        payload: dict[str, object] = {
            "schema": 1,
            "branch": self.branch,
            "kind": self.kind,
            "priority": self.priority.value,
            "members": list(self.members),
            "healAttempts": self.heal_attempts,
            "touchesDarwin": self.touches_darwin,
            "batch": None
            if self.batch is None
            else {
                "pending": list(self.batch.pending),
                "knownGood": list(self.batch.known_good),
                "culprits": list(self.batch.culprits),
                "probe": list(self.batch.probe),
                "steps": self.batch.steps,
            },
        }
        return json.dumps(payload, separators=(",", ":"))


@dataclass(frozen=True)
class OpenUpdatePR:
    """An open ``update/*`` pull request the planner must reuse."""

    number: int
    branch: str
    labels: frozenset[str]
    marker: PlanMarker | None

    @property
    def members(self) -> frozenset[str]:
        """Return the names this pull request already owns."""
        if self.marker is not None:
            return frozenset(self.marker.members)
        if (
            not self.branch.startswith("update/")
            or self.branch.startswith("update/group-")
            or self.branch == BATCH_BRANCH
        ):
            return frozenset()
        name = self.branch.removeprefix("update/")
        if name.startswith("pkg-"):
            name = name.removeprefix("pkg-")
        return frozenset({name}) if name else frozenset()


@dataclass(frozen=True)
class PlannedUpdate:
    """One pull request the planner would open or update."""

    branch: str
    kind: PlanKind
    priority: UpdatePriority
    action: PlanAction
    members: tuple[str, ...]
    targets: tuple[str, ...]
    touches_darwin: bool
    title: str
    labels: tuple[str, ...]
    existing_number: int | None
    marker: PlanMarker
    offers: tuple[UpdateOffer, ...]
    skip_reason: str = ""


@dataclass(frozen=True)
class PlanResult:
    """The planner's full decision, including probes that did not become pulls."""

    updates: tuple[PlannedUpdate, ...]
    deferred: tuple[str, ...]
    failures: tuple[ProbeFailure, ...]


@dataclass(frozen=True)
class CheckResult:
    """One commit check the merge and heal decisions read."""

    name: str
    status: str
    conclusion: str | None


@dataclass(frozen=True)
class FailureResponse:
    """What to do when closure-gate fails on an update pull request."""

    action: FailureAction
    marker: PlanMarker | None
    batch_targets: tuple[str, ...]
    split_targets: tuple[str, ...]


def render_failure_body(response: FailureResponse, *, run_url: str) -> str:
    """Return the pull-request body for a heal or bisect decision."""
    marker = response.marker
    if marker is None:
        return ""
    lines = [marker.branch, "", "Members:"]
    lines.extend(f"- {name}" for name in marker.members)
    if response.batch_targets:
        lines.extend(("", "Next probe:"))
        lines.extend(f"- {name}" for name in response.batch_targets)
    if response.split_targets:
        lines.extend(("", "Split:"))
        lines.extend(f"- {name}" for name in response.split_targets)
    lines.extend(("", f"Evidence: {run_url}", "", render_marker(marker), ""))
    return "\n".join(lines)


def darwin_concurrency_group(priority: UpdatePriority) -> str:
    """Return the Darwin lane for *priority*.

    Jobs that share a lane queue. Distinct lanes run together, and there are
    exactly ``DARWIN_GATE_LIMIT`` lanes.
    """
    return f"nixcfg-darwin-{priority.value}"


def updater_touches_darwin(updater_cls: type[object] | None) -> bool:
    """Return whether a source updater can change a Darwin closure hash."""
    if updater_cls is None:
        return True
    platforms = getattr(updater_cls, "supported_platforms", None)
    if platforms is None:
        return True
    if not isinstance(platforms, tuple):
        msg = "supported_platforms must be a tuple of system names or None"
        raise TypeError(msg)
    return any(isinstance(name, str) and name.endswith("-darwin") for name in platforms)


def package_branch(name: str) -> str:
    """Return the stable branch for one independent package."""
    _require_name(name)
    branch = f"update/{name}"
    if branch == BATCH_BRANCH or name.startswith("group-"):
        return f"update/pkg-{name}"
    return branch


def group_branch(members: tuple[str, ...]) -> str:
    """Return the stable branch for one coupled group."""
    for name in members:
        _require_name(name)
    slug = "+".join(members)
    if len(slug) > _BRANCH_SLUG_LIMIT:
        digest = hashlib.sha256(slug.encode()).hexdigest()[:8]
        slug = f"{members[0]}+{len(members)}+{digest}"
    return f"update/group-{slug}"


def render_marker(marker: PlanMarker) -> str:
    """Return the HTML comment stored in the pull-request body."""
    return f"{_MARKER_PREFIX}{marker.to_json()}{_MARKER_SUFFIX}"


def parse_marker(body: str) -> PlanMarker | None:
    """Return the plan marker in *body*, or ``None`` when the body has none."""
    start = body.find(_MARKER_PREFIX)
    if start < 0:
        return None
    end = body.find(_MARKER_SUFFIX, start)
    if end < 0:
        msg = "update plan marker is truncated"
        raise ValueError(msg)
    raw = body[start + len(_MARKER_PREFIX) : end]
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        msg = "update plan marker is not JSON"
        raise ValueError(msg) from error
    if not isinstance(payload, dict):
        msg = "update plan marker must be an object"
        raise TypeError(msg)
    return _marker_from_payload(payload)


def render_body(plan: PlannedUpdate, *, run_url: str) -> str:
    """Return the pull-request body, including the machine-readable marker."""
    lines = [plan.title, "", "Members:"]
    shown = {offer.name: offer for offer in plan.offers}
    for name in plan.members:
        offer = shown.get(name)
        if offer is None:
            lines.append(f"- {name}: coupled, rehashed with this group")
        else:
            lines.append(f"- {offer.name}: {offer.current} -> {offer.latest}")
    lines.extend(("", f"Evidence: {run_url}", "", render_marker(plan.marker), ""))
    return "\n".join(lines)


def plan_updates(
    offers: tuple[UpdateOffer, ...] | list[UpdateOffer],
    *,
    updaters: dict[str, type[object]],
    open_prs: tuple[OpenUpdatePR, ...] = (),
    failures: tuple[ProbeFailure, ...] = (),
) -> PlanResult:
    """Group *offers* into the pull requests the planner should publish."""
    deduped = _dedupe_offers(tuple(offers))
    by_name = {offer.name: offer for offer in deduped}
    atomic = [
        _atomic_plan(members, by_name=by_name, updaters=updaters)
        for members in _cluster_groups(deduped, updaters=updaters)
    ]
    batch_pr = next((item for item in open_prs if item.branch == BATCH_BRANCH), None)
    bisecting = _batch_is_bisecting(batch_pr)
    owned = _owned_members(open_prs)
    updates, batch_pool, deferred = _route_atomic(
        atomic, owned=owned, bisecting=bisecting, batch_pr=batch_pr
    )
    if not bisecting:
        folded = _fold_batch(batch_pool, batch_pr=batch_pr, by_name=by_name)
        if folded is not None:
            updates.append(folded)
        else:
            updates.extend(batch_pool)
    matched = tuple(_match_open(plan, open_prs) for plan in updates)
    return PlanResult(
        updates=tuple(sorted(_coalesce(matched), key=_plan_sort)),
        deferred=tuple(sorted(deferred)),
        failures=failures,
    )


def render_plan_text(result: PlanResult) -> str:
    """Return the dry-run listing of pull requests, deferrals, and probe failures."""
    lines: list[str] = []
    if not result.updates:
        lines.append("No update pull requests.")
    for plan in result.updates:
        darwin = "yes" if plan.touches_darwin else "no"
        versions = ", ".join(
            f"{offer.name} {offer.current} -> {offer.latest}" for offer in plan.offers
        )
        reason = f" ({plan.skip_reason})" if plan.skip_reason else ""
        lines.append(
            f"{plan.action}\t{plan.branch}\t{plan.kind}\t{plan.priority.value}\t"
            f"darwin={darwin}\t{versions}{reason}"
        )
    if result.deferred:
        lines.append("deferred\t" + " ".join(result.deferred))
    lines.extend(
        f"probe-failed\t{failure.name}\t{failure.error}" for failure in result.failures
    )
    return "\n".join(lines) + "\n"


def plan_to_json(result: PlanResult) -> str:
    """Return *result* as JSON the workflow artifact stores."""
    payload = {
        "updates": [_plan_dict(plan) for plan in result.updates],
        "deferred": list(result.deferred),
        "failures": [
            {"name": item.name, "error": item.error} for item in result.failures
        ],
    }
    return json.dumps(payload, indent=2) + "\n"


def matrix_branches(result: PlanResult) -> tuple[str, ...]:
    """Return branches the prepare workflow should run."""
    return tuple(plan.branch for plan in result.updates if plan.action != "skip")


def select_plan(payload: object, branch: str) -> PlannedUpdate:
    """Return one planned update from a plan JSON document."""
    if not isinstance(payload, dict):
        msg = "update plan must be an object"
        raise TypeError(msg)
    updates = payload.get("updates")
    if not isinstance(updates, list):
        msg = "update plan is missing updates"
        raise TypeError(msg)
    for item in updates:
        if isinstance(item, dict) and item.get("branch") == branch:
            return _plan_from_dict(item)
    msg = f"update plan has no branch {branch}"
    raise ValueError(msg)


def publication_decision(payload: object) -> Literal["publish", "noop"]:
    """Accept a prepared candidate, or refuse a quarantined one.

    An empty patch is a successful no-op. A candidate that withheld a package
    is not publishable: quarantining is not an update.
    """
    if not isinstance(payload, dict):
        msg = "candidate must be an object"
        raise TypeError(msg)
    if payload.get("prepared") is not True:
        msg = "candidate is not prepared"
        raise ValueError(msg)
    quarantines = payload.get("quarantines", [])
    if quarantines:
        msg = "quarantined candidate cannot publish"
        raise ValueError(msg)
    patch = payload.get("patch")
    if patch in ("", None):
        return "noop"
    if not isinstance(patch, str):
        msg = "candidate patch must be a string"
        raise TypeError(msg)
    return "publish"


def squash_merge_args(*, number: int, sha: str, repo: str) -> tuple[str, ...]:
    """Return the merge command that lands one green head and no other.

    The command is a squash merge pinned to *sha*. It does not enable
    auto-merge and it does not bypass branch protection.
    """
    if number < 1:
        msg = "pull request number must be positive"
        raise ValueError(msg)
    if not sha or sha.startswith("-") or not repo or repo.startswith("-"):
        msg = "merge requires a head sha and a repository"
        raise ValueError(msg)
    return (
        "gh",
        "pr",
        "merge",
        str(number),
        "--repo",
        repo,
        "--squash",
        "--match-head-commit",
        sha,
    )


def create_or_edit_args(
    plan: PlannedUpdate,
    *,
    base: str,
    body_file: str,
) -> tuple[str, ...]:
    """Return ``gh`` arguments that open a pull request or update that same one."""
    if plan.existing_number is None:
        return (
            "gh",
            "pr",
            "create",
            "--base",
            base,
            "--head",
            plan.branch,
            "--title",
            plan.title,
            "--body-file",
            body_file,
        )
    return (
        "gh",
        "pr",
        "edit",
        str(plan.existing_number),
        "--title",
        plan.title,
        "--body-file",
        body_file,
    )


def label_args(number: int, labels: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Return ``gh`` arguments that add *labels* to an existing pull request."""
    if not labels:
        msg = "label update requires at least one label"
        raise ValueError(msg)
    return ("gh", "pr", "edit", str(number), "--add-label", ",".join(labels))


def priority_unlabel_args(number: int, keep: UpdatePriority) -> tuple[str, ...]:
    """Return ``gh`` arguments that clear every priority label except *keep*."""
    stale = [priority.value for priority in UpdatePriority if priority is not keep]
    return ("gh", "pr", "edit", str(number), "--remove-label", ",".join(stale))


def is_closure_gate(name: str) -> bool:
    """Return whether *name* is the required closure-gate check."""
    suffix = f"/ {CLOSURE_GATE}"
    return (
        name == CLOSURE_GATE
        or name.startswith(f"{CLOSURE_GATE} /")
        or name.endswith(suffix)
    )


def automerge_allowed(
    *,
    branch: str,
    labels: frozenset[str],
    checks: tuple[CheckResult, ...] | list[CheckResult],
) -> bool:
    """Return whether this head may squash-merge through the normal path."""
    if not branch.startswith("update/"):
        return False
    if labels & {LABEL_NEEDS_HUMAN, LABEL_BISECTING, LABEL_HEALING}:
        return False
    listed = tuple(checks)
    if not listed or not _rollup_green(listed):
        return False
    gates = [item for item in listed if is_closure_gate(item.name)]
    return bool(gates) and all(
        item.status == "completed" and item.conclusion == "success" for item in gates
    )


def failure_response(
    *,
    branch: str,
    labels: frozenset[str],
    marker: PlanMarker | None,
    checks: tuple[CheckResult, ...] | list[CheckResult],
) -> FailureResponse:
    """Choose heal, bisect, or a person, and only when closure-gate failed."""
    if not branch.startswith("update/") or labels & {LABEL_HEALING, LABEL_NEEDS_HUMAN}:
        return FailureResponse("ignore", marker, (), ())
    if not any(
        is_closure_gate(item.name) and item.conclusion == "failure" for item in checks
    ):
        return FailureResponse("ignore", marker, (), ())
    if marker is not None and marker.kind == "batch" and marker.batch is not None:
        return _bisect_response(marker)
    attempts = 0 if marker is None else marker.heal_attempts
    if attempts >= MAX_HEAL_ATTEMPTS:
        return FailureResponse("needs-human", marker, (), ())
    base = marker if marker is not None else _fallback_marker(branch)
    healed = replace(
        base,
        heal_attempts=attempts + 1,
        priority=_priority_after_heal(base.priority),
    )
    return FailureResponse("heal", healed, (), ())


def advance_batch(marker: PlanMarker, *, failed: bool) -> FailureResponse:
    """Narrow a failed batch, or record a passing probe, on the same branch."""
    if marker.batch is None:
        msg = "batch advance requires batch state"
        raise ValueError(msg)
    return _advance_batch(marker, failed=failed)


def open_prs_from_payload(payload: object) -> tuple[OpenUpdatePR, ...]:
    """Parse ``gh pr list`` JSON into the open update pull requests."""
    if not isinstance(payload, list):
        msg = "pull request list must be an array"
        raise TypeError(msg)
    found: list[OpenUpdatePR] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        number = item.get("number")
        branch = item.get("headRefName")
        if not isinstance(number, int) or not isinstance(branch, str):
            continue
        if not branch.startswith("update/"):
            continue
        labels = _label_names(item.get("labels"))
        body = item.get("body")
        marker = parse_marker(body) if isinstance(body, str) and body else None
        found.append(
            OpenUpdatePR(number=number, branch=branch, labels=labels, marker=marker)
        )
    return tuple(found)


def checks_from_payload(payload: object) -> tuple[CheckResult, ...]:
    """Parse a commit check-run list."""
    if isinstance(payload, dict):
        runs = payload.get("check_runs", payload.get("checks"))
    else:
        runs = payload
    if not isinstance(runs, list):
        msg = "check list must be an array"
        raise TypeError(msg)
    found: list[CheckResult] = []
    for item in runs:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        status = item.get("status")
        if not isinstance(name, str) or not isinstance(status, str):
            continue
        conclusion = item.get("conclusion")
        found.append(
            CheckResult(
                name=name,
                status=status,
                conclusion=conclusion if isinstance(conclusion, str) else None,
            )
        )
    return tuple(found)


def batch_success_response(marker: PlanMarker | None) -> FailureResponse | None:
    """Advance a bisect after the current probe passed.

    The first full-batch probe and a finished known-good set return ``None``
    so the caller can merge. A narrowed probe that passed stays on the same
    branch and the next slice is prepared.
    """
    if marker is None or marker.kind != "batch" or marker.batch is None:
        return None
    state = marker.batch
    if state.steps == 0 or (not state.probe and not state.pending):
        return None
    return advance_batch(marker, failed=False)


def gate_failure_job_ids(payload: object) -> tuple[int, ...]:
    """Return Actions job ids for failed closure-gate check runs."""
    ids: list[int] = []
    runs = payload.get("check_runs") if isinstance(payload, dict) else payload
    if not isinstance(runs, list):
        return ()
    for item in runs:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not is_closure_gate(name):
            continue
        if item.get("conclusion") != "failure":
            continue
        details = item.get("details_url")
        if not isinstance(details, str):
            continue
        match = _JOB_ID.search(details)
        if match is not None:
            ids.append(int(match.group(1)))
    return tuple(dict.fromkeys(ids))


def _fallback_marker(branch: str) -> PlanMarker:
    name = branch.removeprefix("update/")
    if name.startswith("pkg-"):
        name = name.removeprefix("pkg-")
    members = (
        () if branch.startswith("update/group-") or branch == BATCH_BRANCH else (name,)
    )
    kind: PlanKind = "group" if branch.startswith("update/group-") else "package"
    return PlanMarker(
        branch=branch, kind=kind, priority=UpdatePriority.ROUTINE, members=members
    )


def _priority_after_heal(priority: UpdatePriority) -> UpdatePriority:
    if priority is UpdatePriority.ROUTINE:
        return UpdatePriority.HEAL
    return priority


def _bisect_response(marker: PlanMarker) -> FailureResponse:
    batch = cast("BatchState", marker.batch)
    if batch.steps >= MAX_BISECT_STEPS:
        return FailureResponse("needs-human", marker, (), ())
    return _advance_batch(marker, failed=True)


def _advance_batch(marker: PlanMarker, *, failed: bool) -> FailureResponse:
    batch = cast("BatchState", marker.batch)
    steps = batch.steps + 1
    if steps > MAX_BISECT_STEPS:
        return FailureResponse("needs-human", marker, (), ())
    pending = list(batch.pending)
    known = list(batch.known_good)
    culprits = list(batch.culprits)
    probe = list(batch.probe)
    split: list[str] = []
    if failed and len(probe) <= 1:
        if probe:
            culprit = probe[0]
            culprits.append(culprit)
            split.append(culprit)
            pending = [name for name in pending if name != culprit]
        probe = _half(pending)
    elif failed:
        probe = _half(probe)
    else:
        known.extend(probe)
        probed = set(probe)
        pending = [name for name in pending if name not in probed]
        probe = [] if not pending else _half(pending)
    state = BatchState(
        pending=tuple(pending),
        known_good=tuple(known),
        culprits=tuple(culprits),
        probe=tuple(probe),
        steps=steps,
    )
    updated = replace(
        marker, batch=state, members=tuple(dict.fromkeys((*known, *probe, *pending)))
    )
    if not probe and not pending:
        action: FailureAction = "needs-human" if not known else "bisect"
        # A finished search still updates the branch to the known-good set.
        # An empty known-good set has no mergeable batch.
        if not known:
            return FailureResponse("needs-human", updated, (), tuple(split))
    else:
        action = "bisect"
    targets = tuple(dict.fromkeys((*known, *probe)))
    return FailureResponse(action, updated, targets, tuple(split))


def _half(names: list[str]) -> list[str]:
    if not names:
        return []
    return names[: max(1, len(names) // 2)]


def _rollup_green(checks: tuple[CheckResult, ...]) -> bool:
    return all(
        item.status == "completed" and item.conclusion in _OK_CONCLUSIONS
        for item in checks
    )


def _require_name(name: str) -> None:
    if _NAME.fullmatch(name) is None:
        msg = f"unsafe update name: {name}"
        raise ValueError(msg)


def _dedupe_offers(offers: tuple[UpdateOffer, ...]) -> tuple[UpdateOffer, ...]:
    by_name: dict[str, UpdateOffer] = {}
    for offer in offers:
        _require_name(offer.name)
        current = by_name.get(offer.name)
        if current is None:
            by_name[offer.name] = offer
            continue
        by_name[offer.name] = UpdateOffer(
            name=offer.name,
            kind="input" if "input" in {offer.kind, current.kind} else "source",
            current=current.current or offer.current,
            latest=offer.latest
            if offer.kind == "source"
            else current.latest or offer.latest,
            security=offer.security or current.security,
            main_fix=offer.main_fix or current.main_fix,
            touches_darwin=offer.touches_darwin or current.touches_darwin,
        )
    return tuple(by_name[name] for name in sorted(by_name))


def _cluster_groups(
    offers: tuple[UpdateOffer, ...],
    *,
    updaters: dict[str, type[object]],
) -> tuple[frozenset[str], ...]:
    if not offers:
        return ()
    moving_inputs = tuple(offer.name for offer in offers if offer.kind == "input")
    clusters = dependency_clusters(
        tuple(updaters), updaters=updaters, input_names=moving_inputs
    )
    offer_names = {offer.name for offer in offers}
    seen: set[frozenset[str]] = set()
    groups: list[frozenset[str]] = []
    for name in sorted(offer_names):
        cluster = clusters.get(name, frozenset({name}))
        relevant = frozenset(
            member for member in cluster if member in offer_names or member in updaters
        )
        if relevant.isdisjoint(offer_names) or relevant in seen:
            continue
        seen.add(relevant)
        groups.append(relevant)
    return tuple(groups)


def _atomic_plan(
    members: frozenset[str],
    *,
    by_name: dict[str, UpdateOffer],
    updaters: dict[str, type[object]],
) -> PlannedUpdate:
    ordered = _ordered_targets(members, by_name=by_name, updaters=updaters)
    # One member is one package, including a source that shares its flake
    # input name. Dedupe marks that pair as an input, and a group branch
    # would keep the routine Darwin bump out of the batch.
    kind: PlanKind = "package" if len(ordered) == 1 else "group"
    priority = _group_priority(ordered, by_name)
    branch = package_branch(ordered[0]) if kind == "package" else group_branch(ordered)
    offers = tuple(by_name[name] for name in ordered if name in by_name)
    touches = any(_member_touches(name, by_name, updaters) for name in ordered)
    marker = PlanMarker(
        branch=branch,
        kind=kind,
        priority=priority,
        members=ordered,
        touches_darwin=touches,
    )
    return PlannedUpdate(
        branch=branch,
        kind=kind,
        priority=priority,
        action="open",
        members=ordered,
        targets=ordered,
        touches_darwin=touches,
        title=_title(kind, offers, ordered),
        labels=_labels(priority, bisecting=False),
        existing_number=None,
        marker=marker,
        offers=offers,
    )


def _input_offer(name: str) -> UpdateOffer:
    return UpdateOffer(
        name=name, kind="input", current="", latest="", touches_darwin=True
    )


def _ordered_targets(
    members: frozenset[str],
    *,
    by_name: dict[str, UpdateOffer],
    updaters: dict[str, type[object]],
) -> tuple[str, ...]:
    inputs = tuple(
        sorted(
            name
            for name in members
            if name not in updaters
            or by_name.get(name, _input_offer(name)).kind == "input"
        )
    )
    sources = {name for name in members if name in updaters and name not in inputs}
    if not sources:
        return inputs
    prerequisites = {
        name: source_prerequisites(updaters, name, selected=sources)
        for name in sorted(sources)
    }
    return tuple(dict.fromkeys((*inputs, *source_dependency_order(prerequisites))))


def _group_priority(
    members: tuple[str, ...], by_name: dict[str, UpdateOffer]
) -> UpdatePriority:
    ranks = [_offer_priority(by_name[name]) for name in members if name in by_name] or [
        UpdatePriority.ROUTINE
    ]
    return min(ranks, key=lambda item: list(UpdatePriority).index(item))


def _offer_priority(offer: UpdateOffer) -> UpdatePriority:
    if offer.main_fix:
        return UpdatePriority.MAIN_FIX
    if offer.security:
        return UpdatePriority.SECURITY
    return UpdatePriority.ROUTINE


def _member_touches(
    name: str,
    by_name: dict[str, UpdateOffer],
    updaters: dict[str, type[object]],
) -> bool:
    offer = by_name.get(name)
    if offer is not None:
        return offer.touches_darwin
    return updater_touches_darwin(updaters.get(name))


def _title(
    kind: PlanKind, offers: tuple[UpdateOffer, ...], members: tuple[str, ...]
) -> str:
    if kind == "batch":
        return f"chore(update): routine batch ({len(members)} packages)"
    if kind == "package" and offers:
        offer = offers[0]
        return f"chore(update): {offer.name} {offer.current} to {offer.latest}"
    shown = [offer.name for offer in offers[:4]]
    extra = len(members) - len(shown)
    suffix = f" +{extra}" if extra > 0 else ""
    return f"chore(update): {', '.join(shown)}{suffix}"


def _labels(priority: UpdatePriority, *, bisecting: bool) -> tuple[str, ...]:
    labels = [priority.value, LABEL_UPDATE]
    if bisecting:
        labels.append(LABEL_BISECTING)
    return tuple(labels)


def _batch_is_bisecting(batch_pr: OpenUpdatePR | None) -> bool:
    if batch_pr is None or batch_pr.marker is None or batch_pr.marker.batch is None:
        return False
    state = batch_pr.marker.batch
    return state.steps > 0 and bool(state.probe)


def _owned_members(open_prs: tuple[OpenUpdatePR, ...]) -> set[str]:
    owned: set[str] = set()
    for pull in open_prs:
        if pull.branch == BATCH_BRANCH:
            continue
        owned.update(pull.members)
    return owned


def _route_atomic(
    atomic: list[PlannedUpdate],
    *,
    owned: set[str],
    bisecting: bool,
    batch_pr: OpenUpdatePR | None,
) -> tuple[list[PlannedUpdate], list[PlannedUpdate], set[str]]:
    kept: list[PlannedUpdate] = []
    pool: list[PlannedUpdate] = []
    deferred: set[str] = set()
    batch_members = set() if batch_pr is None else set(batch_pr.members)
    for plan in atomic:
        name = plan.members[0] if plan.kind == "package" else ""
        routine_darwin = (
            plan.kind == "package"
            and plan.priority is UpdatePriority.ROUTINE
            and plan.touches_darwin
            and name not in owned
        )
        if bisecting and (routine_darwin or name in batch_members):
            deferred.add(name)
            continue
        if routine_darwin:
            pool.append(plan)
        else:
            kept.append(plan)
    return kept, pool, deferred


def _fold_batch(
    pool: list[PlannedUpdate],
    *,
    batch_pr: OpenUpdatePR | None,
    by_name: dict[str, UpdateOffer],
) -> PlannedUpdate | None:
    fresh = [plan.members[0] for plan in pool]
    existing = ()
    if batch_pr is not None and batch_pr.marker is not None:
        existing = tuple(
            name for name in batch_pr.marker.members if name in by_name or name in fresh
        )
    members = tuple(dict.fromkeys((*existing, *fresh)))
    if batch_pr is None and len(members) < _MIN_BATCH_SIZE:
        return None
    if not members:
        return None
    offers = tuple(by_name[name] for name in members if name in by_name)
    state = _initial_batch(members)
    marker = PlanMarker(
        branch=BATCH_BRANCH,
        kind="batch",
        priority=UpdatePriority.ROUTINE,
        members=members,
        heal_attempts=(
            0
            if batch_pr is None or batch_pr.marker is None
            else batch_pr.marker.heal_attempts
        ),
        batch=state,
    )
    return PlannedUpdate(
        branch=BATCH_BRANCH,
        kind="batch",
        priority=UpdatePriority.ROUTINE,
        action="update" if batch_pr is not None else "open",
        members=members,
        targets=members,
        touches_darwin=True,
        title=_title("batch", offers, members),
        labels=_labels(UpdatePriority.ROUTINE, bisecting=False),
        existing_number=None if batch_pr is None else batch_pr.number,
        marker=marker,
        offers=offers,
    )


def _initial_batch(members: tuple[str, ...]) -> BatchState:
    ordered = tuple(sorted(members))
    return BatchState(
        pending=ordered, known_good=(), culprits=(), probe=ordered, steps=0
    )


def _match_open(
    plan: PlannedUpdate, open_prs: tuple[OpenUpdatePR, ...]
) -> PlannedUpdate:
    if plan.kind == "batch":
        return plan
    match = _find_open(plan, open_prs)
    if match is None:
        return plan
    if match.labels & {LABEL_NEEDS_HUMAN, LABEL_HEALING}:
        reason = (
            LABEL_NEEDS_HUMAN if LABEL_NEEDS_HUMAN in match.labels else LABEL_HEALING
        )
        return replace(
            plan,
            action="skip",
            existing_number=match.number,
            branch=match.branch,
            skip_reason=reason,
        )
    attempts = 0 if match.marker is None else match.marker.heal_attempts
    priority = plan.priority
    if match.marker is not None and match.marker.priority is UpdatePriority.HEAL:
        priority = (
            UpdatePriority.HEAL if priority is UpdatePriority.ROUTINE else priority
        )
    marker = replace(
        plan.marker,
        branch=match.branch,
        heal_attempts=attempts,
        priority=priority,
    )
    return replace(
        plan,
        action="update",
        branch=match.branch,
        existing_number=match.number,
        priority=priority,
        labels=_labels(priority, bisecting=LABEL_BISECTING in match.labels),
        marker=marker,
    )


def _find_open(
    plan: PlannedUpdate, open_prs: tuple[OpenUpdatePR, ...]
) -> OpenUpdatePR | None:
    by_branch = [item for item in open_prs if item.branch == plan.branch]
    if len(by_branch) == 1:
        return by_branch[0]
    members = set(plan.members)
    intersecting = [
        item
        for item in open_prs
        if item.branch != BATCH_BRANCH and members & item.members
    ]
    if not intersecting:
        return by_branch[0] if by_branch else None
    return min(intersecting, key=lambda item: item.number)


def _coalesce(plans: tuple[PlannedUpdate, ...]) -> tuple[PlannedUpdate, ...]:
    grouped: dict[str, list[PlannedUpdate]] = {}
    order: list[str] = []
    for plan in plans:
        if plan.branch not in grouped:
            order.append(plan.branch)
            grouped[plan.branch] = []
        grouped[plan.branch].append(plan)
    return tuple(_merge_same_branch(grouped[branch]) for branch in order)


def _merge_same_branch(plans: list[PlannedUpdate]) -> PlannedUpdate:
    if len(plans) == 1:
        return plans[0]
    base = max(plans, key=lambda plan: len(plan.members))
    members = tuple(dict.fromkeys(name for plan in plans for name in plan.members))
    offers = tuple(dict.fromkeys(offer for plan in plans for offer in plan.offers))
    rank = list(UpdatePriority)
    priority = min((plan.priority for plan in plans), key=rank.index)
    touches = any(plan.touches_darwin for plan in plans)
    numbered = (
        plan.existing_number for plan in plans if plan.existing_number is not None
    )
    number = next(numbered, None)
    skipped = all(plan.action == "skip" for plan in plans)
    action: PlanAction = "skip" if skipped else _action_for(number)
    kind: PlanKind = (
        "batch" if any(plan.kind == "batch" for plan in plans) else base.kind
    )
    if kind != "batch":
        kind = "package" if len(members) == 1 else "group"
    marker = replace(
        base.marker, branch=base.branch, kind=kind, priority=priority, members=members
    )
    return replace(
        base,
        kind=kind,
        priority=priority,
        action=action,
        members=members,
        targets=members,
        touches_darwin=touches,
        title=_title(kind, offers, members),
        labels=_labels(priority, bisecting=LABEL_BISECTING in base.labels),
        existing_number=number,
        marker=marker,
        offers=offers,
        skip_reason=base.skip_reason if action == "skip" else "",
    )


def _action_for(number: int | None) -> PlanAction:
    return "open" if number is None else "update"


def _plan_sort(plan: PlannedUpdate) -> tuple[int, str]:
    return (list(UpdatePriority).index(plan.priority), plan.branch)


def _plan_dict(plan: PlannedUpdate) -> dict[str, object]:
    return {
        "branch": plan.branch,
        "kind": plan.kind,
        "priority": plan.priority.value,
        "action": plan.action,
        "members": list(plan.members),
        "targets": list(plan.targets),
        "touchesDarwin": plan.touches_darwin,
        "title": plan.title,
        "labels": list(plan.labels),
        "existingNumber": plan.existing_number,
        "skipReason": plan.skip_reason,
        "offers": [
            {
                "name": offer.name,
                "kind": offer.kind,
                "current": offer.current,
                "latest": offer.latest,
                "security": offer.security,
                "mainFix": offer.main_fix,
                "touchesDarwin": offer.touches_darwin,
            }
            for offer in plan.offers
        ],
        "marker": plan.marker.to_json(),
    }


def _plan_from_dict(item: dict[str, object]) -> PlannedUpdate:
    marker_raw = item.get("marker")
    if not isinstance(marker_raw, str):
        msg = "planned update is missing its marker"
        raise TypeError(msg)
    marker = _marker_from_json(marker_raw)
    offers_raw = item.get("offers")
    offers = _offers_from_json(offers_raw)
    priority = UpdatePriority(str(item.get("priority")))
    kind = cast("PlanKind", item.get("kind"))
    action = cast("PlanAction", item.get("action"))
    number = item.get("existingNumber")
    return PlannedUpdate(
        branch=str(item["branch"]),
        kind=kind,
        priority=priority,
        action=action,
        members=tuple(cast("list[str]", item.get("members", ()))),
        targets=tuple(cast("list[str]", item.get("targets", ()))),
        touches_darwin=bool(item.get("touchesDarwin")),
        title=str(item.get("title", "")),
        labels=tuple(cast("list[str]", item.get("labels", ()))),
        existing_number=number if isinstance(number, int) else None,
        marker=marker,
        offers=offers,
        skip_reason=str(item.get("skipReason", "")),
    )


def _offers_from_json(raw: object) -> tuple[UpdateOffer, ...]:
    if not isinstance(raw, list):
        return ()
    offers: list[UpdateOffer] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        offers.append(
            UpdateOffer(
                name=str(item.get("name", "")),
                kind="input" if kind == "input" else "source",
                current=str(item.get("current", "")),
                latest=str(item.get("latest", "")),
                security=bool(item.get("security")),
                main_fix=bool(item.get("mainFix")),
                touches_darwin=bool(item.get("touchesDarwin")),
            )
        )
    return tuple(offers)


def _marker_from_json(raw: str) -> PlanMarker:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        msg = "planned update marker is not JSON"
        raise ValueError(msg) from error
    if not isinstance(payload, dict):
        msg = "planned update marker must be an object"
        raise TypeError(msg)
    return _marker_from_payload(payload)


def _marker_from_payload(payload: dict[str, object]) -> PlanMarker:
    kind = payload.get("kind")
    if kind not in {"package", "group", "batch"}:
        msg = "update plan marker kind is invalid"
        raise ValueError(msg)
    priority_raw = payload.get("priority")
    try:
        priority = UpdatePriority(str(priority_raw))
    except ValueError as error:
        msg = "update plan marker priority is invalid"
        raise ValueError(msg) from error
    members = payload.get("members")
    if not isinstance(members, list) or not all(
        isinstance(name, str) for name in members
    ):
        msg = "update plan marker members must be names"
        raise ValueError(msg)
    attempts = payload.get("healAttempts", 0)
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        msg = "update plan marker healAttempts must be a non-negative integer"
        raise ValueError(msg)
    branch = payload.get("branch")
    if not isinstance(branch, str) or not branch.startswith("update/"):
        msg = "update plan marker branch must be an update branch"
        raise ValueError(msg)
    return PlanMarker(
        branch=branch,
        kind=cast("PlanKind", kind),
        priority=priority,
        members=tuple(cast("list[str]", members)),
        heal_attempts=attempts,
        batch=_batch_from_payload(payload.get("batch")),
        touches_darwin=_marker_touches(payload.get("touchesDarwin", True)),
    )


def _marker_touches(raw: object) -> bool:
    if not isinstance(raw, bool):
        msg = "update plan marker touchesDarwin must be a boolean"
        raise TypeError(msg)
    return raw


def _batch_from_payload(raw: object) -> BatchState | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        msg = "update plan batch state must be an object"
        raise TypeError(msg)
    steps = raw.get("steps")
    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 0:
        msg = "update plan batch steps must be a non-negative integer"
        raise ValueError(msg)
    return BatchState(
        pending=_name_tuple(raw.get("pending"), "pending"),
        known_good=_name_tuple(raw.get("knownGood"), "knownGood"),
        culprits=_name_tuple(raw.get("culprits"), "culprits"),
        probe=_name_tuple(raw.get("probe"), "probe"),
        steps=steps,
    )


def _name_tuple(raw: object, field: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(name, str) for name in raw):
        msg = f"update plan batch {field} must be names"
        raise ValueError(msg)
    return tuple(cast("list[str]", raw))


def _label_names(raw: object) -> frozenset[str]:
    if not isinstance(raw, list):
        return frozenset()
    names: list[str] = []
    for item in raw:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(cast("str", item["name"]))
    return frozenset(names)


__all__ = [
    "BATCH_BRANCH",
    "CLOSURE_GATE",
    "DARWIN_GATE_LIMIT",
    "DARWIN_RUNNER_COUNT",
    "LABEL_BISECTING",
    "LABEL_HEALING",
    "LABEL_NEEDS_HUMAN",
    "LABEL_UPDATE",
    "MAX_BISECT_STEPS",
    "MAX_HEAL_ATTEMPTS",
    "BatchState",
    "CheckResult",
    "FailureResponse",
    "OpenUpdatePR",
    "PlanMarker",
    "PlanResult",
    "PlannedUpdate",
    "ProbeFailure",
    "UpdateOffer",
    "UpdatePriority",
    "advance_batch",
    "automerge_allowed",
    "batch_success_response",
    "checks_from_payload",
    "create_or_edit_args",
    "darwin_concurrency_group",
    "failure_response",
    "gate_failure_job_ids",
    "group_branch",
    "is_closure_gate",
    "label_args",
    "matrix_branches",
    "open_prs_from_payload",
    "package_branch",
    "parse_marker",
    "plan_to_json",
    "plan_updates",
    "priority_unlabel_args",
    "publication_decision",
    "render_body",
    "render_marker",
    "render_plan_text",
    "select_plan",
    "squash_merge_args",
    "updater_touches_darwin",
]
