"""Runtime source identity for update runs and checkout re-exec.

The installed ``nixcfg`` wrapper packages a subset of the checkout declared by
``[tool.nixcfg.runtimeSource]`` in ``pyproject.toml`` (mirrored by
``packages/nixcfg.nix``). This module owns that policy on the Python side:

- deciding whether the running update code matches the checkout,
- re-executing ``nixcfg update`` from a checkout snapshot when it does not,
- rejecting a source race between runtime selection and workspace capture.

``lib.update.durable`` reuses the same file set to fingerprint resumable runs.
"""

import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from lib.update import persistence as update_persistence
from lib.update.paths import get_repo_root

REEXEC_ENV = "NIXCFG_UPDATE_REEXECED_FROM_CHECKOUT"
EXECUTION_SOURCE_ENV = "NIXCFG_UPDATE_EXECUTION_SOURCE"


class UpdateSourceChangedError(update_persistence.UpdateWorkspaceError):
    """The update runtime no longer matches the stable workspace snapshot."""


@dataclass(frozen=True, slots=True)
class RuntimeSourcePolicy:
    """Packaged runtime source boundary declared in ``pyproject.toml``."""

    root_paths: tuple[str, ...]
    library_extensions: tuple[str, ...]
    library_names: tuple[str, ...]
    excluded_library_paths: tuple[str, ...]
    dynamic_roots: tuple[str, ...]
    dynamic_extensions: tuple[str, ...]
    dynamic_excluded_file_suffixes: tuple[str, ...]


def runtime_source_policy(root: Path) -> RuntimeSourcePolicy:
    """Load the cross-language runtime source policy from project metadata."""
    with (root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    raw_value = project["tool"]["nixcfg"]["runtimeSource"]
    if not isinstance(raw_value, dict):
        msg = "Invalid nixcfg runtime source policy table"
        raise TypeError(msg)
    raw = cast("dict[str, object]", raw_value)
    schema_version = raw.get("schemaVersion")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 1
    ):
        msg = "Unsupported nixcfg runtime source policy schema"
        raise RuntimeError(msg)

    def _strings(name: str) -> tuple[str, ...]:
        values = raw[name]
        if not isinstance(values, list) or not all(
            isinstance(value, str) and value for value in values
        ):
            msg = f"Invalid nixcfg runtime source policy field: {name}"
            raise TypeError(msg)
        return tuple(values)

    def _paths(name: str) -> tuple[str, ...]:
        values = _strings(name)
        if any(
            Path(value).is_absolute() or ".." in Path(value).parts for value in values
        ):
            msg = f"Invalid nixcfg runtime source policy path field: {name}"
            raise TypeError(msg)
        return values

    return RuntimeSourcePolicy(
        root_paths=_paths("rootPaths"),
        library_extensions=_strings("libraryExtensions"),
        library_names=_strings("libraryNames"),
        excluded_library_paths=_paths("excludedLibraryPaths"),
        dynamic_roots=_paths("dynamicRoots"),
        dynamic_extensions=_strings("dynamicExtensions"),
        dynamic_excluded_file_suffixes=_strings("dynamicExcludedFileSuffixes"),
    )


def runtime_source_relpaths(
    root: Path,
    policy: RuntimeSourcePolicy,
) -> set[Path]:
    """Return the source files admitted by the shared runtime policy."""
    runtime_paths: set[Path] = set()
    for raw_path in policy.root_paths:
        relative_root = Path(raw_path)
        source_root = root / relative_root
        if source_root.is_file() or source_root.is_symlink():
            runtime_paths.add(relative_root)
            continue
        if not source_root.is_dir():
            msg = f"Declared runtime source root does not exist: {raw_path}"
            raise RuntimeError(msg)
        runtime_paths.update(
            relative_root / path.relative_to(source_root)
            for path in source_root.rglob("*")
            if path.is_file() or path.is_symlink()
        )
    library_root = root / "lib"
    for path in library_root.rglob("*"):
        if not path.is_file() and not path.is_symlink():
            continue
        relative_path = path.relative_to(library_root)
        if any(
            relative_path.is_relative_to(excluded)
            for excluded in map(Path, policy.excluded_library_paths)
        ):
            continue
        if (
            path.suffix.removeprefix(".") in policy.library_extensions
            or path.name in policy.library_names
        ):
            runtime_paths.add(Path("lib") / relative_path)
    for raw_root in policy.dynamic_roots:
        relative_root = Path(raw_root)
        dynamic_root = root / relative_root
        if dynamic_root.is_file() or dynamic_root.is_symlink():
            dynamic_paths = (dynamic_root,)
        elif dynamic_root.is_dir():
            dynamic_paths = dynamic_root.rglob("*")
        else:
            msg = f"Declared dynamic runtime source root does not exist: {raw_root}"
            raise RuntimeError(msg)
        for path in dynamic_paths:
            if not path.is_file() and not path.is_symlink():
                continue
            if path.suffix.removeprefix(".") not in policy.dynamic_extensions:
                continue
            if any(
                path.name.endswith(suffix)
                for suffix in policy.dynamic_excluded_file_suffixes
            ):
                continue
            runtime_paths.add(
                relative_root
                if path == dynamic_root
                else relative_root / path.relative_to(dynamic_root)
            )
    return runtime_paths


def _same_source_path(left: Path, right: Path) -> bool:
    """Compare one packaged path without hiding symlink identity changes."""
    if left.is_symlink() or right.is_symlink():
        return (
            left.is_symlink()
            and right.is_symlink()
            and left.readlink() == right.readlink()
        )
    return (
        left.is_file() and right.is_file() and left.read_bytes() == right.read_bytes()
    )


def update_library_matches_checkout(
    repo_root: Path,
    *,
    runtime_source_root: Path | None = None,
) -> bool:
    """Return whether the complete packaged runtime matches the checkout."""
    checkout_root = repo_root.expanduser().resolve()
    configured_runtime_root = os.environ.get(EXECUTION_SOURCE_ENV)
    runtime_root = (
        Path(configured_runtime_root).expanduser().resolve()
        if runtime_source_root is None and configured_runtime_root
        else (
            Path(__file__).resolve().parents[2]
            if runtime_source_root is None
            else runtime_source_root.expanduser().resolve()
        )
    )
    try:
        if runtime_root.samefile(checkout_root):
            return True
    except OSError:
        return False

    try:
        repo_policy = runtime_source_policy(checkout_root)
        runtime_policy = runtime_source_policy(runtime_root)
    except KeyError, OSError, RuntimeError, TypeError, tomllib.TOMLDecodeError:
        return False
    if repo_policy != runtime_policy:
        return False

    try:
        repo_files = runtime_source_relpaths(checkout_root, repo_policy)
        runtime_files = runtime_source_relpaths(runtime_root, runtime_policy)
    except OSError, RuntimeError:
        return False
    try:
        return repo_files == runtime_files and all(
            _same_source_path(checkout_root / relpath, runtime_root / relpath)
            for relpath in repo_files
        )
    except OSError:
        return False


def _argv_runs_top_level_update(argv: list[str]) -> bool:
    """Return whether argv reaches ``update`` after global Nix options."""
    index = 1
    while index < len(argv):
        argument = argv[index]
        if argument == "update":
            return True
        if argument in {"--nix-option", "-N"}:
            index += 2
        elif argument.startswith(("--nix-option=", "-N")):
            index += 1
        else:
            return False
    return False


def revalidate_runtime_source_snapshot(workspace_root: Path) -> None:
    """Reject a source race between runtime selection and workspace capture."""
    if not (
        _argv_runs_top_level_update(sys.argv) or os.environ.get(EXECUTION_SOURCE_ENV)
    ):
        return
    if update_library_matches_checkout(workspace_root):
        return
    msg = (
        "Update source changed while preparing the isolated workspace. "
        "No changes were applied; retry `nixcfg update`."
    )
    raise UpdateSourceChangedError(msg)


def maybe_reexec_checkout_update() -> int | None:
    """Run update commands with checkout-matching update code when needed."""
    if not _argv_runs_top_level_update(sys.argv):
        return None

    repo_root = get_repo_root()
    if update_library_matches_checkout(repo_root):
        return None

    if os.environ.get(REEXEC_ENV):
        sys.stderr.write(
            "Error: running nixcfg update code still differs from this checkout "
            "after re-exec. Run `nix run path:.#nixcfg -- update ...` directly.\n"
        )
        return 1

    nix = shutil.which("nix")
    if nix is None:
        sys.stderr.write(
            "Error: installed nixcfg update code differs from this checkout, "
            "but `nix` was not found for a checkout re-exec.\n"
        )
        return 1

    env = dict(os.environ)
    env[REEXEC_ENV] = "1"
    env["REPO_ROOT"] = os.fspath(repo_root)
    with update_persistence.visible_source_snapshot(repo_root) as snapshot_root:
        result = subprocess.run(  # noqa: S603 -- fixed local Nix executable
            [
                nix,
                "run",
                f"path:{snapshot_root}#nixcfg",
                "--",
                *sys.argv[1:],
            ],
            cwd=repo_root,
            env=env,
            check=False,
        )
    return result.returncode
