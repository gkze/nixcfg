"""Semantic contracts for repository whitespace policy."""

import configparser
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]


def test_git_treats_unified_diff_payload_whitespace_as_semantic(tmp_path: Path) -> None:
    """Use Git itself because attribute behavior cannot be proven by parsing text."""
    git = shutil.which("git")
    assert git is not None

    shutil.copy2(REPO_ROOT / ".gitattributes", tmp_path / ".gitattributes")
    (tmp_path / "fixture.patch").write_text(" \tcontext line\n", encoding="utf-8")
    subprocess.run(  # noqa: S603 -- fixed Git executable against an owned fixture
        [git, "init", "--quiet"],
        cwd=tmp_path,
        check=True,
    )

    result = subprocess.run(  # noqa: S603 -- fixed Git executable against an owned fixture
        [git, "check-attr", "whitespace", "--", "fixture.patch"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == "fixture.patch: whitespace: unset\n"


def test_editorconfig_preserves_unified_diff_payload_whitespace() -> None:
    """The editor policy should agree with Git and the repository formatter."""
    parser = configparser.ConfigParser(interpolation=None)
    source = (REPO_ROOT / ".editorconfig").read_text(encoding="utf-8")
    parser.read_string("[editorconfig-root]\n" + source)

    assert parser.getboolean("*.patch", "trim_trailing_whitespace") is False
