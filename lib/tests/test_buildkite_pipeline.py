"""Graph parity with ``.github/workflows`` and the generator's pure decisions."""

import json
import platform
import re
import sys
from pathlib import Path

import pytest
import yaml

from lib.buildkite.admission import (
    apply_admission,
    cancel_build,
    cancel_job,
    concurrency_cancels,
    fetch_branch_builds,
    matching_job_ids,
    parse_build_list,
)
from lib.buildkite.artifacts import arrange_download, stage_upload
from lib.buildkite.concurrency import Admission, BuildRef, admit
from lib.buildkite.emit import (
    actions_job_name,
    allow_dependency_failure,
    cell_token,
    cells_from_output,
    concurrency_expression,
    deferred_job_ids,
    render_continuation,
    render_pipeline,
    statically_untrusted,
    step_key,
)
from lib.buildkite.execute import action_kind, eval_text
from lib.buildkite.github_expr import ExprContext
from lib.buildkite.graph import (
    WorkflowParseError,
    _as_bool,
    _as_text,
    _job,
    _load_yaml,
    _matrix,
    _optional_int,
    load_action_contract,
    load_action_steps,
    load_workflows,
    queue_for_runner,
)
from lib.buildkite.runtime import (
    combine_results,
    daemon_argv,
    github_process_env,
    hook_script,
    nix_user_conf,
    prefer_conclusion,
    prepare_build_slots,
    publish_allowed,
    runner_identity,
)
from lib.buildkite.siblings import (
    conclusion_for_state,
    fetch_build,
    jobs_payload,
    write_jobs,
)
from lib.buildkite.siblings import (
    main as siblings_main,
)
from lib.buildkite.triggers import (
    BuildEvent,
    event_from_env,
    event_source,
    expr_context,
    path_matches,
    select_workflows,
    workflow_inputs,
)
from lib.update.ci import jobs

ROOT = Path(__file__).resolve().parents[2]
_NATIVE = "./.github/workflows/update-native.yml"
_SECRET = re.compile(r"secrets\.([A-Z][A-Z0-9_]*)")


def _event(**overrides: object) -> BuildEvent:
    values: dict[str, object] = {
        "source": "push",
        "branch": "main",
        "default_branch": "main",
        "changed_paths": ("lib/update/ci/jobs.py",),
        "workflow": None,
        "head_repository": "gkze/nixcfg",
        "repository": "gkze/nixcfg",
        "sha": "abc",
        "pull_request": "false",
        "build_number": "12",
        "build_id": "build-12",
    }
    values.update(overrides)
    return BuildEvent(**values)  # type: ignore[arg-type]


def _flatten(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(f"{key}\n{_flatten(item)}" for key, item in value.items())
    if isinstance(value, list):
        return "\n".join(_flatten(item) for item in value)
    return ""


def _keys(pipeline: dict[str, object]) -> set[str]:
    found: set[str] = set()

    def walk(steps: object) -> None:
        if not isinstance(steps, list):
            return
        for step in steps:
            if not isinstance(step, dict):
                continue
            if isinstance(step.get("key"), str):
                found.add(step["key"])
            walk(step.get("steps"))

    walk(pipeline.get("steps"))
    return found


def test_every_workflow_job_matches_the_yaml() -> None:
    loaded = load_workflows(ROOT)
    paths = sorted((ROOT / ".github/workflows").glob("*.yml"))
    assert [path.name for path in paths] == sorted(loaded)
    for path in paths:
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        workflow = loaded[path.name]
        assert list(document["jobs"]) == [job.job_id for job in workflow.jobs]
        native = loaded["update-native.yml"].job("native")
        for job_id, body in document["jobs"].items():
            job = workflow.job(job_id)
            _assert_needs(job.needs, body.get("needs"))
            _assert_if(job.if_expr, body.get("if"))
            _assert_matrix(job, body.get("strategy"))
            _assert_queue_and_timeout(job, body, native.timeout_minutes)
            _assert_secrets(job, body, native)
            _assert_outputs(job, body, native)
            assert job.environment == (
                body.get("environment").strip() if body.get("environment") else None
            )
            if isinstance(body.get("uses"), str):
                action_kind(body["uses"])
            for step in body.get("steps") or ():
                if isinstance(step, dict) and isinstance(step.get("uses"), str):
                    action_kind(step["uses"])
        _assert_triggers(workflow, document["on"])
        _assert_concurrency(workflow, document.get("concurrency"))


def test_update_runtime_action_classifies_and_exposes_outputs() -> None:
    defaults, outputs = load_action_contract(ROOT)
    assert defaults["cache-generators"] == "false"
    assert "runtime" in dict(outputs)
    for step in load_action_steps(ROOT):
        action_kind(step.uses)


def test_update_artifact_name_expression_matches_the_workflow() -> None:
    native = load_workflows(ROOT)["update-native.yml"].job("native")
    upload = next(
        step for step in native.steps if action_kind(step.uses) == "upload-artifact"
    )
    expression = dict(upload.with_args)["name"]
    cases = (
        (
            {"stage": "merge-candidates", "system": "x86_64-linux", "scope": "all"},
            "prepare-x86_64-linux",
        ),
        (
            {
                "stage": "merge-prepare-shards",
                "system": "aarch64-darwin",
                "scope": "all",
            },
            "prepare-aarch64-darwin",
        ),
        (
            {"stage": "prepare-pins", "system": "x86_64-linux", "scope": "all"},
            "prepare-pins",
        ),
        (
            {
                "stage": "prepare",
                "system": "aarch64-darwin",
                "scope": "all",
                "prepare_shard": "0",
            },
            "prepare-aarch64-darwin-shard-0",
        ),
        (
            {
                "stage": "validate",
                "system": "aarch64-darwin",
                "scope": "closure-shard",
                "shard": "roots-0",
            },
            "validate-aarch64-darwin-closure-shard-roots-0",
        ),
        (
            {"stage": "validate", "system": "x86_64-linux", "scope": "all"},
            "validate-x86_64-linux",
        ),
        (
            {"stage": "validate", "system": "aarch64-darwin", "scope": "rust-warmup"},
            "validate-aarch64-darwin-rust-warmup",
        ),
    )
    for inputs, expected in cases:
        assert _artifact_name(expression, inputs, device_fault="") == expected
    faulted = _artifact_name(
        expression,
        {
            "stage": "prepare",
            "system": "aarch64-darwin",
            "scope": "all",
            "prepare_shard": "1",
        },
        device_fault="true",
    )
    assert faulted == "prepare-aarch64-darwin-shard-1-device-fault"


def test_update_pipeline_shape_follows_needs() -> None:
    workflow = load_workflows(ROOT)["update.yml"]
    context = expr_context(_event())
    pipeline = render_pipeline((workflow,), context=context)
    keys = _keys(pipeline)
    assert step_key("update.yml", "prepare-pins") in keys
    assert step_key("update.yml", "prepare-darwin-0") in keys
    assert step_key("update.yml", "validate-x86") in keys
    assert step_key("update.yml", "canary-darwin-fods", {"host": "0"}) in keys
    assert step_key("update.yml", "canary-darwin-fods", {"host": "1"}) in keys
    assert step_key("update.yml", "validate-darwin-warm-rust", {"slot": "0"}) in keys
    assert step_key("update.yml", "canary-warm-rust") + "--fanout" in keys
    for job_id in (
        "canary-warm-rust",
        "validate-darwin-roots",
        "publish",
        "repair",
        "start-repair",
    ):
        assert step_key("update.yml", job_id) not in keys
    assert "canary-warm-rust" in deferred_job_ids(workflow)
    continuation = render_continuation(
        workflow,
        "canary-warm-rust",
        ({"slot": "0"},),
        context=context,
    )
    continued = _keys(continuation)
    assert step_key("update.yml", "canary-warm-rust", {"slot": "0"}) in continued
    assert step_key("update.yml", "canary-warm-rust") in continued
    assert step_key("update.yml", "validate-darwin-roots") + "--fanout" in continued
    roots = render_continuation(
        workflow,
        "validate-darwin-roots",
        ({"shard": "roots-0", "roots": "a", "system": "aarch64-darwin"},),
        context=context,
    )
    root_keys = _keys(roots)
    assert step_key("update.yml", "publish") in root_keys
    assert step_key("update.yml", "start-repair") + "--approval" in root_keys
    fods = [
        step
        for step in _all_steps(pipeline)
        if step.get("key")
        == step_key("update.yml", "canary-darwin-fods", {"host": "0"})
    ]
    assert (
        fods[0]["concurrency_group"]
        == "matrix-update-canary-darwin-fods-$BUILDKITE_BUILD_ID"
    )
    assert fods[0]["concurrency"] == 2


def test_generator_edges() -> None:
    workflow = load_workflows(ROOT)["update.yml"]
    context = expr_context(_event())
    assert (
        render_pipeline((), context=context)["steps"][0]["key"]
        == "no-matching-workflow"
    )
    with pytest.raises(WorkflowParseError, match="not a dynamic matrix"):
        render_continuation(workflow, "prepare-pins", (), context=context)
    assert cells_from_output({"include": [{"shard": "a", "roots": "b"}]}) == (
        {"shard": "a", "roots": "b"},
    )
    assert cells_from_output({"slot": ["0", "1"]}) == ({"slot": "0"}, {"slot": "1"})
    with pytest.raises(WorkflowParseError, match="not an object"):
        cells_from_output([])
    with pytest.raises(WorkflowParseError, match="include row"):
        cells_from_output({"include": ["bad"]})
    assert cell_token({"shard": "a+b"}) == "shard-a-b"
    assert cell_token({"host": "0"}) == "host-0"
    assert cell_token({"": ""})
    assert actions_job_name("prepare", None) == "prepare"
    assert actions_job_name("validate", {"slot": "1"}) == "validate (1)"
    assert (
        actions_job_name("roots", {"shard": "s", "system": "aarch64-darwin"})
        == "roots (s)"
    )
    assert actions_job_name("job", {"b": "1", "a": "2"}) == "job (2, 1)"
    assert allow_dependency_failure(None) is False
    assert allow_dependency_failure("always()") is True
    assert allow_dependency_failure("needs.a.result == 'failure'") is True
    assert allow_dependency_failure("needs.a.result == 'skipped'") is True
    assert allow_dependency_failure("success()") is False
    assert statically_untrusted(None, context) is False
    assert statically_untrusted("((( ", context) is False
    assert statically_untrusted("needs.a.result == 'success'", context) is False
    assert statically_untrusted("github.ref_name == 'missing'", context) is True
    assert statically_untrusted("unknown.field", context) is False
    assert concurrency_expression("${{ true }}") == " true "
    assert concurrency_expression("true") == "true"
    empty = render_continuation(workflow, "canary-warm-rust", (), context=context)
    assert step_key("update.yml", "canary-warm-rust") in _keys(empty)


def test_fork_proof_job_does_not_receive_secrets() -> None:
    workflow = load_workflows(ROOT)["cachix-flush-proof.yml"]
    event = _event(
        source="pull_request",
        head_repository="fork/nixcfg",
        pull_request="8",
        changed_paths=None,
    )
    pipeline = render_pipeline((workflow,), context=expr_context(event))
    trusted = next(
        step
        for step in _all_steps(pipeline)
        if step.get("key") == "cachix-flush-proof--fail-after-push"
    )
    assert "secrets" not in trusted


def test_queues_cover_the_hosted_labels_and_reject_unknown_ones() -> None:
    assert queue_for_runner("ubuntu-24.04") == "linux-x86"
    assert queue_for_runner("ubuntu-24.04-arm") == "linux-arm"
    assert queue_for_runner("macos-15") == "macos-arm"
    assert queue_for_runner("macos-13") == "macos-x86"
    assert queue_for_runner("x86_64-darwin") == "macos-x86"
    with pytest.raises(WorkflowParseError, match="no Buildkite queue"):
        queue_for_runner("windows-2022")
    queues = {
        job.queue for workflow in load_workflows(ROOT).values() for job in workflow.jobs
    }
    assert "macos-x86" not in queues


def test_parser_rejects_malformed_fragments(tmp_path: Path) -> None:
    broken = tmp_path / "bad.yml"
    broken.write_text("- just-a-list\n", encoding="utf-8")
    with pytest.raises(WorkflowParseError, match="mapping"):
        _load_yaml(broken)
    with pytest.raises(WorkflowParseError, match="runs-on"):
        _job("demo.yml", "build", {"runs-on": ["ubuntu-24.04"]})
    with pytest.raises(WorkflowParseError, match="fromJSON"):
        _matrix({"matrix": "${{ inputs.matrix }}"})
    with pytest.raises(WorkflowParseError, match=r"\$\{\{"):
        _matrix({"matrix": "slot"})
    with pytest.raises(WorkflowParseError, match="job output"):
        _matrix({"matrix": "${{ fromJSON(github.ref) }}"})
    with pytest.raises(WorkflowParseError, match="integer"):
        _optional_int("nope")
    with pytest.raises(WorkflowParseError, match="boolean"):
        _as_bool("maybe", default=False)
    with pytest.raises(WorkflowParseError, match="text"):
        _as_text({"no": "text"})
    assert _as_text(value=True) == "true"
    assert _as_bool(None, default=True) is True
    assert _optional_int(None) is None


def test_triggers_select_the_matching_workflows() -> None:
    workflows = tuple(
        load_workflows(ROOT)[name] for name in sorted(load_workflows(ROOT))
    )
    push = select_workflows(workflows, _event())
    assert any(item.filename == "update.yml" for item in push)
    assert all(item.filename != "update-native.yml" for item in push)
    scheduled = select_workflows(
        workflows, _event(source="schedule", changed_paths=None)
    )
    assert [item.filename for item in scheduled] == ["update.yml"]
    assert select_workflows(workflows, _event(source="workflow_dispatch")) == ()
    named = select_workflows(workflows, _event(workflow="PR quality"))
    assert [item.filename for item in named] == ["pr-quality.yml"]
    with pytest.raises(WorkflowParseError, match="unknown workflow"):
        select_workflows(workflows, _event(workflow="missing"))
    assert select_workflows(workflows, _event(changed_paths=())) == ()
    assert any(
        item.filename == "pr-quality.yml"
        for item in select_workflows(
            workflows,
            _event(source="pull_request", changed_paths=None, pull_request="4"),
        )
    )
    zed = select_workflows(
        workflows,
        _event(branch="cursor/topic-fa49", changed_paths=(".github/zed-kick",)),
    )
    assert any(item.filename == "zed-darwin.yml" for item in zed)
    assert path_matches("packages/**", "packages/a/b.nix")
    assert not path_matches("packages/*", "packages/a/b.nix")
    manual = event_from_env(
        {"BUILDKITE_SOURCE": "ui", "BUILDKITE_PULL_REQUEST": "false"}, None
    )
    assert manual.source == "workflow_dispatch"
    assert event_source({"BUILDKITE_PULL_REQUEST": "9"}) == "pull_request"
    assert event_source({"BUILDKITE_SOURCE": "schedule"}) == "schedule"
    assert event_source({"BUILDKITE_SOURCE": "api"}) == "workflow_dispatch"
    assert event_source({"BUILDKITE_SOURCE": "webhook"}) == "push"
    dispatch = _event(source="workflow_dispatch")
    inputs = workflow_inputs(
        dispatch, {"NIXCFG_REPAIR": "true", "NIXCFG_UPDATE_TARGETS": "zed"}
    )
    assert inputs["repair"] is True
    assert inputs["targets"] == "zed"
    assert inputs["validate_all_packages"] is None
    assert workflow_inputs(_event(), {"NIXCFG_REPAIR": "true"}) == {}
    assert workflow_inputs(dispatch, {"NIXCFG_REPAIR": ""})["repair"] is None
    assert workflow_inputs(dispatch, {"NIXCFG_REPAIR": "maybe"})["repair"] == "maybe"
    assert (
        workflow_inputs(dispatch, {"NIXCFG_VALIDATE_ALL_PACKAGES": "false"})[
            "validate_all_packages"
        ]
        is False
    )


def test_admission_cancels_older_builds_and_waits_for_newer_ones() -> None:
    current = BuildRef("current", 10, "running")
    older = BuildRef("older", 9, "running")
    newer = BuildRef("newer", 11, "scheduled")
    idle = BuildRef("done", 8, "passed")
    assert admit((current, idle), current_id="current", cancel=True) == Admission(
        (), wait=False
    )
    assert admit((current, older), current_id="current", cancel=False) == Admission(
        (), wait=True
    )
    assert admit((older,), current_id="missing", cancel=True) == Admission(
        ("older",), wait=False
    )
    decision = admit((current, older, newer), current_id="current", cancel=True)
    assert decision == Admission(("older",), wait=True)
    workflow = load_workflows(ROOT)["update.yml"]
    quality = load_workflows(ROOT)["pr-quality.yml"]
    assert concurrency_cancels((quality,), expr_context(_event())) is None
    assert apply_admission(
        (quality,), expr_context(_event()), (older,), current_id="current"
    ) == Admission(
        (),
        wait=False,
    )
    main_context = expr_context(_event())
    assert concurrency_cancels((workflow,), main_context) is False
    feature = expr_context(_event(branch="topic"))
    assert concurrency_cancels((workflow,), feature) is True
    builds = parse_build_list([
        {"id": "a", "number": 3, "state": "running"},
        {"id": "b", "number": "4", "state": "passed"},
        {"number": 1, "state": "running"},
        "skip",
        {"id": "c", "number": True, "state": "running"},
    ])
    assert [item.build_id for item in builds] == ["a", "b"]
    with pytest.raises(WorkflowParseError, match="not a list"):
        parse_build_list({"builds": []})
    assert matching_job_ids(
        {"jobs": [{"name": "validate (0)", "id": "job-1"}, "nope"]}, ("validate (0)",)
    ) == ("job-1",)
    assert matching_job_ids({}, ("validate (0)",)) == ()


def test_admission_http_and_runtime_decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Response:
        def __init__(self, payload: object) -> None:
            self._payload = payload

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, length: int = -1) -> bytes:
            del length
            return json.dumps(self._payload).encode()

    seen: list[str] = []

    def opener(request: object, timeout: int) -> _Response:
        del timeout
        seen.append(getattr(request, "full_url", ""))
        if seen[-1].endswith("/cancel"):
            return _Response({})
        return _Response([{"id": "a", "number": 1, "state": "running"}])

    builds = fetch_branch_builds(
        organization="gkze",
        pipeline="nixcfg",
        branch="main",
        token="token",  # noqa: S106 -- fixture credential, not a secret
        opener=opener,
    )
    assert builds[0].build_id == "a"
    cancel_build(
        organization="gkze",
        pipeline="nixcfg",
        build_id="a",
        token="token",  # noqa: S106 -- fixture credential, not a secret
        opener=opener,
    )
    cancel_job(
        organization="gkze",
        pipeline="nixcfg",
        number="1",
        job_id="job",
        token="token",  # noqa: S106 -- fixture credential, not a secret
        opener=opener,
    )
    assert any(url.endswith("/cancel") for url in seen)
    assert publish_allowed("flush-cachix", None) is True
    assert publish_allowed("publish", None) is False
    assert publish_allowed("publish", "true") is True
    assert publish_allowed("start-repair", "false") is False
    assert combine_results(()) == "skipped"
    assert combine_results(("success", "timed_out")) == "timed_out"
    assert combine_results(("success", "skipped")) == "failure"
    assert combine_results(("success", "success")) == "success"
    assert combine_results(("skipped", "skipped")) == "skipped"
    assert combine_results(("failure", "timed_out")) == "failure"
    assert combine_results(("startup_failure",)) == "startup_failure"
    assert prefer_conclusion("success", "failure") == "failure"
    assert prefer_conclusion("timed_out", "failure") == "timed_out"
    assert prefer_conclusion("failure", "success") == "failure"
    assert prefer_conclusion("startup_failure", "failure") == "startup_failure"
    assert prefer_conclusion("cancelled", "failure") == "cancelled"
    assert prefer_conclusion("failure", "cancelled") == "cancelled"
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "aarch64")
    assert runner_identity() == ("Linux", "ARM64")
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    assert runner_identity() == ("macOS", "X64")
    monkeypatch.setattr(platform, "system", lambda: "FreeBSD")
    assert runner_identity()[0] == "FreeBSD"
    assert prepare_build_slots("validate", {}) is None
    assert prepare_build_slots("prepare", {"UPDATE_MAX_NIX_BUILDS": "1"}) is None
    monkeypatch.setattr("os.cpu_count", lambda: None)
    assert prepare_build_slots("prepare-pins", {}) == "1"
    assert daemon_argv("/tmp/sock") == [
        "cachix",
        "daemon",
        "run",
        "--socket",
        "/tmp/sock",
    ]
    hook = hook_script(
        socket="/tmp/sock",
        token_file="/tmp/token",  # noqa: S106 -- fixture path, not a secret
    )
    assert "cachix push gkze" in hook
    assert "token-value" not in hook
    assert "post-build-hook" in nix_user_conf("/tmp/hook")
    env = github_process_env({"BUILDKITE_BRANCH": "main", "BUILDKITE_COMMIT": "abc"})
    assert "GITHUB_ACTIONS" not in env
    assert env["GITHUB_RUN_ID"] == ""
    assert env["RUNNER_OS"] in {"Linux", "macOS", "FreeBSD"}


def test_sibling_document_and_jobs_refresh(  # noqa: PLR0915
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert conclusion_for_state("passed") == ("completed", "success")
    assert conclusion_for_state("running") == ("in_progress", None)
    assert conclusion_for_state("mystery") == ("in_progress", None)
    assert conclusion_for_state("timed_out") == ("completed", "timed_out")
    payload = jobs_payload({
        "jobs": [
            {"label": "prepare (0)", "state": "failed", "finished_at": None},
            {"name": "waiting", "state": "waiting_failed"},
            {"name": 1, "state": "passed"},
            "skip",
        ]
    })
    assert payload["jobs"][0]["conclusion"] == "failure"
    assert jobs_payload({}) == {"jobs": []}
    destination = tmp_path / "nested" / "jobs.json"
    write_jobs(destination, payload)
    assert json.loads(destination.read_text(encoding="utf-8"))["jobs"]

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, length: int = -1) -> bytes:
            del length
            return b'{"jobs":[]}'

    build = fetch_build(
        organization="gkze",
        pipeline="nixcfg",
        number="1",
        token="token",  # noqa: S106 -- fixture credential, not a secret
        opener=lambda *args, **kwargs: _Response(),
    )
    assert build == {"jobs": []}
    monkeypatch.setenv("NIXCFG_SIBLING_JOBS_PATH", str(destination))
    monkeypatch.setenv("BUILDKITE_API_TOKEN", "token")
    monkeypatch.setenv("BUILDKITE_ORGANIZATION_SLUG", "gkze")
    monkeypatch.setenv("BUILDKITE_PIPELINE_SLUG", "nixcfg")
    monkeypatch.setenv("BUILDKITE_BUILD_NUMBER", "1")
    monkeypatch.setattr(
        "lib.buildkite.siblings.fetch_build",
        lambda **kwargs: {"jobs": [{"name": "prepare-x86", "state": "failed"}]},
    )
    assert siblings_main() == 0
    assert (
        json.loads(destination.read_text(encoding="utf-8"))["jobs"][0]["name"]
        == "prepare-x86"
    )
    assert siblings_main({"NIXCFG_SIBLING_JOBS_PATH": ""}) == 2
    monkeypatch.setattr(
        "lib.buildkite.siblings.fetch_build",
        lambda **kwargs: (_ for _ in ()).throw(OSError("down")),
    )
    assert siblings_main() == 3

    monkeypatch.setenv("GITHUB_ACTIONS", "false")
    monkeypatch.setenv("NIXCFG_SYSTEM", "aarch64-darwin")
    monkeypatch.setenv("NIXCFG_VALIDATE_SCOPE", "all")
    monkeypatch.setenv("NIXCFG_CI_STAGE", "prepare")
    monkeypatch.delenv("NIXCFG_CANARY", raising=False)
    path = tmp_path / "refresh.json"
    path.write_text(
        json.dumps({
            "jobs": [
                {"name": "prepare-arm", "status": "completed", "conclusion": "failure"}
            ]
        }),
        encoding="utf-8",
    )
    script = tmp_path / "refresh.py"
    script.write_text(
        f"from pathlib import Path\nPath({str(path)!r}).write_text({path.read_text()!r}, encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "NIXCFG_SIBLING_REFRESH_ARGV",
        json.dumps([__import__("sys").executable, str(script)]),
    )
    monkeypatch.setenv("NIXCFG_SIBLING_JOBS_PATH", str(path))
    assert jobs._sibling_watch_configured("prepare") is True
    stop = jobs._poll_sibling_stop("prepare", __import__("io").StringIO())
    assert stop is not None
    assert stop.job == "prepare-arm"
    monkeypatch.setenv("NIXCFG_SIBLING_REFRESH_ARGV", "{")
    assert jobs._poll_sibling_stop("prepare", __import__("io").StringIO()) is None
    monkeypatch.setenv("NIXCFG_SIBLING_REFRESH_ARGV", json.dumps([1]))
    assert jobs._poll_sibling_stop("prepare", __import__("io").StringIO()) is None
    monkeypatch.setenv("NIXCFG_SIBLING_REFRESH_ARGV", json.dumps(["missing-binary"]))
    monkeypatch.setenv("NIXCFG_SIBLING_JOBS_PATH", "")
    assert jobs._sibling_watch_configured("prepare") is False
    with pytest.raises(jobs.sibling_failfast.SiblingPollError, match="refresh path"):
        jobs._sibling_jobs()
    monkeypatch.setenv("NIXCFG_SIBLING_JOBS_PATH", str(path))
    assert jobs._poll_sibling_stop("prepare", __import__("io").StringIO()) is None
    monkeypatch.setenv(
        "NIXCFG_SIBLING_REFRESH_ARGV",
        json.dumps([__import__("sys").executable, "-c", "raise SystemExit(7)"]),
    )
    assert jobs._poll_sibling_stop("prepare", __import__("io").StringIO()) is None
    monkeypatch.setenv(
        "NIXCFG_SIBLING_REFRESH_ARGV",
        json.dumps([__import__("sys").executable, "-c", "print(1)"]),
    )
    path.write_text("not-json", encoding="utf-8")
    assert jobs._poll_sibling_stop("prepare", __import__("io").StringIO()) is None


def test_step_and_upload_entrypoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lib.buildkite import step as step_mod
    from lib.buildkite.siblings import fetch_build

    monkeypatch.setitem(step_mod._COMMANDS, "skip", lambda *args, **kwargs: 7)
    assert step_mod.main(["skip", "update.yml", "publish"], {}) == 7
    monkeypatch.setattr(sys, "argv", ["step", "skip", "update.yml", "publish"])
    assert step_mod.main() == 7

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, length: int = -1) -> bytes:
            del length
            return b"[]"

    with pytest.raises(TypeError, match="not an object"):
        fetch_build(
            organization="gkze",
            pipeline="nixcfg",
            number="1",
            token="token",  # noqa: S106 -- fixture credential, not a secret
            opener=lambda *args, **kwargs: _Response(),
        )


def test_artifact_layout(tmp_path: Path) -> None:
    source = tmp_path / "candidate"
    source.mkdir()
    (source / "candidate.json").write_text("{}", encoding="utf-8")
    assert stage_upload(tmp_path / "stage", "prepare-x86_64-linux", source) == 1
    single = tmp_path / "one.txt"
    single.write_text("x", encoding="utf-8")
    assert stage_upload(tmp_path / "stage", "note", single) == 1
    assert stage_upload(tmp_path / "stage", "missing", tmp_path / "absent") == 0
    dest = tmp_path / "dest"
    arrange_download(tmp_path / "stage", dest, None)
    assert (dest / "prepare-x86_64-linux" / "candidate.json").is_file()
    named = tmp_path / "named"
    arrange_download(tmp_path / "stage", named, "note")
    assert (named / "one.txt").read_text(encoding="utf-8") == "x"
    with pytest.raises(WorkflowParseError, match="missing artifact"):
        arrange_download(tmp_path / "stage", tmp_path / "other", "nope")
    arrange_download(tmp_path / "empty", tmp_path / "nowhere", None)
    nested = tmp_path / "nested-src" / "sub"
    nested.mkdir(parents=True)
    (nested / "child.txt").write_text("c", encoding="utf-8")
    assert (
        stage_upload(tmp_path / "nested-stage", "nested", tmp_path / "nested-src") == 1
    )
    file_stage = tmp_path / "file-stage" / "bk-artifacts"
    file_stage.mkdir(parents=True)
    (file_stage / "solo").write_text("z", encoding="utf-8")
    arrange_download(tmp_path / "file-stage", tmp_path / "file-dest", "solo")
    assert (tmp_path / "file-dest" / "solo").read_text(encoding="utf-8") == "z"


def test_parser_and_emit_defensive_branches() -> None:
    from lib.buildkite.emit import (
        _approval_and_step,
        _concrete_step,
        _group,
        static_cells,
    )
    from lib.buildkite.graph import (
        Job,
        Matrix,
        Workflow,
        _as_bool,
        _call_secrets,
        _flatten,
        _literal_with,
        _mapping,
        _matrix,
        _sequence,
        _string,
        _triggers,
        contract_from_document,
    )

    workflow = load_workflows(ROOT)["update.yml"]
    with pytest.raises(WorkflowParseError, match="has no job"):
        workflow.job("missing")
    native = load_workflows(ROOT)["update-native.yml"]
    with pytest.raises(WorkflowParseError, match="no runner queue"):
        render_pipeline((native,), context=expr_context(_event()))
    dynamic = Matrix(
        fail_fast=False, max_parallel=None, axes=(), dynamic_from="producer.matrix"
    )
    assert static_cells(dynamic) == ()
    assert static_cells(None) == ()
    empty = Workflow(
        filename="empty.yml",
        name="Empty",
        triggers=workflow.triggers,
        concurrency=None,
        jobs=(
            Job(
                "empty.yml",
                "second",
                "second",
                ("first",),
                "",
                "linux-x86",
                None,
                None,
                dynamic,
                (),
                (),
                None,
                None,
                (),
                (),
            ),
            Job(
                "empty.yml",
                "first",
                "first",
                ("second",),
                "",
                "linux-x86",
                None,
                None,
                dynamic,
                (),
                (),
                None,
                None,
                (),
                (),
            ),
        ),
        call_secrets=(),
    )
    assert _group(empty, expr_context(_event())) is None
    publish = workflow.job("start-repair")

    def concrete(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return {"depends_on": "not-a-list", "key": "start"}

    monkey = pytest.MonkeyPatch()
    monkey.setattr("lib.buildkite.emit._concrete_step", concrete)
    with pytest.raises(WorkflowParseError, match="depends_on"):
        _approval_and_step(workflow, publish, None, expr_context(_event()))
    monkey.undo()
    assert _concrete_step.__name__ == "_concrete_step"
    assert contract_from_document({}) == ({}, ())
    assert contract_from_document({
        "inputs": {"bare": "x"},
        "outputs": {"bare": "y"},
    }) == ({}, ())
    assert contract_from_document({
        "inputs": {"a": {"default": "1"}, "b": {"default": "2"}},
        "outputs": {"c": {"value": "z"}},
    }) == ({"a": "1", "b": "2"}, (("c", "z"),))
    assert (
        _literal_with((("other", "x"), ("runner", "${{ matrix.os }}")), "runner") == ""
    )
    assert _matrix({"fail-fast": "true"}) is None
    assert _triggers({"push": None, "pull_request": None}).events == frozenset({
        "push",
        "pull_request",
    })
    assert _call_secrets({"on": None}) == ()
    assert _call_secrets({"on": {"workflow_call": None}}) == ()
    assert _call_secrets({"on": {"workflow_call": {"secrets": None}}}) == ()
    assert _flatten(1) == ""
    assert _as_bool(value=True, default=False) is True
    with pytest.raises(WorkflowParseError, match="must be a string"):
        _string(None, "name")
    with pytest.raises(WorkflowParseError, match="must be a mapping"):
        _mapping("no", "body")
    with pytest.raises(WorkflowParseError, match="must be a sequence"):
        _sequence("no", "steps")
    from lib.buildkite.triggers import _matches

    assert _matches(native, _event(source="workflow_call")) is False
    assert parse_build_list_gap()


def parse_build_list_gap() -> bool:
    assert matching_job_ids(
        {
            "jobs": [
                {"name": "other", "id": "job-2"},
                {"label": "wanted", "id": "job-3"},
            ]
        },
        ("wanted",),
    ) == ("job-3",)
    return True


def _artifact_name(
    expression: str, inputs: dict[str, str], *, device_fault: str
) -> str:
    context = ExprContext(
        inputs=inputs,
        needs={},
        steps={
            "candidate": {"outputs": {"device_fault": device_fault}},
            "storage": {"outputs": {"device_fault": ""}},
        },
        github={},
        runner={},
        secrets={},
        matrix={},
    )
    return eval_text(expression, context)


def _all_steps(pipeline: dict[str, object]) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []

    def walk(steps: object) -> None:
        if not isinstance(steps, list):
            return
        for step in steps:
            if isinstance(step, dict):
                found.append(step)
                walk(step.get("steps"))

    walk(pipeline.get("steps"))
    return found


def _assert_needs(actual: tuple[str, ...], raw: object) -> None:
    if raw is None:
        assert actual == ()
    elif isinstance(raw, str):
        assert actual == (raw,)
    else:
        assert actual == tuple(raw)


def _assert_if(actual: str | None, raw: object) -> None:
    if raw is None:
        assert actual is None
    else:
        assert actual == str(raw).strip()


def _assert_matrix(job: object, strategy: object) -> None:
    from lib.buildkite.graph import Job

    assert isinstance(job, Job)
    if not isinstance(strategy, dict) or "matrix" not in strategy:
        assert job.matrix is None
        return
    assert job.matrix is not None
    matrix = strategy["matrix"]
    if isinstance(matrix, str):
        assert job.matrix.dynamic
    else:
        axes = {
            key: tuple(matrix[key])
            for key in matrix
            if key not in {"include", "exclude"}
        }
        assert dict(job.matrix.axes) == axes
    fail_fast = strategy.get("fail-fast", "true") != "false"
    assert job.matrix.fail_fast is fail_fast


def _assert_queue_and_timeout(
    job: object, body: dict[str, object], native_timeout: int | None
) -> None:
    from lib.buildkite.graph import Job

    assert isinstance(job, Job)
    runs_on = body.get("runs-on")
    if isinstance(runs_on, str) and not runs_on.strip().startswith("${{"):
        assert job.queue == queue_for_runner(runs_on.strip())
    elif job.uses == _NATIVE:
        assert job.queue == queue_for_runner(dict(job.with_args)["runner"])
        if "timeout-minutes" not in body:
            assert job.timeout_minutes == native_timeout
    if isinstance(body.get("timeout-minutes"), str):
        assert job.timeout_minutes == int(body["timeout-minutes"])


def _assert_secrets(job: object, body: dict[str, object], native: object) -> None:
    from lib.buildkite.graph import Job

    assert isinstance(job, Job)
    assert isinstance(native, Job)
    text = _flatten(body)
    expected = set(_SECRET.findall(text))
    if "github.token" in text:
        expected.add("GITHUB_TOKEN")
    if job.uses == _NATIVE:
        expected.update(native.secrets)
        native_workflow = load_workflows(ROOT)["update-native.yml"]
        expected.update(native_workflow.call_secrets)
    assert set(job.secrets) == expected


def _assert_outputs(job: object, body: dict[str, object], native: object) -> None:
    from lib.buildkite.graph import Job

    assert isinstance(job, Job)
    assert isinstance(native, Job)
    if isinstance(body.get("outputs"), dict):
        assert job.outputs == tuple(
            (key, value) for key, value in body["outputs"].items()
        )
    elif job.uses == _NATIVE:
        assert job.outputs == native.outputs


def _assert_triggers(workflow: object, body: dict[str, object]) -> None:
    from lib.buildkite.graph import Workflow

    assert isinstance(workflow, Workflow)
    if "pull_request" in body:
        assert "pull_request" in workflow.triggers.events
    if "push" in body:
        assert "push" in workflow.triggers.events
    if "schedule" in body:
        assert workflow.triggers.crons


def _assert_concurrency(workflow: object, body: object) -> None:
    from lib.buildkite.graph import Workflow

    assert isinstance(workflow, Workflow)
    if body is None:
        assert workflow.concurrency is None
        return
    assert isinstance(body, dict)
    assert workflow.concurrency is not None
    assert workflow.concurrency.group == body["group"]
