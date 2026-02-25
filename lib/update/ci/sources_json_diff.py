"""Generate a diff for per-package source entry JSON changes."""

from __future__ import annotations

import contextlib
import difflib
import importlib
import io
import json
import pathlib
import re
import shutil
import sys
from typing import TYPE_CHECKING, Annotated, TypeGuard

import typer
from deepdiff import DeepDiff

from lib.update.ci._cli import make_typer_app, run_main
from lib.update.ci._subprocess import run_command

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
PathSegment = str | int
PathTuple = tuple[PathSegment, ...]
NoChangesMessage = "No source entry changes detected."


class _MissingType:
    pass


_MISSING = _MissingType()


class _BufferWriter:
    def __init__(self) -> None:
        self._parts: list[str] = []

    def write(self, text: str) -> int:
        self._parts.append(text)
        return len(text)

    def isatty(self) -> bool:
        return False

    def flush(self) -> None:
        return

    def value(self) -> str:
        return "".join(self._parts)


def _coerce_json_value(value: object, *, context: str) -> JsonValue:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_coerce_json_value(item, context=f"{context}[]") for item in value]
    if isinstance(value, dict):
        obj: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                msg = f"Expected string key in {context}, got {type(key).__name__}"
                raise TypeError(msg)
            obj[key] = _coerce_json_value(item, context=f"{context}.{key}")
        return obj
    msg = f"Unsupported JSON value in {context}: {type(value).__name__}"
    raise TypeError(msg)


def _coerce_json_object(value: object, *, context: str) -> dict[str, JsonValue]:
    coerced = _coerce_json_value(value, context=context)
    if not isinstance(coerced, dict):
        msg = f"Expected JSON object in {context}, got {type(coerced).__name__}"
        raise TypeError(msg)
    return coerced


def _is_json_value(value: JsonValue | _MissingType) -> TypeGuard[JsonValue]:
    return not isinstance(value, _MissingType)


def _render_graphtage_diff(
    old_data: dict[str, JsonValue],
    new_data: dict[str, JsonValue],
) -> str:
    try:
        graphtage_json = importlib.import_module("graphtage.json")
        graphtage_printer = importlib.import_module("graphtage.printer")
    except ImportError:
        return ""

    default_printer = graphtage_printer.DEFAULT_PRINTER
    printer_cls = graphtage_printer.Printer

    previous_quiet = default_printer.quiet
    default_printer.quiet = True
    try:
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            from_tree = graphtage_json.build_tree(old_data)
            to_tree = graphtage_json.build_tree(new_data)
            diff_tree = from_tree.diff(to_tree)
    finally:
        default_printer.quiet = previous_quiet

    writer = _BufferWriter()
    printer = printer_cls(
        out_stream=writer,
        ansi_color=False,
        quiet=True,
        options={"join_lists": True, "join_dict_items": True},
    )
    with printer:
        graphtage_json.JSONFormatter.DEFAULT_INSTANCE.print(printer, diff_tree)

    return writer.value().strip()


def _render_jd_diff(_old_path: pathlib.Path, _new_path: pathlib.Path) -> str:
    """Render optional external ``jd`` integration output."""
    jd_binary = shutil.which("jd")
    if jd_binary is None:
        return ""

    result = _run_command([jd_binary, str(_old_path), str(_new_path)])
    # jd exits 0=identical, 1=different, 2+=error
    if result.returncode > 1 or not result.stdout:
        return ""
    return result.stdout.strip()


def _run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return run_command(args, check=False, capture_output=True)


def _json_value(value: JsonValue) -> str:
    return json.dumps(value, sort_keys=True)


def _format_value(value: JsonValue, *, max_len: int = 140) -> str:
    rendered = _json_value(value)
    if len(rendered) <= max_len:
        return rendered
    return rendered[: max_len - 3] + "..."


def _format_path(path: list[PathSegment]) -> str:
    if not path:
        return "$"

    out = ""
    for segment in path:
        if isinstance(segment, int):
            out += f"[{segment}]"
            continue
        if out:
            out += "."
        out += segment
    return out


def _path_sort_key(path: PathTuple) -> tuple[tuple[int, str], ...]:
    return tuple(
        (0, str(part)) if isinstance(part, str) else (1, str(part)) for part in path
    )


_DEEPDIFF_PATH_SEGMENT_RE = re.compile(r"\['([^']+)'\]|\[(\d+)\]")


def _parse_deepdiff_path(path: str) -> PathTuple:
    if path == "root":
        return ()
    segments: list[PathSegment] = []
    for string_segment, int_segment in _DEEPDIFF_PATH_SEGMENT_RE.findall(path):
        if int_segment:
            segments.append(int(int_segment))
            continue
        segments.append(string_segment)
    return tuple(segments)


def _path_from_deepdiff_node(node: object) -> PathTuple:
    path_func = getattr(node, "path", None)
    if not callable(path_func):
        return ()
    with contextlib.suppress(TypeError):
        path_list = path_func(output_format="list")
        if isinstance(path_list, list):
            segments: list[PathSegment] = []
            for segment in path_list:
                if isinstance(segment, (int, str)):
                    segments.append(segment)
                else:
                    segments.append(str(segment))
            return tuple(segments)
    with contextlib.suppress(TypeError):
        path_text = path_func()
        if isinstance(path_text, str):
            return _parse_deepdiff_path(path_text)
    return ()


def _extract_change_value(node: object, attr: str) -> JsonValue | _MissingType:
    value = getattr(node, attr, _MISSING)
    if value is _MISSING:
        return _MISSING
    return _coerce_json_value(value, context=f"deepdiff.{attr}")


def _iter_leaf_values(
    path: PathTuple, value: JsonValue
) -> list[tuple[PathTuple, JsonValue]]:
    if isinstance(value, dict):
        leaves: list[tuple[PathTuple, JsonValue]] = []
        for key, item in value.items():
            leaves.extend(_iter_leaf_values((*path, key), item))
        return leaves
    if isinstance(value, list):
        leaves = []
        for idx, item in enumerate(value):
            leaves.extend(_iter_leaf_values((*path, idx), item))
        return leaves
    return [(path, value)]


def _collect_leaf_changes(
    old_data: JsonValue,
    new_data: JsonValue,
) -> list[tuple[list[PathSegment], JsonValue | _MissingType, JsonValue | _MissingType]]:
    diff = DeepDiff(old_data, new_data, view="tree", verbose_level=2)
    change_map: dict[
        PathTuple, tuple[JsonValue | _MissingType, JsonValue | _MissingType]
    ] = {}

    for node in diff.get("values_changed", []):
        path = _path_from_deepdiff_node(node)
        change_map[path] = (
            _extract_change_value(node, "t1"),
            _extract_change_value(node, "t2"),
        )

    for node in diff.get("type_changes", []):
        path = _path_from_deepdiff_node(node)
        change_map[path] = (
            _extract_change_value(node, "t1"),
            _extract_change_value(node, "t2"),
        )

    for kind in ("dictionary_item_added", "iterable_item_added", "set_item_added"):
        for node in diff.get(kind, []):
            path = _path_from_deepdiff_node(node)
            new_value = _extract_change_value(node, "t2")
            if _is_json_value(new_value):
                for leaf_path, leaf_value in _iter_leaf_values(path, new_value):
                    change_map[leaf_path] = (_MISSING, leaf_value)
            else:
                change_map[path] = (_MISSING, new_value)

    for kind in (
        "dictionary_item_removed",
        "iterable_item_removed",
        "set_item_removed",
    ):
        for node in diff.get(kind, []):
            path = _path_from_deepdiff_node(node)
            old_value = _extract_change_value(node, "t1")
            if _is_json_value(old_value):
                for leaf_path, leaf_value in _iter_leaf_values(path, old_value):
                    change_map[leaf_path] = (leaf_value, _MISSING)
            else:
                change_map[path] = (old_value, _MISSING)

    changes: list[
        tuple[list[PathSegment], JsonValue | _MissingType, JsonValue | _MissingType]
    ] = []
    for path in sorted(change_map, key=_path_sort_key):
        old_value, new_value = change_map[path]
        if old_value == new_value:
            continue
        changes.append((list(path), old_value, new_value))
    return changes


def _render_structural_hunks(old_data: JsonValue, new_data: JsonValue) -> str:
    lines: list[str] = []
    for path, old_value, new_value in _collect_leaf_changes(old_data, new_data):
        lines.append(f"@ {json.dumps(path)}")
        if _is_json_value(old_value):
            lines.append(f"- {_json_value(old_value)}")
        if _is_json_value(new_value):
            lines.append(f"+ {_json_value(new_value)}")
    return "\n".join(lines).strip()


def _render_summary_diff(
    old_data: dict[str, JsonValue], new_data: dict[str, JsonValue]
) -> str:
    lines: list[str] = []
    for path, old_value, new_value in _collect_leaf_changes(old_data, new_data):
        path_str = _format_path(path)
        if _is_json_value(new_value) and not _is_json_value(old_value):
            lines.append(f"added {path_str}: {_format_value(new_value)}")
            continue
        if _is_json_value(old_value) and not _is_json_value(new_value):
            lines.append(f"removed {path_str}: {_format_value(old_value)}")
            continue
        if _is_json_value(old_value) and _is_json_value(new_value):
            lines.append(
                "changed "
                f"{path_str}: {_format_value(old_value)} -> "
                f"{_format_value(new_value)}",
            )

    return "\n".join(lines).strip()


def _render_plain_diff(
    old_data: dict[str, JsonValue], new_data: dict[str, JsonValue]
) -> str:
    structured = _render_structural_hunks(old_data, new_data)
    if structured:
        return structured

    old_text = json.dumps(old_data, indent=2, sort_keys=True).splitlines()
    new_text = json.dumps(new_data, indent=2, sort_keys=True).splitlines()
    lines = difflib.unified_diff(
        old_text,
        new_text,
        fromfile="old/source-entry.json",
        tofile="new/source-entry.json",
        lineterm="",
    )
    return "\n".join(lines).strip()


def _read_json(path: pathlib.Path) -> dict[str, JsonValue]:
    with path.open(encoding="utf-8") as handle:
        loaded = json.load(handle)
    return _coerce_json_object(loaded, context=str(path))


def _render_selected_format(
    output_format: str,
    old_path: pathlib.Path,
    new_path: pathlib.Path,
    old_data: dict[str, JsonValue],
    new_data: dict[str, JsonValue],
) -> str:
    renderers: dict[str, Callable[[], str]] = {
        "summary": lambda: _render_summary_diff(old_data, new_data),
        "jd": lambda: _render_jd_diff(old_path, new_path),
        "graphtage": lambda: _render_graphtage_diff(old_data, new_data),
        "structural": lambda: _render_structural_hunks(old_data, new_data),
        "unified": lambda: _render_plain_diff(old_data, new_data),
    }

    if output_format != "auto":
        rendered = renderers[output_format]()
        if rendered:
            return rendered
        if output_format in {"jd", "graphtage"}:
            return _render_structural_hunks(old_data, new_data)
        return ""

    for renderer in (
        lambda: _render_jd_diff(old_path, new_path),
        lambda: _render_graphtage_diff(old_data, new_data),
        lambda: _render_plain_diff(old_data, new_data),
    ):
        rendered = renderer()
        if rendered:
            return rendered
    return ""


def run_diff(
    old_path: pathlib.Path,
    new_path: pathlib.Path,
    *,
    output_format: str = "auto",
) -> str:
    """Compare two source entry JSON files and return a diff string."""
    old_path = pathlib.Path(old_path)
    new_path = pathlib.Path(new_path)
    old_data = _read_json(old_path)
    new_data = _read_json(new_path)

    if old_data == new_data:
        return NoChangesMessage

    rendered = _render_selected_format(
        output_format, old_path, new_path, old_data, new_data
    )
    return rendered or NoChangesMessage


def run(
    *,
    old_sources: pathlib.Path,
    new_sources: pathlib.Path,
    output_format: str = "auto",
) -> int:
    """Render a diff between two source-entry JSON files."""
    old_sources = pathlib.Path(old_sources)
    new_sources = pathlib.Path(new_sources)
    sys.stdout.write(run_diff(old_sources, new_sources, output_format=output_format))
    sys.stdout.write("\n")
    return 0


app = make_typer_app(
    help_text="Generate a diff for source entry JSON changes.",
    no_args_is_help=False,
)


_standalone_app = make_typer_app(
    help_text="Generate a diff for source entry JSON changes.",
    no_args_is_help=False,
)


@_standalone_app.command()
@app.callback(invoke_without_command=True)
def cli(
    old_sources: Annotated[
        pathlib.Path,
        typer.Argument(help="Path to old source JSON file."),
    ],
    new_sources: Annotated[
        pathlib.Path,
        typer.Argument(help="Path to new source JSON file."),
    ],
    *,
    output_format: Annotated[
        str,
        typer.Option("--format", help="Diff output format."),
    ] = "auto",
) -> None:
    """Compare source entry JSON files and print a diff."""
    raise typer.Exit(
        code=run(
            old_sources=old_sources,
            new_sources=new_sources,
            output_format=output_format,
        )
    )


def main(argv: list[str] | None = None) -> int:
    """Run the CLI entrypoint."""
    return run_main(_standalone_app, argv=argv, prog_name="sources-json-diff")


if __name__ == "__main__":
    raise SystemExit(main())
