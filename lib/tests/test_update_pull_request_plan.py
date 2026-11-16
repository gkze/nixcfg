"""Pull-request grouping, Darwin lanes, heal caps, and batch bisection."""

import json
from dataclasses import replace

import pytest

from lib.update.pull_request_plan import (
    BATCH_BRANCH,
    DARWIN_GATE_LIMIT,
    LABEL_BISECTING,
    LABEL_HEALING,
    LABEL_NEEDS_HUMAN,
    MAX_BISECT_STEPS,
    MAX_HEAL_ATTEMPTS,
    BatchState,
    CheckResult,
    FailureResponse,
    OpenUpdatePR,
    PlanMarker,
    UpdateOffer,
    UpdatePriority,
    _merge_same_branch,
    advance_batch,
    automerge_allowed,
    batch_success_response,
    checks_from_payload,
    create_or_edit_args,
    darwin_concurrency_group,
    failure_response,
    gate_failure_job_ids,
    label_args,
    matrix_branches,
    open_prs_from_payload,
    package_branch,
    parse_marker,
    plan_to_json,
    plan_updates,
    priority_unlabel_args,
    publication_decision,
    render_body,
    render_failure_body,
    render_marker,
    render_plan_text,
    select_plan,
    squash_merge_args,
    updater_touches_darwin,
)


class _Parent:
    supported_platforms = ("aarch64-linux",)


class _Child:
    companion_of = "parent"
    supported_platforms = ("aarch64-linux",)


class _Bar:
    input_name = "nixpkgs"
    supported_platforms = ("aarch64-darwin",)


class _Baz:
    input_name = "nixpkgs"
    supported_platforms = ("x86_64-linux",)


class _Solo:
    supported_platforms = ("x86_64-linux",)


class _Other:
    supported_platforms = ("aarch64-darwin",)


UPDATERS: dict[str, type[object]] = {
    "parent": _Parent,
    "child": _Child,
    "bar": _Bar,
    "baz": _Baz,
    "solo": _Solo,
    "other": _Other,
}


def _offer(name: str, **kwargs: object) -> UpdateOffer:
    payload = {
        "name": name,
        "kind": "source",
        "current": "1",
        "latest": "2",
        "touches_darwin": False,
    }
    payload.update(kwargs)
    return UpdateOffer(**payload)  # type: ignore[arg-type]


def _gate(conclusion: str = "success", status: str = "completed") -> CheckResult:
    return CheckResult(name="closure-gate", status=status, conclusion=conclusion)


def test_independent_packages_stay_apart_and_a_moved_input_couples_them() -> None:
    """Companion edges and a moved flake input share one pull request."""
    separate = plan_updates(
        (_offer("bar", touches_darwin=True), _offer("solo")),
        updaters=UPDATERS,
    )
    assert [plan.branch for plan in separate.updates] == ["update/bar", "update/solo"]
    assert separate.updates[0].touches_darwin is True
    assert separate.updates[1].touches_darwin is False

    coupled = plan_updates(
        (
            _offer("bar", touches_darwin=True),
            _offer("baz"),
            _offer(
                "nixpkgs",
                kind="input",
                current="old",
                latest="new",
                touches_darwin=True,
            ),
        ),
        updaters=UPDATERS,
    )
    assert len(coupled.updates) == 1
    assert coupled.updates[0].kind == "group"
    assert coupled.updates[0].branch.startswith("update/group-")
    assert set(coupled.updates[0].members) == {"bar", "baz", "nixpkgs"}
    assert coupled.updates[0].targets[0] == "nixpkgs"


def test_companion_is_rehashed_with_its_parent() -> None:
    planned = plan_updates((_offer("child"),), updaters=UPDATERS)
    assert planned.updates[0].kind == "group"
    assert planned.updates[0].members == ("parent", "child")
    assert "coupled" in render_body(
        planned.updates[0], run_url="https://example.test/run"
    )


def test_source_that_shares_its_flake_input_stays_one_package() -> None:
    """Dedupe marks that pair as an input. The branch stays update/<name>."""
    alone = plan_updates(
        (
            _offer("solo"),
            _offer("solo", kind="input", current="v1", latest="v2"),
        ),
        updaters=UPDATERS,
    )
    assert len(alone.updates) == 1
    assert alone.updates[0].kind == "package"
    assert alone.updates[0].branch == "update/solo"
    assert alone.updates[0].offers[0].kind == "input"

    batched = plan_updates(
        (
            _offer("bar", touches_darwin=True),
            _offer(
                "bar",
                kind="input",
                current="v1",
                latest="v2",
                touches_darwin=True,
            ),
            _offer("other", touches_darwin=True),
        ),
        updaters=UPDATERS,
    )
    assert [plan.branch for plan in batched.updates] == [BATCH_BRANCH]
    assert set(batched.updates[0].members) == {"bar", "other"}


def test_routine_darwin_bumps_share_one_batch_and_linux_does_not() -> None:
    """Two Darwin routine bumps batch. One bump, or a Linux bump, stays alone."""
    planned = plan_updates(
        (
            _offer("bar", touches_darwin=True),
            _offer("other", touches_darwin=True),
            _offer("solo"),
        ),
        updaters=UPDATERS,
    )
    branches = {plan.branch: plan for plan in planned.updates}
    assert BATCH_BRANCH in branches
    assert set(branches[BATCH_BRANCH].members) == {"bar", "other"}
    assert branches[BATCH_BRANCH].kind == "batch"
    assert branches["update/solo"].kind == "package"
    alone = plan_updates((_offer("bar", touches_darwin=True),), updaters=UPDATERS)
    assert alone.updates[0].branch == "update/bar"


def test_security_and_main_fix_are_not_batched() -> None:
    planned = plan_updates(
        (
            _offer("bar", touches_darwin=True, security=True),
            _offer("solo", main_fix=True),
        ),
        updaters=UPDATERS,
    )
    assert {plan.priority for plan in planned.updates} == {
        UpdatePriority.SECURITY,
        UpdatePriority.MAIN_FIX,
    }
    assert all(plan.kind == "package" for plan in planned.updates)
    assert [plan.branch for plan in planned.updates] == ["update/solo", "update/bar"]


def test_open_pull_requests_are_updated_in_place() -> None:
    marker = PlanMarker(
        branch="update/solo",
        kind="package",
        priority=UpdatePriority.HEAL,
        members=("solo",),
        heal_attempts=1,
    )
    existing = OpenUpdatePR(
        number=9,
        branch="update/solo",
        labels=frozenset({"heal", "nixcfg-update"}),
        marker=marker,
    )
    planned = plan_updates((_offer("solo"),), updaters=UPDATERS, open_prs=(existing,))
    plan = planned.updates[0]
    assert plan.action == "update"
    assert plan.existing_number == 9
    assert plan.branch == "update/solo"
    assert plan.marker.heal_attempts == 1
    assert plan.priority is UpdatePriority.HEAL
    assert matrix_branches(planned) == ("update/solo",)


def test_needs_human_and_healing_are_not_pushed_again() -> None:
    blocked = OpenUpdatePR(
        number=3,
        branch="update/solo",
        labels=frozenset({LABEL_NEEDS_HUMAN}),
        marker=None,
    )
    planned = plan_updates((_offer("solo"),), updaters=UPDATERS, open_prs=(blocked,))
    assert planned.updates[0].action == "skip"
    assert planned.updates[0].skip_reason == LABEL_NEEDS_HUMAN
    assert matrix_branches(planned) == ()
    healing = OpenUpdatePR(
        number=4,
        branch="update/solo",
        labels=frozenset({LABEL_HEALING}),
        marker=None,
    )
    again = plan_updates((_offer("solo"),), updaters=UPDATERS, open_prs=(healing,))
    assert again.updates[0].skip_reason == LABEL_HEALING


def test_members_of_one_open_group_are_not_split_into_new_pulls() -> None:
    owned = OpenUpdatePR(
        number=2,
        branch="update/group-bar+baz",
        labels=frozenset(),
        marker=PlanMarker(
            branch="update/group-bar+baz",
            kind="group",
            priority=UpdatePriority.ROUTINE,
            members=("bar", "baz"),
        ),
    )
    # Owned names are not batched, and both offers update that one branch.
    planned = plan_updates(
        (_offer("bar", touches_darwin=True), _offer("baz")),
        updaters=UPDATERS,
        open_prs=(owned,),
    )
    assert len(planned.updates) == 1
    assert planned.updates[0].branch == "update/group-bar+baz"
    assert planned.updates[0].action == "update"
    assert set(planned.updates[0].members) == {"bar", "baz"}


def test_bisecting_batch_defers_new_routine_darwin_bumps() -> None:
    batch = OpenUpdatePR(
        number=8,
        branch=BATCH_BRANCH,
        labels=frozenset({LABEL_BISECTING}),
        marker=PlanMarker(
            branch=BATCH_BRANCH,
            kind="batch",
            priority=UpdatePriority.ROUTINE,
            members=("bar", "parent"),
            batch=BatchState(
                pending=("bar", "parent"),
                known_good=(),
                culprits=(),
                probe=("bar",),
                steps=1,
            ),
        ),
    )
    planned = plan_updates(
        (_offer("bar", touches_darwin=True), _offer("solo", touches_darwin=True)),
        updaters=UPDATERS,
        open_prs=(batch,),
    )
    assert planned.deferred == ("bar", "solo")
    assert planned.updates == ()
    assert "deferred\tbar solo" in render_plan_text(planned)


def test_existing_batch_gains_a_new_routine_bump() -> None:
    batch = OpenUpdatePR(
        number=8,
        branch=BATCH_BRANCH,
        labels=frozenset(),
        marker=PlanMarker(
            branch=BATCH_BRANCH,
            kind="batch",
            priority=UpdatePriority.ROUTINE,
            members=("bar",),
            heal_attempts=2,
            batch=BatchState(
                pending=("bar",), known_good=(), culprits=(), probe=("bar",), steps=0
            ),
        ),
    )
    planned = plan_updates(
        (_offer("bar", touches_darwin=True), _offer("other", touches_darwin=True)),
        updaters=UPDATERS,
        open_prs=(batch,),
    )
    plan = planned.updates[0]
    assert plan.action == "update"
    assert plan.existing_number == 8
    assert set(plan.members) == {"bar", "other"}
    assert plan.marker.heal_attempts == 2


def test_branch_names_and_darwin_lanes() -> None:
    assert package_branch("routine-batch") == "update/pkg-routine-batch"
    assert package_branch("group-tool") == "update/pkg-group-tool"
    with pytest.raises(ValueError, match="unsafe"):
        package_branch("../nixpkgs")
    assert (
        len({darwin_concurrency_group(priority) for priority in UpdatePriority})
        == DARWIN_GATE_LIMIT
    )
    assert darwin_concurrency_group(UpdatePriority.MAIN_FIX) == "nixcfg-darwin-main-fix"
    assert updater_touches_darwin(None) is True
    assert updater_touches_darwin(_Solo) is False
    assert updater_touches_darwin(_Bar) is True

    class _Everywhere:
        supported_platforms = None

    assert updater_touches_darwin(_Everywhere) is True
    with pytest.raises(TypeError, match="supported_platforms"):
        updater_touches_darwin(
            type("Bad", (), {"supported_platforms": "aarch64-darwin"})
        )


def test_long_group_branch_is_hashed() -> None:
    names = tuple(f"package{index:02d}name" for index in range(8))
    updaters = {name: type(name, (), {"input_name": "nixpkgs"}) for name in names}
    offers = (
        *(_offer(name) for name in names),
        _offer("nixpkgs", kind="input", current="a", latest="b", touches_darwin=True),
    )
    planned = plan_updates(offers, updaters=updaters)
    branch = planned.updates[0].branch
    assert branch.startswith("update/group-")
    assert len(branch) < 80


def test_marker_roundtrip_and_rejection() -> None:
    planned = plan_updates((_offer("solo"),), updaters=UPDATERS)
    body = render_body(planned.updates[0], run_url="https://example.test/1")
    marker = parse_marker(body)
    assert marker is not None
    assert marker.members == ("solo",)
    assert parse_marker("no marker") is None
    with pytest.raises(ValueError, match="truncated"):
        parse_marker(render_marker(marker).replace(" -->", ""))
    with pytest.raises(ValueError, match="not JSON"):
        parse_marker("<!-- nixcfg-update-plan { -->")
    with pytest.raises(TypeError, match="object"):
        parse_marker("<!-- nixcfg-update-plan [] -->")
    with pytest.raises(ValueError, match="kind"):
        parse_marker('<!-- nixcfg-update-plan {"kind":"nope"} -->')


def test_marker_field_validation() -> None:
    def marker(**overrides: object) -> str:
        payload = {
            "branch": "update/solo",
            "kind": "package",
            "priority": "routine",
            "members": ["solo"],
            "healAttempts": 0,
            "batch": None,
        }
        payload.update(overrides)
        return f"<!-- nixcfg-update-plan {json.dumps(payload)} -->"

    with pytest.raises(ValueError, match="priority"):
        parse_marker(marker(priority="urgent"))
    with pytest.raises(ValueError, match="members"):
        parse_marker(marker(members="solo"))
    with pytest.raises(ValueError, match="healAttempts"):
        parse_marker(marker(healAttempts=-1))
    with pytest.raises(ValueError, match="branch"):
        parse_marker(marker(branch="feature/solo"))
    with pytest.raises(TypeError, match="touchesDarwin"):
        parse_marker(marker(touchesDarwin="yes"))
    assert parse_marker(marker()).touches_darwin is True
    with pytest.raises(TypeError, match="batch"):
        parse_marker(marker(batch=[]))
    with pytest.raises(ValueError, match="steps"):
        parse_marker(
            marker(
                batch={
                    "pending": [],
                    "knownGood": [],
                    "culprits": [],
                    "probe": [],
                    "steps": -1,
                }
            )
        )
    with pytest.raises(ValueError, match="pending"):
        parse_marker(
            marker(
                batch={
                    "pending": [1],
                    "knownGood": [],
                    "culprits": [],
                    "probe": [],
                    "steps": 0,
                }
            )
        )


def test_publication_refuses_quarantine_and_accepts_a_noop() -> None:
    assert (
        publication_decision({"prepared": True, "quarantines": [], "patch": ""})
        == "noop"
    )
    assert publication_decision({"prepared": True, "patch": "ZGF0YQ=="}) == "publish"
    with pytest.raises(ValueError, match="quarantined"):
        publication_decision({
            "prepared": True,
            "quarantines": [{"package": "solo"}],
            "patch": "eA==",
        })
    with pytest.raises(ValueError, match="not prepared"):
        publication_decision({"prepared": False, "patch": "eA=="})
    with pytest.raises(TypeError, match="object"):
        publication_decision([])
    with pytest.raises(TypeError, match="string"):
        publication_decision({"prepared": True, "patch": 1})


def test_merge_command_pins_the_head_and_does_not_bypass_checks() -> None:
    command = squash_merge_args(number=12, sha="abc123", repo="gkze/nixcfg")
    assert command == (
        "gh",
        "pr",
        "merge",
        "12",
        "--repo",
        "gkze/nixcfg",
        "--squash",
        "--match-head-commit",
        "abc123",
    )
    assert "--auto" not in command
    assert "--admin" not in command
    with pytest.raises(ValueError, match="positive"):
        squash_merge_args(number=0, sha="abc", repo="gkze/nixcfg")
    with pytest.raises(ValueError, match="head sha"):
        squash_merge_args(number=1, sha="-abc", repo="gkze/nixcfg")


def test_pull_request_edit_updates_the_same_number() -> None:
    planned = plan_updates((_offer("solo"),), updaters=UPDATERS)
    create = create_or_edit_args(planned.updates[0], base="main", body_file="body.md")
    assert create[2] == "create"
    updated = plan_updates(
        (_offer("solo"),),
        updaters=UPDATERS,
        open_prs=(
            OpenUpdatePR(
                number=5, branch="update/solo", labels=frozenset(), marker=None
            ),
        ),
    ).updates[0]
    edit = create_or_edit_args(updated, base="main", body_file="body.md")
    assert edit[:4] == ("gh", "pr", "edit", "5")
    assert "create" not in edit
    assert label_args(5, ("routine",))[-1] == "routine"
    with pytest.raises(ValueError, match="label"):
        label_args(5, ())
    assert "main-fix" in priority_unlabel_args(5, UpdatePriority.ROUTINE)[-1]
    assert "routine" not in priority_unlabel_args(5, UpdatePriority.ROUTINE)[-1]


def test_automerge_requires_a_green_gate_and_a_finished_rollup() -> None:
    labels: frozenset[str] = frozenset()
    checks = (
        _gate(),
        CheckResult(name="lint", status="completed", conclusion="skipped"),
    )
    assert automerge_allowed(branch="update/solo", labels=labels, checks=checks)
    assert not automerge_allowed(branch="feature", labels=labels, checks=checks)
    assert not automerge_allowed(
        branch="update/solo", labels=frozenset({LABEL_NEEDS_HUMAN}), checks=checks
    )
    assert not automerge_allowed(
        branch="update/solo", labels=frozenset({LABEL_HEALING}), checks=checks
    )
    assert not automerge_allowed(
        branch="update/solo", labels=frozenset({LABEL_BISECTING}), checks=checks
    )
    assert not automerge_allowed(branch="update/solo", labels=labels, checks=())
    assert not automerge_allowed(
        branch="update/solo",
        labels=labels,
        checks=(_gate(conclusion="failure"),),
    )
    assert not automerge_allowed(
        branch="update/solo",
        labels=labels,
        checks=(CheckResult(name="lint", status="completed", conclusion="success"),),
    )
    assert not automerge_allowed(
        branch="update/solo",
        labels=labels,
        checks=(_gate(status="queued", conclusion=None),),
    )


def test_heal_stays_on_the_same_branch_and_stops_at_the_cap() -> None:
    failure = (_gate(conclusion="failure"),)
    first = failure_response(
        branch="update/solo", labels=frozenset(), marker=None, checks=failure
    )
    assert first.action == "heal"
    assert first.marker is not None
    assert first.marker.heal_attempts == 1
    assert first.marker.priority is UpdatePriority.HEAL
    second = failure_response(
        branch="update/solo",
        labels=frozenset(),
        marker=first.marker,
        checks=failure,
    )
    assert second.marker is not None
    assert second.marker.heal_attempts == 2
    capped = replace_attempts(second.marker, MAX_HEAL_ATTEMPTS)
    stopped = failure_response(
        branch="update/solo", labels=frozenset(), marker=capped, checks=failure
    )
    assert stopped.action == "needs-human"
    ignored = failure_response(
        branch="update/solo",
        labels=frozenset({LABEL_HEALING}),
        marker=first.marker,
        checks=failure,
    )
    assert ignored.action == "ignore"
    other = failure_response(
        branch="update/solo",
        labels=frozenset(),
        marker=first.marker,
        checks=(CheckResult(name="lint", status="completed", conclusion="failure"),),
    )
    assert other.action == "ignore"
    assert (
        failure_response(
            branch="main", labels=frozenset(), marker=None, checks=failure
        ).action
        == "ignore"
    )
    security = PlanMarker(
        branch="update/solo",
        kind="package",
        priority=UpdatePriority.SECURITY,
        members=("solo",),
    )
    kept = failure_response(
        branch="update/solo", labels=frozenset(), marker=security, checks=failure
    )
    assert kept.marker is not None
    assert kept.marker.priority is UpdatePriority.SECURITY


def test_batch_bisect_isolates_one_package_and_then_stops() -> None:
    marker = PlanMarker(
        branch=BATCH_BRANCH,
        kind="batch",
        priority=UpdatePriority.ROUTINE,
        members=("a", "b", "c", "d"),
        batch=BatchState(
            pending=("a", "b", "c", "d"),
            known_good=(),
            culprits=(),
            probe=("a", "b", "c", "d"),
            steps=0,
        ),
    )
    failed = failure_response(
        branch=BATCH_BRANCH,
        labels=frozenset(),
        marker=marker,
        checks=(_gate(conclusion="failure"),),
    )
    assert failed.action == "bisect"
    assert failed.marker is not None
    assert failed.marker.batch is not None
    assert failed.marker.batch.probe == ("a", "b")
    passed = batch_success_response(failed.marker)
    assert passed is not None
    assert passed.marker is not None
    assert passed.marker.batch is not None
    assert passed.marker.batch.known_good == ("a", "b")
    assert batch_success_response(marker) is None
    assert batch_success_response(None) is None
    lone = PlanMarker(
        branch=BATCH_BRANCH,
        kind="batch",
        priority=UpdatePriority.ROUTINE,
        members=("a",),
        batch=BatchState(
            pending=("a",), known_good=(), culprits=(), probe=("a",), steps=0
        ),
    )
    isolated = failure_response(
        branch=BATCH_BRANCH,
        labels=frozenset(),
        marker=lone,
        checks=(_gate(conclusion="failure"),),
    )
    assert isolated.split_targets == ("a",)
    assert isolated.action == "needs-human"
    exhausted = PlanMarker(
        branch=BATCH_BRANCH,
        kind="batch",
        priority=UpdatePriority.ROUTINE,
        members=("a", "b"),
        batch=BatchState(
            pending=("a", "b"),
            known_good=(),
            culprits=(),
            probe=("a", "b"),
            steps=MAX_BISECT_STEPS,
        ),
    )
    assert (
        failure_response(
            branch=BATCH_BRANCH,
            labels=frozenset(),
            marker=exhausted,
            checks=(_gate(conclusion="failure"),),
        ).action
        == "needs-human"
    )


def test_plan_json_and_text_roundtrip() -> None:
    planned = plan_updates(
        (_offer("solo"),),
        updaters=UPDATERS,
        failures=(
            __import__(
                "lib.update.pull_request_plan", fromlist=["ProbeFailure"]
            ).ProbeFailure("bar", "boom"),
        ),
    )
    text = render_plan_text(planned)
    assert "update/solo" in text
    assert "probe-failed\tbar\tboom" in text
    assert "No update pull requests." in render_plan_text(
        plan_updates((), updaters=UPDATERS)
    )
    selected = select_plan(json.loads(plan_to_json(planned)), "update/solo")
    assert selected.members == ("solo",)
    with pytest.raises(ValueError, match="no branch"):
        select_plan(json.loads(plan_to_json(planned)), "update/missing")
    with pytest.raises(TypeError, match="object"):
        select_plan([], "update/solo")
    with pytest.raises(TypeError, match="missing updates"):
        select_plan({}, "update/solo")


def test_open_pr_and_check_parsers() -> None:
    pulls = open_prs_from_payload([
        {"number": 1, "headRefName": "feature", "labels": [], "body": ""},
        "nope",
        {"number": "x", "headRefName": "update/solo"},
        {
            "number": 4,
            "headRefName": "update/solo",
            "labels": ["routine", {"name": "nixcfg-update"}, 1],
            "body": None,
        },
    ])
    assert len(pulls) == 1
    assert pulls[0].members == frozenset({"solo"})
    with pytest.raises(TypeError, match="array"):
        open_prs_from_payload({})
    checks = checks_from_payload({
        "check_runs": [
            {"name": "closure-gate", "status": "completed", "conclusion": "failure"},
            "x",
        ]
    })
    assert checks[0].conclusion == "failure"
    assert (
        checks_from_payload([{"name": "lint", "status": "queued"}])[0].conclusion
        is None
    )
    with pytest.raises(TypeError, match="array"):
        checks_from_payload({"checks": "nope"})
    ids = gate_failure_job_ids({
        "check_runs": [
            {
                "name": "closure-gate",
                "status": "completed",
                "conclusion": "failure",
                "details_url": "https://github.com/gkze/nixcfg/actions/runs/1/job/42",
            },
            {
                "name": "closure-gate / darwin",
                "status": "completed",
                "conclusion": "success",
                "details_url": "https://github.com/gkze/nixcfg/actions/runs/1/job/43",
            },
            {
                "name": "lint",
                "conclusion": "failure",
                "details_url": "https://example.test/job/9",
            },
        ]
    })
    assert ids == (42,)
    assert gate_failure_job_ids({"check_runs": "nope"}) == ()
    unlabeled = open_prs_from_payload([
        {
            "number": 3,
            "headRefName": "update/solo",
            "labels": "nixcfg-update",
            "body": "",
        }
    ])
    assert unlabeled[0].labels == frozenset()


def test_duplicate_offer_names_merge_and_empty_batch_is_omitted() -> None:
    planned = plan_updates(
        (
            _offer("solo", latest="2"),
            _offer("solo", kind="input", current="1", latest="9", touches_darwin=True),
        ),
        updaters=UPDATERS,
    )
    assert len(planned.updates) == 1
    assert planned.updates[0].offers[0].kind == "input"
    assert planned.updates[0].offers[0].latest == "2"
    empty = OpenUpdatePR(number=1, branch=BATCH_BRANCH, labels=frozenset(), marker=None)
    assert plan_updates((), updaters=UPDATERS, open_prs=(empty,)).updates == ()


def replace_attempts(marker: PlanMarker, attempts: int) -> PlanMarker:
    return replace(marker, heal_attempts=attempts)


def test_failure_body_records_the_probe_and_the_split() -> None:
    marker = PlanMarker(
        branch=BATCH_BRANCH,
        kind="batch",
        priority=UpdatePriority.ROUTINE,
        members=("bar",),
    )
    assert (
        render_failure_body(
            FailureResponse("ignore", None, (), ()), run_url="https://example.test"
        )
        == ""
    )
    body = render_failure_body(
        FailureResponse("bisect", marker, ("bar",), ("solo",)),
        run_url="https://example.test",
    )
    assert "Next probe:" in body
    assert "Split:" in body
    assert "nixcfg-update-plan" in body


def test_branch_without_a_marker_owns_the_package_name() -> None:
    assert (
        OpenUpdatePR(
            number=1, branch="feature/solo", labels=frozenset(), marker=None
        ).members
        == frozenset()
    )
    assert (
        OpenUpdatePR(
            number=1, branch="update/group-ab", labels=frozenset(), marker=None
        ).members
        == frozenset()
    )
    assert (
        OpenUpdatePR(
            number=1, branch=BATCH_BRANCH, labels=frozenset(), marker=None
        ).members
        == frozenset()
    )
    assert OpenUpdatePR(
        number=1, branch="update/pkg-solo", labels=frozenset(), marker=None
    ).members == frozenset({"solo"})
    assert (
        OpenUpdatePR(
            number=1, branch="update/", labels=frozenset(), marker=None
        ).members
        == frozenset()
    )


def test_open_bisect_label_stays_on_the_updated_plan() -> None:
    existing = OpenUpdatePR(
        number=4,
        branch="update/solo",
        labels=frozenset({LABEL_BISECTING}),
        marker=PlanMarker(
            branch="update/solo",
            kind="package",
            priority=UpdatePriority.ROUTINE,
            members=("solo",),
        ),
    )
    planned = plan_updates((_offer("solo"),), updaters=UPDATERS, open_prs=(existing,))
    assert LABEL_BISECTING in planned.updates[0].labels


def test_merged_single_source_stays_a_package() -> None:
    planned = plan_updates((_offer("solo"),), updaters=UPDATERS).updates[0]
    merged = _merge_same_branch([planned, replace(planned, title="again")])
    assert merged.kind == "package"
    labeled = replace(planned, labels=(*planned.labels, LABEL_BISECTING))
    assert LABEL_BISECTING in _merge_same_branch([labeled, planned]).labels


def test_batch_edges_and_untrusted_plan_documents() -> None:
    bare = PlanMarker(
        branch=BATCH_BRANCH,
        kind="batch",
        priority=UpdatePriority.ROUTINE,
        members=("solo",),
    )
    with pytest.raises(ValueError, match="batch state"):
        advance_batch(bare, failed=True)
    exhausted = replace(
        bare,
        batch=BatchState(
            pending=("solo",),
            known_good=(),
            culprits=(),
            probe=("solo",),
            steps=MAX_BISECT_STEPS,
        ),
    )
    exhausted_response = batch_success_response(exhausted)
    assert exhausted_response is not None
    assert exhausted_response.action == "needs-human"
    empty_probe = replace(
        bare,
        batch=BatchState(
            pending=("bar", "solo"),
            known_good=(),
            culprits=(),
            probe=(),
            steps=1,
        ),
    )
    advanced = advance_batch(empty_probe, failed=True)
    assert advanced.marker is not None
    assert advanced.marker.batch is not None
    assert "bar" in advanced.marker.batch.probe
    healed = failure_response(
        branch="update/pkg-solo",
        labels=frozenset(),
        marker=None,
        checks=(
            CheckResult(name="closure-gate", status="completed", conclusion="failure"),
        ),
    )
    assert healed.marker is not None
    assert healed.marker.members == ("solo",)
    assert (
        checks_from_payload([
            "not-a-check",
            {"name": "lint", "status": "completed", "conclusion": 1},
            {"name": "lint"},
        ])[0].conclusion
        is None
    )
    assert (
        gate_failure_job_ids([
            "not-a-run",
            {"name": "closure-gate", "conclusion": "failure", "details_url": 5},
            {
                "name": "closure-gate",
                "conclusion": "failure",
                "details_url": "https://example.test/no-job",
            },
        ])
        == ()
    )
    document = json.loads(
        plan_to_json(plan_updates((_offer("solo"),), updaters=UPDATERS))
    )
    document["updates"][0]["marker"] = "not json"
    with pytest.raises(ValueError, match="not JSON"):
        select_plan(document, "update/solo")
    document["updates"][0]["marker"] = "[]"
    with pytest.raises(TypeError, match="object"):
        select_plan(document, "update/solo")
    document["updates"][0].pop("marker")
    with pytest.raises(TypeError, match="marker"):
        select_plan(document, "update/solo")
    document["updates"][0]["marker"] = PlanMarker(
        branch="update/solo",
        kind="package",
        priority=UpdatePriority.ROUTINE,
        members=("solo",),
    ).to_json()
    document["updates"][0]["offers"] = None
    assert select_plan(document, "update/solo").offers == ()
    document["updates"][0]["offers"] = ["nope", {"name": "solo", "kind": "source"}]
    assert select_plan(document, "update/solo").offers[0].name == "solo"
