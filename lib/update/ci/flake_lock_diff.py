"""Generate a human-readable diff of flake.lock changes."""

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from lib.nix.models.flake_lock import FlakeLock, LockedRef


@dataclass
class InputInfo:
    """Relevant info from a flake input node."""

    name: str
    type: str
    owner: str
    repo: str
    rev: str
    rev_full: str
    date: str


def _format_source(info: InputInfo) -> str:
    if info.owner and info.repo:
        return f"{info.owner}/{info.repo}"
    return info.name


def _format_source_cell(info: InputInfo) -> str:
    source = _format_source(info)
    if info.owner and info.repo and info.type == "github":
        return f"[{source}](https://github.com/{info.owner}/{info.repo})"
    return source


def _format_rev_date(info: InputInfo) -> str:
    if info.date:
        return f"{info.rev} ({info.date})"
    return info.rev


def _format_revision_cell(info: InputInfo) -> str:
    label = info.rev
    if info.owner and info.repo and info.type == "github" and info.rev_full:
        return f"[{label}](https://github.com/{info.owner}/{info.repo}/commit/{info.rev_full})"
    return label


def _format_compare_cell(old_info: InputInfo, new_info: InputInfo) -> str:
    if (
        old_info.type == "github"
        and new_info.type == "github"
        and old_info.owner
        and old_info.repo
        and old_info.owner == new_info.owner
        and old_info.repo == new_info.repo
        and old_info.rev_full
        and new_info.rev_full
    ):
        url = (
            f"https://github.com/{old_info.owner}/{old_info.repo}/compare/"
            f"{old_info.rev_full}...{new_info.rev_full}"
        )
        return f"[Diff]({url})"
    return "-"


def _collect_changes(
    old_lock: FlakeLock,
    new_lock: FlakeLock,
) -> tuple[list[InputInfo], list[InputInfo], list[tuple[InputInfo, InputInfo]]]:
    added: list[InputInfo] = []
    removed: list[InputInfo] = []
    updated: list[tuple[InputInfo, InputInfo]] = []

    all_inputs = set(old_lock.input_names) | set(new_lock.input_names)
    for name in sorted(all_inputs):
        old_info = get_input_info(old_lock, name)
        new_info = get_input_info(new_lock, name)

        if old_info is None and new_info is not None:
            added.append(new_info)
            continue
        if old_info is not None and new_info is None:
            removed.append(old_info)
            continue
        if (
            old_info is not None
            and new_info is not None
            and old_info.rev_full != new_info.rev_full
        ):
            updated.append((old_info, new_info))

    return added, removed, updated


def _append_section(lines: list[str], heading: str, section_lines: list[str]) -> None:
    if not section_lines:
        return
    if lines:
        lines.append("")
    lines.append(heading)
    lines.extend(section_lines)


def _escape_md(value: str) -> str:
    return value.replace("|", "\\|")


def _append_table(
    lines: list[str],
    heading: str,
    headers: list[str],
    rows: list[list[str]],
) -> None:
    if not rows:
        return
    if lines:
        lines.append("")
    lines.append(heading)
    lines.append("")
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    lines.extend(
        "| " + " | ".join(_escape_md(cell) for cell in row) + " |" for row in rows
    )


def run_diff(old_lock_path: Path, new_lock_path: Path) -> str:
    """Compare two flake.lock files and return a readable summary."""
    old_lock = FlakeLock.from_file(old_lock_path)
    new_lock = FlakeLock.from_file(new_lock_path)

    added, removed, updated = _collect_changes(old_lock, new_lock)

    if not (added or removed or updated):
        return ""

    lines: list[str] = []
    _append_table(
        lines,
        "### Updated flake inputs",
        ["Input", "Source", "From", "To", "Diff"],
        [
            [
                new_info.name,
                _format_source_cell(new_info),
                _format_revision_cell(old_info),
                _format_revision_cell(new_info),
                _format_compare_cell(old_info, new_info),
            ]
            for old_info, new_info in updated
        ],
    )
    _append_table(
        lines,
        "### Added flake inputs",
        ["Input", "Source", "Revision"],
        [
            [
                info.name,
                _format_source_cell(info),
                _format_revision_cell(info),
            ]
            for info in added
        ],
    )
    _append_table(
        lines,
        "### Removed flake inputs",
        ["Input", "Source", "Revision"],
        [
            [
                info.name,
                _format_source_cell(info),
                _format_revision_cell(info),
            ]
            for info in removed
        ],
    )

    return "\n".join(lines)


def get_input_info(lock: FlakeLock, name: str) -> InputInfo | None:
    """Extract relevant info from a flake input node."""
    locked: LockedRef | None = lock.get_locked(name)
    if locked is None:
        return None

    last_modified = locked.last_modified or 0

    return InputInfo(
        name=name,
        type=locked.type,
        owner=locked.owner or "",
        repo=locked.repo or "",
        rev=(locked.rev or "")[:7],
        rev_full=locked.rev or "",
        date=datetime.fromtimestamp(last_modified, tz=UTC).strftime("%Y-%m-%d")
        if last_modified
        else "",
    )


def _run_diff(old_lock_path: Path, new_lock_path: Path) -> None:
    """Compare two flake.lock files and print the differences."""
    diff = run_diff(old_lock_path, new_lock_path)
    if diff:
        sys.stdout.write(f"{diff}\n")


def main(argv: list[str] | None = None) -> int:
    """Run the CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_lock", type=Path, help="Path to old flake.lock")
    parser.add_argument("new_lock", type=Path, help="Path to new flake.lock")
    args = parser.parse_args(argv)
    _run_diff(args.old_lock, args.new_lock)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
