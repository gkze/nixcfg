"""Input receipts depend on parsed declarations and the relevant lock graph."""

import json
from pathlib import Path

import pytest
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.flake_lock import FlakeLock, FlakeLockNode, LockedRef, OriginalRef
from lib.update.input_state import flake_input_state


def _declarations(**overrides: object) -> bytes:
    inputs = {
        "input": {"url": "github:owner/input/v1", "flake": False},
        "other": {"url": "github:owner/other/v1"},
        "shared": {"url": "github:owner/shared/v1"},
        "alias": {"follows": "shared"},
    }
    return AttributeSet.from_dict({"inputs": inputs | overrides}).rebuild().encode()


def _node(name: str, **kwargs: object) -> FlakeLockNode:
    return FlakeLockNode.model_validate({
        "locked": LockedRef(
            type="github", owner="owner", repo=name, rev="v1", narHash="sha256-test"
        ),
        "original": OriginalRef(type="github", owner="owner", repo=name, ref="v1"),
        **kwargs,
    })


@pytest.fixture
def lock() -> FlakeLock:
    # Root names deliberately differ from node IDs; follows paths use root names.
    return FlakeLock(
        version=7,
        root="workspace",
        nodes={
            "workspace": FlakeLockNode(
                inputs={
                    "input": "input-node",
                    "other": "other-node",
                    "shared": "shared-node",
                    "alias": ["shared"],
                }
            ),
            "input-node": _node("input", flake=False),
            "other-node": _node("other"),
            "shared-node": _node("shared", inputs={"nested": "nested-node"}),
            "nested-node": _node("nested"),
        },
    )


def _state(declarations: bytes, lock: FlakeLock) -> tuple[bytes, bytes]:
    return flake_input_state("input", declarations, lock.model_dump_json().encode())


def test_unrelated_reference_changes_keep_the_receipt(lock: FlakeLock) -> None:
    original = _state(_declarations(), lock)
    lock.nodes["other-v2"] = _node("other", original={"type": "github", "ref": "v2"})
    lock.root_node.inputs = {**(lock.root_node.inputs or {}), "other": "other-v2"}
    declarations = _declarations(other={"url": "github:owner/other/v2"})
    assert original == _state(declarations, lock)


@pytest.mark.parametrize(
    "change",
    ["declaration", "root_mapping", "locked", "original", "flake", "dependency"],
)
def test_own_input_identity_changes_invalidate_receipt(
    lock: FlakeLock, change: str
) -> None:
    declarations = _declarations()
    original = _state(declarations, lock)
    match change:
        case "declaration":
            declarations = _declarations(input={"url": "github:owner/input/v2"})
        case "root_mapping":
            lock.nodes["same-content"] = lock.nodes["input-node"].model_copy(deep=True)
            lock.root_node.inputs = {
                **(lock.root_node.inputs or {}),
                "input": "same-content",
            }
        case "locked":
            lock.nodes["input-node"].locked = LockedRef(type="path", path="/new")
        case "original":
            lock.nodes["input-node"].original = OriginalRef(type="path", path="/new")
        case "flake":
            lock.nodes["input-node"].flake = True
        case "dependency":
            lock.nodes["input-node"].inputs = {"dependency": "shared-node"}
    assert original != _state(declarations, lock)


def test_node_identity_preserves_explicit_fields(lock: FlakeLock) -> None:
    declarations = _declarations()
    original = _state(declarations, lock)
    data = json.loads(lock.model_dump_json())
    del data["nodes"]["input-node"]["original"]["url"]
    assert original != flake_input_state(
        "input", declarations, json.dumps(data).encode()
    )


@pytest.mark.parametrize("dependency", ["shared-node", ["shared"], ["alias", "nested"]])
@pytest.mark.parametrize(
    "change", ["reachable_node", "reachable_declaration", "root_mapping", "unrelated"]
)
def test_reachable_and_follows_dependencies_are_part_of_identity(
    lock: FlakeLock, dependency: str | list[str], change: str
) -> None:
    lock.nodes["input-node"].inputs = {"dependency": dependency}
    declarations = _declarations()
    original = _state(declarations, lock)
    # Establish scoped comparison rather than accidentally passing via fallback.
    assert original != (declarations, lock.model_dump_json().encode())
    match change:
        case "reachable_node":
            lock.nodes["nested-node"].locked = LockedRef(type="path", path="/changed")
        case "reachable_declaration":
            declarations = _declarations(shared={"url": "github:owner/shared/v2"})
        case "root_mapping":
            lock.nodes["replacement"] = _node("replacement")
            lock.root_node.inputs = {
                **(lock.root_node.inputs or {}),
                "shared": "replacement",
            }
        case "unrelated":
            lock.nodes["other-node"] = _node("other-v2")
            declarations = _declarations(other={"url": "github:owner/other/v2"})
    assert (original == _state(declarations, lock)) == (change == "unrelated")


def test_root_follows_and_nested_follows_are_resolved(lock: FlakeLock) -> None:
    lock.root_node.inputs = {
        **(lock.root_node.inputs or {}),
        "input": ["alias", "nested"],
    }
    lock.nodes["shared-node"].inputs = {"nested": ["other"]}
    declarations = _declarations(input={"follows": "alias/nested"})
    original = _state(declarations, lock)
    assert original != (declarations, lock.model_dump_json().encode())
    lock.nodes["other-node"].original = OriginalRef(type="path", path="/new")
    assert original != _state(declarations, lock)


def test_direct_dependency_cycles_have_finite_identity(lock: FlakeLock) -> None:
    lock.nodes["input-node"].inputs = {"dependency": "shared-node"}
    lock.nodes["shared-node"].inputs = {"back": "input-node"}
    declarations = _declarations()
    original = _state(declarations, lock)
    assert original != (declarations, lock.model_dump_json().encode())
    lock.nodes["shared-node"].flake = False
    assert original != _state(declarations, lock)


@pytest.mark.parametrize(
    "declarations",
    [
        b"broken declarations",
        b'{ inputs.input.url = "x";',
        b"{ inputs.input.url = ; }",
        b'{ inputs.input.url = "x"; } trailing',
        b'rec { inputs.input.url = "x"; }',
        b'let ref = "x"; in { inputs.input.url = ref; }',
        b"{ inherit inputs; }",
        b"{ inputs = otherInputs; }",
        b'{ inputs = rec { input.url = "x"; }; }',
        b"{ inputs = { inherit input; }; }",
        b'{ inputs = { "${name}".url = "x"; }; }',
        b'{ inputs.input.url = "${other.url}"; }',
        b"{ inputs.input.url = other.url; }",
        b"{ inputs.input.url = with other; url; }",
        b'{ inputs.input.url = let ref = "x"; in ref; }',
        b'{ inputs.input = rec { url = "x"; }; }',
        b'{ inputs.input.url = "x"; inputs.input.url = "y"; }',
        b'{ inputs.input = {url="x";}; inputs.input.flake = false; }',
        b'{ inputs.other.url = "x"; }',
        b'{ ${name} = {}; inputs.input.url = "x"; }',
        b'{ inputs.input.url = "x"; ?? }',
    ],
)
def test_unproven_declarations_keep_full_file_identity(
    lock: FlakeLock, declarations: bytes
) -> None:
    contents = lock.model_dump_json().encode()
    assert flake_input_state("input", declarations, contents) == (
        declarations,
        contents,
    )


@pytest.mark.parametrize(
    "change",
    [
        "version",
        "root_extension",
        "node_extension",
        "locked_extension",
        "original_extension",
        "source_type",
        "unlocked_node",
        "missing_node",
        "missing_root",
        "missing_mapping",
        "empty_follows",
        "cycle_follows",
        "missing_follows",
        "invalid_json",
        "unknown_top_field",
        "implicit_root",
        "coerced_version",
    ],
)
def test_unproven_lock_graphs_keep_full_file_identity(
    lock: FlakeLock, change: str
) -> None:
    match change:
        case "version":
            lock.version = 8
        case "root_extension":
            lock.nodes[lock.root] = FlakeLockNode.model_validate({
                "future": "extension"
            })
        case "node_extension":
            lock.nodes["input-node"] = _node("input", future="extension")
        case "locked_extension":
            lock.nodes["input-node"].locked = LockedRef.model_validate({
                "type": "github",
                "narHash": "sha256-test",
                "parent": ["other"],
            })
        case "original_extension":
            lock.nodes["input-node"].original = OriginalRef.model_validate({
                "type": "github",
                "future": "extension",
            })
        case "source_type":
            lock.nodes["input-node"].locked = LockedRef(
                type="future-source-type", narHash="sha256-test"
            )
        case "unlocked_node":
            lock.nodes["input-node"].locked = None
        case "missing_node":
            del lock.nodes["input-node"]
        case "missing_root":
            del lock.nodes[lock.root]
        case "missing_mapping":
            lock.root_node.inputs = {}
        case "empty_follows":
            lock.root_node.inputs = {"input": []}
        case "cycle_follows":
            lock.root_node.inputs = {"input": ["alias"], "alias": ["input"]}
        case "missing_follows":
            lock.nodes["input-node"].inputs = {"dependency": ["missing"]}
    contents = lock.model_dump_json().encode()
    if change == "invalid_json":
        contents = b"not JSON"
    if change == "unknown_top_field":
        contents = json.dumps({**lock.model_dump(), "future": "extension"}).encode()
    if change == "implicit_root":
        contents = lock.model_dump_json(exclude={"root"}).encode()
    if change == "coerced_version":
        contents = json.dumps({**lock.model_dump(), "version": "7"}).encode()
    declarations = _declarations()
    assert flake_input_state("input", declarations, contents) == (
        declarations,
        contents,
    )


def test_layout_and_unrelated_output_expressions_do_not_change_identity(
    lock: FlakeLock,
) -> None:
    original = _state(_declarations(), lock)
    declarations = b"""# Root comment
    { # Inside root
      inputs = { # Inside inputs
        input.url = "github:owner/input/v1";
        input.flake = false;
        other.url = "github:owner/other/v2";
      };
      # Comment between root bindings
      outputs = {self}: self;
    } # trailing comment
    """
    assert original == _state(declarations, lock)


def test_current_repo_input_can_be_scoped_despite_unrelated_outputs_syntax() -> None:
    # Faithfully exercise the real declaration consumer without Nix evaluation.
    root = Path(__file__).parents[2]
    declarations = (root / "flake.nix").read_bytes()
    contents = (root / "flake.lock").read_bytes()
    assert flake_input_state("emdash", declarations, contents) != (
        declarations,
        contents,
    )
