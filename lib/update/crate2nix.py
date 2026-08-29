"""Shared crate2nix regeneration logic for updates and maintenance commands."""

import asyncio
import errno
import hashlib
import json
import os
import queue
import re
import selectors
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field, fields
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypedDict, cast

from nix_manipulator import parse
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.expression import NixExpression
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.let import LetExpression
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.path import NixPath
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet

from lib.import_utils import load_module_from_path
from lib.update.artifacts import GeneratedArtifact
from lib.update.events import (
    EventStream,
    StatusInfo,
    StatusKind,
    UpdateEvent,
    UpdateEventKind,
)
from lib.update.flake import get_flake_input_node
from lib.update.io import atomic_write_text
from lib.update.nix import (
    _build_flake_attr_expr,
    _build_package_path_attr_expr,
    get_current_nix_platform,
)
from lib.update.nix_expr import compact_nix_expr, select_attrs
from lib.update.paths import REPO_ROOT, local_flake_url

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import IO

    from lib.nix.models.sources import SourceEntry

_NORMALIZER_RESULT_SIZE = 3
_CRATE2NIX_COMMAND_TIMEOUT_SECONDS = 2400
_CRATE2NIX_CAPTURE_LIMIT_CHARS = 256 * 1024
_CRATE2NIX_PROGRESS_INTERVAL_SECONDS = 30.0
_CRATE2NIX_PROGRESS_LINE_LIMIT = 320
_CRATE2NIX_PROGRESS_QUEUE_SIZE = 16
_CRATE2NIX_PROCESS_POLL_SECONDS = 0.1
_CRATE2NIX_TERMINATION_GRACE_SECONDS = 1.0
_CRATE2NIX_IO_CHUNK_BYTES = 8192
_CRATE2NIX_CARGO_HOME_ENV = "NIXCFG_CRATE2NIX_CARGO_HOME"
_CRATE2NIX_GENERATE_ATTEMPTS = 3
_CRATE2NIX_GENERATE_RETRY_DELAY_SECONDS = 2.0
_CRATE2NIX_GENERATE_LOCK = threading.Lock()
_RETRYABLE_CRATE2NIX_NETWORK_CONTEXT_MARKERS = (
    "cargo metadata",
    "crates.io",
    "failed to download",
    "failed to get",
    "git fetch",
    "index.crates.io",
    "nix-prefetch-git",
)
_RETRYABLE_CRATE2NIX_TRANSIENT_MARKERS = (
    "Directory not empty",
    "No such file or directory",
    "Operation timed out",
    "Operation too slow",
    "Timeout was reached",
    "cannot create the lock file",
    "Connection timed out",
    "Could not resolve host",
    "Failed to connect",
    "TLS connection",
    "RPC failed",
    "HTTP/2 stream",
    "early EOF",
    "The requested URL returned error: 5",
)
_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|$))")
_ENOSPC_MARKERS = ("no space left on device", "enospc")


class Crate2NixCommandTimeoutError(RuntimeError):
    """A managed crate2nix subprocess exceeded its wall-clock budget."""


class Crate2NixCommandCancelledError(RuntimeError):
    """A managed crate2nix subprocess was cancelled by its caller."""


class Crate2NixResourceError(RuntimeError):
    """A crate2nix operation failed because local resources were exhausted."""


class _BoundedCapture:
    """Retain a fixed-size diagnostic tail without buffering complete output."""

    def __init__(self, limit: int = _CRATE2NIX_CAPTURE_LIMIT_CHARS) -> None:
        self._chunks: deque[str] = deque()
        self._classification_tail = ""
        self.enospc = False
        self._length = 0
        self._limit = limit
        self._truncated = False

    def append(self, text: str) -> None:
        """Append text while discarding the oldest content beyond the limit."""
        classification_text = (self._classification_tail + text).casefold()
        self.enospc = self.enospc or any(
            marker in classification_text for marker in _ENOSPC_MARKERS
        )
        self._classification_tail = classification_text[-32:]
        self._chunks.append(text)
        self._length += len(text)
        while self._length > self._limit and self._chunks:
            excess = self._length - self._limit
            first = self._chunks[0]
            if len(first) <= excess:
                self._chunks.popleft()
                self._length -= len(first)
            else:
                self._chunks[0] = first[excess:]
                self._length -= excess
            self._truncated = True

    def render(self) -> str:
        """Return the retained tail with an explicit truncation marker."""
        text = "".join(self._chunks)
        if not self._truncated:
            return text
        marker = "[... output truncated ...]\n"
        available = max(0, self._limit - len(marker))
        return marker + text[-available:]


class _Normalizer(Protocol):
    def __call__(self, text: str) -> tuple[str, int, bool]: ...


@dataclass(frozen=True)
class Crate2NixTarget:
    """Package-specific crate2nix regeneration metadata."""

    name: str
    patched_src_installable: str
    cargo_nix: Path
    crate_hashes: Path
    normalizer_path: Path
    supported_platforms: tuple[str, ...]
    cargo_manifest_relpath: Path = field(default_factory=lambda: Path("Cargo.toml"))
    source_input: str | None = None
    root_src_relpath: Path = field(default_factory=Path)
    crate_sources: Path | None = None
    externally_overridden_source_paths: tuple[str, ...] = ()

    @property
    def artifact_paths(self) -> tuple[Path, ...]:
        """Return every checked-in artifact owned by this target."""
        return (
            self.cargo_nix,
            self.crate_hashes,
            *((self.crate_sources,) if self.crate_sources is not None else ()),
        )


@dataclass(frozen=True)
class RefreshResult:
    """Materialized crate2nix outputs for one package."""

    cargo_nix: str
    crate_hashes: str
    crate_sources: str | None = None


class CrateSourceSlice(TypedDict):
    """Content-addressed metadata for one local crate source directory."""

    hash: str
    name: str


@dataclass(frozen=True)
class _LockedGitPackage:
    """Resolved Git identity for one package in the candidate Cargo.lock."""

    locator: str
    name: str
    revision: str
    version: str


_CLEAN_SOURCE_WITH_SRC_RE = re.compile(
    r"src\s*=\s*lib\.cleanSourceWith\s*\{\s*filter\s*=\s*sourceFilter;"
    r"\s*src\s*=\s*(?P<src>[^;]+);\s*\};"
)
_ROOT_SRC_ARGUMENT_LINE_RE = re.compile(
    r"(?m)^(?P<line>[ \t]*, rootSrc \? \./\.[ \t]*)$"
)
_STANDALONE_FORMAL_COMMA_RE = re.compile(r"(?m)^(?P<indent>[ \t]*),[ \t]*$")
_CRATE_SOURCE_ARGUMENT = (
    ', crateSource ? relativePath: throw "Cargo.nix requires crateSource when a '
    'local crate source is evaluated"'
)


def _walk_nix_expressions(root: NixExpression) -> Iterator[NixExpression]:
    """Yield each semantic expression once without following scope back-references."""
    pending = [root]
    seen: set[int] = set()
    while pending:
        expression = pending.pop()
        identity = id(expression)
        if identity in seen:
            continue
        seen.add(identity)
        yield expression
        for descriptor in fields(expression):
            if descriptor.name in {"scope", "scope_state"}:
                continue
            value = getattr(expression, descriptor.name)
            if isinstance(value, NixExpression):
                pending.append(value)
            elif isinstance(value, (list, tuple)):
                pending.extend(
                    item for item in value if isinstance(item, NixExpression)
                )


def _references_root_src(root: NixExpression) -> bool:
    """Return whether an expression semantically refers to the rootSrc formal."""
    return any(
        (isinstance(expression, Identifier) and expression.name == "rootSrc")
        or (
            isinstance(expression, StringPrimitive)
            and "${" in expression.value
            and re.search(r"\brootSrc\b", expression.value) is not None
        )
        for expression in _walk_nix_expressions(root)
    )


def _assert_no_unconverted_local_clean_source(refreshed: str) -> None:
    """Fail closed when generator drift leaves a rootSrc filter in Cargo.nix."""
    # crate2nix emits valid trailing commas as standalone lines in function
    # formal sets. tree-sitter-nix currently rejects that layout, so normalize
    # only the inspection copy while leaving the generated artifact untouched.
    inspection_source = _STANDALONE_FORMAL_COMMA_RE.sub(
        r"\g<indent>",
        refreshed,
    )
    parsed = parse(inspection_source)
    if parsed.contains_error or parsed.expr is None:
        msg = "Could not parse transformed Cargo.nix source contract"
        raise RuntimeError(msg)

    for expression in _walk_nix_expressions(parsed.expr):
        if not isinstance(expression, FunctionCall):
            continue
        function = expression.name
        while isinstance(function, Parenthesis):
            function = function.value
        function_name = function if isinstance(function, str) else function.rebuild()
        if function_name != "lib.cleanSourceWith":
            continue
        if isinstance(expression.argument, NixExpression) and _references_root_src(
            expression.argument
        ):
            msg = "Unconverted rootSrc-backed cleanSourceWith expression in Cargo.nix"
            raise RuntimeError(msg)


def _stabilize_generated_command_comment(
    target: Crate2NixTarget,
    refreshed: str,
) -> str:
    """Replace crate2nix's dynamic command comment with a stable one."""
    refreshed_lines = refreshed.splitlines()
    command_comment_index = next(
        (
            index
            for index, line in enumerate(refreshed_lines)
            if line.startswith('#   "generate"')
        ),
        None,
    )
    if command_comment_index is None:
        return refreshed
    refreshed_lines[command_comment_index] = (
        f'#   "generate" "-f" "{target.cargo_manifest_relpath.as_posix()}" '
        f'"-o" "{target.cargo_nix.as_posix()}" '
        f'"-h" "{target.crate_hashes.as_posix()}" '
        '"--default-features"'
    )
    trailing_newline = refreshed.endswith("\n")
    rebuilt = "\n".join(refreshed_lines)
    return rebuilt + ("\n" if trailing_newline else "")


def _stabilize_generated_root_src_paths(
    refreshed: str,
    *,
    patched_src: Path,
    generated_cargo: Path,
) -> str:
    """Rewrite generated store-path source roots back to ``${rootSrc}`` references."""
    patched_src_candidates = (patched_src, patched_src.resolve())
    generated_parent_candidates = (
        generated_cargo.parent,
        generated_cargo.parent.resolve(),
    )
    prefixes = tuple(
        dict.fromkeys(
            candidate
            for source in patched_src_candidates
            for candidate in (
                source.as_posix(),
                *(
                    os.path.relpath(source, parent).replace(os.sep, "/")
                    for parent in generated_parent_candidates
                ),
            )
        )
    )

    def _rewrite(match: re.Match[str]) -> str:
        raw_src = match.group("src").strip()
        candidate = raw_src.strip('"')
        for prefix in prefixes:
            if candidate == prefix or candidate.startswith(prefix + "/"):
                suffix = candidate[len(prefix) :].lstrip("/")
                normalized = '"${rootSrc}"'
                if suffix:
                    normalized = f'"${{rootSrc}}/{suffix}"'
                return match.group(0).replace(raw_src, normalized)
        return match.group(0)

    return _CLEAN_SOURCE_WITH_SRC_RE.sub(_rewrite, refreshed)


def _apply_crate_source_contract(refreshed: str) -> tuple[str, tuple[str, ...]]:
    """Replace evaluator-time local filters with an injected source provider."""
    source_paths: set[str] = set()

    def _rewrite(match: re.Match[str]) -> str:
        raw_src = match.group("src").strip()
        if raw_src == '"${rootSrc}"':
            relative_path = "."
        elif raw_src.startswith('"${rootSrc}/') and raw_src.endswith('"'):
            relative_path = raw_src.removeprefix('"${rootSrc}/').removesuffix('"')
        else:
            return match.group(0)
        source_paths.add(relative_path)
        return f"src = crateSource sourceFilter {json.dumps(relative_path)};"

    rewritten = _CLEAN_SOURCE_WITH_SRC_RE.sub(_rewrite, refreshed)
    _assert_no_unconverted_local_clean_source(rewritten)
    if not source_paths or "crateSource ?" in rewritten:
        return rewritten, tuple(sorted(source_paths))

    argument_match = _ROOT_SRC_ARGUMENT_LINE_RE.search(rewritten)
    if argument_match is None:
        msg = "Could not find rootSrc argument for crateSource injection"
        raise RuntimeError(msg)
    rewritten = (
        rewritten[: argument_match.end()]
        + f"\n{_CRATE_SOURCE_ARGUMENT}"
        + rewritten[argument_match.end() :]
    )
    return rewritten, tuple(sorted(source_paths))


def _resolve_production_root(
    target: Crate2NixTarget,
    *,
    cancel_event: threading.Event | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[Path, str]:
    """Resolve Cargo.nix's evaluator-visible source root and locked NAR hash."""
    if target.source_input is None:
        msg = f"Missing production source input for {target.name}"
        raise RuntimeError(msg)
    expression = _build_flake_attr_expr(
        local_flake_url(),
        "inputs",
        target.source_input,
        "outPath",
        quoted_indices=(1,),
    )
    args = [
        "nix",
        "eval",
        "--impure",
        "--raw",
        "--expr",
        expression,
    ]
    completed = (
        _run(args)
        if cancel_event is None and progress is None
        else _run(
            args,
            cancel_event=cancel_event,
            progress=progress,
        )
    )
    raw_input_root = completed.stdout.strip()
    if not raw_input_root:
        msg = f"Flake input {target.source_input!r} has no evaluator-visible outPath"
        raise RuntimeError(msg)
    input_root = Path(raw_input_root)
    root = (
        input_root
        if target.root_src_relpath == Path()
        else input_root / target.root_src_relpath
    )
    locked = get_flake_input_node(target.source_input).locked
    if locked is None or not locked.nar_hash:
        msg = f"Flake input {target.source_input!r} has no locked source metadata"
        raise RuntimeError(msg)
    return root, locked.nar_hash


def _materialize_source_slices(
    root: Path,
    source_paths: tuple[str, ...],
    cargo_nix: Path,
    *,
    root_source_name: str,
    cancel_event: threading.Event | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, CrateSourceSlice]:
    """Materialize filtered local source paths and return their source metadata."""
    if not source_paths:
        return {}

    source_names: dict[str, str] = {}
    for relative_path in source_paths:
        candidate = Path(relative_path)
        if relative_path == "" or candidate.is_absolute() or ".." in candidate.parts:
            msg = f"Invalid local crate source path: {relative_path!r}"
            raise ValueError(msg)
        source_names[relative_path] = (
            root_source_name if relative_path == "." else candidate.name
        )

    helper_path = REPO_ROOT / "lib/crate2nix-source-slice.nix"
    helper_import = FunctionCall(
        name=Identifier(name="import"),
        argument=NixPath(path=str(helper_path)),
    )
    cargo_import = FunctionCall(
        name=FunctionCall(
            name=Identifier(name="import"),
            argument=NixPath(path=str(cargo_nix)),
        ),
        argument=AttributeSet(
            values=[
                Binding(
                    name="lib",
                    value=Select(
                        expression=Identifier(name="helper"),
                        attribute="sourceFilterLib",
                    ),
                )
            ]
        ),
    )
    materialize = FunctionCall(
        name=Select(
            expression=Identifier(name="helper"),
            attribute="materialize",
        ),
        argument=AttributeSet(
            values=[
                Binding(name="rootSrc", value=NixPath(path=str(root))),
                Binding(
                    name="sourceFilter",
                    value=select_attrs(
                        Identifier(name="cargo"),
                        "internal",
                        "sourceFilter",
                    ),
                ),
                Binding(
                    name="sources",
                    value=FunctionCall(
                        name=select_attrs(Identifier(name="builtins"), "fromJSON"),
                        argument=StringPrimitive(
                            value=json.dumps(
                                {
                                    relative_path: {"name": name}
                                    for relative_path, name in source_names.items()
                                },
                                sort_keys=True,
                            )
                        ),
                    ),
                ),
            ]
        ),
    )
    expression = LetExpression(
        local_variables=[
            Binding(name="helper", value=helper_import),
            Binding(name="cargo", value=cargo_import),
        ],
        value=materialize,
    )
    materialize_args = [
        "nix",
        "eval",
        "--impure",
        "--json",
        "--expr",
        compact_nix_expr(expression.rebuild()),
    ]
    completed = (
        _run(materialize_args)
        if cancel_event is None and progress is None
        else _run(
            materialize_args,
            cancel_event=cancel_event,
            progress=progress,
        )
    )
    materialized = json.loads(completed.stdout)
    if not isinstance(materialized, dict) or set(materialized) != set(source_names):
        msg = "Nix returned invalid crate source materialization metadata"
        raise TypeError(msg)
    store_paths: dict[str, str] = {}
    for relative_path, store_path in materialized.items():
        if not isinstance(relative_path, str) or not isinstance(store_path, str):
            msg = "Nix returned invalid crate source materialization metadata"
            raise TypeError(msg)
        store_paths[relative_path] = store_path

    path_info_args = [
        "nix",
        "path-info",
        "--json",
        "--json-format",
        "1",
        *dict.fromkeys(store_paths.values()),
    ]
    path_info_result = (
        _run(path_info_args)
        if cancel_event is None and progress is None
        else _run(
            path_info_args,
            cancel_event=cancel_event,
            progress=progress,
        )
    )
    path_info = json.loads(path_info_result.stdout)
    if not isinstance(path_info, dict):
        msg = "Nix returned invalid crate source path metadata"
        raise TypeError(msg)

    slices: dict[str, CrateSourceSlice] = {}
    for relative_path, store_path in store_paths.items():
        info = path_info.get(store_path)
        nar_hash = info.get("narHash") if isinstance(info, dict) else None
        if not isinstance(nar_hash, str):
            msg = f"Nix did not report a NAR hash for crate source {relative_path!r}"
            raise TypeError(msg)
        slices[relative_path] = {
            "hash": nar_hash,
            "name": source_names[relative_path],
        }
    return slices


def _render_crate_source_manifest(
    target: Crate2NixTarget,
    source_paths: tuple[str, ...],
    *,
    cargo_nix: Path,
    cancel_event: threading.Event | None = None,
    progress: Callable[[str], None] | None = None,
) -> str:
    """Render source-bound content hashes for the target's local crate slices."""
    if target.source_input is None or target.crate_sources is None:
        msg = f"Missing crate source manifest metadata for {target.name}"
        raise RuntimeError(msg)
    exemptions = set(target.externally_overridden_source_paths)
    missing_exemptions = exemptions.difference(source_paths)
    if missing_exemptions:
        msg = (
            f"Externally overridden crate source paths are absent from {target.name} "
            f"Cargo.nix: {', '.join(sorted(missing_exemptions))}"
        )
        raise RuntimeError(msg)
    materialized_source_paths = tuple(
        path for path in source_paths if path not in exemptions
    )
    if cancel_event is None and progress is None:
        root, nar_hash = _resolve_production_root(target)
        slices = _materialize_source_slices(
            root,
            materialized_source_paths,
            cargo_nix,
            root_source_name=target.root_src_relpath.name or "source",
        )
    else:
        root, nar_hash = _resolve_production_root(
            target,
            cancel_event=cancel_event,
            progress=progress,
        )
        slices = _materialize_source_slices(
            root,
            materialized_source_paths,
            cargo_nix,
            root_source_name=target.root_src_relpath.name or "source",
            cancel_event=cancel_event,
            progress=progress,
        )
    return (
        json.dumps(
            {
                "source": {
                    "cargoNixSha256": hashlib.sha256(
                        cargo_nix.read_bytes()
                    ).hexdigest(),
                    "input": target.source_input,
                    "narHash": nar_hash,
                    "subdir": target.root_src_relpath.as_posix(),
                },
                "slices": slices,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


# Keep these tuples in sync with the system constraints declared in
# packages/registry.nix for each target's `*-crate2nix-src` companion. Local
# artifact refresh must not attempt a companion output on a platform the flake
# does not expose.
TARGETS = {
    "codex": Crate2NixTarget(
        name="codex",
        patched_src_installable="path:.#codex-crate2nix-src",
        cargo_nix=Path("packages/codex/Cargo.nix"),
        crate_hashes=Path("packages/codex/crate-hashes.json"),
        crate_sources=Path("packages/codex/crate-sources.json"),
        normalizer_path=Path("packages/codex/normalize_cargo_nix.py"),
        source_input="codex",
        root_src_relpath=Path("codex-rs"),
        supported_platforms=("aarch64-darwin", "x86_64-linux"),
    ),
    "goose-cli": Crate2NixTarget(
        name="goose-cli",
        patched_src_installable="path:.#goose-cli-crate2nix-src",
        cargo_nix=Path("overlays/goose-cli/Cargo.nix"),
        crate_hashes=Path("overlays/goose-cli/crate-hashes.json"),
        crate_sources=Path("overlays/goose-cli/crate-sources.json"),
        normalizer_path=Path("overlays/goose-cli/normalize_cargo_nix.py"),
        source_input="goose",
        externally_overridden_source_paths=("vendor/v8-goose-src",),
        supported_platforms=("aarch64-darwin", "x86_64-linux"),
    ),
    "gitbutler": Crate2NixTarget(
        name="gitbutler",
        patched_src_installable="path:.#gitbutler-crate2nix-src",
        cargo_nix=Path("packages/gitbutler/Cargo.nix"),
        crate_hashes=Path("packages/gitbutler/crate-hashes.json"),
        crate_sources=Path("packages/gitbutler/crate-sources.json"),
        normalizer_path=Path("packages/gitbutler/normalize_cargo_nix.py"),
        source_input="gitbutler",
        supported_platforms=("aarch64-darwin", "x86_64-linux"),
    ),
    "zed-editor-nightly": Crate2NixTarget(
        name="zed-editor-nightly",
        patched_src_installable="path:.#zed-editor-nightly-crate2nix-src",
        cargo_nix=Path("packages/zed-editor-nightly/Cargo.nix"),
        crate_hashes=Path("packages/zed-editor-nightly/crate-hashes.json"),
        crate_sources=Path("packages/zed-editor-nightly/crate-sources.json"),
        normalizer_path=Path("packages/zed-editor-nightly/normalize_cargo_nix.py"),
        source_input="zed",
        supported_platforms=("aarch64-darwin", "x86_64-linux"),
    ),
}


def _current_platform() -> str:
    return get_current_nix_platform()


def _normalize_json_text(text: str) -> str:
    payload = json.loads(text)
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _normalize_trailing_newline(text: str) -> str:
    return text.rstrip("\n") + "\n"


def _read_generated_hash_text(path: Path) -> str:
    """Return generated crate hashes, or an empty JSON object when none were emitted."""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "{}\n"


def _string_binding(attributes: AttributeSet, name: str) -> str | None:
    """Return one literal string binding from a parsed Nix attribute set."""
    for value in attributes.values:
        if not isinstance(value, Binding) or value.name.strip('"') != name:
            continue
        if isinstance(value.value, StringPrimitive):
            return value.value.value
        return None
    return None


def _checked_fetchgit_sources(cargo_nix: Path) -> set[tuple[str, str, str]]:
    """Read resolved ``(url, revision, hash)`` identities from checked Cargo.nix."""
    source = _STANDALONE_FORMAL_COMMA_RE.sub(
        r"\g<indent>",
        cargo_nix.read_text(encoding="utf-8"),
    )
    parsed = parse(source)
    if parsed.contains_error or parsed.expr is None:
        msg = f"Could not parse checked crate2nix artifact {cargo_nix}"
        raise RuntimeError(msg)

    identities: set[tuple[str, str, str]] = set()
    for expression in _walk_nix_expressions(parsed.expr):
        if not isinstance(expression, FunctionCall):
            continue
        function = expression.name
        while isinstance(function, Parenthesis):
            function = function.value
        function_name = function if isinstance(function, str) else function.rebuild()
        if function_name != "pkgs.fetchgit" or not isinstance(
            expression.argument, AttributeSet
        ):
            continue
        url = _string_binding(expression.argument, "url")
        revision = _string_binding(expression.argument, "rev")
        hash_value = _string_binding(expression.argument, "sha256")
        if url is not None and revision is not None and hash_value is not None:
            identities.add((url, revision, hash_value))
    return identities


def _locked_git_packages(cargo_lock: Path) -> tuple[_LockedGitPackage, ...]:
    """Return resolved Git packages from a candidate Cargo.lock."""
    with cargo_lock.open("rb") as handle:
        payload = tomllib.load(handle)
    raw_packages = payload.get("package", [])
    if not isinstance(raw_packages, list):
        msg = f"Cargo lock package table is invalid: {cargo_lock}"
        raise TypeError(msg)

    packages: list[_LockedGitPackage] = []
    for raw_package in raw_packages:
        if not isinstance(raw_package, dict):
            continue
        name = raw_package.get("name")
        version = raw_package.get("version")
        source = raw_package.get("source")
        if (
            not isinstance(name, str)
            or not isinstance(version, str)
            or not isinstance(source, str)
            or not source.startswith("git+")
            or "#" not in source
        ):
            continue
        locator, revision = source.rsplit("#", 1)
        if not revision:
            continue
        packages.append(
            _LockedGitPackage(
                locator=locator,
                name=name,
                revision=revision,
                version=version,
            )
        )
    return tuple(packages)


def _hash_key_matches_locked_package(
    key: str,
    package: _LockedGitPackage,
) -> bool:
    """Return whether one crate2nix cache key names a locked Git package."""
    if not key.startswith("git+") or "#" not in key:
        return False
    locator, package_fragment = key.rsplit("#", 1)
    if locator != package.locator:
        return False
    if "@" not in package_fragment:
        return package_fragment == package.version
    name, version = package_fragment.rsplit("@", 1)
    return name == package.name and version == package.version


def _filtered_crate_hash_seed(
    target: Crate2NixTarget,
    patched_src: Path,
) -> bytes | None:
    """Build a safe temporary hash seed for the candidate's resolved Git graph."""
    checked_hashes_path = REPO_ROOT / target.crate_hashes
    checked_cargo_path = REPO_ROOT / target.cargo_nix
    candidate_lock_path = (
        patched_src / target.cargo_manifest_relpath.parent / "Cargo.lock"
    )
    if not (
        checked_hashes_path.is_file()
        and checked_cargo_path.is_file()
        and candidate_lock_path.is_file()
    ):
        return None

    raw_hashes = json.loads(checked_hashes_path.read_text(encoding="utf-8"))
    if not isinstance(raw_hashes, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in raw_hashes.items()
    ):
        msg = f"Invalid crate hash cache: {checked_hashes_path}"
        raise RuntimeError(msg)

    locked_packages = _locked_git_packages(candidate_lock_path)
    checked_sources = _checked_fetchgit_sources(checked_cargo_path)
    filtered: dict[str, str] = {}
    for key, hash_value in raw_hashes.items():
        for package in locked_packages:
            if not _hash_key_matches_locked_package(key, package):
                continue
            source_url = package.locator.removeprefix("git+").split("?", 1)[0]
            if (source_url, package.revision, hash_value) in checked_sources:
                filtered[key] = hash_value
            break

    return (json.dumps(filtered, indent=2, sort_keys=True) + "\n").encode()


def load_normalizer(path: Path) -> _Normalizer:
    """Load and validate one registered Cargo.nix normalizer."""
    module_path = (REPO_ROOT / path).resolve()
    try:
        module = load_module_from_path(
            module_path,
            f"_crate2nix_normalizer_{path.stem}",
        )
    except RuntimeError as exc:
        msg = f"Could not load normalizer from {module_path}"
        raise RuntimeError(msg) from exc
    normalize_obj = getattr(module, "normalize", None)
    if not callable(normalize_obj):
        msg = f"Normalizer module {module_path} does not expose normalize()"
        raise TypeError(msg)

    def _normalize(text: str) -> tuple[str, int, bool]:
        result = normalize_obj(text)
        if not isinstance(result, tuple) or len(result) != _NORMALIZER_RESULT_SIZE:
            msg = f"Normalizer module {module_path} returned an invalid result"
            raise TypeError(msg)
        cargo_text, rewrites, added_root_src = result
        if (
            not isinstance(cargo_text, str)
            or type(rewrites) is not int
            or type(added_root_src) is not bool
        ):
            msg = f"Normalizer module {module_path} returned an invalid result"
            raise TypeError(msg)
        return cast("tuple[str, int, bool]", result)

    return _normalize


def _local_flake_installable(installable: str) -> str:
    """Rewrite repo-local ``path:`` installables through Git's clean source view."""
    if installable.startswith("path:.#"):
        return f"{local_flake_url()}#{installable.removeprefix('path:.#')}"

    repo_prefix = f"path:{Path(REPO_ROOT).resolve()}#"
    if installable.startswith(repo_prefix):
        return f"{local_flake_url()}#{installable.removeprefix(repo_prefix)}"

    return installable


def _xdg_cache_home() -> Path:
    raw_cache_home = os.environ.get("XDG_CACHE_HOME")
    if raw_cache_home:
        return Path(raw_cache_home).expanduser()
    return Path.home() / ".cache"


def _crate2nix_cargo_home() -> Path:
    raw_cargo_home = os.environ.get(_CRATE2NIX_CARGO_HOME_ENV)
    if raw_cargo_home:
        return Path(raw_cargo_home).expanduser()
    return _xdg_cache_home() / "nixcfg" / "crate2nix-cargo-home"


def _sanitize_progress_line(text: str) -> str:
    """Strip terminal control data and bound one user-visible progress line."""
    plain = _ANSI_ESCAPE_RE.sub("", text.replace("\r", ""))
    plain = "".join(
        character
        for character in plain
        if character == "\t" or (ord(character) >= ord(" ") and character != "\x7f")
    ).strip()
    if len(plain) <= _CRATE2NIX_PROGRESS_LINE_LIMIT:
        return plain
    suffix = " [...]"
    return plain[: _CRATE2NIX_PROGRESS_LINE_LIMIT - len(suffix)].rstrip() + suffix


def _put_bounded_progress(progress_queue: queue.Queue[str], message: str) -> None:
    """Keep only the newest sanitized worker progress without blocking."""
    sanitized = _sanitize_progress_line(message)
    if not sanitized:
        return
    while True:
        try:
            progress_queue.put_nowait(sanitized)
        except queue.Full:
            with suppress(queue.Empty):
                progress_queue.get_nowait()
        else:
            return


def _process_group_exists(process_group: int) -> bool:
    """Return whether a POSIX process group still has any members."""
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_process_group(
    process: subprocess.Popen[bytes],
    *,
    grace_seconds: float = _CRATE2NIX_TERMINATION_GRACE_SECONDS,
) -> None:
    """Terminate an isolated process group, escalate, and reap its leader."""
    process_group = process.pid
    with suppress(PermissionError, ProcessLookupError):
        os.killpg(process_group, signal.SIGTERM)

    deadline = time.monotonic() + grace_seconds
    while _process_group_exists(process_group) and time.monotonic() < deadline:
        process.poll()
        time.sleep(min(_CRATE2NIX_PROCESS_POLL_SECONDS, grace_seconds))

    if _process_group_exists(process_group):
        with suppress(PermissionError, ProcessLookupError):
            os.killpg(process_group, signal.SIGKILL)

    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            process.kill()
        process.wait()


def _format_command_failure(
    args: list[str],
    *,
    stdout: str,
    stderr: str,
) -> str:
    """Render bounded command context and the most useful retained diagnostic."""
    details = stderr.strip() or stdout.strip() or "command failed"
    return f"{shlex.join(args)}\n{details}"


def _raise_resource_error(message: str) -> None:
    """Raise a distinct error for an ENOSPC diagnostic."""
    detail = f"crate2nix storage exhausted (ENOSPC)\n{message}"
    raise Crate2NixResourceError(detail)


def _raise_for_command_failure(
    args: list[str],
    *,
    stdout: str,
    stderr: str,
    enospc: bool = False,
) -> None:
    """Classify one non-zero subprocess result and raise its bounded failure."""
    message = _format_command_failure(args, stdout=stdout, stderr=stderr)
    if enospc or any(marker in message.casefold() for marker in _ENOSPC_MARKERS):
        _raise_resource_error(message)
    raise RuntimeError(message)


def _report_progress_chunk(
    text: str,
    progress: Callable[[str], None] | None,
) -> bool:
    """Report sanitized lines from one bounded process-output chunk."""
    if progress is None:
        return False
    reported = False
    for raw_line in text.splitlines() or [text]:
        sanitized = _sanitize_progress_line(raw_line)
        if sanitized:
            progress(sanitized)
            reported = True
    return reported


def _raise_cancelled_command(
    args: list[str],
    *,
    stdout: str,
    stderr: str,
) -> None:
    """Raise the terminal cancellation diagnostic after process cleanup."""
    context = _format_command_failure(args, stdout=stdout, stderr=stderr)
    msg = f"{context}\ncommand cancelled by caller"
    raise Crate2NixCommandCancelledError(msg)


def _raise_timed_out_command(
    args: list[str],
    *,
    stdout: str,
    stderr: str,
    timeout: float,
) -> None:
    """Raise the terminal timeout diagnostic after process cleanup."""
    context = _format_command_failure(args, stdout=stdout, stderr=stderr)
    msg = f"{context}\ncommand timed out after {timeout}s"
    raise Crate2NixCommandTimeoutError(msg)


def _prepare_process_collection(
    process: subprocess.Popen[bytes],
) -> tuple[
    selectors.BaseSelector,
    dict[str, _BoundedCapture],
    IO[bytes],
    IO[bytes],
]:
    """Create bounded captures and register both managed output pipes."""
    if process.stdout is None or process.stderr is None:
        _terminate_process_group(process)
        msg = "Managed crate2nix subprocess did not expose output pipes"
        raise RuntimeError(msg)
    captures = {
        "stdout": _BoundedCapture(),
        "stderr": _BoundedCapture(),
    }
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    return selector, captures, process.stdout, process.stderr


def _collect_managed_process(
    process: subprocess.Popen[bytes],
    args: list[str],
    *,
    timeout: float,
    cancel_event: threading.Event | None = None,
    progress: Callable[[str], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Collect one isolated subprocess with bounded output and live progress."""
    selector, captures, stdout_stream, stderr_stream = _prepare_process_collection(
        process
    )
    started_at = time.monotonic()
    deadline = started_at + timeout
    last_progress_at = started_at
    interrupted = False

    try:
        while selector.get_map() or process.poll() is None:
            now = time.monotonic()
            if cancel_event is not None and cancel_event.is_set():
                interrupted = True
                _terminate_process_group(process)
                _raise_cancelled_command(
                    args,
                    stdout=captures["stdout"].render(),
                    stderr=captures["stderr"].render(),
                )
            if now >= deadline:
                interrupted = True
                _terminate_process_group(process)
                _raise_timed_out_command(
                    args,
                    stdout=captures["stdout"].render(),
                    stderr=captures["stderr"].render(),
                    timeout=timeout,
                )

            wait_seconds = min(
                _CRATE2NIX_PROCESS_POLL_SECONDS,
                max(0.0, deadline - now),
            )
            for key, _mask in selector.select(wait_seconds):
                try:
                    chunk = os.read(key.fd, _CRATE2NIX_IO_CHUNK_BYTES)
                except BlockingIOError:
                    continue
                if not chunk:
                    stream = stdout_stream if key.data == "stdout" else stderr_stream
                    selector.unregister(stream)
                    stream.close()
                    continue
                text = chunk.decode(errors="replace")
                captures[key.data].append(text)
                if _report_progress_chunk(text, progress):
                    last_progress_at = time.monotonic()

            now = time.monotonic()
            if (
                progress is not None
                and now - last_progress_at >= _CRATE2NIX_PROGRESS_INTERVAL_SECONDS
            ):
                elapsed = int(now - started_at)
                progress(f"crate2nix command still running ({elapsed}s elapsed)")
                last_progress_at = now

        returncode = process.wait()
    except BaseException:
        if not interrupted:
            _terminate_process_group(process)
        raise
    finally:
        selector.close()
        for stream in (stdout_stream, stderr_stream):
            if not stream.closed:
                stream.close()

    completed = subprocess.CompletedProcess(
        args,
        returncode,
        stdout=captures["stdout"].render(),
        stderr=captures["stderr"].render(),
    )
    if completed.returncode != 0:
        _raise_for_command_failure(
            args,
            stdout=completed.stdout,
            stderr=completed.stderr,
            enospc=any(capture.enospc for capture in captures.values()),
        )
    return completed


def _run(
    args: list[str],
    *,
    cwd: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
    timeout: float = _CRATE2NIX_COMMAND_TIMEOUT_SECONDS,
    cancel_event: threading.Event | None = None,
    progress: Callable[[str], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command with bounded output, live progress, and process-tree cleanup."""
    if cancel_event is not None and cancel_event.is_set():
        msg = f"{shlex.join(args)}\ncommand cancelled before start"
        raise Crate2NixCommandCancelledError(msg)
    merged_env = (os.environ | env) if env is not None else None
    try:
        process = subprocess.Popen(  # noqa: S603
            args,
            cwd=cwd,
            env=merged_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            bufsize=0,
        )
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            msg = f"Could not start {shlex.join(args)}"
            _raise_resource_error(msg)
        raise
    return _collect_managed_process(
        process,
        args,
        timeout=timeout,
        cancel_event=cancel_event,
        progress=progress,
    )


def _is_retryable_crate2nix_generate_failure(message: str) -> bool:
    """Return whether a crate2nix generation failure is likely transient."""
    output = message.casefold()
    return any(
        marker.casefold() in output
        for marker in _RETRYABLE_CRATE2NIX_NETWORK_CONTEXT_MARKERS
    ) and any(
        marker.casefold() in output for marker in _RETRYABLE_CRATE2NIX_TRANSIENT_MARKERS
    )


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    """Stop synchronous refresh work promptly when its async caller is gone."""
    if cancel_event is not None and cancel_event.is_set():
        msg = "crate2nix generation cancelled by caller"
        raise Crate2NixCommandCancelledError(msg)


def _acquire_generate_lock(
    cancel_event: threading.Event | None,
    progress: Callable[[str], None] | None,
) -> None:
    """Acquire the shared Cargo cache lock without making cancellation unbounded."""
    if cancel_event is None and progress is None:
        _CRATE2NIX_GENERATE_LOCK.acquire()
        return
    if _CRATE2NIX_GENERATE_LOCK.acquire(blocking=False):
        return
    if progress is not None:
        progress("Waiting for the shared crate2nix Cargo cache")
    while not _CRATE2NIX_GENERATE_LOCK.acquire(timeout=_CRATE2NIX_PROCESS_POLL_SECONDS):
        _raise_if_cancelled(cancel_event)
    if cancel_event is not None and cancel_event.is_set():
        _CRATE2NIX_GENERATE_LOCK.release()
        _raise_if_cancelled(cancel_event)


def _restore_generated_outputs(
    generated_outputs: tuple[Path, ...],
    seeded_outputs: dict[Path, bytes],
) -> None:
    """Remove partial artifacts and restore immutable retry seeds."""
    try:
        for path in generated_outputs:
            path.unlink(missing_ok=True)
        for path, content in seeded_outputs.items():
            path.write_bytes(content)
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            _raise_resource_error(
                "Could not prepare temporary crate2nix artifacts: "
                "No space left on device"
            )
        raise


def _remaining_generate_budget(
    deadline: float,
    *,
    total_timeout: float,
    args: list[str],
) -> float:
    """Return the shared retry budget or raise its terminal timeout."""
    remaining = deadline - time.monotonic()
    if remaining > 0:
        return remaining
    msg = (
        f"{shlex.join(args)}\ncrate2nix generation exhausted its "
        f"{total_timeout}s total timeout budget"
    )
    raise Crate2NixCommandTimeoutError(msg)


def _run_crate2nix_generate(
    args: list[str],
    *,
    env: dict[str, str],
    generated_outputs: tuple[Path, ...],
    seeded_outputs: dict[Path, bytes] | None = None,
    attempts: int = _CRATE2NIX_GENERATE_ATTEMPTS,
    cancel_event: threading.Event | None = None,
    progress: Callable[[str], None] | None = None,
    total_timeout: float = _CRATE2NIX_COMMAND_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run crate2nix generation with bounded retries for transient prefetch flakes."""
    _acquire_generate_lock(cancel_event, progress)
    try:
        deadline = time.monotonic() + total_timeout
        attempt = 1
        while True:
            _raise_if_cancelled(cancel_event)
            remaining = _remaining_generate_budget(
                deadline,
                total_timeout=total_timeout,
                args=args,
            )
            _restore_generated_outputs(generated_outputs, seeded_outputs or {})
            try:
                if cancel_event is None and progress is None:
                    return _run(args, env=env, timeout=remaining)
                return _run(
                    args,
                    env=env,
                    timeout=remaining,
                    cancel_event=cancel_event,
                    progress=progress,
                )
            except (
                Crate2NixCommandCancelledError,
                Crate2NixCommandTimeoutError,
                Crate2NixResourceError,
            ):
                raise
            except RuntimeError as exc:
                if attempt >= attempts or not _is_retryable_crate2nix_generate_failure(
                    str(exc)
                ):
                    raise
                retry_message = (
                    "Retrying crate2nix generation after transient network failure "
                    f"({attempt}/{attempts})..."
                )
                sys.stderr.write(f"{retry_message}\n")
                if progress is not None:
                    progress(retry_message)
                delay = _CRATE2NIX_GENERATE_RETRY_DELAY_SECONDS * attempt
                sleep_seconds = min(
                    delay,
                    _remaining_generate_budget(
                        deadline,
                        total_timeout=total_timeout,
                        args=args,
                    ),
                )
                if cancel_event is None:
                    time.sleep(sleep_seconds)
                elif cancel_event.wait(sleep_seconds):
                    _raise_if_cancelled(cancel_event)
                attempt += 1
    finally:
        _CRATE2NIX_GENERATE_LOCK.release()


def _build_patched_src(
    target: Crate2NixTarget,
    *,
    source_overrides: dict[str, SourceEntry] | None = None,
    cancel_event: threading.Event | None = None,
    progress: Callable[[str], None] | None = None,
) -> Path:
    installable = (
        _build_package_path_attr_expr(
            f"{target.name}-crate2nix-src",
            "",
            source_overrides=source_overrides,
        )
        if source_overrides
        else _local_flake_installable(target.patched_src_installable)
    )
    args = [
        "nix",
        "build",
        "--impure",
        "--no-link",
        "--print-out-paths",
        *(["--expr"] if source_overrides else []),
        installable,
    ]
    completed = (
        _run(args)
        if cancel_event is None and progress is None
        else _run(
            args,
            cancel_event=cancel_event,
            progress=progress,
        )
    )
    out_paths = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(out_paths) != 1:
        msg = f"Expected one patchedSrc output path for {target.name}, got {len(out_paths)}"
        raise RuntimeError(msg)
    return Path(out_paths[0])


def _refresh_target_impl(
    target: Crate2NixTarget,
    *,
    source_overrides: dict[str, SourceEntry] | None = None,
    cancel_event: threading.Event | None = None,
    progress: Callable[[str], None] | None = None,
) -> RefreshResult:
    if cancel_event is None and progress is None:
        patched_src = (
            _build_patched_src(target, source_overrides=source_overrides)
            if source_overrides is not None
            else _build_patched_src(target)
        )
    else:
        patched_src = _build_patched_src(
            target,
            source_overrides=source_overrides,
            cancel_event=cancel_event,
            progress=progress,
        )
    normalize = load_normalizer(target.normalizer_path)

    with tempfile.TemporaryDirectory(prefix=f"crate2nix-{target.name}-") as tmp_dir:
        tmp_root = Path(tmp_dir)
        cargo_home = _crate2nix_cargo_home()
        cargo_home.mkdir(parents=True, exist_ok=True)
        generated_cargo = tmp_root / "Cargo.nix"
        generated_hashes = tmp_root / "crate-hashes.json"
        hash_seed = _filtered_crate_hash_seed(target, patched_src)

        generate_args = [
            "nix",
            "run",
            "--inputs-from",
            ".",
            "nixpkgs#crate2nix",
            "--",
            "generate",
            "-f",
            str(patched_src / target.cargo_manifest_relpath),
            "-o",
            str(generated_cargo),
            "-h",
            str(generated_hashes),
            "--default-features",
        ]
        generate_env = {
            "CARGO_HOME": str(cargo_home),
            "CARGO_NET_GIT_FETCH_WITH_CLI": "true",
        }
        seeded_outputs = {generated_hashes: hash_seed} if hash_seed is not None else {}
        if cancel_event is None and progress is None:
            _run_crate2nix_generate(
                generate_args,
                env=generate_env,
                generated_outputs=(generated_cargo, generated_hashes),
                seeded_outputs=seeded_outputs,
            )
        else:
            _run_crate2nix_generate(
                generate_args,
                env=generate_env,
                generated_outputs=(generated_cargo, generated_hashes),
                seeded_outputs=seeded_outputs,
                cancel_event=cancel_event,
                progress=progress,
            )

        cargo_text, _rewrites, _added_root_src = normalize(
            generated_cargo.read_text(encoding="utf-8")
        )
        cargo_text = _stabilize_generated_root_src_paths(
            cargo_text,
            patched_src=patched_src,
            generated_cargo=generated_cargo,
        )
        cargo_text, source_paths = _apply_crate_source_contract(cargo_text)
        cargo_text = _stabilize_generated_command_comment(target, cargo_text)
        cargo_text = _normalize_trailing_newline(cargo_text)
        crate_sources = None
        if target.crate_sources is not None:
            generated_cargo.write_text(cargo_text, encoding="utf-8")
            crate_sources = (
                _render_crate_source_manifest(
                    target,
                    source_paths,
                    cargo_nix=generated_cargo,
                )
                if cancel_event is None and progress is None
                else _render_crate_source_manifest(
                    target,
                    source_paths,
                    cargo_nix=generated_cargo,
                    cancel_event=cancel_event,
                    progress=progress,
                )
            )
        elif source_paths:
            msg = f"Missing crate source artifact path for {target.name}"
            raise RuntimeError(msg)
        hash_text = _normalize_json_text(_read_generated_hash_text(generated_hashes))
        hash_text = _normalize_trailing_newline(hash_text)
        return RefreshResult(
            cargo_nix=cargo_text,
            crate_hashes=hash_text,
            crate_sources=crate_sources,
        )


def _refresh_target(
    target: Crate2NixTarget,
    *,
    source_overrides: dict[str, SourceEntry] | None = None,
    cancel_event: threading.Event | None = None,
    progress: Callable[[str], None] | None = None,
) -> RefreshResult:
    """Refresh one target and classify local storage exhaustion consistently."""
    try:
        return _refresh_target_impl(
            target,
            source_overrides=source_overrides,
            cancel_event=cancel_event,
            progress=progress,
        )
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            _raise_resource_error(
                f"Could not prepare temporary artifacts for {target.name}: "
                "No space left on device"
            )
        raise


def crate2nix_artifact_updates(
    name: str,
    *,
    source_overrides: dict[str, SourceEntry] | None = None,
    cancel_event: threading.Event | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[GeneratedArtifact, ...]:
    """Return changed checked-in crate2nix artifacts for one target."""
    target = TARGETS.get(name)
    if target is None:
        return ()
    if _current_platform() not in target.supported_platforms:
        return ()

    if cancel_event is None and progress is None:
        refreshed = (
            _refresh_target(target, source_overrides=source_overrides)
            if source_overrides is not None
            else _refresh_target(target)
        )
    else:
        refreshed = _refresh_target(
            target,
            source_overrides=source_overrides,
            cancel_event=cancel_event,
            progress=progress,
        )
    generated = [
        GeneratedArtifact.text(target.cargo_nix, refreshed.cargo_nix),
        GeneratedArtifact.text(target.crate_hashes, refreshed.crate_hashes),
    ]
    if target.crate_sources is not None and refreshed.crate_sources is not None:
        generated.append(
            GeneratedArtifact.text(target.crate_sources, refreshed.crate_sources)
        )
    elif target.crate_sources is not None or refreshed.crate_sources is not None:
        msg = f"Incomplete crate source artifact metadata for {target.name}"
        raise RuntimeError(msg)
    return tuple(artifact for artifact in generated if artifact.has_changed())


async def _cancel_artifact_worker(
    future: asyncio.Future[tuple[GeneratedArtifact, ...]],
    cancel_event: threading.Event,
) -> None:
    """Signal and retrieve a background artifact worker during stream teardown."""
    cancel_event.set()
    try:
        await asyncio.shield(future)
    except Crate2NixCommandCancelledError:
        pass
    except OSError, RuntimeError, TypeError, ValueError:
        pass


async def stream_crate2nix_artifact_updates(
    name: str,
    *,
    operation: str = "materialize_artifacts",
    source_overrides: dict[str, SourceEntry] | None = None,
) -> EventStream:
    """Emit normal updater events for checked-in crate2nix artifacts."""
    target = TARGETS.get(name)
    if target is None:
        yield UpdateEvent.status(
            name,
            "No crate2nix target registered; skipping artifact refresh",
            operation=operation,
            status=StatusInfo(kind=StatusKind.SKIPPED, value="unknown_target"),
        )
        return
    current_platform = _current_platform()
    if current_platform not in target.supported_platforms:
        yield UpdateEvent.status(
            name,
            "crate2nix target is unsupported on this platform; skipping artifact refresh",
            operation=operation,
            status=StatusInfo(
                kind=StatusKind.UNSUPPORTED_PLATFORM,
                value=current_platform,
            ),
        )
        return

    yield UpdateEvent.status(
        name,
        "Refreshing crate2nix artifacts...",
        operation=operation,
        status=StatusInfo(
            kind=StatusKind.COMPUTING_HASH,
            value="crate2nix artifacts",
        ),
    )

    loop = asyncio.get_running_loop()
    cancel_event = threading.Event()
    progress_queue: queue.Queue[str] = queue.Queue(
        maxsize=_CRATE2NIX_PROGRESS_QUEUE_SIZE
    )

    def _enqueue_progress(message: str) -> None:
        _put_bounded_progress(progress_queue, message)

    worker = (
        partial(
            crate2nix_artifact_updates,
            name,
            source_overrides=source_overrides,
            cancel_event=cancel_event,
            progress=_enqueue_progress,
        )
        if source_overrides is not None
        else partial(
            crate2nix_artifact_updates,
            name,
            cancel_event=cancel_event,
            progress=_enqueue_progress,
        )
    )
    future = loop.run_in_executor(None, worker)
    try:
        while not future.done():
            try:
                message = progress_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(_CRATE2NIX_PROCESS_POLL_SECONDS)
                continue
            yield UpdateEvent(
                source=name,
                kind=UpdateEventKind.LINE,
                message=message,
                stream="crate2nix",
            )
        artifacts = await asyncio.shield(future)
        while True:
            try:
                message = progress_queue.get_nowait()
            except queue.Empty:
                break
            yield UpdateEvent(
                source=name,
                kind=UpdateEventKind.LINE,
                message=message,
                stream="crate2nix",
            )
    except asyncio.CancelledError:
        await _cancel_artifact_worker(future, cancel_event)
        raise
    finally:
        if not future.done():
            await _cancel_artifact_worker(future, cancel_event)
    if artifacts:
        yield UpdateEvent.artifact(name, list(artifacts))
        yield UpdateEvent.status(
            name,
            "Prepared crate2nix artifacts",
            operation=operation,
            status=StatusInfo(kind=StatusKind.UPDATED, value="crate2nix artifacts"),
        )
        return

    yield UpdateEvent.status(
        name,
        "crate2nix artifacts up to date",
        operation=operation,
        status=StatusInfo(
            kind=StatusKind.UP_TO_DATE,
            scope="artifacts",
            value="crate2nix artifacts",
        ),
    )


def _target_has_changes(target: Crate2NixTarget, refreshed: RefreshResult) -> bool:
    current_cargo = (REPO_ROOT / target.cargo_nix).read_text(encoding="utf-8")
    current_hashes = _normalize_json_text(
        (REPO_ROOT / target.crate_hashes).read_text(encoding="utf-8")
    )
    crate_sources_changed = False
    if target.crate_sources is not None and refreshed.crate_sources is not None:
        source_path = REPO_ROOT / target.crate_sources
        crate_sources_changed = (
            not source_path.exists()
            or source_path.read_text(encoding="utf-8") != refreshed.crate_sources
        )
    elif target.crate_sources is not None or refreshed.crate_sources is not None:
        msg = f"Incomplete crate source artifact metadata for {target.name}"
        raise RuntimeError(msg)
    return (
        current_cargo != refreshed.cargo_nix
        or current_hashes != refreshed.crate_hashes
        or crate_sources_changed
    )


def _target_artifact_payloads(
    target: Crate2NixTarget,
    refreshed: RefreshResult,
) -> tuple[tuple[Path, str], ...]:
    """Validate and return the complete checked-in artifact set for one target."""
    if (target.crate_sources is None) != (refreshed.crate_sources is None):
        msg = f"Incomplete crate source artifact metadata for {target.name}"
        raise RuntimeError(msg)

    payloads = [
        (target.cargo_nix, refreshed.cargo_nix),
        (target.crate_hashes, refreshed.crate_hashes),
    ]
    if target.crate_sources is not None and refreshed.crate_sources is not None:
        payloads.append(
            (target.crate_sources, refreshed.crate_sources),
        )
    return tuple(payloads)


def _write_target(target: Crate2NixTarget, refreshed: RefreshResult) -> None:
    """Atomically replace each validated artifact from a same-directory temp file."""
    for relative_path, content in _target_artifact_payloads(target, refreshed):
        atomic_write_text(REPO_ROOT / relative_path, content)


def _resolve_targets(
    requested: tuple[str, ...],
) -> tuple[list[Crate2NixTarget], list[str]]:
    platform_name = _current_platform()
    if requested:
        missing = sorted(name for name in requested if name not in TARGETS)
        if missing:
            msg = "Unknown crate2nix target(s): " + ", ".join(missing)
            raise RuntimeError(msg)
        selected = [TARGETS[name] for name in requested]
    else:
        selected = list(TARGETS.values())

    runnable: list[Crate2NixTarget] = []
    skipped: list[str] = []
    for target in selected:
        if platform_name in target.supported_platforms:
            runnable.append(target)
        else:
            skipped.append(target.name)
    return runnable, skipped


def _run_targets(
    *,
    packages: tuple[str, ...],
    write: bool,
) -> tuple[int, tuple[str, ...]]:
    """Check or prepare target artifacts in the active repository root."""
    try:
        runnable, skipped = _resolve_targets(packages)
    except RuntimeError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1, ()

    if packages and skipped:
        sys.stderr.write(
            "Requested crate2nix targets are unsupported on this platform: "
            + ", ".join(skipped)
            + "\n"
        )
        return 1, ()

    if skipped:
        sys.stderr.write(
            "Skipping unsupported crate2nix targets on this platform: "
            + ", ".join(skipped)
            + "\n"
        )

    if not runnable:
        sys.stderr.write("No crate2nix targets are runnable on this platform.\n")
        return 0, ()

    failures = False
    changed_targets: list[str] = []

    for target in runnable:

        def _progress(message: str, *, target_name: str = target.name) -> None:
            sys.stderr.write(f"{target_name}: {_sanitize_progress_line(message)}\n")

        sys.stderr.write(f"Refreshing crate2nix artifacts for {target.name}...\n")
        try:
            refreshed = _refresh_target(
                target,
                progress=_progress,
            )
            if _target_has_changes(target, refreshed):
                changed_targets.append(target.name)
                if write:
                    _write_target(target, refreshed)
                else:
                    sys.stderr.write(f"STALE {target.name}\n")
                    failures = True
            else:
                sys.stderr.write(f"OK {target.name}\n")
        except (RuntimeError, ValueError, TypeError) as exc:
            sys.stderr.write(f"FAIL {target.name}: {exc}\n")
            failures = True

    if changed_targets and not write:
        sys.stderr.write(
            "Detected crate2nix drift for: " + ", ".join(changed_targets) + "\n"
        )
    elif not failures and not changed_targets:
        sys.stderr.write("All checked-in crate2nix artifacts are up to date.\n")

    return (1 if failures else 0), tuple(changed_targets)


def _write_targets_transactionally(packages: tuple[str, ...]) -> int:
    """Generate in an isolated tree and atomically promote the complete result."""
    from lib.update.persistence import (  # noqa: PLC0415 -- avoid import cycle
        IsolatedUpdateWorkspace,
    )

    global REPO_ROOT  # noqa: PLW0603 -- isolate existing root-relative helpers

    live_root = REPO_ROOT
    selected_names = packages or tuple(TARGETS)
    allowed_paths = tuple(
        dict.fromkeys(
            path
            for name in selected_names
            if (target := TARGETS.get(name)) is not None
            for path in target.artifact_paths
        )
    )
    try:
        with IsolatedUpdateWorkspace(live_root) as workspace:
            REPO_ROOT = workspace.root
            try:
                result, changed_targets = _run_targets(
                    packages=packages,
                    write=True,
                )
            finally:
                REPO_ROOT = live_root
            if result != 0:
                return result
            workspace.promote(allowed_paths)
    except RuntimeError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1

    if changed_targets:
        for name in changed_targets:
            sys.stderr.write(f"UPDATED {name}\n")
        sys.stderr.write(
            "Wrote crate2nix drift for: " + ", ".join(changed_targets) + "\n"
        )
    return 0


def run(*, packages: tuple[str, ...] = (), write: bool = False) -> int:
    """Check or refresh checked-in crate2nix artifacts."""
    if write:
        return _write_targets_transactionally(packages)
    result, _changed_targets = _run_targets(packages=packages, write=False)
    return result


__all__ = [
    "REPO_ROOT",
    "TARGETS",
    "Crate2NixTarget",
    "RefreshResult",
    "_current_platform",
    "_is_retryable_crate2nix_generate_failure",
    "_normalize_json_text",
    "_normalize_trailing_newline",
    "_refresh_target",
    "_resolve_targets",
    "_run_crate2nix_generate",
    "_stabilize_generated_command_comment",
    "_stabilize_generated_root_src_paths",
    "_target_has_changes",
    "_write_target",
    "crate2nix_artifact_updates",
    "load_normalizer",
    "run",
    "stream_crate2nix_artifact_updates",
]
