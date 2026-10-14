"""Installed-application identity index.

Maps bundle identifiers to installed bundles by scanning the standard app
roots. This is what lets the audit match TCC rows by bundle id instead of
display name, so rebranded apps keep their identity.
"""

import plistlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from lib.macos.tcc_audit.model import Bundle

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

DEFAULT_APP_ROOTS: tuple[Path, ...] = (
    Path("/Applications"),
    Path("~/Applications").expanduser(),
    Path("~/Applications/Home Manager Apps").expanduser(),
    Path("/System/Applications"),
)

# Apps that legitimately live outside the standard roots. The bundled
# claude-code CLI (which carries the com.anthropic.claude-code TCC
# identity) is hosted inside Claude's application-support directory.
DEFAULT_EXTRA_GLOBS: tuple[str, ...] = (
    "~/Library/Application Support/Claude/claude-code/*/claude.app",
)


def _bundle_from_plist(plist_path: Path) -> Bundle | None:
    """Parse one Info.plist into a :class:`Bundle`, or None if unusable."""
    try:
        info = plistlib.loads(plist_path.read_bytes())
    except OSError, plistlib.InvalidFileException:
        return None
    bundle_id = info.get("CFBundleIdentifier")
    if not isinstance(bundle_id, str) or not bundle_id:
        return None
    name = info.get("CFBundleName") or info.get("CFBundleDisplayName") or bundle_id
    app_dir = plist_path.parent.parent
    return Bundle(bundle_id=bundle_id, name=str(name), path=app_dir)


def _plists_under_app_roots(roots: Iterable[Path]) -> Iterator[Path]:
    for root in roots:
        yield from sorted(Path(root).glob("*.app/Contents/Info.plist"))


def _plists_from_extra_globs(patterns: Iterable[str]) -> Iterator[Path]:
    for pattern in patterns:
        yield from _extra_glob_plists(pattern)


def _extra_glob_plists(pattern: str) -> list[Path]:
    """Expand one extra app-glob into candidate Info.plist paths."""
    expanded = str(Path(pattern).expanduser())
    parts = Path(expanded).parts
    star_index = next((i for i, part in enumerate(parts) if "*" in part), None)
    if star_index is None:
        candidate = Path(expanded) / "Contents" / "Info.plist"
        return [candidate] if candidate.exists() else []
    base = Path(*parts[:star_index])
    relative = Path(*parts[star_index:])
    return sorted(base.glob(str(relative / "Contents" / "Info.plist")))


@dataclass(frozen=True, slots=True)
class BundleIndex:
    """Installed bundle identities keyed by bundle identifier."""

    by_id: Mapping[str, Bundle]

    def carrier(self, bundle_id: str) -> Bundle | None:
        """Return the installed bundle carrying this id, if any."""
        return self.by_id.get(bundle_id)

    def __contains__(self, bundle_id: str) -> bool:
        """Return whether the bundle id is installed."""
        return bundle_id in self.by_id

    @classmethod
    def scan(
        cls,
        roots: Sequence[Path] = DEFAULT_APP_ROOTS,
        extra_globs: Sequence[str] = DEFAULT_EXTRA_GLOBS,
    ) -> BundleIndex:
        """Build the index from app roots plus extra glob patterns."""
        by_id: dict[str, Bundle] = {}
        sources = (
            *_plists_under_app_roots(roots),
            *_plists_from_extra_globs(extra_globs),
        )
        for plist_path in sources:
            bundle = _bundle_from_plist(plist_path)
            if bundle is not None and bundle.bundle_id not in by_id:
                by_id[bundle.bundle_id] = bundle
        return cls(by_id=by_id)
