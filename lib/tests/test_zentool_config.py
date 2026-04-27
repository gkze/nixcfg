"""Focused pure-Python tests for zentool config parsing helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lib.tests._zen_tooling import load_zen_script_module

if TYPE_CHECKING:
    from types import ModuleType


@pytest.fixture(scope="module")
def zentool() -> ModuleType:
    """Load the zentool script for config-helper testing."""
    return load_zen_script_module("zentool", "zentool_config_helpers")


def test_expand_path_expands_user_home_and_resolves_segments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """User-authored paths should expand ``~`` and normalize path segments."""
    home_dir = tmp_path / "home"
    target = home_dir / "workspace" / "config.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("workspaces: {}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home_dir))

    resolved = zentool._expand_path("~/workspace/../workspace/config.yaml")

    assert resolved == target.resolve()


def test_resolve_optional_dir_validates_explicit_paths(
    tmp_path: Path,
    zentool: ModuleType,
) -> None:
    """Explicit directory overrides should reject missing or non-directory targets."""
    default = tmp_path / "default"
    default.mkdir()
    missing = tmp_path / "missing"
    not_dir = tmp_path / "not-a-dir"
    not_dir.write_text("x", encoding="utf-8")
    explicit = tmp_path / "explicit"
    explicit.mkdir()

    with pytest.raises(zentool.ZenFoldersError, match="config dir not found"):
        zentool._resolve_optional_dir(str(missing), default, label="config dir")

    with pytest.raises(zentool.ZenFoldersError, match="config dir is not a directory"):
        zentool._resolve_optional_dir(str(not_dir), default, label="config dir")

    assert (
        zentool._resolve_optional_dir(str(explicit), default, label="config dir")
        == explicit.resolve()
    )


def test_resolve_optional_dir_uses_default_when_present(
    tmp_path: Path,
    zentool: ModuleType,
) -> None:
    """Implicit directory discovery should resolve valid defaults and ignore absent ones."""
    default_dir = tmp_path / "default-dir"
    default_dir.mkdir()
    missing = tmp_path / "missing"
    not_dir = tmp_path / "not-a-dir"
    not_dir.write_text("x", encoding="utf-8")

    assert (
        zentool._resolve_optional_dir(None, default_dir, label="profile dir")
        == default_dir.resolve()
    )
    assert zentool._resolve_optional_dir(None, missing, label="profile dir") is None

    with pytest.raises(zentool.ZenFoldersError, match="profile dir is not a directory"):
        zentool._resolve_optional_dir(None, not_dir, label="profile dir")


def test_resolve_optional_file_validates_explicit_paths(
    tmp_path: Path,
    zentool: ModuleType,
) -> None:
    """Explicit file overrides should reject missing or non-file targets."""
    default = tmp_path / "default.yaml"
    default.write_text("{}\n", encoding="utf-8")
    missing = tmp_path / "missing.yaml"
    not_file = tmp_path / "not-a-file"
    not_file.mkdir()
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text("{}\n", encoding="utf-8")

    with pytest.raises(zentool.ZenFoldersError, match="config file not found"):
        zentool._resolve_optional_file(str(missing), default, label="config file")

    with pytest.raises(zentool.ZenFoldersError, match="config file is not a file"):
        zentool._resolve_optional_file(str(not_file), default, label="config file")

    assert (
        zentool._resolve_optional_file(str(explicit), default, label="config file")
        == explicit.resolve()
    )


def test_resolve_optional_file_uses_default_when_present(
    tmp_path: Path,
    zentool: ModuleType,
) -> None:
    """Implicit file discovery should resolve valid defaults and ignore absent ones."""
    default_file = tmp_path / "default.yaml"
    default_file.write_text("{}\n", encoding="utf-8")
    missing = tmp_path / "missing.yaml"
    not_file = tmp_path / "not-a-file"
    not_file.mkdir()

    assert (
        zentool._resolve_optional_file(None, default_file, label="manifest")
        == default_file.resolve()
    )
    assert zentool._resolve_optional_file(None, missing, label="manifest") is None

    with pytest.raises(zentool.ZenFoldersError, match="manifest is not a file"):
        zentool._resolve_optional_file(None, not_file, label="manifest")


def test_load_yaml_parses_valid_yaml(tmp_path: Path, zentool: ModuleType) -> None:
    """The YAML loader should return parsed objects for valid files."""
    config_path = tmp_path / "folders.yaml"
    config_path.write_text(
        "Work:\n  - Gmail: https://mail.google.com\n", encoding="utf-8"
    )

    assert zentool.load_yaml(config_path) == {
        "Work": [{"Gmail": "https://mail.google.com"}]
    }


def test_load_yaml_wraps_read_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Filesystem read failures should become user-facing config errors."""
    config_path = tmp_path / "folders.yaml"

    def raise_oserror(self: Path, *, encoding: str) -> str:
        raise OSError("permission denied")

    monkeypatch.setattr(type(config_path), "read_text", raise_oserror)

    with pytest.raises(zentool.ZenFoldersError, match="Unable to read config file"):
        zentool.load_yaml(config_path)


def test_load_yaml_rejects_duplicate_keys(tmp_path: Path, zentool: ModuleType) -> None:
    """Duplicate YAML keys should be rejected before model validation."""
    config_path = tmp_path / "folders.yaml"
    config_path.write_text("Work: []\nWork: []\n", encoding="utf-8")

    with pytest.raises(zentool.ZenFoldersError, match="Invalid config file"):
        zentool.load_yaml(config_path)


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        pytest.param([], "single-key mapping", id="not-mapping"),
        pytest.param(
            {"one": "a", "two": "b"}, "single-key mapping", id="multiple-keys"
        ),
        pytest.param(
            {1: "https://example.com"},
            "node names must be strings",
            id="non-string-name",
        ),
        pytest.param(
            {"Folder": {1: "https://example.com"}},
            "node 'Folder' mapping keys must be strings",
            id="non-string-node-field",
        ),
        pytest.param(
            {"Folder": {"collapsed": "false", "children": []}},
            "folder 'Folder' collapsed must be a boolean",
            id="collapsed-not-bool",
        ),
        pytest.param({"Broken": 3}, "unable to parse node 'Broken'", id="bad-scalar"),
    ],
)
def test_coerce_authored_node_input_rejects_invalid_shapes(
    raw: object,
    match: str,
    zentool: ModuleType,
) -> None:
    """Authored node coercion should fail fast on unsupported shorthand shapes."""
    with pytest.raises(zentool.ZenFoldersError, match=match):
        zentool._coerce_authored_node_input(raw)


def test_coerce_authored_node_input_handles_string_list_and_mapping_forms(
    zentool: ModuleType,
) -> None:
    """The coercer should normalize the three user-authored node spellings."""
    assert zentool._coerce_authored_node_input({
        "Inbox": "https://mail.example.com"
    }) == {
        "name": "Inbox",
        "url": "https://mail.example.com",
    }

    folder = zentool._coerce_authored_node_input({
        "AI": [{"Anthropic": "https://claude.ai"}]
    })
    assert folder == {
        "name": "AI",
        "children": [
            zentool.AuthoredLeafNode(
                name="Anthropic", url="https://claude.ai", role=None
            )
        ],
    }

    assert zentool._coerce_authored_node_input({
        "Docs": {"url": "https://docs.example.com", "role": "essential"}
    }) == {
        "name": "Docs",
        "url": "https://docs.example.com",
        "role": "essential",
    }


def test_coerce_authored_node_input_handles_children_and_implicit_folder_forms(
    zentool: ModuleType,
) -> None:
    """Folder mappings should accept explicit children lists and implicit child entries."""
    with pytest.raises(zentool.ZenFoldersError, match="children must be a list"):
        zentool._coerce_authored_node_input({
            "AI": {"children": {"Anthropic": "https://claude.ai"}}
        })

    explicit = zentool._coerce_authored_node_input({
        "AI": {
            "collapsed": False,
            "children": [{"Anthropic": "https://claude.ai"}],
        }
    })
    assert explicit == {
        "name": "AI",
        "collapsed": False,
        "children": [
            zentool.AuthoredLeafNode(
                name="Anthropic", url="https://claude.ai", role=None
            )
        ],
    }

    implicit = zentool._coerce_authored_node_input({
        "AI": {
            "collapsed": False,
            "Anthropic": "https://claude.ai",
            "OpenAI": {"url": "https://platform.openai.com", "role": "tab"},
        }
    })
    assert implicit == {
        "name": "AI",
        "collapsed": False,
        "children": [
            zentool.AuthoredLeafNode(
                name="Anthropic", url="https://claude.ai", role=None
            ),
            zentool.AuthoredLeafNode(
                name="OpenAI",
                url="https://platform.openai.com",
                role="tab",
            ),
        ],
    }


def test_normalize_authored_node_returns_models_for_leaf_and_folder_forms(
    zentool: ModuleType,
) -> None:
    """Normalization should produce concrete authored-node models."""
    leaf = zentool._normalize_authored_node({"Inbox": "https://mail.example.com"})
    folder = zentool._normalize_authored_node({
        "AI": {"children": [{"Anthropic": "https://claude.ai"}]}
    })

    assert leaf == zentool.AuthoredLeafNode(
        name="Inbox",
        url="https://mail.example.com",
        role=None,
    )
    assert folder == zentool.AuthoredFolderNode(
        name="AI",
        collapsed=True,
        children=[
            zentool.AuthoredLeafNode(
                name="Anthropic", url="https://claude.ai", role=None
            )
        ],
    )


def test_normalize_authored_node_wraps_validation_failures(zentool: ModuleType) -> None:
    """Pydantic validation failures should be wrapped in ``ZenFoldersError``."""
    with pytest.raises(zentool.ZenFoldersError, match="Invalid authored node"):
        zentool._normalize_authored_node({"Inbox": {"url": "   "}})

    with pytest.raises(zentool.ZenFoldersError, match="Invalid authored node"):
        zentool._normalize_authored_node({"Inbox": {"url": 123}})


def test_container_and_workspace_models_reject_blank_container_fields(
    zentool: ModuleType,
) -> None:
    """Container-aware config models should reject ambiguous blank fields."""
    with pytest.raises(zentool.ValidationError, match="workspace containers"):
        zentool.WorkspaceSpec(name="Work", container="   ")

    with pytest.raises(zentool.ValidationError, match="container fields"):
        zentool.ContainerSpec(key="   ", icon="fingerprint", color="blue")

    assert zentool.ContainerSpec(key="Town", name=None).name == "Town"

    with pytest.raises(zentool.ValidationError, match="container names"):
        zentool.ContainerSpec(key="Town", name="   ")

    with pytest.raises(zentool.ValidationError, match="duplicate container key"):
        zentool.ZenConfig(
            containers=[
                zentool.ContainerSpec(key="Town"),
                zentool.ContainerSpec(key="town"),
            ]
        )

    with pytest.raises(zentool.ValidationError, match="duplicate container name"):
        zentool.ZenConfig(
            containers=[
                zentool.ContainerSpec(key="Town", name="Shared"),
                zentool.ContainerSpec(key="Personal", name="shared"),
            ]
        )


def test_authored_folder_to_item_converts_nested_children(zentool: ModuleType) -> None:
    """Authored folder nodes should become canonical pinned folder items."""
    folder = zentool.AuthoredFolderNode(
        name="AI",
        collapsed=False,
        children=[
            zentool.AuthoredLeafNode(
                name="Anthropic", url="https://claude.ai", role=None
            ),
            zentool.AuthoredFolderNode(
                name="Research",
                children=[
                    zentool.AuthoredLeafNode(
                        name="OpenAI",
                        url="https://platform.openai.com",
                        role=None,
                    )
                ],
            ),
        ],
    )

    assert zentool._authored_folder_to_item(folder) == zentool.FolderSpec(
        name="AI",
        collapsed=False,
        items=[
            zentool.ItemTabSpec(name="Anthropic", url="https://claude.ai"),
            zentool.FolderSpec(
                name="Research",
                collapsed=True,
                items=[
                    zentool.ItemTabSpec(
                        name="OpenAI",
                        url="https://platform.openai.com",
                    )
                ],
            ),
        ],
    )


def test_authored_node_to_item_handles_leaf_folder_and_tab_role_errors(
    zentool: ModuleType,
) -> None:
    """Pinned-tree conversion should reject authored tab-role leaves."""
    leaf = zentool.AuthoredLeafNode(
        name="Inbox", url="https://mail.example.com", role=None
    )
    folder = zentool.AuthoredFolderNode(name="AI", children=[])
    tab_role = zentool.AuthoredLeafNode(
        name="Scratch",
        url="https://scratch.example.com",
        role="tab",
    )

    assert zentool._authored_node_to_item(leaf) == zentool.ItemTabSpec(
        name="Inbox",
        url="https://mail.example.com",
    )
    assert zentool._authored_node_to_item(folder) == zentool.FolderSpec(
        name="AI",
        collapsed=True,
        items=[],
    )

    with pytest.raises(
        zentool.ZenFoldersError, match="tab-role node 'Scratch' cannot appear"
    ):
        zentool._authored_node_to_item(tab_role)
