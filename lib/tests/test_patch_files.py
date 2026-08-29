"""Repository-wide structural checks for static patch files."""

import shutil
import subprocess

from lib.update.paths import REPO_ROOT


def test_tracked_patch_files_have_consistent_hunk_headers() -> None:
    """Require every tracked patch to be parseable without implicit recounting."""
    git = shutil.which("git")
    assert git is not None

    inventory = subprocess.run(  # noqa: S603
        [git, "ls-files", "-z", "--", "*.patch"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    patch_files = [
        relative.decode() for relative in inventory.stdout.split(b"\0") if relative
    ]
    assert patch_files

    failures: list[str] = []
    for relative in patch_files:
        validation = subprocess.run(  # noqa: S603
            [git, "apply", "--numstat", "--", relative],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if validation.returncode != 0:
            failures.append(f"{relative}: {validation.stderr.strip()}")

    assert not failures, "\n".join(failures)
