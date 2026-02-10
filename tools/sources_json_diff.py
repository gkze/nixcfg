"""Generate a Graphtage diff for sources.json changes."""

import argparse
import contextlib
import difflib
import importlib
import io
import json
import sys
from pathlib import Path
from typing import Any, cast


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


def _render_graphtage_diff(old_data: dict[str, Any], new_data: dict[str, Any]) -> str:
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
        out_stream=cast("Any", writer),
        ansi_color=False,
        quiet=True,
        options={"join_lists": True, "join_dict_items": True},
    )
    with printer:
        graphtage_json.JSONFormatter.DEFAULT_INSTANCE.print(printer, diff_tree)

    return writer.value().strip()


def _render_plain_diff(old_data: dict[str, Any], new_data: dict[str, Any]) -> str:
    old_text = json.dumps(old_data, indent=2, sort_keys=True).splitlines()
    new_text = json.dumps(new_data, indent=2, sort_keys=True).splitlines()
    lines = difflib.unified_diff(
        old_text,
        new_text,
        fromfile="old/sources.json",
        tofile="new/sources.json",
        lineterm="",
    )
    return "\n".join(lines).strip()


def run_diff(old_path: Path, new_path: Path) -> str:
    """Compare two sources.json files and return a diff string."""
    with old_path.open() as f:
        old_data: dict[str, Any] = json.load(f)
    with new_path.open() as f:
        new_data: dict[str, Any] = json.load(f)

    if old_data == new_data:
        return "No sources.json changes detected."

    rendered = _render_graphtage_diff(old_data, new_data)
    if not rendered:
        rendered = _render_plain_diff(old_data, new_data)
    return rendered or "No sources.json changes detected."


def main(argv: list[str] | None = None) -> int:
    """Run the CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_sources", type=Path, help="Path to old sources.json")
    parser.add_argument("new_sources", type=Path, help="Path to new sources.json")
    args = parser.parse_args(argv)
    sys.stdout.write(run_diff(args.old_sources, args.new_sources))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
