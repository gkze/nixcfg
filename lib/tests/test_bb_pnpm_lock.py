"""Tests for bb's pnpm 9 to pnpm 10 patch-hash normalization."""

import hashlib
import json
from pathlib import Path
from types import ModuleType

import pytest

from lib.tests._updater_helpers import load_repo_module


def _load_module() -> ModuleType:
    return load_repo_module(
        "packages/bb/normalize_pnpm_patch_hashes.py",
        "bb_normalize_pnpm_patch_hashes_test",
    )


def _write_source(source: Path, *, lock_text: str) -> bytes:
    patch = b"diff --git a/demo b/demo\n"
    patch_path = source / "patches/demo.patch"
    patch_path.parent.mkdir(parents=True)
    patch_path.write_bytes(patch)
    (source / "package.json").write_text(
        json.dumps({
            "pnpm": {
                "patchedDependencies": {
                    "demo@1.0.0": "patches/demo.patch",
                }
            }
        }),
        encoding="utf-8",
    )
    (source / "pnpm-lock.yaml").write_text(lock_text, encoding="utf-8")
    return patch


def test_normalize_pnpm_patch_hashes_updates_every_frozen_lock_reference(
    tmp_path: Path,
) -> None:
    """Rewrite pnpm 9 patch IDs to the SHA-256 IDs required by pnpm 10."""
    old_hash = "abcdefghijklmnopqrstuvwxzy"
    patch = _write_source(
        tmp_path,
        lock_text=(
            "lockfileVersion: '9.0'\n\n"
            "patchedDependencies:\n"
            "  demo@1.0.0:\n"
            f"    hash: {old_hash}\n"
            "    path: patches/demo.patch\n\n"
            "snapshots:\n"
            f"  demo@1.0.0(patch_hash={old_hash}): {{}}\n"
        ),
    )
    digest = hashlib.sha256(patch).hexdigest()
    module = _load_module()

    assert module.main(["--source", str(tmp_path)]) == 0

    normalized = (tmp_path / "pnpm-lock.yaml").read_text(encoding="utf-8")
    assert old_hash not in normalized
    assert normalized.count(digest) == 2


def test_normalize_pnpm_patch_hashes_is_a_noop_without_patches(
    tmp_path: Path,
) -> None:
    """Leave older bb releases without patched dependencies byte-exact."""
    (tmp_path / "package.json").write_text(
        json.dumps({"pnpm": {"overrides": {"zod": "4.3.6"}}}),
        encoding="utf-8",
    )
    lock_path = tmp_path / "pnpm-lock.yaml"
    lock_path.write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    module = _load_module()

    module.normalize_pnpm_patch_hashes(tmp_path)

    assert lock_path.read_bytes() == b"lockfileVersion: '9.0'\n"


def test_normalize_pnpm_patch_hashes_preserves_an_already_normalized_lock(
    tmp_path: Path,
) -> None:
    """Avoid rewriting a lock whose metadata and snapshot already use SHA-256."""
    patch = b"diff --git a/demo b/demo\n"
    digest = hashlib.sha256(patch).hexdigest()
    lock_text = (
        "lockfileVersion: '9.0'\n\n"
        "patchedDependencies:\n"
        "  demo@1.0.0:\n"
        f"    hash: {digest}\n"
        "    path: patches/demo.patch\n\n"
        "snapshots:\n"
        f"  demo@1.0.0(patch_hash={digest}): {{}}\n"
    )
    _write_source(tmp_path, lock_text=lock_text)
    lock_path = tmp_path / "pnpm-lock.yaml"
    before = lock_path.read_bytes()

    _load_module().normalize_pnpm_patch_hashes(tmp_path)

    assert lock_path.read_bytes() == before


@pytest.mark.parametrize(
    ("manifest", "lock_text", "message"),
    [
        ([], "lockfileVersion: '9.0'\n", "JSON object"),
        ({"pnpm": []}, "lockfileVersion: '9.0'\n", "pnpm object"),
        (
            {"pnpm": {"patchedDependencies": []}},
            "lockfileVersion: '9.0'\n",
            "patchedDependencies object",
        ),
        (
            {"pnpm": {"patchedDependencies": {"demo@1.0.0": 1}}},
            "lockfileVersion: '9.0'\n",
            "patch path",
        ),
        (
            {"pnpm": {"patchedDependencies": {"": "patches/demo.patch"}}},
            "lockfileVersion: '9.0'\n",
            "dependency name",
        ),
        (
            {
                "pnpm": {
                    "patchedDependencies": {
                        "demo@1.0.0": "../demo.patch",
                    }
                }
            },
            "lockfileVersion: '9.0'\n",
            "escapes the source tree",
        ),
        (
            {
                "pnpm": {
                    "patchedDependencies": {
                        "demo@1.0.0": "patches/missing.patch",
                    }
                }
            },
            (
                "patchedDependencies:\n"
                "  demo@1.0.0:\n"
                "    hash: oldhash\n"
                "    path: patches/missing.patch\n"
            ),
            "regular file",
        ),
        (
            {
                "pnpm": {
                    "patchedDependencies": {
                        "demo@1.0.0": "patches/demo.patch",
                    }
                }
            },
            (
                "patchedDependencies:\n"
                "  demo@1.0.0:\n"
                "    hash: oldhash\n"
                "    path: patches/demo.patch\n"
            ),
            "metadata and a lock entry",
        ),
        (
            {
                "pnpm": {
                    "patchedDependencies": {
                        "demo@1.0.0": "patches/demo.patch",
                    }
                }
            },
            "lockfileVersion: '9.0'\n",
            "lock entry",
        ),
    ],
)
def test_normalize_pnpm_patch_hashes_rejects_malformed_contracts(
    tmp_path: Path,
    manifest: object,
    lock_text: str,
    message: str,
) -> None:
    """Fail closed when upstream's patch manifest and lock disagree."""
    patch_path = tmp_path / "patches/demo.patch"
    patch_path.parent.mkdir(parents=True)
    patch_path.write_text("patch\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "pnpm-lock.yaml").write_text(lock_text, encoding="utf-8")
    module = _load_module()

    with pytest.raises(module.PnpmPatchHashError, match=message):
        module.normalize_pnpm_patch_hashes(tmp_path)
