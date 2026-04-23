"""Shared crate2nix regeneration logic for update workflows and CI helpers."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast

from lib.import_utils import load_module_from_path
from lib.update.artifacts import GeneratedArtifact
from lib.update.events import EventStream, UpdateEvent
from lib.update.paths import REPO_ROOT

_NORMALIZER_RESULT_SIZE = 3


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


@dataclass(frozen=True)
class RefreshResult:
    """Materialized crate2nix outputs for one package."""

    cargo_nix: str
    crate_hashes: str


_CLEAN_SOURCE_WITH_SRC_RE = re.compile(
    r"src = lib\.cleanSourceWith \{ filter = sourceFilter;  src = (?P<src>[^;]+); \};"
)


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
    relative_root_src = os.path.relpath(patched_src, generated_cargo.parent).replace(
        os.sep,
        "/",
    )
    prefixes = tuple(dict.fromkeys((patched_src.as_posix(), relative_root_src)))

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


TARGETS = {
    "codex": Crate2NixTarget(
        name="codex",
        patched_src_installable="path:.#codex-crate2nix-src",
        cargo_nix=Path("packages/codex/Cargo.nix"),
        crate_hashes=Path("packages/codex/crate-hashes.json"),
        normalizer_path=Path("packages/codex/normalize_cargo_nix.py"),
        supported_platforms=("linux", "darwin"),
    ),
    "goose-cli": Crate2NixTarget(
        name="goose-cli",
        patched_src_installable="path:.#goose-cli-crate2nix-src",
        cargo_nix=Path("overlays/goose-cli/Cargo.nix"),
        crate_hashes=Path("overlays/goose-cli/crate-hashes.json"),
        normalizer_path=Path("overlays/goose-cli/normalize_cargo_nix.py"),
        supported_platforms=("linux", "darwin"),
    ),
    "zed-editor-nightly": Crate2NixTarget(
        name="zed-editor-nightly",
        patched_src_installable="path:.#zed-editor-nightly-crate2nix-src",
        cargo_nix=Path("packages/zed-editor-nightly/Cargo.nix"),
        crate_hashes=Path("packages/zed-editor-nightly/crate-hashes.json"),
        normalizer_path=Path("packages/zed-editor-nightly/normalize_cargo_nix.py"),
        supported_platforms=("linux", "darwin"),
    ),
    "opencode-desktop": Crate2NixTarget(
        name="opencode-desktop",
        patched_src_installable="path:.#opencode-desktop-crate2nix-src",
        cargo_nix=Path("packages/opencode-desktop/Cargo.nix"),
        crate_hashes=Path("packages/opencode-desktop/crate-hashes.json"),
        normalizer_path=Path("packages/opencode-desktop/normalize_cargo_nix.py"),
        supported_platforms=("darwin",),
        cargo_manifest_relpath=Path("packages/desktop/src-tauri/Cargo.toml"),
    ),
}


def _current_platform() -> str:
    system = platform.system()
    if system == "Darwin":
        return "darwin"
    if system == "Linux":
        return "linux"
    return system.lower()


def _normalize_json_text(text: str) -> str:
    payload = json.loads(text)
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _normalize_trailing_newline(text: str) -> str:
    return text.rstrip("\n") + "\n"


def _load_normalizer(path: Path) -> _Normalizer:
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
        return cast("tuple[str, int, bool]", result)

    return _normalize


def _run(
    args: list[str],
    *,
    cwd: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(  # noqa: S603
        args,
        cwd=cwd,
        env=(os.environ | env) if env is not None else None,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        details = (
            completed.stderr.strip() or completed.stdout.strip() or "command failed"
        )
        msg = f"{' '.join(args)}\n{details}"
        raise RuntimeError(msg)
    return completed


def _build_patched_src(target: Crate2NixTarget) -> Path:
    completed = _run([
        "nix",
        "build",
        "--impure",
        "--no-link",
        "--print-out-paths",
        target.patched_src_installable,
    ])
    out_paths = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not out_paths:
        msg = f"No patchedSrc output path returned for {target.name}"
        raise RuntimeError(msg)
    return Path(out_paths[-1])


def _refresh_target(target: Crate2NixTarget) -> RefreshResult:
    patched_src = _build_patched_src(target)
    normalize = _load_normalizer(target.normalizer_path)

    with tempfile.TemporaryDirectory(prefix=f"crate2nix-{target.name}-") as tmp_dir:
        tmp_root = Path(tmp_dir)
        generated_cargo = tmp_root / "Cargo.nix"
        generated_hashes = tmp_root / "crate-hashes.json"

        _run(
            [
                "nix",
                "run",
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
            ],
            env={
                "CARGO_NET_GIT_FETCH_WITH_CLI": "true",
            },
        )

        cargo_text, _rewrites, _added_root_src = normalize(
            generated_cargo.read_text(encoding="utf-8")
        )
        cargo_text = _stabilize_generated_root_src_paths(
            cargo_text,
            patched_src=patched_src,
            generated_cargo=generated_cargo,
        )
        cargo_text = _stabilize_generated_command_comment(target, cargo_text)
        cargo_text = _normalize_trailing_newline(cargo_text)
        hash_text = _normalize_json_text(generated_hashes.read_text(encoding="utf-8"))
        hash_text = _normalize_trailing_newline(hash_text)
        return RefreshResult(cargo_nix=cargo_text, crate_hashes=hash_text)


def crate2nix_artifact_updates(name: str) -> tuple[GeneratedArtifact, ...]:
    """Return changed checked-in crate2nix artifacts for one target."""
    target = TARGETS.get(name)
    if target is None:
        return ()
    if _current_platform() not in target.supported_platforms:
        return ()

    refreshed = _refresh_target(target)
    return tuple(
        artifact
        for artifact in (
            GeneratedArtifact.text(target.cargo_nix, refreshed.cargo_nix),
            GeneratedArtifact.text(target.crate_hashes, refreshed.crate_hashes),
        )
        if artifact.has_changed()
    )


async def stream_crate2nix_artifact_updates(
    name: str,
    *,
    operation: str = "materialize_artifacts",
) -> EventStream:
    """Emit normal updater events for checked-in crate2nix artifacts."""
    target = TARGETS.get(name)
    if target is None:
        return
    if _current_platform() not in target.supported_platforms:
        return

    yield UpdateEvent.status(
        name,
        "Refreshing crate2nix artifacts...",
        operation=operation,
        status="computing_hash",
        detail="crate2nix artifacts",
    )

    artifacts = await asyncio.to_thread(crate2nix_artifact_updates, name)
    if artifacts:
        yield UpdateEvent.artifact(name, list(artifacts))
        yield UpdateEvent.status(
            name,
            "Prepared crate2nix artifacts",
            operation=operation,
            status="updated",
            detail="crate2nix artifacts",
        )
        return

    yield UpdateEvent.status(
        name,
        "crate2nix artifacts up to date",
        operation=operation,
        status="up_to_date",
        detail={"scope": "artifacts", "value": "crate2nix artifacts"},
    )


def _target_has_changes(target: Crate2NixTarget, refreshed: RefreshResult) -> bool:
    current_cargo = (REPO_ROOT / target.cargo_nix).read_text(encoding="utf-8")
    current_hashes = _normalize_json_text(
        (REPO_ROOT / target.crate_hashes).read_text(encoding="utf-8")
    )
    return (
        current_cargo != refreshed.cargo_nix or current_hashes != refreshed.crate_hashes
    )


def _write_target(target: Crate2NixTarget, refreshed: RefreshResult) -> None:
    (REPO_ROOT / target.cargo_nix).write_text(refreshed.cargo_nix, encoding="utf-8")
    (REPO_ROOT / target.crate_hashes).write_text(
        refreshed.crate_hashes,
        encoding="utf-8",
    )


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


def run(*, packages: tuple[str, ...] = (), write: bool = False) -> int:
    """Check or refresh checked-in crate2nix artifacts."""
    try:
        runnable, skipped = _resolve_targets(packages)
    except RuntimeError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1

    if packages and skipped:
        sys.stderr.write(
            "Requested crate2nix targets are unsupported on this platform: "
            + ", ".join(skipped)
            + "\n"
        )
        return 1

    if skipped:
        sys.stderr.write(
            "Skipping unsupported crate2nix targets on this platform: "
            + ", ".join(skipped)
            + "\n"
        )

    if not runnable:
        sys.stderr.write("No crate2nix targets are runnable on this platform.\n")
        return 0

    failures = False
    changed_targets: list[str] = []

    for target in runnable:
        sys.stderr.write(f"Refreshing crate2nix artifacts for {target.name}...\n")
        try:
            refreshed = _refresh_target(target)
        except RuntimeError as exc:
            sys.stderr.write(f"FAIL {target.name}: {exc}\n")
            failures = True
            continue

        if _target_has_changes(target, refreshed):
            changed_targets.append(target.name)
            if write:
                _write_target(target, refreshed)
                sys.stderr.write(f"UPDATED {target.name}\n")
            else:
                sys.stderr.write(f"STALE {target.name}\n")
                failures = True
        else:
            sys.stderr.write(f"OK {target.name}\n")

    if changed_targets:
        sys.stderr.write(
            ("Wrote" if write else "Detected")
            + " crate2nix drift for: "
            + ", ".join(changed_targets)
            + "\n"
        )
    elif not failures:
        sys.stderr.write("All checked-in crate2nix artifacts are up to date.\n")

    return 1 if failures else 0


__all__ = [
    "REPO_ROOT",
    "TARGETS",
    "Crate2NixTarget",
    "RefreshResult",
    "_current_platform",
    "_normalize_json_text",
    "_normalize_trailing_newline",
    "_refresh_target",
    "_resolve_targets",
    "_stabilize_generated_command_comment",
    "_stabilize_generated_root_src_paths",
    "_target_has_changes",
    "_write_target",
    "crate2nix_artifact_updates",
    "run",
    "stream_crate2nix_artifact_updates",
]
