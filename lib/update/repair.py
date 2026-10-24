"""Bounded agent repair proposals, isolated from updater validation authority."""

import ast
import asyncio
import json
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, fields, is_dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.comment import Comment
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.primitive import BooleanPrimitive, StringPrimitive
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.unary import UnaryExpression

from lib.codemods.packaging_source_policy import parse_nix_expr_for_policy
from lib.update.candidate import git
from lib.update.cli_options import RepairAgent
from lib.update.events import UpdateEventKind
from lib.update.persistence import IsolatedUpdateWorkspace
from lib.update.process import RunCommandOptions, run_command
from lib.update.run_monitor import default_run_log_root

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from nix_manipulator.expressions.expression import NixExpression

    from lib.update.cli_options import UpdateOptions
    from lib.update.events import UpdateEvent


_PACKAGING_ROOTS = frozenset({"packages", "overlays"})
_ALLOWED_EXACT = frozenset({
    Path("flake.nix"),
    Path("flake.lock"),
    Path("lib/update/planner.py"),
    Path("lib/tests/test_update_planner.py"),
})


def _repair_allowed(path: Path) -> bool:
    """Return whether a changed path is inside the repair allow-list."""
    return path.parts[0] in _PACKAGING_ROOTS or path in _ALLOWED_EXACT


def repair_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    """Repairs may change packaging, pins, and planner selection, never gates."""
    rejected = [path for path in paths if not _repair_allowed(path)]
    if rejected:
        msg = (
            "Repair changed files outside packaging and planner selection: "
            f"{', '.join(map(str, rejected))}"
        )
        raise ValueError(msg)
    if not paths:
        msg = "Agent produced no repair"
        raise ValueError(msg)
    return paths


# Each check-switching binding and the literal value that keeps checks on.
_CHECK_SWITCHES = {"doCheck": True, "dontCheck": False}


def _is_check_skip(node: Binding) -> bool:
    """Return whether a binding disables checks or skips named tests."""
    if node.name == "checkFlags":
        return "--skip" in node.rebuild()
    return node.name in _CHECK_SWITCHES and not (
        isinstance(node.value, BooleanPrimitive)
        and node.value.value is _CHECK_SWITCHES[node.name]
    )


def _walk(
    value: object,
    owner_path: tuple[str, ...] = (),
    seen: set[int] | None = None,
) -> Iterator[tuple[tuple[str, ...], object]]:
    """Yield each AST node once with its enclosing binding attribute path."""
    seen = set() if seen is None else seen
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, Binding):
        owner_path = (*owner_path, value.name)
    yield owner_path, value
    if is_dataclass(value):
        value = [getattr(value, field.name) for field in fields(value)]
    if isinstance(value, list | tuple):
        for child in value:
            yield from _walk(child, owner_path, seen)


def _added_check_skips(
    path: Path, new: bytes | None, old: bytes | None
) -> list[Binding]:
    """Return check-skip occurrences that the repair added or relocated."""

    def skips(content: bytes | None) -> list[tuple[tuple[str, ...], str, Binding]]:
        if content is None:
            return []
        tree = parse_nix_expr_for_policy(content.decode(), context=str(path))
        return [
            (attribute_path, node.rebuild(), node)
            for attribute_path, node in _walk(tree)
            if isinstance(node, Binding) and _is_check_skip(node)
        ]

    baseline = Counter(
        (attribute_path, rendered) for attribute_path, rendered, _node in skips(old)
    )
    added: list[Binding] = []
    for attribute_path, rendered, node in skips(new):
        key = (attribute_path, rendered)
        if baseline[key]:
            baseline[key] -= 1
        else:
            added.append(node)
    return added


def _without_parentheses(node: object) -> object:
    while isinstance(node, Parenthesis):
        node = node.value
    return node


def _is_version_reference(node: object) -> bool:
    """Accept only the package's own version, not unrelated version attributes."""
    node = _without_parentheses(node)
    if isinstance(node, Identifier):
        return node.name == "version"
    return (
        isinstance(node, Select)
        and node.attribute == "version"
        and node.default is None
        and isinstance(node.expression, Identifier)
        and node.expression.name in {"final", "finalAttrs"}
    )


def _version_condition_polarity(node: object) -> bool | None:
    """Return whether an expression is true (or false) only at one literal version."""
    node = _without_parentheses(node)
    if isinstance(node, UnaryExpression) and node.operator == "!":
        polarity = _version_condition_polarity(node.expression)
        return None if polarity is None else not polarity
    if not isinstance(node, BinaryExpression) or node.operator.name not in {"==", "!="}:
        return None
    left, right = node.left, node.right
    literal_on_right = _is_version_reference(left) and isinstance(
        _without_parentheses(right), StringPrimitive
    )
    literal_on_left = _is_version_reference(right) and isinstance(
        _without_parentheses(left), StringPrimitive
    )
    if literal_on_right == literal_on_left:
        return None
    literal = _without_parentheses(right if literal_on_right else left)
    if (
        not isinstance(literal, StringPrimitive)
        or not literal.value
        or "${" in literal.value
    ):
        return None
    return node.operator.name == "=="


def _is_lib_optionals(node: object) -> bool:
    """Recognize ``lib.optionals`` and the equivalent package-set ``*.lib`` path."""
    node = _without_parentheses(node)
    if not (isinstance(node, Select) and node.default is None):
        return False
    if node.attribute == "lib.optionals":
        return isinstance(node.expression, Identifier) and node.expression.name in {
            "final",
            "nixpkgs",
            "pkgs",
            "prev",
        }
    if node.attribute != "optionals":
        return False
    owner = _without_parentheses(node.expression)
    if isinstance(owner, Identifier):
        return owner.name == "lib"
    return (
        isinstance(owner, Select)
        and owner.attribute == "lib"
        and owner.default is None
        and isinstance(owner.expression, Identifier)
        and owner.expression.name in {"final", "nixpkgs", "pkgs", "prev"}
    )


def _pinned_if_flags(node: IfExpression, *, version_gated: bool) -> bool:
    polarity = _version_condition_polarity(node.condition)
    if polarity is None:
        return _pinned_check_flags(
            node.consequence, version_gated=version_gated
        ) and _pinned_check_flags(node.alternative, version_gated=version_gated)
    return _pinned_check_flags(
        node.consequence, version_gated=version_gated or polarity
    ) and _pinned_check_flags(
        node.alternative, version_gated=version_gated or not polarity
    )


def _pinned_flag_call(node: FunctionCall, *, version_gated: bool) -> bool:
    if isinstance(node.name, FunctionCall) and _is_lib_optionals(node.name.name):
        # The parser constructs every call with an argument.
        condition = cast("NixExpression", node.name.argument)
        flags = cast("NixExpression", node.argument)
        polarity = _version_condition_polarity(condition)
        return polarity is True and _pinned_check_flags(flags, version_gated=True)
    argument = cast("NixExpression", node.argument)
    return _pinned_check_flags(
        node.name, version_gated=version_gated
    ) and _pinned_check_flags(argument, version_gated=version_gated)


def _pinned_check_flags(node: object, *, version_gated: bool = False) -> bool:
    """Prove every skip flag occurs only beneath one exact failing-version gate."""
    node = _without_parentheses(node)
    if isinstance(node, StringPrimitive):
        return version_gated or "--skip" not in node.value
    if isinstance(node, NixList):
        return all(
            _pinned_check_flags(item, version_gated=version_gated)
            for item in node.value
        )
    if isinstance(node, IfExpression):
        return _pinned_if_flags(node, version_gated=version_gated)
    if isinstance(node, FunctionCall):
        return _pinned_flag_call(node, version_gated=version_gated)
    if isinstance(node, BinaryExpression):
        return _pinned_check_flags(
            node.left, version_gated=version_gated
        ) and _pinned_check_flags(node.right, version_gated=version_gated)
    expression = cast("NixExpression", node)
    return version_gated or "--skip" not in expression.rebuild()


def _pinned_and_explained(binding: Binding) -> bool:
    """Require binding-specific failing-version polarity and an adjacent comment."""
    if binding.name == "checkFlags":
        pinned = _pinned_check_flags(binding.value)
    else:
        # doCheck must be false only at the failure; dontCheck must be true there.
        pinned = _version_condition_polarity(binding.value) is (
            not _CHECK_SWITCHES[binding.name]
        )
    comment = any(
        isinstance(trivia, Comment) for trivia in (*binding.before, *binding.after)
    )
    return pinned and comment


def _added_holds(new: bytes | None, old: bytes | None) -> set[str]:
    """Return ``bulk_update_hold`` declarations that the repair added."""

    def holds(content: bytes | None) -> set[str]:
        return {
            ast.dump(node.value)
            for node in ast.walk(ast.parse(content or b""))
            if isinstance(node, ast.Assign | ast.AnnAssign)
            and node.value is not None
            and not (isinstance(node.value, ast.Constant) and node.value.value is None)
            and any(
                isinstance(target, ast.Name) and target.id == "bulk_update_hold"
                for target in (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
            )
        }

    return holds(new) - holds(old)


def reject_unsafe_skips(
    root: Path,
    baseline_content: Callable[[Path], bytes | None],
    paths: tuple[Path, ...],
) -> None:
    """Enforce the AGENTS.md checkPhase-skip rules and the no-holds rule on a diff."""
    rejected: list[str] = []
    for path in paths:
        target = root / path
        new = target.read_bytes() if target.is_file() else None
        old = baseline_content(path)
        if path.suffix == ".py" and _added_holds(new, old):
            rejected.append(f"{path}: adds a bulk_update_hold")
        if path.suffix != ".nix":
            continue
        rejected.extend(
            f"{path}: {binding.name} skip is not pinned to a version "
            "with an adjacent comment"
            for binding in _added_check_skips(path, new, old)
            if not _pinned_and_explained(binding)
        )
    if rejected:
        msg = "Repair violates the checkPhase-skip policy: " + "; ".join(rejected)
        raise ValueError(msg)


def check_applied_repair(root: Path) -> None:
    """Re-check a repair patch staged in *root* against ``HEAD`` before commit."""
    names = git(root, "diff", "--cached", "--name-only", "-z").decode().split("\0")
    paths = repair_paths(tuple(Path(name) for name in names if name))

    def baseline(path: Path) -> bytes | None:
        try:
            return git(root, "show", f"HEAD:{path}")
        except subprocess.CalledProcessError:
            return None

    reject_unsafe_skips(root, baseline, paths)


def repair_agent_prompt(evidence: Path) -> str:
    """Return the one-shot prompt the bounded repair agent must follow."""
    return (
        f"Fix the updater or package failure recorded in {evidence}. "
        "Work in this isolated repository. Read AGENTS.md and inspect the causal "
        "failure before editing. Make the smallest coherent fix in packages/, "
        "overlays/, flake.nix, flake.lock, or planner/selection coherence in "
        "lib/update/planner.py (and lib/tests/test_update_planner.py). Preserve "
        "platform support, discovery, validation declarations, upstream pin "
        "intent, and generated-file ownership. "
        "You may disable checkPhase only when all of the following are true: "
        "(1) the failure is an upstream package's own tests (not compile/link); "
        "(2) the derivation built before checks; "
        "(3) the failure is not in George's overlay/app/home-manager module code; "
        "(4) the skip is `doCheck = false` (or equivalent) pinned to the failing "
        "version with a comment that cites the test and signal; "
        "(5) the skip is documented in the change. "
        "Never skip SIGBUS, runner-lost-communication, store "
        "`ValidationIncomplete`, compile/link failures, George's own "
        "overlay/app/module code, or Darwin validation itself. "
        "The caller rejects a doCheck, dontCheck, or checkFlags --skip whose "
        "whole binding does not disable checks solely at one package-owned "
        "literal failing version with binding-specific polarity, or which "
        "lacks an adjacent comment, and any new bulk_update_hold. "
        "Do not add holds, suppress errors, change CI, acceptance "
        "gates, persistence, or other updater-framework files, commit, push, "
        "or contact anyone. Regenerate outputs through their generators. Add "
        "a focused package or planner regression test when behavior changes. "
        "This is one bounded repair attempt; explain an unfixable "
        "infrastructure or credential failure instead of masking it. The "
        "caller will independently rerun quality and native build gates."
    )


def agent_command(agent: RepairAgent, evidence: Path) -> list[str]:
    """Give the agent evidence and authority to propose one packaging repair."""
    prompt = repair_agent_prompt(evidence)
    match agent:
        case RepairAgent.CODEX:
            return [
                "codex",
                "exec",
                "--sandbox",
                "workspace-write",
                "--add-dir",
                str(evidence),
                "--ephemeral",
                prompt,
            ]
        case RepairAgent.COPILOT:  # pragma: no branch -- enum exhausted
            return [
                "copilot",
                "--no-ask-user",
                "--allow-all-tools",
                "--deny-tool",
                "shell(git push)",
                "--add-dir",
                str(evidence),
                "--prompt",
                prompt,
            ]


async def repair_in_workspace(
    workspace: IsolatedUpdateWorkspace, evidence: Path, agent: RepairAgent
) -> tuple[Path, ...]:
    """Run one agent with the existing subprocess lifetime and bounded log capture."""
    with (evidence / "repair.log").open("w") as log:

        async def emit(event: UpdateEvent) -> None:
            if event.kind is UpdateEventKind.LINE and event.message:
                log.write(event.message + "\n")
                log.flush()

        result = await run_command(
            agent_command(agent, evidence),
            options=RunCommandOptions(
                source="repair",
                command_timeout=1800,
                output_limit=65536,
            ),
            emit=emit,
        )
        if result.returncode:
            msg = f"Repair agent failed with exit code {result.returncode}; see {evidence}"
            raise RuntimeError(msg)
    paths = repair_paths(workspace.changed_paths())
    reject_unsafe_skips(workspace.root, workspace.baseline_content, paths)
    workspace.validate_changes(paths)
    return paths


def propose_repair(
    root: Path, *, evidence: Path, output: Path, agent: RepairAgent
) -> None:
    """Export an unvalidated proposal; it must start a fresh update attempt."""
    root, evidence, output = root.resolve(), evidence.resolve(), output.resolve()
    if evidence.is_relative_to(root) or output.is_relative_to(root):
        msg = "Repair evidence and output must be outside the repository"
        raise ValueError(msg)
    if not evidence.is_dir() or not any(evidence.iterdir()):
        msg = "Repair requires failure evidence"
        raise ValueError(msg)
    output.parent.mkdir(parents=True, exist_ok=True)
    with IsolatedUpdateWorkspace(root) as workspace:
        paths = asyncio.run(repair_in_workspace(workspace, evidence, agent))
        output.write_bytes(workspace.patch(paths))


async def _attempt(options: UpdateOptions, root: Path, evidence: Path) -> int:
    """Reload repaired code in a fresh process and a fresh DBOS history."""
    # The executable supplies dependencies; the working directory supplies the
    # repaired Python source. Repairs cannot alter pyproject.toml or uv.lock.
    program = (
        "import json, sys; "
        "from lib.update.cli import run_update_command; "
        "from lib.update.cli_options import UpdateOptions; "
        "sys.exit(run_update_command(UpdateOptions.from_mapping(json.loads(sys.argv[1]))))"
    )
    with (
        (evidence / "result.json").open("w") as result_file,
        (evidence / "stderr.log").open("w") as log,
    ):

        async def emit(event: UpdateEvent) -> None:
            if event.kind is UpdateEventKind.LINE and event.message:
                output = result_file if event.stream == "stdout" else log
                output.write(event.message + "\n")
                output.flush()
                if event.stream != "stdout" and not options.quiet:
                    sys.stderr.write(event.message + "\n")

        result = await run_command(
            [sys.executable, "-c", program, json.dumps(asdict(options))],
            options=RunCommandOptions(
                source="update",
                allow_failure=True,
                command_timeout=86400,
                output_limit=65536,
                env={
                    "REPO_ROOT": str(root),
                    "NIXCFG_UPDATE_EXECUTION_SOURCE": str(root),
                    "UPDATE_RUN_LOG_DIR": str(evidence / "runs"),
                },
            ),
            emit=emit,
        )
    return result.returncode


async def _quality(evidence: Path) -> int:
    """Check repairs and generated outputs with the repository's existing gates."""
    with (evidence / "quality.log").open("w") as log:

        async def emit(event: UpdateEvent) -> None:
            if event.kind is UpdateEventKind.LINE and event.message:
                log.write(event.message + "\n")
                log.flush()

        result = await run_command(
            [
                "nix",
                "develop",
                "--no-write-lock-file",
                "--command",
                "python",
                "lib/update/ci/jobs.py",
                "quality",
            ],
            options=RunCommandOptions(
                source="repair-quality", allow_failure=True, command_timeout=3600
            ),
            emit=emit,
        )
    return result.returncode


def run_repairing_update(options: UpdateOptions, root: Path) -> int:
    """Try once, repair once on failure, and promote only a fully checked result."""
    if (
        options.repair is None
        or options.resume is not None
        or options.run_id is not None
    ):
        msg = "--repair requires a fresh update, without --resume or --run-id"
        raise ValueError(msg)
    evidence = default_run_log_root().parent / "repairs" / uuid4().hex
    evidence.mkdir(parents=True, mode=0o700)
    sys.stderr.write(f"Update attempt and repair evidence: {evidence}\n")
    attempt_options = replace(
        options,
        repair=None,
        check=False,
        strict=True,
        patch=None,
        json=True,
        tty="off",
    )
    patch = b""
    with IsolatedUpdateWorkspace(root) as workspace:
        attempt = evidence / "attempt-1"
        attempt.mkdir()
        status = asyncio.run(_attempt(attempt_options, workspace.root, attempt))
        if status:
            paths = asyncio.run(
                repair_in_workspace(workspace, evidence, options.repair)
            )
            (evidence / "repair.patch").write_bytes(workspace.patch(paths))
            attempt = evidence / "attempt-2"
            attempt.mkdir()
            status = asyncio.run(
                _attempt(
                    replace(attempt_options, validate_all_packages=True),
                    workspace.root,
                    attempt,
                )
            )
            if not status:
                workspace.patch(workspace.changed_paths())
                # Freeze the already built tree before hooks; formatter changes
                # cannot inherit successful build evidence.
                with workspace.validation_snapshot():
                    status = asyncio.run(_quality(evidence))
        if not status:
            paths = workspace.changed_paths()
            patch = workspace.patch(paths)
            if not options.check:
                workspace.promote(paths)
    if options.patch:
        Path(options.patch).expanduser().write_bytes(patch)
    if options.json:
        result = json.loads((attempt / "result.json").read_text())
        result["success"] = status == 0
        result["repair_evidence"] = str(evidence)
        sys.stdout.write(json.dumps(result) + "\n")
    elif not options.quiet:
        sys.stdout.write("Update succeeded\n" if status == 0 else "Update failed\n")
    return status
