"""Exercise Mesh patch ordering, inventory rejection, and real Git application."""

import subprocess
import sys
from pathlib import Path

import pytest

from packages.buzz.native import llama_patches


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "mesh"
    directory = root / "third_party/llama.cpp/patches"
    directory.mkdir(parents=True)
    (directory / "0001-base.patch").write_text("base patch\n")
    (directory.parent / "upstream.txt").write_text("selected\n")
    return root


def generated(source: Path) -> Path:
    directory = source / "third_party/llama.cpp/patches/generated"
    directory.mkdir()
    (directory / "series").write_text("0001-family-llama.patch\r\n")
    (directory / "0001-family-llama.patch").write_text("family patch\n")
    return directory


def test_ordered_queue(source: Path) -> None:
    """Generated patches follow base patches, including CRLF series files."""
    assert [path.name for path in llama_patches.patch_queue(source)] == [
        "0001-base.patch"
    ]
    (generated(source) / "series.json").write_text("{}\n")
    (source / "third_party/llama.cpp/patches/.gitattributes").write_text(
        "*.patch -whitespace\n"
    )
    assert [path.name for path in llama_patches.patch_queue(source)] == [
        "0001-base.patch",
        "0001-family-llama.patch",
    ]


@pytest.mark.parametrize(
    "case",
    ["missing", "symlink", "empty", "unexpected", "empty-patch"],
)
def test_invalid_base_queue(source: Path, case: str) -> None:
    """Bad inventories fail before any source mutation."""
    directory = source / "third_party/llama.cpp/patches"
    patch = directory / "0001-base.patch"
    if case == "missing":
        patch.unlink()
        directory.rmdir()
    elif case == "symlink":
        moved = directory.with_name("moved")
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    elif case == "empty":
        patch.unlink()
    elif case == "unexpected":
        (directory / "README").write_text("unexpected")
    else:
        patch.write_text("")
    with pytest.raises(SystemExit):
        llama_patches.patch_queue(source)


@pytest.mark.parametrize(
    "case",
    [
        "directory-link",
        "missing-series",
        "series-link",
        "empty-series",
        "bad-order",
        "traversal",
        "extra",
        "missing-patch",
        "patch-link",
    ],
)
def test_invalid_generated_queue(source: Path, case: str) -> None:
    """The series must describe every regular family patch exactly once."""
    directory = generated(source)
    series = directory / "series"
    patch = directory / "0001-family-llama.patch"
    if case == "directory-link":
        moved = source / "generated"
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    elif case == "missing-series":
        series.unlink()
    elif case == "series-link":
        series.unlink()
        series.symlink_to(patch)
    elif case == "empty-series":
        series.write_text("")
    elif case == "bad-order":
        series.write_text("0002-family-llama.patch\n")
    elif case == "traversal":
        series.write_text("../0001-base.patch\n")
    elif case == "extra":
        (directory / "extra.patch").write_text("extra")
    elif case == "missing-patch":
        patch.unlink()
    else:
        patch.unlink()
        patch.symlink_to(directory.parent / "0001-base.patch")
    with pytest.raises(SystemExit):
        llama_patches.patch_queue(source)


def test_apply_checks_pin_before_initializing_git(source: Path, tmp_path: Path) -> None:
    """A wrong source identity cannot create a repository or apply patches."""
    with pytest.raises(SystemExit, match="upstream pin"):
        llama_patches.apply_patches(source, "different", tmp_path)
    assert not (tmp_path / ".git").exists()


def test_cli_applies_base_then_generated_patches(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real format-patch commits require the same order as the vendor series."""
    author = tmp_path / "author"
    author.mkdir()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603 -- fixed Git fixture operations
            ["git", *args],  # noqa: S607 -- test dependency
            cwd=author,
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    git("init", "--quiet")
    git("config", "user.name", "Patch Test")
    git("config", "user.email", "test@example.invalid")
    path = author / "value"
    path.write_text("base\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "base")
    directory = generated(source)
    for value, patch in [
        ("updated\n", directory.parent / "0001-base.patch"),
        ("generated\n", directory / "0001-family-llama.patch"),
    ]:
        path.write_text(value)
        git("commit", "--quiet", "-am", value.strip())
        patch.write_text(git("format-patch", "-1", "--stdout"))
    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / "value").write_text("base\n")
    monkeypatch.setattr(
        sys, "argv", ["llama_patches", str(source), "selected", str(destination)]
    )
    llama_patches.main()
    assert (destination / "value").read_text() == "generated\n"
