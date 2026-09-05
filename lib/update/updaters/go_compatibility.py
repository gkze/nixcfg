"""Compatibility checks for flake-backed Go package updaters."""

import re
from typing import TYPE_CHECKING, ClassVar, override

from lib.nix.commands.base import run_nix
from lib.update import locked_source as update_locked_source
from lib.update import nix as update_nix
from lib.update.nix import _build_flake_attr_expr
from lib.update.paths import local_flake_url
from lib.update.updaters.flake_backed import GoVendorHashUpdater

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.flake_lock import FlakeLockNode
    from lib.update.updaters.metadata import VersionInfo

_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_GO_VERSION_COMPONENT = r"(?:0|[1-9][0-9]*)"
_GO_VERSION_PATTERN = re.compile(
    rf"^{_GO_VERSION_COMPONENT}\.{_GO_VERSION_COMPONENT}"
    rf"(?:\.{_GO_VERSION_COMPONENT})?$"
)
_GO_DIRECTIVE_PATTERN = re.compile(
    r"^[ \t]*go[ \t]+(?P<version>[^ \t\r\n/]+)[ \t]*(?://[^\r\n]*)?\r?$",
)
_FACTORED_DIRECTIVE_START_PATTERN = re.compile(
    r"^[ \t]*[^ \t()]+[ \t]*\([ \t]*(?://.*)?$"
)
_FACTORED_DIRECTIVE_END_PATTERN = re.compile(r"^[ \t]*\)[ \t]*(?://.*)?$")
_TOOLCHAIN_DIRECTIVE_START_PATTERN = re.compile(r"^[ \t]*toolchain(?=[ \t=]|$)")
_TOOLCHAIN_DIRECTIVE_PATTERN = re.compile(
    r"^[ \t]*toolchain[ \t]+(?P<name>default|go1(?:\.[^ \t\r\n/]*)?)"
    r"[ \t]*(?://.*)?$"
)
_MAX_GO_MOD_BYTES = 1024 * 1024


def _go_version_triplet(version: str, *, context: str) -> tuple[int, int, int]:
    """Parse an exact Go ``major.minor[.patch]`` version."""
    if _GO_VERSION_PATTERN.fullmatch(version) is None:
        msg = f"{context} must be an exact Go version, got {version!r}"
        raise RuntimeError(msg)
    major, minor, *patch = version.split(".")
    return int(major), int(minor), int(patch[0]) if patch else 0


def _top_level_lines(text: str) -> list[str]:
    """Return lines outside factored ``go.mod`` directive blocks."""
    lines: list[str] = []
    in_block = False
    for line in text.splitlines():
        if in_block:
            if _FACTORED_DIRECTIVE_END_PATTERN.fullmatch(line) is not None:
                in_block = False
            continue
        if line.lstrip().startswith("//"):
            continue
        lines.append(line)
        if _FACTORED_DIRECTIVE_START_PATTERN.fullmatch(line) is not None:
            in_block = True
    return lines


def _extract_toolchain_suggestion(text: str, *, source_name: str) -> str | None:
    """Extract an optional, forward-compatible toolchain suggestion."""
    directive_lines = [
        line
        for line in _top_level_lines(text)
        if _TOOLCHAIN_DIRECTIVE_START_PATTERN.match(line) is not None
    ]
    if len(directive_lines) > 1:
        msg = (
            f"{source_name} go.mod must declare at most one Go toolchain, "
            f"found {len(directive_lines)}"
        )
        raise RuntimeError(msg)
    if not directive_lines:
        return None

    match = _TOOLCHAIN_DIRECTIVE_PATTERN.fullmatch(directive_lines[0])
    if match is None:
        msg = (
            f"{source_name} go.mod toolchain directive must name 'default' "
            "or one Go toolchain"
        )
        raise RuntimeError(msg)
    return match.group("name")


def _extract_go_requirement(go_mod: bytes, *, source_name: str) -> str:
    """Extract the minimum Go version declared by ``go.mod``."""
    try:
        text = go_mod.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = f"{source_name} go.mod is not valid UTF-8"
        raise RuntimeError(msg) from exc

    matches = [
        match
        for line in _top_level_lines(text)
        if (match := _GO_DIRECTIVE_PATTERN.fullmatch(line)) is not None
    ]
    if len(matches) != 1:
        msg = (
            f"{source_name} go.mod must declare exactly one Go version, "
            f"found {len(matches)}"
        )
        raise RuntimeError(msg)
    go_requirement = matches[0].group("version")
    _go_version_triplet(
        go_requirement,
        context=f"{source_name} go.mod requirement",
    )
    # A toolchain line is a suggestion, not a requirement. Nix's
    # buildGoModule uses GOTOOLCHAIN=local, so only the go line constrains the
    # package-selected compiler. Still reject ambiguous or malformed real
    # directives before update work proceeds.
    _extract_toolchain_suggestion(
        text,
        source_name=source_name,
    )
    return go_requirement


def _locked_github_source(
    node: FlakeLockNode,
    *,
    source_name: str,
    expected_owner: str,
    expected_repo: str,
) -> tuple[str, str, str]:
    """Return the expected repository and immutable commit from a lock node."""
    locked = node.locked
    if (
        locked is None
        or locked.type != "github"
        or not locked.owner
        or not locked.repo
        or not locked.rev
    ):
        msg = f"{source_name} flake input must resolve to a complete GitHub source"
        raise RuntimeError(msg)
    if (locked.owner, locked.repo) != (expected_owner, expected_repo):
        msg = (
            f"{source_name} flake input must resolve to "
            f"{expected_owner}/{expected_repo}, got {locked.owner}/{locked.repo}"
        )
        raise RuntimeError(msg)
    if _COMMIT_PATTERN.fullmatch(locked.rev) is None:
        msg = (
            f"{source_name} flake input revision must be an immutable commit, "
            f"got {locked.rev!r}"
        )
        raise RuntimeError(msg)
    return locked.owner, locked.repo, locked.rev


class GoModCompatibilityUpdater(GoVendorHashUpdater):
    """Validate a locked release against its package-selected Go toolchain."""

    GITHUB_OWNER: ClassVar[str]
    GITHUB_REPO: ClassVar[str]

    @classmethod
    def _go_version_expr(cls, platform: str) -> str:
        """Build an expression for the package-owned selected Go version."""
        return _build_flake_attr_expr(
            local_flake_url(),
            "pkgs",
            platform,
            cls.name,
            "passthru",
            "goVersion",
            quoted_indices=(1, 2),
        )

    async def _resolve_selected_go_version(self) -> str:
        """Evaluate the exact version behind the package's selected Go attribute."""
        platform = update_nix.get_current_nix_platform()
        result = await run_nix(
            [
                "nix",
                "eval",
                "--impure",
                "--raw",
                "--expr",
                self._go_version_expr(platform),
            ],
            command_timeout=self.config.default_subprocess_timeout,
            check=False,
        )
        version = result.stdout.strip()
        if result.returncode != 0 or not version:
            details = (
                result.stderr.strip() or result.stdout.strip() or "nix eval failed"
            )
            msg = (
                f"Failed to evaluate package-selected Go version for "
                f"{self.name}: {details}"
            )
            raise RuntimeError(msg)

        _go_version_triplet(
            version,
            context=f"{self.name} package-selected Go version",
        )
        return version

    def _validate_go_requirement(self, *, required: str, selected: str) -> None:
        required_version = _go_version_triplet(
            required,
            context=f"{self.name} go.mod requirement",
        )
        selected_version = _go_version_triplet(
            selected,
            context=f"{self.name} package-selected Go version",
        )
        if required_version > selected_version:
            msg = (
                f"{self.name} go.mod requires Go {required}, newer than "
                f"package-selected Go {selected}"
            )
            raise RuntimeError(msg)

    @override
    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Validate ``go.mod`` at the immutable refreshed input commit."""
        info = await super().fetch_latest(session)
        node = self._resolve_flake_node(info)
        _locked_github_source(
            node,
            source_name=self.name,
            expected_owner=self.GITHUB_OWNER,
            expected_repo=self.GITHUB_REPO,
        )
        source = await update_locked_source.resolve_locked_source(
            node,
            context=self.name,
            command_timeout=self.config.default_subprocess_timeout,
        )
        go_mod = await source.read_bytes(
            "go.mod",
            max_bytes=_MAX_GO_MOD_BYTES,
            description="go.mod",
        )
        required = _extract_go_requirement(go_mod, source_name=self.name)
        selected = await self._resolve_selected_go_version()
        self._validate_go_requirement(required=required, selected=selected)
        return info


__all__ = ["GoModCompatibilityUpdater"]
