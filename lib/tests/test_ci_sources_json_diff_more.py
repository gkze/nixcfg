"""Additional tests for sources-json diff helper."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.update.ci import sources_json_diff as sdiff


def test_buffer_writer_and_format_helpers() -> None:
    """Cover small formatting helper branches."""
    writer = io.StringIO()
    assert writer.write("abc") == 3
    writer.flush()
    assert writer.isatty() is False
    assert writer.getvalue() == "abc"

    assert sdiff._format_path([]) == "$"
    assert sdiff._format_path(["a", 0, "b"]) == "a[0].b"
    assert sdiff._format_value("x" * 500, max_len=12).endswith("...")


def test_coerce_json_value_rejects_bad_types() -> None:
    """Raise TypeError for unsupported values and keys."""
    with pytest.raises(TypeError, match="Expected string key"):
        sdiff._coerce_json_value({1: "v"}, context="root")

    with pytest.raises(TypeError, match="Unsupported JSON value"):
        sdiff._coerce_json_value({"x": object()}, context="root")

    with pytest.raises(TypeError, match="Expected JSON object"):
        sdiff._coerce_json_object(["not", "obj"], context="root")


def test_path_parsing_and_node_fallbacks() -> None:
    """Parse deepdiff path strings and callable node adapters."""
    parse = sdiff._parse_deepdiff_path
    assert parse("root") == ()
    assert parse("root['a'][2]['b']") == ("a", 2, "b")

    node_no_path = SimpleNamespace()
    with pytest.raises(sdiff.DeepDiffPathError, match="callable path"):
        sdiff._path_from_deepdiff_node(node_no_path)

    class _NodeList:
        def path(self, *, output_format: str = "list") -> list[object]:
            _ = output_format
            return ["root", "a", 1]

    assert sdiff._path_from_deepdiff_node(_NodeList()) == ("root", "a", 1)

    class _NodeText:
        def path(self, **_kwargs: object) -> str:
            return "root['p'][0]"

    assert sdiff._path_from_deepdiff_node(_NodeText()) == ("p", 0)

    class _NodeTextWithoutListFormat:
        def path(self) -> str:
            return "root['fallback']"

    assert sdiff._path_from_deepdiff_node(_NodeTextWithoutListFormat()) == ("fallback",)

    class _NodeAlwaysTypeError:
        def path(self, **_kwargs: object) -> str:
            msg = "unsupported path signature"
            raise TypeError(msg)

    with pytest.raises(sdiff.DeepDiffPathError, match="Could not decode DeepDiff path"):
        sdiff._path_from_deepdiff_node(_NodeAlwaysTypeError())

    class _NodeUnparseableText:
        def path(self, **_kwargs: object) -> str:
            return "not-a-deepdiff-path"

    with pytest.raises(sdiff.DeepDiffPathError, match="not-a-deepdiff-path"):
        sdiff._path_from_deepdiff_node(_NodeUnparseableText())

    class _NodeListWithObject:
        def path(self, *, output_format: str = "list") -> list[object]:
            _ = output_format
            return ["a", object()]

    converted = sdiff._path_from_deepdiff_node(_NodeListWithObject())
    assert converted[0] == "a"
    assert isinstance(converted[1], str)

    class _NodeWeird:
        def path(self, **_kwargs: object) -> int:
            return 42

    with pytest.raises(sdiff.DeepDiffPathError, match="Could not decode DeepDiff path"):
        sdiff._path_from_deepdiff_node(_NodeWeird())


def test_iter_leaf_values_and_change_extraction() -> None:
    """Flatten nested values and extract node attributes."""
    leaves = sdiff._iter_leaf_values(("top",), {"a": [1, 2], "b": "x"})
    assert (("top", "a", 0), 1) in leaves
    assert (("top", "a", 1), 2) in leaves
    assert (("top", "b"), "x") in leaves

    node = SimpleNamespace(t1="old", t2="new")
    assert sdiff._extract_change_value(node, "t1") == "old"
    missing = sdiff._extract_change_value(SimpleNamespace(), "t1")
    assert sdiff._is_json_value(missing) is False


def test_render_selected_format_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise explicit and auto renderer fallbacks."""
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    old_data = sdiff._coerce_json_object({"v": "1"}, context="old")
    new_data = sdiff._coerce_json_object({"v": "2"}, context="new")
    old_path.write_text(json.dumps(old_data), encoding="utf-8")
    new_path.write_text(json.dumps(new_data), encoding="utf-8")

    monkeypatch.setattr(sdiff, "_render_jd_diff", lambda *_a: "")
    monkeypatch.setattr(sdiff, "_render_graphtage_diff", lambda *_a: "")
    monkeypatch.setattr(sdiff, "_render_plain_diff", lambda *_a: "plain")

    assert (
        sdiff._render_selected_format(  # type: ignore[arg-type]
            "auto", old_path, new_path, old_data, new_data
        )
        == "plain"
    )
    assert sdiff._render_selected_format(  # type: ignore[arg-type]
        "graphtage", old_path, new_path, old_data, new_data
    ).startswith("@")


def test_render_jd_diff_command_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return empty output when jd is missing or errors."""
    old_path = tmp_path / "o.json"
    new_path = tmp_path / "n.json"
    old_path.write_text("{}", encoding="utf-8")
    new_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(sdiff.shutil, "which", lambda _name: None)
    assert sdiff._render_jd_diff(old_path, new_path) == ""

    monkeypatch.setattr(sdiff.shutil, "which", lambda _name: "/usr/bin/jd")
    monkeypatch.setattr(
        sdiff,
        "_run_command",
        lambda _args: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    assert sdiff._render_jd_diff(old_path, new_path) == ""


def test_render_graphtage_diff_import_error_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return empty graphtage output when optional dependency is unavailable."""

    def _import_module(_name: str) -> object:
        raise ImportError

    monkeypatch.setattr(sdiff.importlib, "import_module", _import_module)
    assert sdiff._render_graphtage_diff({"a": 1}, {"a": 2}) == ""


def test_render_graphtage_diff_with_fake_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Render graphtage output when modules are importable."""

    class _Tree:
        def __init__(self, value: object) -> None:
            self.value = value

        def diff(self, _other: object) -> object:
            return {"ok": True}

    class _Formatter:
        @staticmethod
        def print(printer: object, _diff_tree: object) -> None:
            out_stream = printer.out_stream
            out_stream.write("rendered")

    class _Printer:
        def __init__(self, *, out_stream: object, **_kwargs: object) -> None:
            self.out_stream = out_stream

        def __enter__(self) -> _Printer:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    graphtage_json = SimpleNamespace(
        build_tree=lambda value: _Tree(value),
        JSONFormatter=SimpleNamespace(DEFAULT_INSTANCE=_Formatter()),
    )
    default_printer = SimpleNamespace(quiet=False)
    graphtage_printer = SimpleNamespace(
        DEFAULT_PRINTER=default_printer, Printer=_Printer
    )

    def _import_module(name: str) -> object:
        if name == "graphtage.json":
            return graphtage_json
        if name == "graphtage.printer":
            return graphtage_printer
        msg = f"unexpected module: {name}"
        raise AssertionError(msg)

    monkeypatch.setattr(sdiff.importlib, "import_module", _import_module)
    rendered = sdiff._render_graphtage_diff({"a": 1}, {"a": 2})

    assert rendered == "rendered"
    assert default_printer.quiet is False


def test_render_plain_diff_unified_fallback_and_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise plain unified fallback and explicit structural fallback paths."""
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    old_data = sdiff._coerce_json_object({"k": "v1"}, context="old")
    new_data = sdiff._coerce_json_object({"k": "v2"}, context="new")
    old_path.write_text(json.dumps(old_data), encoding="utf-8")
    new_path.write_text(json.dumps(new_data), encoding="utf-8")

    monkeypatch.setattr(sdiff, "_collect_leaf_changes", lambda *_a: [])
    unified = sdiff._render_plain_diff(old_data, new_data)
    assert "--- old/source-entry.json" in unified
    assert "+++ new/source-entry.json" in unified

    monkeypatch.setattr(sdiff, "_render_structural_hunks", lambda *_a: "")
    monkeypatch.setattr(sdiff, "_render_plain_diff", lambda *_a: "")
    monkeypatch.setattr(sdiff, "_render_jd_diff", lambda *_a: "")
    monkeypatch.setattr(sdiff, "_render_graphtage_diff", lambda *_a: "")
    assert (
        sdiff._render_selected_format("auto", old_path, new_path, old_data, new_data)
        == ""
    )
    monkeypatch.setattr(sdiff.shutil, "which", lambda _name: "/usr/bin/jd")
    monkeypatch.setattr(
        sdiff,
        "_run_command",
        lambda _args: SimpleNamespace(returncode=2, stdout="", stderr="bad"),
    )
    assert sdiff._render_jd_diff(old_path, new_path) == ""


def test_collect_leaf_changes_covers_type_and_added_removed_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover DeepDiff branch handling for type/add/remove/equal-change cases."""

    class _Node:
        def __init__(self, path: str, **attrs: object) -> None:
            self._path = path
            for key, value in attrs.items():
                setattr(self, key, value)

        def path(self, **_kwargs: object) -> str:
            return self._path

    class _FakeDiff(dict[str, list[object]]):
        pass

    fake_diff = _FakeDiff({
        "type_changes": [_Node("root['t']", t1=1, t2="1")],
        "dictionary_item_added": [_Node("root['added']")],
        "dictionary_item_removed": [_Node("root['removed']")],
        "values_changed": [_Node("root['same']", t1="x", t2="x")],
    })

    monkeypatch.setattr(sdiff, "DeepDiff", lambda *_a, **_k: fake_diff)
    changes = sdiff._collect_leaf_changes({"old": 1}, {"new": 2})
    assert any(path == ["t"] for path, *_rest in changes)
    assert not any(path == ["same"] for path, *_rest in changes)


def test_render_structural_and_summary_branch_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover old/new missing combinations in structural/summary renderers."""
    missing = sdiff._MISSING
    monkeypatch.setattr(
        sdiff,
        "_collect_leaf_changes",
        lambda *_a: [
            (["added"], missing, "new"),
            (["removed"], "old", missing),
            (["changed"], "old", "new"),
        ],
    )

    structural = sdiff._render_structural_hunks({"x": 1}, {"x": 2})
    assert '@ ["added"]' in structural
    assert '+ "new"' in structural
    assert '- "old"' in structural

    summary = sdiff._render_summary_diff({"x": 1}, {"x": 2})
    assert "added added" in summary
    assert "removed removed" in summary
    assert "changed changed" in summary

    monkeypatch.setattr(
        sdiff, "_collect_leaf_changes", lambda *_a: [(["noop"], missing, missing)]
    )
    assert sdiff._render_summary_diff({"x": 1}, {"x": 2}) == ""


def test_run_command_wrapper_delegates_to_run_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute _run_command helper for direct delegation coverage."""
    seen: dict[str, object] = {}

    def _runner(args: list[str], **kwargs: object) -> object:
        seen["args"] = args
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sdiff, "run_command", _runner)
    _ = sdiff._run_command(["jd", "a", "b"])
    assert seen["args"] == ["jd", "a", "b"]


def test_run_prints_diff_and_returns_zero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI run wrapper writes diff text followed by newline."""
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    old_path.write_text(json.dumps({"a": 1}), encoding="utf-8")
    new_path.write_text(json.dumps({"a": 2}), encoding="utf-8")

    rc = sdiff.run(old_sources=old_path, new_sources=new_path, output_format="summary")

    assert rc == 0
    out = capsys.readouterr().out
    assert out.endswith("\n")
    assert "changed a" in out


def test_render_selected_format_jd_falls_back_to_structural(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback to structural output when explicit jd renderer returns empty."""
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    old_data = sdiff._coerce_json_object({"a": 1}, context="old")
    new_data = sdiff._coerce_json_object({"a": 2}, context="new")
    old_path.write_text(json.dumps(old_data), encoding="utf-8")
    new_path.write_text(json.dumps(new_data), encoding="utf-8")

    monkeypatch.setattr(sdiff, "_render_jd_diff", lambda *_a: "")
    monkeypatch.setattr(
        sdiff, "_render_structural_hunks", lambda *_a: '@ ["a"]\n- 1\n+ 2'
    )
    rendered = sdiff._render_selected_format(
        "jd", old_path, new_path, old_data, new_data
    )
    assert rendered.startswith('@ ["a"]')

    monkeypatch.setattr(sdiff, "_render_summary_diff", lambda *_a: "")
    assert (
        sdiff._render_selected_format("summary", old_path, new_path, old_data, new_data)
        == ""
    )


def test_main_callback_path_invokes_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invoke Typer callback path to cover cli wrapper exit."""
    monkeypatch.setattr(sdiff, "run", lambda **_kwargs: 6)
    assert sdiff.main(["old.json", "new.json"]) == 6
