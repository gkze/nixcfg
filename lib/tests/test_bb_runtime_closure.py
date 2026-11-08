"""Tests for bb's runtime-closure roots and the Electron 44 SQLite ABI patch."""

import json
import shutil
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from lib.tests._updater_helpers import load_repo_module

_SNAPSHOTS = """
packages:

  bindings@1.5.0:
    resolution: {integrity: sha512-loader}

  file-uri-to-path@1.0.0:
    resolution: {integrity: sha512-loader}

  wrappy@1.0.2:
    resolution: {integrity: sha512-logger}
"""

# Desktop dependency blocks copied from get-bb/bb desktop-v0.48.1
# (e003e9510cbfd3bb6c26a28630b46ffb1c242a44) and desktop-v0.49.0
# (7b26cae79ec978fafd0a5db0e8517e94f12eb8f4). Neighbor versions and
# @bb/process-utils's position are the hunk context that stopped applying.
_RELEASES = {
    "0.48.1": {
        "dependencies": {
            "@bb/connect-client": "workspace:*",
            "@bb/host-daemon-contract": "workspace:*",
            "bb-app": "workspace:*",
            "electron-updater": "^6.8.3",
            "semver": "^7.7.0",
            "ws": "^8.19.0",
            "zod": "^4.3.6",
            "@bb/process-utils": "workspace:*",
        },
        "lock_dependencies": dedent(
            """\
                  '@bb/connect-client':
                    specifier: workspace:*
                    version: link:../../packages/connect-client
                  '@bb/host-daemon-contract':
                    specifier: workspace:*
                    version: link:../../packages/host-daemon-contract
                  '@bb/process-utils':
                    specifier: workspace:*
                    version: link:../../packages/process-utils
                  bb-app:
                    specifier: workspace:*
                    version: link:../../packages/bb-app
                  electron-updater:
                    specifier: ^6.8.3
                    version: 6.8.3
                  semver:
                    specifier: ^7.7.0
                    version: 7.7.4
                  ws:
                    specifier: ^8.19.0
                    version: 8.21.0
                  zod:
                    specifier: 4.6.5
                    version: 4.6.5
            """
        ),
        "rooted_keys": [
            "@bb/connect-client",
            "@bb/host-daemon-contract",
            "bb-app",
            "bindings",
            "electron-updater",
            "file-uri-to-path",
            "semver",
            "wrappy",
            "ws",
            "zod",
            "@bb/process-utils",
        ],
    },
    "0.49.0": {
        "dependencies": {
            "@bb/connect-client": "workspace:*",
            "@bb/host-daemon-contract": "workspace:*",
            "@bb/process-utils": "workspace:*",
            "bb-app": "workspace:*",
            "electron-updater": "^6.8.11",
            "semver": "^7.8.6",
            "ws": "^8.22.1",
            "zod": "^4.6.7",
        },
        "lock_dependencies": dedent(
            """\
                  '@bb/connect-client':
                    specifier: workspace:*
                    version: link:../../packages/connect-client
                  '@bb/host-daemon-contract':
                    specifier: workspace:*
                    version: link:../../packages/host-daemon-contract
                  '@bb/process-utils':
                    specifier: workspace:*
                    version: link:../../packages/process-utils
                  bb-app:
                    specifier: workspace:*
                    version: link:../../packages/bb-app
                  electron-updater:
                    specifier: ^6.8.11
                    version: 6.8.11
                  semver:
                    specifier: ^7.8.6
                    version: 7.8.6
                  ws:
                    specifier: ^8.22.1
                    version: 8.22.1
                  zod:
                    specifier: 4.6.7
                    version: 4.6.7
            """
        ),
        "rooted_keys": [
            "@bb/connect-client",
            "@bb/host-daemon-contract",
            "@bb/process-utils",
            "bb-app",
            "bindings",
            "electron-updater",
            "file-uri-to-path",
            "semver",
            "wrappy",
            "ws",
            "zod",
        ],
    },
}


def _closure():
    return load_repo_module(
        "packages/bb/ensure_runtime_closure.py",
        "bb_ensure_runtime_closure_test",
    )


def _abi():
    return load_repo_module(
        "packages/bb/apply_better_sqlite3_abi_patch.py",
        "bb_apply_better_sqlite3_abi_patch_test",
    )


def _manifest(dependencies: dict[str, str]) -> str:
    return (
        json.dumps({"name": "@bb/desktop", "dependencies": dependencies}, indent=2)
        + "\n"
    )


def _lock(dependency_block: str, *, snapshots: str = _SNAPSHOTS) -> str:
    body = dependency_block
    if body and not body.endswith("\n"):
        body += "\n"
    # Release excerpts are dedented to column 0. pnpm wants six-space keys.
    if body and not body.startswith("      "):
        body = "".join(
            f"      {line}\n" if line else "\n" for line in body.splitlines()
        )
    return (
        "lockfileVersion: '9.0'\n\nimporters:\n\n  apps/desktop:\n    dependencies:\n"
        f"{body}"
        "    devDependencies:\n      electron:\n        specifier: 44.9.3\n        version: 44.9.3\n"
        f"{snapshots}"
    )


def _write_tree(root: Path, *, manifest: str, lock: str) -> None:
    manifest_path = root / "apps" / "desktop" / "package.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(manifest, encoding="utf-8")
    (root / "pnpm-lock.yaml").write_text(lock, encoding="utf-8")


def _dependency_names(lock_text: str) -> list[str]:
    names: list[str] = []
    in_dependencies = False
    for line in lock_text.splitlines():
        if line == "    dependencies:":
            in_dependencies = True
            continue
        if (
            in_dependencies
            and line.startswith("    ")
            and not line.startswith("      ")
        ):
            break
        if (
            in_dependencies
            and line.startswith("      ")
            and not line.startswith("        ")
        ):
            names.append(line.removeprefix("      ").removesuffix(":"))
    return names


@pytest.mark.parametrize("version", ["0.48.1", "0.49.0"])
def test_runtime_closure_roots_apply_to_pinned_and_candidate_releases(
    tmp_path: Path,
    version: str,
) -> None:
    """Neighbor drift must not drop bindings, file-uri-to-path, or wrappy."""
    release = _RELEASES[version]
    module = _closure()
    manifest = _manifest(release["dependencies"])
    lock = _lock(release["lock_dependencies"])
    _write_tree(tmp_path, manifest=manifest, lock=lock)

    assert module.main(["--source", str(tmp_path)]) == 0

    rooted = json.loads(
        (tmp_path / "apps" / "desktop" / "package.json").read_text(encoding="utf-8")
    )
    assert list(rooted["dependencies"]) == release["rooted_keys"]
    for name, declared in release["dependencies"].items():
        assert rooted["dependencies"][name] == declared
    assert rooted["dependencies"]["bindings"] == "1.5.0"
    assert rooted["dependencies"]["file-uri-to-path"] == "1.0.0"
    assert rooted["dependencies"]["wrappy"] == "1.0.2"
    lock_text = (tmp_path / "pnpm-lock.yaml").read_text(encoding="utf-8")
    assert _dependency_names(lock_text) == [
        "'@bb/connect-client'",
        "'@bb/host-daemon-contract'",
        "'@bb/process-utils'",
        "bb-app",
        "bindings",
        "electron-updater",
        "file-uri-to-path",
        "semver",
        "wrappy",
        "ws",
        "zod",
    ]
    for line in release["lock_dependencies"].splitlines():
        preserved = line.strip()
        if preserved.startswith(("specifier:", "version:")):
            assert preserved in lock_text
    before = lock_text
    module.ensure_runtime_closure(tmp_path)
    assert (tmp_path / "pnpm-lock.yaml").read_text(encoding="utf-8") == before
    assert (tmp_path / "apps" / "desktop" / "package.json").read_text(
        encoding="utf-8"
    ) == json.dumps(rooted, indent=2) + "\n"


def test_runtime_closure_keeps_an_already_rooted_manifest_byte_stable(
    tmp_path: Path,
) -> None:
    """A second preparation must not rewrite a lock that already has the roots."""
    module = _closure()
    dependencies = {
        "bb-app": "workspace:*",
        "bindings": "1.5.0",
        "file-uri-to-path": "1.0.0",
        "wrappy": "1.0.2",
    }
    manifest = _manifest(dependencies)
    lock = _lock(
        "      bb-app:\n        specifier: workspace:*\n"
        "        version: link:../../packages/bb-app\n"
        "      bindings:\n        specifier: 1.5.0\n        version: 1.5.0\n"
        "      file-uri-to-path:\n        specifier: 1.0.0\n        version: 1.0.0\n"
        "      wrappy:\n        specifier: 1.0.2\n        version: 1.0.2\n"
    )
    _write_tree(tmp_path, manifest=manifest, lock=lock)
    lock_bytes = (tmp_path / "pnpm-lock.yaml").read_bytes()
    manifest_bytes = (tmp_path / "apps" / "desktop" / "package.json").read_bytes()

    module.ensure_runtime_closure(tmp_path)

    assert (tmp_path / "pnpm-lock.yaml").read_bytes() == lock_bytes
    assert (
        tmp_path / "apps" / "desktop" / "package.json"
    ).read_bytes() == manifest_bytes


def test_runtime_closure_fills_only_the_missing_side(tmp_path: Path) -> None:
    """Manifest and lock roots are independent writes when one side is already rooted."""
    module = _closure()
    manifest = _manifest({
        "bindings": "1.5.0",
        "file-uri-to-path": "1.0.0",
        "wrappy": "1.0.2",
    })
    lock = _lock("")
    _write_tree(tmp_path, manifest=manifest, lock=lock)
    manifest_bytes = (tmp_path / "apps" / "desktop" / "package.json").read_bytes()

    module.ensure_runtime_closure(tmp_path)

    assert (
        tmp_path / "apps" / "desktop" / "package.json"
    ).read_bytes() == manifest_bytes
    assert "specifier: 1.5.0" in (tmp_path / "pnpm-lock.yaml").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("manifest", "lock", "message"),
    [
        ("[]", None, "JSON object"),
        ('{"dependencies": []}', None, "dependencies"),
        ('{"dependencies": {"bindings": 1}}', None, "runtime root"),
        ('{"dependencies": {"bindings": "9.9.9"}}', None, "runtime root"),
        ("{", None, "not JSON"),
        (None, "lockfileVersion: '9.0'\n", "no apps/desktop"),
        (
            None,
            "  apps/desktop:\n    dependencies:\n  apps/desktop:\n    dependencies:\n",
            "multiple",
        ),
        (
            None,
            "  apps/desktop:\n    dependencies:\n      bb-app:\n        specifier: workspace:*\n",
            "does not end",
        ),
        (
            None,
            "  apps/desktop:\n    dependencies:\n\n    devDependencies:\n" + _SNAPSHOTS,
            "preamble",
        ),
        (
            None,
            "  apps/desktop:\n    dependencies:\n      '':\n        specifier: 1.5.0\n"
            "        version: 1.5.0\n    devDependencies:\n" + _SNAPSHOTS,
            "name is empty",
        ),
        (
            None,
            "  apps/desktop:\n    dependencies:\n      bindings:\n        version: 1.5.0\n"
            "    devDependencies:\n" + _SNAPSHOTS,
            "missing specifier",
        ),
        (
            None,
            "  apps/desktop:\n    dependencies:\n      bindings:\n        specifier: 1.5.0\n"
            "    devDependencies:\n" + _SNAPSHOTS,
            "missing version",
        ),
        (
            None,
            "  apps/desktop:\n    dependencies:\n      bindings:\n        specifier: 1.5.0\n"
            "        version: 1.5.0\n      bindings:\n        specifier: 1.5.0\n"
            "        version: 1.5.0\n    devDependencies:\n" + _SNAPSHOTS,
            "more than once",
        ),
        (
            None,
            "  apps/desktop:\n    dependencies:\n      bindings:\n        specifier: 9.9.9\n"
            "        version: 9.9.9\n    devDependencies:\n" + _SNAPSHOTS,
            "resolves",
        ),
        (
            None,
            "  apps/desktop:\n    dependencies:\n    devDependencies:\n",
            "does not resolve",
        ),
    ],
)
def test_runtime_closure_rejects_malformed_contracts(
    tmp_path: Path,
    manifest: str | None,
    lock: str | None,
    message: str,
) -> None:
    """Fail closed when the desktop manifest or lock cannot host the roots."""
    module = _closure()
    manifest_text = manifest if manifest is not None else _manifest({})
    lock_text = lock if lock is not None else _lock("")
    if manifest is not None and not manifest_text.lstrip().startswith("{"):
        _write_tree(tmp_path, manifest=manifest_text, lock=lock_text)
        with pytest.raises(module.RuntimeClosureError, match=message):
            module.ensure_runtime_closure(tmp_path)
        return
    if manifest == "[":
        _write_tree(tmp_path, manifest="[]\n", lock=lock_text)
        with pytest.raises(module.RuntimeClosureError, match=message):
            module.ensure_runtime_closure(tmp_path)
        return
    if manifest is not None and manifest.startswith("{"):
        parsed = json.loads(manifest) if manifest != "{" else None
        if parsed is None:
            _write_tree(tmp_path, manifest="{\n", lock=lock_text)
            with pytest.raises(module.RuntimeClosureError, match=message):
                module.ensure_runtime_closure(tmp_path)
            return
        with pytest.raises(module.RuntimeClosureError, match=message):
            module.assert_runtime_closure_applicable(parsed, lock_text)
        return
    with pytest.raises(module.RuntimeClosureError, match=message):
        module.assert_runtime_closure_applicable(json.loads(manifest_text), lock_text)


def test_runtime_closure_reports_missing_source_files(tmp_path: Path) -> None:
    """A tree without the desktop manifest or lock is not a bb source."""
    module = _closure()

    with pytest.raises(module.RuntimeClosureError, match="pnpm-lock.yaml is missing"):
        module.ensure_runtime_closure(tmp_path)

    (tmp_path / "pnpm-lock.yaml").write_text(_lock(""), encoding="utf-8")
    with pytest.raises(module.RuntimeClosureError, match="package.json is missing"):
        module.ensure_runtime_closure(tmp_path)


def test_runtime_closure_preserves_a_quoted_existing_root(tmp_path: Path) -> None:
    """Quoted lock keys are the same roots as bare keys."""
    module = _closure()
    lock = _lock(
        "      'bindings':\n        specifier: 1.5.0\n        version: 1.5.0\n"
        "      'file-uri-to-path':\n        specifier: 1.0.0\n        version: 1.0.0\n"
        "      'wrappy':\n        specifier: 1.0.2\n        version: 1.0.2\n"
    )
    _write_tree(tmp_path, manifest=_manifest({}), lock=lock)
    before = (tmp_path / "pnpm-lock.yaml").read_bytes()

    module.ensure_runtime_closure(tmp_path)

    assert (tmp_path / "pnpm-lock.yaml").read_bytes() == before


def test_runtime_closure_ends_the_importer_at_the_next_package(
    tmp_path: Path,
) -> None:
    """The next importer, not only devDependencies, closes the dependency block."""
    module = _closure()
    lock = (
        "lockfileVersion: '9.0'\n\nimporters:\n\n  apps/desktop:\n    dependencies:\n"
        "      zod:\n        specifier: 4.6.7\n        version: 4.6.7\n"
        "    kept-inside-the-desktop-block\n"
        "  apps/server:\n    dependencies:\n"
        f"{_SNAPSHOTS}"
    )
    _write_tree(tmp_path, manifest=_manifest({"zod": "4.6.7"}), lock=lock)

    module.ensure_runtime_closure(tmp_path)

    text = (tmp_path / "pnpm-lock.yaml").read_text(encoding="utf-8")
    desktop, server = text.split("  apps/server:", maxsplit=1)
    assert "bindings:" in desktop
    assert "bindings:" not in server


def _files_from_patch(patch_text: str, *, side: str) -> dict[str, str]:
    files: dict[str, list[str]] = {}
    current: str | None = None
    for line in patch_text.splitlines():
        if line.startswith("--- "):
            name = line[4:]
            name = name.removeprefix("a/")
            current = name.split("\t", 1)[0]
            files[current] = []
            continue
        if current is None or line.startswith(("+++ ", "diff ", "@@", "\\")):
            continue
        if side == "old" and line.startswith("+"):
            continue
        if side == "new" and line.startswith("-"):
            continue
        if line[:1] in {"+", "-", " "}:
            files[current].append(line[1:])
    return {
        path: "\n".join(lines) + ("\n" if lines else "")
        for path, lines in files.items()
    }


def _write_module(root: Path, files: dict[str, str]) -> None:
    for path, content in files.items():
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")


def test_sqlite_abi_patch_applies_to_12_10_and_accepts_12_11(
    tmp_path: Path,
) -> None:
    """12.10.0 receives the fix; 12.11.1 already has it and must stay unchanged."""
    module = _abi()
    patch = Path("packages/bb/patches/better-sqlite3-electron-44-v8-abi.patch")
    patch_text = patch.read_text(encoding="utf-8")
    pre_fix = tmp_path / "12.10.0"
    _write_module(pre_fix, _files_from_patch(patch_text, side="old"))

    assert module.apply_electron_44_abi_patch(pre_fix, patch) == "applied"
    fixed = {
        path: (pre_fix / path).read_text(encoding="utf-8")
        for path in _files_from_patch(patch_text, side="new")
    }
    assert fixed == _files_from_patch(patch_text, side="new")
    assert "EXTERNAL_NEW" in fixed["src/util/macros.cpp"]

    already = tmp_path / "12.11.1"
    _write_module(already, fixed)
    before = {path: (already / path).read_bytes() for path in fixed}
    assert module.apply_electron_44_abi_patch(already, patch) == "already-present"
    assert {path: (already / path).read_bytes() for path in fixed} == before
    assert module.main(["--module", str(already), "--patch", str(patch)]) == 0


def test_sqlite_abi_patch_rejects_an_unrelated_module(tmp_path: Path) -> None:
    """A third source shape is not treated as already fixed."""
    module = _abi()
    patch = Path("packages/bb/patches/better-sqlite3-electron-44-v8-abi.patch")
    source = tmp_path / "other"
    _write_module(
        source,
        {
            "src/better_sqlite3.cpp": "int unrelated();\n",
            "src/util/helpers.cpp": "int unrelated();\n",
            "src/util/macros.cpp": "int unrelated();\n",
        },
    )

    with pytest.raises(module.BetterSqliteAbiError, match="neither"):
        module.apply_electron_44_abi_patch(source, patch)
    assert not list(source.rglob("*.rej"))


def test_sqlite_abi_patch_rejects_missing_inputs(tmp_path: Path) -> None:
    """Missing module, patch, or patch(1) fails before any source edit."""
    module = _abi()
    patch = tmp_path / "missing.patch"
    module_dir = tmp_path / "module"

    with pytest.raises(module.BetterSqliteAbiError, match="module directory"):
        module.apply_electron_44_abi_patch(module_dir, patch)

    module_dir.mkdir()
    with pytest.raises(module.BetterSqliteAbiError, match="patch is missing"):
        module.apply_electron_44_abi_patch(module_dir, patch)

    patch.write_text("diff --git a/a b/a\n", encoding="utf-8")
    original_which = shutil.which

    def _hide_patch(name: str) -> str | None:
        if name == "patch":
            return None
        return original_which(name)

    shutil.which = _hide_patch  # type: ignore[method-assign]
    try:
        with pytest.raises(module.BetterSqliteAbiError, match="not on PATH"):
            module.apply_electron_44_abi_patch(module_dir, patch)
    finally:
        shutil.which = original_which  # type: ignore[method-assign]


def test_sqlite_abi_patch_reports_a_dry_run_that_does_not_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forward dry-run that then fails to apply is an internal patch failure."""
    module = _abi()
    patch = tmp_path / "abi.patch"
    patch.write_text("diff --git a/a b/a\n", encoding="utf-8")
    module_dir = tmp_path / "module"
    module_dir.mkdir()
    calls: list[bool] = []

    def _run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        dry_run = "--dry-run" in command
        calls.append(dry_run)
        return subprocess.CompletedProcess(
            command,
            0 if dry_run else 1,
            stderr="hunk failed\n",
        )

    monkeypatch.setattr(module.subprocess, "run", _run)

    with pytest.raises(module.BetterSqliteAbiError, match="did not apply"):
        module.apply_electron_44_abi_patch(module_dir, patch)
    assert calls == [True, False]
