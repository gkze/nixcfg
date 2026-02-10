"""Generate a human-readable diff of flake.lock changes."""

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from libnix.models.flake_lock import FlakeLock, LockedRef


@dataclass
class InputInfo:
    """Relevant info from a flake input node."""

    name: str
    type: str
    owner: str
    repo: str
    rev: str
    date: str


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
        date=datetime.fromtimestamp(last_modified, tz=UTC).strftime("%Y-%m-%d")
        if last_modified
        else "",
    )


def _run_diff(old_lock_path: Path, new_lock_path: Path) -> None:
    """Compare two flake.lock files and print the differences."""
    old_lock = FlakeLock.from_file(old_lock_path)
    new_lock = FlakeLock.from_file(new_lock_path)

    all_inputs = set(old_lock.input_names) | set(new_lock.input_names)

    added: list[InputInfo] = []
    removed: list[InputInfo] = []
    updated: list[tuple[InputInfo, InputInfo]] = []
    for name in sorted(all_inputs):
        old_info = get_input_info(old_lock, name)
        new_info = get_input_info(new_lock, name)

        if old_info and new_info and old_info.rev != new_info.rev:
            updated.append((old_info, new_info))
        elif new_info and not old_info:
            added.append(new_info)
        elif old_info and not new_info:
            removed.append(old_info)

    if not (added or removed or updated):
        return

    for _old, _new in updated:
        pass
    for _info in added:
        pass
    for _info in removed:
        pass


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
