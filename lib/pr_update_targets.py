"""Map a pull request's changed files to the updater targets it touches.

The Update pipeline only learns that an updater fix is wrong 30-95 min
after merge, in ``prepare-darwin`` or ``prepare-merge`` (39791273689,
39832195356). ``.github/workflows/pr-update-prepare.yml`` runs those
same native prepare stages before merge, scoped to these targets.
"""

import sys
from pathlib import Path, PurePosixPath

from lib.update.paths import UPDATER_FILE_NAME, get_repo_root, package_file_map_in

_OWNER_ROOTS = frozenset({"packages", "overlays"})
# ``packages/<name>`` is two parts; anything deeper lives under the owner dir.
_OWNER_DEPTH = 2


def touched_targets(changed: list[str], *, root: Path) -> tuple[str, ...]:
    """Return sorted updater targets whose package or overlay files changed.

    ``packages/<name>/...`` and ``overlays/<name>/...`` map to ``<name>``;
    flat overlay sidecars such as ``overlays/<name>.updater.py`` map by
    their first dotted component. Only names with an updater count.
    """
    names = set(package_file_map_in(root, UPDATER_FILE_NAME))
    found: set[str] = set()
    for raw in changed:
        parts = PurePosixPath(raw.strip()).parts
        if len(parts) < _OWNER_DEPTH or parts[0] not in _OWNER_ROOTS:
            continue
        name = parts[1] if len(parts) > _OWNER_DEPTH else parts[1].split(".", 1)[0]
        if name in names:
            found.add(name)
    return tuple(sorted(found))


def main(stdin: list[str] | None = None) -> int:
    """Print ``targets=<space-separated>`` for ``$GITHUB_OUTPUT``."""
    lines = sys.stdin.read().splitlines() if stdin is None else stdin
    targets = touched_targets(lines, root=get_repo_root())
    sys.stdout.write(f"targets={' '.join(targets)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
