"""Workflow contracts for per-package update pull requests."""

import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


class _UniqueLoader(yaml.SafeLoader):
    """Reject a mapping key that appears twice. The stock loaders keep the last one."""


def _unique_mapping(
    loader: yaml.SafeLoader, node: yaml.MappingNode
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=False)
        if key in mapping:
            msg = f"duplicate key {key}"
            raise AssertionError(msg)
        mapping[key] = loader.construct_object(value_node, deep=False)
    return mapping


_UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _unique_mapping,
)


def _workflow(name: str) -> dict[str, object]:
    loaded = yaml.load(
        (WORKFLOWS / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert isinstance(loaded, dict)
    return loaded


def _python_steps(workflow: dict[str, object]) -> list[str]:
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    sources: list[str] = []
    for job in jobs.values():
        assert isinstance(job, dict)
        for step in job.get("steps") or []:
            if isinstance(step, dict) and step.get("shell") == "python":
                sources.append(str(step["run"]))
    return sources


def _argv_lists(source: str) -> list[list[object]]:
    lists: list[list[object]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.List):
            continue
        values: list[object] = []
        for elt in node.elts:
            if isinstance(elt, ast.Constant):
                values.append(elt.value)
            else:
                values.append(ast.unparse(elt))
        lists.append(values)
    return lists


def _all_argvs(name: str) -> list[list[object]]:
    found: list[list[object]] = []
    for source in _python_steps(_workflow(name)):
        found.extend(_argv_lists(source))
    return found


def test_nightly_update_and_prepare_skip_planner_branches() -> None:
    update = _workflow("update.yml")
    assert update["on"]["schedule"] == [{"cron": "17 7 * * *"}]
    prepare = _workflow("pr-update-prepare.yml")
    condition = " ".join(prepare["jobs"]["targets"]["if"].split())
    assert condition == (
        "github.event.pull_request.head.repo.full_name == github.repository && "
        "!startsWith(github.head_ref, 'codex/update-') && "
        "!startsWith(github.head_ref, 'update/')"
    )


def test_automerge_pins_the_head_and_does_not_bypass_checks() -> None:
    workflow = _workflow("update-automerge.yml")
    trigger = workflow["on"]["workflow_run"]
    assert trigger["workflows"] == ["PR closure gate"]
    assert trigger["types"] == ["completed"]
    job = workflow["jobs"]["merge"]
    assert "workflow_run.head_branch" in job["if"]
    assert "workflow_run.conclusion == 'success'" in job["if"]
    argvs = _all_argvs("update-automerge.yml")
    merge = next(argv for argv in argvs if argv[:3] == ["gh", "pr", "merge"])
    assert "--squash" in merge
    assert "--match-head-commit" in merge
    assert "--auto" not in merge
    assert "--admin" not in merge
    bisect = next(
        argv for argv in argvs if argv[:4] == ["gh", "workflow", "run", "update-pr.yml"]
    )
    assert "from_default=true" in bisect
    assert "mode=rehash" in bisect
    assert any("normalize-checks" in argv for argv in argvs)


def test_self_heal_stays_on_the_same_branch() -> None:
    workflow = _workflow("update-self-heal.yml")
    trigger = workflow["on"]["workflow_run"]
    assert trigger["workflows"] == ["PR closure gate"]
    assert "workflow_run.conclusion == 'failure'" in workflow["jobs"]["decide"]["if"]
    heal = workflow["jobs"]["heal"]
    assert heal["concurrency"] == {
        "group": "nixcfg-darwin-${{ needs.decide.outputs.priority }}",
        "cancel-in-progress": "false",
        "queue": "max",
    }
    push = workflow["jobs"]["push"]
    commit = next(
        step
        for step in push["steps"]
        if step.get("name") == "Commit the repair on this branch"
    )
    assert commit["env"]["NIXCFG_CI_STAGE"] == "commit-heal"
    argvs = _all_argvs("update-self-heal.yml")
    assert not any(argv[:3] == ["gh", "pr", "create"] for argv in argvs)
    assert not any("--force" in argv for argv in argvs)
    dispatches = [
        argv for argv in argvs if argv[:4] == ["gh", "workflow", "run", "update-pr.yml"]
    ]
    assert len(dispatches) == 3
    same_branch = next(argv for argv in dispatches if "from_default=true" in argv)
    assert "os.environ['BRANCH']" in same_branch
    opened = next(
        argv
        for argv in dispatches
        if "from_default=false" in argv and "os.environ['BASE']" in argv
    )
    assert "existing_number=0" in opened
    rehash = next(
        argv
        for argv in dispatches
        if "from_default=false" in argv and "os.environ['BASE']" not in argv
    )
    assert "mode=rehash" in rehash
    assert any("normalize-checks" in argv for argv in argvs)
    permissions = workflow["jobs"]["heal"]["permissions"]
    assert permissions["copilot-requests"] == "write"
    config = yaml.load(
        (ROOT / ".github" / "actionlint.yaml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    ignored = config["paths"][".github/workflows/update-self-heal.yml"]["ignore"]
    assert any("copilot-requests" in item for item in ignored)
    assert any("queue" in item for item in ignored)


def test_planner_reuses_one_pull_request_and_caps_darwin() -> None:
    planner = _workflow("update-planner.yml")
    assert planner["on"]["schedule"] == [{"cron": "23 9 * * *"}]
    assert planner["concurrency"]["group"] == "nixcfg-update-planner"
    plan = next(
        step
        for step in planner["jobs"]["discover"]["steps"]
        if step.get("name") == "Plan update pull requests"
    )
    assert "GH_TOKEN" in plan["env"]
    assert "GITHUB_TOKEN" in plan["env"]
    called = planner["jobs"]["prepare"]["with"]
    assert called["mode"] == "plan"
    pull = _workflow("update-pr.yml")
    assert pull["on"]["workflow_call"]["inputs"]["mode"]["default"] == "plan"
    assert pull["on"]["workflow_dispatch"]["inputs"]["mode"]["default"] == "rehash"
    assert pull["jobs"]["prepare-darwin"]["concurrency"]["queue"] == "max"
    assert (
        pull["jobs"]["prepare-darwin"]["concurrency"]["cancel-in-progress"] == "false"
    )
    publish = next(
        step
        for step in pull["jobs"]["publish"]["steps"]
        if step.get("name") == "Publish the prepared candidate on this branch"
    )
    argvs = _argv_lists(publish["run"])
    pushes = [argv for argv in argvs if argv[:2] == ["git", "push"]]
    assert pushes == [["git", "push", "origin", "f'HEAD:{branch}'"]]
    assert not any("--force" in argv for argv in argvs)
    assert any("accept-candidate" in argv for argv in argvs)
    assert ["git", "read-tree", "--reset", "-u", "f'origin/{base}'"] in argvs
    native = _workflow("update-native.yml")
    inputs = native["on"]["workflow_call"]["inputs"]
    assert inputs["artifact_namespace"]["default"] == ""
    assert list(inputs).count("checkout_ref") == 1
    downloads = [
        step
        for step in native["jobs"]["native"]["steps"]
        if str(step.get("uses", "")).startswith("actions/download-artifact@")
        and step.get("if") == "inputs.previous != ''"
    ]
    assert "inputs.artifact_namespace" in downloads[0]["with"]["name"]


def test_update_workflows_have_unique_mapping_keys() -> None:
    for name in (
        "update-native.yml",
        "update-pr.yml",
        "update-planner.yml",
        "update-self-heal.yml",
        "update-automerge.yml",
        "pr-closure-gate.yml",
    ):
        loaded = yaml.load(
            (WORKFLOWS / name).read_text(encoding="utf-8"),
            Loader=_UniqueLoader,  # noqa: S506 -- unique keys; maps, lists, and scalars only
        )
        assert isinstance(loaded, dict)
