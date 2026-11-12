"""Cursor 3.30 moved ripgrep; the patch phase must follow the binary."""

import shutil
import stat
import subprocess
from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest

from lib.tests._nix_source import nix_file_binding_expr
from lib.update.paths import REPO_ROOT

_SCRIPT = REPO_ROOT / "overlays/code-cursor/chmod-bundled-ripgrep.sh"
_KNOWN_LAYOUTS = (
    "Contents/Resources/app/node_modules/@vscode/ripgrep/bin/rg",
    "Contents/Resources/app/node_modules.asar.unpacked/@vscode/ripgrep/bin/rg",
    "Contents/Resources/app/node_modules/@vscode/ripgrep-universal/bin/darwin-arm64/rg",
    "Contents/Resources/app/node_modules.asar.unpacked/@vscode/ripgrep-universal/bin/linux-x64/rg",
    "Contents/Resources/app/node_modules/@cursor/ripgrep-darwin-arm64/bin/rg",
    "resources/app/node_modules/@cursor/ripgrep-linux-x64/bin/rg",
)


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash is not None
    return subprocess.run(  # noqa: S603 -- owned ripgrep layout script
        [bash, str(_SCRIPT), *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def _write_binary(path: Path, payload: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(mode)


def _string_leaves(node: object) -> list[str]:
    leaves: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, str):
            leaves.append(value)
            return
        if isinstance(value, list | tuple):
            for item in value:
                walk(item)
            return
        if is_dataclass(value) and not isinstance(value, type):
            for field in fields(value):
                walk(getattr(value, field.name))

    walk(node)
    return leaves


@pytest.mark.parametrize("relative", _KNOWN_LAYOUTS)
def test_chmod_marks_each_known_ripgrep_layout(tmp_path: Path, relative: str) -> None:
    """DMG unpack can drop +x; every layout Cursor or VS Code has shipped is marked."""
    binary = tmp_path / relative
    _write_binary(binary, b"rg", 0o644)
    decoy = tmp_path / "other" / "bin" / "rg"
    _write_binary(decoy, b"no", 0o644)

    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(binary.stat().st_mode) == 0o755
    assert stat.S_IMODE(decoy.stat().st_mode) == 0o644


def test_missing_ripgrep_layout_fails(tmp_path: Path) -> None:
    """A new location is a failed build, not a skipped chmod."""
    result = _run(tmp_path)

    assert result.returncode != 0
    assert "known layout" in result.stderr


def test_link_retargets_bundled_binaries_without_replacing_nixpkgs(
    tmp_path: Path,
) -> None:
    """Linux keeps nixpkgs ripgrep at the path Cursor's resolver executes."""
    replacement = tmp_path / "nix-rg"
    _write_binary(replacement, b"nix", 0o755)
    root = tmp_path / "app"
    bundled = (
        root / "resources/app/node_modules/@cursor/ripgrep-linux-x64/bin/rg",
        root / "resources/app/node_modules/@vscode/ripgrep/bin/rg",
    )
    for binary in bundled:
        _write_binary(binary, b"bundled", 0o755)

    result = _run(root, "--link-to", str(replacement))

    assert result.returncode == 0, result.stderr
    for binary in bundled:
        assert binary.is_symlink()
        assert binary.read_bytes() == b"nix"
    assert replacement.read_bytes() == b"nix"
    assert not replacement.is_symlink()


def test_link_rejects_a_missing_replacement(tmp_path: Path) -> None:
    """A bad nixpkgs ripgrep path fails before any bundled binary is replaced."""
    binary = tmp_path / "resources/app/node_modules/@cursor/ripgrep-linux-x64/bin/rg"
    _write_binary(binary, b"bundled", 0o755)

    result = _run(tmp_path, "--link-to", str(tmp_path / "missing"))

    assert result.returncode != 0
    assert binary.read_bytes() == b"bundled"
    assert not binary.is_symlink()


def test_usage_requires_chmod_or_link(tmp_path: Path) -> None:
    """An unexpected argument does not chmod whatever happens to be in the tree."""
    result = _run(tmp_path, "--chmod")

    assert result.returncode != 0
    assert "usage:" in result.stderr


def test_code_cursor_overlay_rewrites_the_nixpkgs_ripgrep_phase() -> None:
    """Darwin chmods the real binary; Linux retargets it and keeps nixpkgs' link."""
    texts = _string_leaves(nix_file_binding_expr("overlays/default.nix", "code-cursor"))
    joined = "\n".join(texts)

    assert (
        "chmod +x Contents/Resources/app/node_modules/@vscode/ripgrep/bin/rg" in texts
    )
    assert "rm resources/app/node_modules/@vscode/ripgrep/bin/rg" in texts
    assert "chmod-bundled-ripgrep.sh" in joined
    assert "--link-to" in joined
    assert "mkdir -p resources/app/node_modules/@vscode/ripgrep/bin" in joined
