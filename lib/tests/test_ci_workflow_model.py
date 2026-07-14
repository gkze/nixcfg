"""Behavior tests for the typed GitHub Actions workflow model and renderer."""

from __future__ import annotations

import json

import pytest
import yaml

from lib.update.ci import workflow_model as wm
from lib.update.ci._workflow_yaml import GitHubActionsYamlLoader


def _workflow(**overrides: object) -> wm.Workflow:
    base: dict[str, object] = {
        "name": "Demo",
        "triggers": wm.Triggers(),
        "permissions": {"contents": "read"},
        "concurrency": wm.Concurrency(group="demo", cancel_in_progress=True),
        "jobs": {
            "build": wm.Job(
                runs_on="ubuntu-24.04",
                steps=(wm.Step(name="Run", run="echo ok"),),
            ),
        },
    }
    base.update(overrides)
    return wm.Workflow.model_validate(base)


def _render(workflow: wm.Workflow) -> str:
    return workflow.render(header_comments=("generated for tests",))


def _parse(rendered: str) -> object:
    return yaml.load(rendered, Loader=GitHubActionsYamlLoader)  # noqa: S506


# ---------------------------------------------------------------------------
# Scalar emission
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "",
        " padded ",
        "true",
        "ON",
        "null",
        "~",
        "42",
        "-3.5",
        "1e6",
        "&anchor",
        "- item",
        "-",
        "key: value",
        "trailing:",
        "text # comment",
        "tab\tseparated",
    ],
)
def test_needs_quote_flags_ambiguous_scalars(value: str) -> None:
    """Quote every scalar whose plain form would change type or structure."""
    assert wm._needs_quote(value)


@pytest.mark.parametrize(
    "value",
    ["plain", "${{ matrix.target }}", ".#pkgs.x86_64-linux.nixcfg", "a-b_c", "-flag"],
)
def test_needs_quote_accepts_plain_scalars(value: str) -> None:
    """Keep unambiguous scalars unquoted."""
    assert not wm._needs_quote(value)


def test_scalar_entry_lines_cover_value_shapes() -> None:
    """Render null, bool, int, quoted, plain, folded, and literal scalars."""
    assert wm._scalar_entry_lines("k", None, indent=0) == ["k:"]
    assert wm._scalar_entry_lines("k", True, indent=0) == ["k: true"]  # noqa: FBT003
    assert wm._scalar_entry_lines("k", False, indent=0) == ["k: false"]  # noqa: FBT003
    assert wm._scalar_entry_lines("k", 7, indent=0) == ["k: 7"]
    assert wm._scalar_entry_lines("k", "a: b", indent=0) == ['k: "a: b"']
    assert wm._scalar_entry_lines("k", "plain", indent=0) == ["k: plain"]

    folded = wm._scalar_entry_lines("k", "word " * 40 + "end", indent=2)
    assert folded[0] == "  k: >-"
    assert all(len(line) <= wm.MAX_LINE_WIDTH for line in folded)

    literal = wm._scalar_entry_lines("k", "line one\nline two\n", indent=0)
    assert literal == ["k: |", "  line one", "  line two"]
    stripped = wm._scalar_entry_lines("k", "line one\n\nline two", indent=0)
    assert stripped == ["k: |-", "  line one", "", "  line two"]


def test_scalar_entry_lines_reject_unrenderable_values() -> None:
    """Refuse quoted-too-long, unfoldable, and malformed block scalars."""
    with pytest.raises(wm.WorkflowRenderError, match="does not fit"):
        wm._scalar_entry_lines("k", "x: " + "y" * 120, indent=0)
    with pytest.raises(wm.WorkflowRenderError, match="Cannot fold"):
        wm._scalar_entry_lines(
            "k",
            "double  space " + " ".join(["word"] * 30),
            indent=0,
        )
    with pytest.raises(wm.WorkflowRenderError, match="trailing whitespace"):
        wm._scalar_entry_lines("k", "line \nnext\n", indent=0)
    with pytest.raises(wm.WorkflowRenderError, match="unindented line"):
        wm._scalar_entry_lines("k", "  indented\nnext\n", indent=0)


def test_folded_lines_round_trip_through_yaml() -> None:
    """Folded long scalars parse back to the original single-line value."""
    value = " ".join(f"token-{index}" for index in range(40))
    rendered = "\n".join(wm._scalar_entry_lines("k", value, indent=0)) + "\n"
    assert _parse(rendered) == {"k": value}


def test_sequence_scalar_line_covers_item_shapes() -> None:
    """Render bool, int, quoted, and plain sequence items."""
    assert wm._sequence_scalar_line(True, indent=0) == "- true"  # noqa: FBT003
    assert wm._sequence_scalar_line(False, indent=0) == "- false"  # noqa: FBT003
    assert wm._sequence_scalar_line(3, indent=0) == "- 3"
    assert wm._sequence_scalar_line("yes", indent=0) == '- "yes"'
    assert wm._sequence_scalar_line("plain", indent=2) == "  - plain"
    with pytest.raises(wm.WorkflowRenderError, match="Unsupported scalar sequence"):
        wm._sequence_scalar_line("a\nb", indent=0)


def test_entry_lines_reject_empty_and_nested_collections() -> None:
    """Refuse empty containers and nested sequence items."""
    with pytest.raises(wm.WorkflowRenderError, match="empty mapping"):
        wm._entry_lines("k", {}, indent=0)
    with pytest.raises(wm.WorkflowRenderError, match="empty sequence"):
        wm._entry_lines("k", [], indent=0)
    with pytest.raises(wm.WorkflowRenderError, match="nested sequence item"):
        wm._entry_lines("k", [["nested"]], indent=0)


def test_entry_lines_render_mappings_and_sequences_of_mappings() -> None:
    """Render nested mappings and matrix-include style sequences."""
    rendered = "\n".join(
        wm._entry_lines(
            "matrix",
            {"include": [{"platform": "a"}, {"platform": "b", "extra": True}]},
            indent=0,
        )
    )
    assert _parse(rendered + "\n") == {
        "matrix": {
            "include": [
                {"platform": "a"},
                {"platform": "b", "extra": True},
            ],
        },
    }


# ---------------------------------------------------------------------------
# Model data shapes
# ---------------------------------------------------------------------------


def test_step_iter_items_skips_unset_fields() -> None:
    """Only render populated step keys, in canonical order."""
    step = wm.Step(  # noqa: S604 -- "shell" here is a GitHub Actions step field
        name="Demo",
        id="demo",
        if_="always()",
        continue_on_error=True,
        uses="actions/demo@sha",
        with_={"name": "artifact"},
        env={"TOKEN": "x"},
        shell="bash",
        working_directory="sub",
    )
    assert list(step.to_data()) == [
        "name",
        "id",
        "if",
        "continue-on-error",
        "uses",
        "with",
        "env",
        "shell",
        "working-directory",
    ]
    assert wm.Step(run="echo hi").to_data() == {"run": "echo hi"}


def test_job_to_data_covers_optional_fields() -> None:
    """Serialize scalar vs list needs and every optional job field."""
    single = wm.Job(
        needs=("a",),
        runs_on="ubuntu-24.04",
        steps=(wm.Step(run="true1"),),
    )
    assert single.to_data()["needs"] == "a"

    full = wm.Job(
        name="named",
        needs=("a", "b"),
        if_="always()",
        runs_on="ubuntu-24.04",
        timeout_minutes=5,
        permissions={"contents": "read"},
        outputs={"value": "${{ steps.s.outputs.value }}"},
        strategy=wm.Strategy(
            fail_fast=False,
            max_parallel=2,
            matrix=wm.Matrix(
                values={"check": ("one", "two")},
                include=({"platform": "a"},),
            ),
        ),
        steps=(wm.Step(run="true2"),),
    )
    data = full.to_data()
    assert data["needs"] == ["a", "b"]
    assert data["strategy"] == {
        "fail-fast": False,
        "max-parallel": 2,
        "matrix": {"check": ["one", "two"], "include": [{"platform": "a"}]},
    }

    expression = wm.Strategy(matrix="${{ fromJSON(needs.discover.outputs.t) }}")
    assert expression.to_data() == {
        "matrix": "${{ fromJSON(needs.discover.outputs.t) }}",
    }


def test_workflow_call_trigger_data_shapes() -> None:
    """Serialize bare and input-bearing workflow_call triggers."""
    assert wm.Triggers().to_data() == {"workflow_call": None}
    trigger = wm.WorkflowCallTrigger(
        inputs={
            "ref": wm.WorkflowCallInput(
                description="Branch",
                required=False,
                default="main",
                type="string",
            ),
        },
    )
    assert trigger.to_data() == {
        "inputs": {
            "ref": {
                "description": "Branch",
                "required": False,
                "default": "main",
                "type": "string",
            },
        },
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_render_is_deterministic_and_round_trips() -> None:
    """Render the same document twice and parse it back to the model data."""
    workflow = _workflow(env={"CACHIX_NAME": "demo"})
    first = _render(workflow)
    second = _render(workflow)
    assert first == second
    assert first.startswith("--- # !yamlfmt!:ignore\n# generated for tests\n")
    assert _parse(first) == json.loads(json.dumps(workflow.to_data()))


def test_render_emits_comments_and_blank_job_separators() -> None:
    """Emit trigger, job, step, and trailing comments plus job separators."""
    workflow = _workflow(
        triggers=wm.Triggers(comment_lines=("triggers disabled", "")),
        jobs={
            "first": wm.Job(
                comment_lines=("Phase 1",),
                runs_on="ubuntu-24.04",
                steps=(
                    wm.Step(
                        comment_lines=("before",),
                        trailing_comment_lines=("after",),
                        name="Run",
                        run="echo ok",
                    ),
                ),
            ),
            "second": wm.Job(
                needs=("first",),
                runs_on="ubuntu-24.04",
                steps=(wm.Step(run="echo ok"),),
            ),
        },
    )
    rendered = _render(workflow)
    lines = rendered.splitlines()
    assert "  # triggers disabled" in lines
    assert "  #" in lines
    assert "  # Phase 1" in lines
    assert "      # before" in lines
    assert "      # after" in lines
    assert lines[lines.index("  second:") - 1] == ""


def test_render_places_uses_comment_on_the_uses_line() -> None:
    """Keep pinact version annotations attached to the pinned SHA line."""
    workflow = _workflow(
        jobs={
            "build": wm.Job(
                runs_on="ubuntu-24.04",
                steps=(
                    wm.Step(
                        uses="actions/checkout@0123456789abcdef",
                        uses_comment="v4.2.2",
                        with_={"persist-credentials": False},
                    ),
                ),
            ),
        },
    )
    rendered = _render(workflow)
    assert "      - uses: actions/checkout@0123456789abcdef # v4.2.2\n" in rendered


def test_render_rejects_overlong_uses_comment_lines() -> None:
    """Refuse a pinned uses line whose annotation exceeds the line budget."""
    workflow = _workflow(
        jobs={
            "build": wm.Job(
                runs_on="ubuntu-24.04",
                steps=(
                    wm.Step(
                        uses="actions/checkout@" + "a" * 60,
                        uses_comment="v" * 30,
                    ),
                ),
            ),
        },
    )
    with pytest.raises(wm.WorkflowRenderError, match="too long"):
        _render(workflow)


def test_render_skips_env_block_when_empty() -> None:
    """Omit the top-level env block when no variables are set."""
    rendered = _render(_workflow())
    assert "\nenv:" not in rendered


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------


def _upload(name: str) -> wm.Step:
    return wm.Step(
        uses="actions/upload-artifact@sha",
        with_={"name": name, "path": "out"},
    )


def _download(**with_: str | int | bool) -> wm.Step:
    return wm.Step(uses="actions/download-artifact@sha", with_={**with_, "path": "."})


def _job(*steps: wm.Step, **overrides: object) -> wm.Job:
    base: dict[str, object] = {"runs_on": "ubuntu-24.04", "steps": tuple(steps)}
    base.update(overrides)
    return wm.Job.model_validate(base)


def test_validate_structure_accepts_consistent_artifact_flows() -> None:
    """Accept named and pattern downloads with matching transitive needs."""
    workflow = _workflow(
        jobs={
            "producer": _job(_upload("data-a")),
            "middle": _job(needs=("producer",)),
            "consumer": _job(
                _download(name="data-a"),
                _download(pattern="data-*"),
                needs=("middle",),
            ),
        },
    )
    workflow.validate_structure()


def test_validate_structure_rejects_unknown_artifact_downloads() -> None:
    """Reject downloads whose artifact name or pattern nothing uploads."""
    workflow = _workflow(
        jobs={
            "consumer": _job(
                _download(name="missing"),
                _download(pattern="nothing-*"),
            ),
        },
    )
    with pytest.raises(wm.WorkflowValidationError) as excinfo:
        workflow.validate_structure()
    message = str(excinfo.value)
    assert "downloads unknown artifact `missing`" in message
    assert "downloads unknown pattern `nothing-*`" in message


def test_validate_structure_rejects_missing_needs_dependency() -> None:
    """Reject a consumer that does not transitively need its producer."""
    workflow = _workflow(
        jobs={
            "producer": _job(_upload("data")),
            "consumer": _job(_download(name="data")),
        },
    )
    with pytest.raises(
        wm.WorkflowValidationError,
        match="downloads `data` from `producer` without depending on it",
    ):
        workflow.validate_structure()


def test_validate_structure_rejects_duplicate_uploads_across_jobs() -> None:
    """Reject one artifact name uploaded by two different jobs."""
    workflow = _workflow(
        jobs={
            "one": _job(_upload("data")),
            "two": _job(_upload("data")),
        },
    )
    with pytest.raises(
        wm.WorkflowValidationError,
        match="uploaded by both `one` and `two`",
    ):
        workflow.validate_structure()


def test_validate_structure_allows_repeat_uploads_within_one_job() -> None:
    """Allow matrix-style repeated uploads from the same job."""
    workflow = _workflow(
        jobs={"one": _job(_upload("data"), _upload("data"))},
    )
    workflow.validate_structure()


def test_validate_structure_rejects_nameless_artifact_steps() -> None:
    """Reject uploads without names and downloads without name or pattern."""
    nameless_upload = wm.Step(
        uses="actions/upload-artifact@sha",
        with_={"name": True, "path": "out"},
    )
    workflow = _workflow(
        jobs={"bad": _job(nameless_upload, _download())},
    )
    with pytest.raises(wm.WorkflowValidationError) as excinfo:
        workflow.validate_structure()
    message = str(excinfo.value)
    assert "uploads an artifact without a name" in message
    assert "downloads an artifact without a name or pattern" in message


def test_validate_structure_expands_static_matrix_uploads() -> None:
    """Resolve matrix-templated artifact names via include expansion."""
    producer = wm.Job(
        runs_on="ubuntu-24.04",
        strategy=wm.Strategy(
            matrix=wm.Matrix(include=({"platform": "a"}, {"platform": "b"})),
        ),
        steps=(
            wm.Step(
                if_="matrix.platform == '${{ matrix.platform }}'",
                uses="actions/upload-artifact@sha",
                with_={
                    "name": "sources-${{ matrix.platform }}",
                    "path": "out",
                    "compression-level": 6,
                },
            ),
        ),
    )
    workflow = _workflow(
        jobs={
            "producer": producer,
            "consumer": _job(_download(name="sources-b"), needs=("producer",)),
        },
    )
    workflow.validate_structure()


def test_validate_structure_ignores_dynamic_matrix_expressions() -> None:
    """Keep expression matrices unexpanded and match them via patterns."""
    producer = wm.Job(
        runs_on="ubuntu-24.04",
        strategy=wm.Strategy(matrix="${{ fromJSON(needs.discover.outputs.t) }}"),
        steps=(_upload("target-${{ matrix.slug }}"),),
    )
    workflow = _workflow(
        jobs={
            "producer": producer,
            "consumer": _job(_download(pattern="target-*"), needs=("producer",)),
        },
    )
    workflow.validate_structure()


def test_validate_structure_propagates_needs_graph_errors() -> None:
    """Surface unknown needs from the shared workflow analysis layer."""
    workflow = _workflow(
        jobs={"build": _job(needs=("ghost",))},
    )
    with pytest.raises(RuntimeError, match="unknown needs"):
        workflow.validate_structure()
