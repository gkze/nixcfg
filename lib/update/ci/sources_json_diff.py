"""Generate a diff for per-package source entry JSON changes."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import difflib
import importlib
import io
import json
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, TypeGuard

if TYPE_CHECKING:
    from collections.abc import Callable

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
PathSegment = str | int
PathTuple = tuple[PathSegment, ...]
NoChangesMessage = "No source entry changes detected."


class _MissingType:
    pass


_MISSING = _MissingType()


@dataclasses.dataclass(frozen=True)
class _CommandResult:
    returncode: int
    stdout: str


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


def _render_jd_diff(_old_path: Path, _new_path: Path) -> str:
    """Render optional external ``jd`` integration output."""
    jd_binary = shutil.which("jd")
    if jd_binary is None:
        return ""

    result = _run_command([jd_binary, str(_old_path), str(_new_path)])
    # jd exits 0=identical, 1=different, 2+=error
    if result.returncode > 1 or not result.stdout:
        return ""
    return result.stdout.strip()


async def _run_command_async(args: list[str]) -> _CommandResult:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_data, _stderr_data = await process.communicate()
    return _CommandResult(
        returncode=int(process.returncode or 0),
        stdout=(stdout_data or b"").decode(),
    )


def _run_command(args: list[str]) -> _CommandResult:
    return asyncio.run(_run_command_async(args))


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


def _flatten_leaves(
    value: JsonValue, path: PathTuple = ()
) -> dict[PathTuple, JsonValue]:
    if isinstance(value, dict):
        out: dict[PathTuple, JsonValue] = {}
        for key in sorted(value):
            out.update(_flatten_leaves(value[key], (*path, key)))
        return out

    if isinstance(value, list):
        out: dict[PathTuple, JsonValue] = {}
        for idx, item in enumerate(value):
            out.update(_flatten_leaves(item, (*path, idx)))
        return out

    return {path: value}


def _collect_leaf_changes(
    old_data: JsonValue,
    new_data: JsonValue,
) -> list[tuple[list[PathSegment], JsonValue | _MissingType, JsonValue | _MissingType]]:
    old_leaves = _flatten_leaves(old_data)
    new_leaves = _flatten_leaves(new_data)
    all_paths = sorted(set(old_leaves) | set(new_leaves), key=_path_sort_key)

    changes: list[
        tuple[list[PathSegment], JsonValue | _MissingType, JsonValue | _MissingType]
    ] = []
    for path in all_paths:
        old_value = old_leaves.get(path, _MISSING)
        new_value = new_leaves.get(path, _MISSING)
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


def _read_json(path: Path) -> dict[str, JsonValue]:
    with path.open(encoding="utf-8") as handle:
        loaded = json.load(handle)
    return _coerce_json_object(loaded, context=str(path))


def _render_selected_format(
    output_format: str,
    old_path: Path,
    new_path: Path,
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


def run_diff(old_path: Path, new_path: Path, *, output_format: str = "auto") -> str:
    """Compare two source entry JSON files and return a diff string."""
    old_data = _read_json(old_path)
    new_data = _read_json(new_path)

    if old_data == new_data:
        return NoChangesMessage

    rendered = _render_selected_format(
        output_format, old_path, new_path, old_data, new_data
    )
    return rendered or NoChangesMessage


def main(argv: list[str] | None = None) -> int:
    """Run the CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_sources", type=Path, help="Path to old source JSON file")
    parser.add_argument("new_sources", type=Path, help="Path to new source JSON file")
    parser.add_argument(
        "--format",
        choices=["auto", "summary", "jd", "graphtage", "structural", "unified"],
        default="auto",
        help="Diff output format (default: auto)",
    )
    args = parser.parse_args(argv)
    sys.stdout.write(
        run_diff(args.old_sources, args.new_sources, output_format=args.format)
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
