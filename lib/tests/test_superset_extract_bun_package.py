"""Pure-Python tests for Superset's Bun package extraction helper."""

from __future__ import annotations

import runpy
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT


def _load_module() -> ModuleType:
    return load_module_from_path(
        REPO_ROOT / "packages/superset/extract_bun_package.py",
        "superset_extract_bun_package_dedicated_test",
    )


def test_parse_args_reads_required_command_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parse the three required CLI flags into a namespace."""
    module = _load_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "extract-bun-package",
            "--bsdtar",
            "/bin/bsdtar",
            "--package",
            "/tmp/pkg.tgz",
            "--out",
            "/tmp/out",
        ],
    )

    args = module.parse_args()

    assert args.bsdtar == "/bin/bsdtar"
    assert args.package == "/tmp/pkg.tgz"
    assert args.out == "/tmp/out"


def test_make_user_writable_sets_rwx_and_skips_missing_and_symlink(
    tmp_path: Path,
) -> None:
    """Grant user rwx recursively without following symlinks or failing on races."""
    module = _load_module()
    root = tmp_path / "package"
    nested = root / "nested"
    nested.mkdir(parents=True)
    leaf = nested / "index.js"
    leaf.write_text("console.log('hi')\n", encoding="utf-8")
    missing = root / "missing"
    symlink_target = tmp_path / "target"
    symlink_target.write_text("outside\n", encoding="utf-8")
    symlink_path = root / "link"
    symlink_path.symlink_to(symlink_target)

    root.chmod(stat.S_IRUSR | stat.S_IXUSR)
    nested.chmod(stat.S_IRUSR | stat.S_IXUSR)
    leaf.chmod(stat.S_IRUSR)
    symlink_target.chmod(stat.S_IRUSR)

    original_iterdir = Path.iterdir

    def _iterdir(path: Path):
        yield from original_iterdir(path)
        if path == root:
            yield missing

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(Path, "iterdir", _iterdir)
    try:
        module.make_user_writable(root)
    finally:
        monkeypatch.undo()

    for path in (root, nested, leaf):
        mode = path.stat().st_mode
        assert mode & stat.S_IRUSR
        assert mode & stat.S_IWUSR
        assert mode & stat.S_IXUSR
    assert symlink_path.is_symlink()
    assert not (symlink_target.stat().st_mode & stat.S_IWUSR)


def test_extract_archive_invokes_bsdtar_with_expected_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Extract `.tgz` packages with the legacy tar flags."""
    module = _load_module()
    package = tmp_path / "pkg.tgz"
    output = tmp_path / "out"
    calls: list[tuple[list[str], bool]] = []

    def _run(command: list[str], *, check: bool) -> None:
        calls.append((command, check))

    monkeypatch.setattr(module.subprocess, "run", _run)

    module.extract_archive("/bin/bsdtar", package, output)

    assert calls == [
        (
            [
                "/bin/bsdtar",
                "--extract",
                "--file",
                str(package),
                "--directory",
                str(output),
                "--strip-components=1",
                "--no-same-owner",
                "--no-same-permissions",
            ],
            True,
        )
    ]


@pytest.mark.parametrize(
    ("side_effect", "message"),
    [
        (FileNotFoundError("missing"), "bsdtar executable not found: bsdtar"),
        (
            subprocess.CalledProcessError(7, ["bsdtar"]),
            "bsdtar failed with exit code 7 for /tmp/pkg.tgz",
        ),
    ],
)
def test_extract_archive_translates_failures(
    monkeypatch: pytest.MonkeyPatch,
    side_effect: Exception,
    message: str,
) -> None:
    """Wrap subprocess failures in the user-facing extraction error type."""
    module = _load_module()

    def _run(*_args: object, **_kwargs: object) -> None:
        raise side_effect

    monkeypatch.setattr(module.subprocess, "run", _run)

    with pytest.raises(module.ExtractBunPackageError, match=message):
        module.extract_archive("bsdtar", Path("/tmp/pkg.tgz"), Path("/tmp/out"))


def test_copy_directory_preserves_dirs_exist_ok_and_symlinks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Copy unpacked packages with the same tree semantics as the shell helper."""
    module = _load_module()
    calls: list[tuple[Path, Path, bool, bool]] = []

    def _copytree(src: Path, dst: Path, *, dirs_exist_ok: bool, symlinks: bool) -> None:
        calls.append((src, dst, dirs_exist_ok, symlinks))

    monkeypatch.setattr(module.shutil, "copytree", _copytree)

    module.copy_directory(Path("/tmp/pkg"), Path("/tmp/out"))

    assert calls == [(Path("/tmp/pkg"), Path("/tmp/out"), True, True)]


def test_main_extracts_tgz_and_marks_output_writable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Extract tarball inputs, then normalize output permissions."""
    module = _load_module()
    output = tmp_path / "out"
    extracted: list[tuple[str, Path, Path]] = []
    writable_roots: list[Path] = []

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: module.argparse.Namespace(
            bsdtar="/bin/bsdtar",
            package=str(tmp_path / "pkg.tgz"),
            out=str(output),
        ),
    )
    monkeypatch.setattr(
        module,
        "extract_archive",
        lambda bsdtar, package, out: extracted.append((bsdtar, package, out)),
    )
    monkeypatch.setattr(
        module,
        "copy_directory",
        lambda *_args: pytest.fail("copy_directory should not be used for .tgz inputs"),
    )
    monkeypatch.setattr(module, "make_user_writable", writable_roots.append)

    assert module.main() == 0
    assert output.is_dir()
    assert extracted == [
        ("/bin/bsdtar", tmp_path / "pkg.tgz", output),
    ]
    assert writable_roots == [output]


def test_main_copies_directories_and_reports_user_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Copy unpacked directories and return a failing exit code on helper errors."""
    module = _load_module()
    output = tmp_path / "out"
    copied: list[tuple[Path, Path]] = []

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: module.argparse.Namespace(
            bsdtar="/bin/bsdtar",
            package=str(tmp_path / "pkg-dir"),
            out=str(output),
        ),
    )
    monkeypatch.setattr(
        module,
        "extract_archive",
        lambda *_args: pytest.fail(
            "extract_archive should not be used for directory inputs"
        ),
    )

    def _copy_directory(package: Path, out: Path) -> None:
        copied.append((package, out))
        raise module.ExtractBunPackageError("copy failed")

    monkeypatch.setattr(module, "copy_directory", _copy_directory)
    monkeypatch.setattr(
        module,
        "make_user_writable",
        lambda *_args: pytest.fail("make_user_writable should not run after failure"),
    )

    assert module.main() == 1
    assert copied == [(tmp_path / "pkg-dir", output)]
    assert capsys.readouterr().err == "copy failed\n"


def test_script_main_guard_exits_with_main_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Execute the file as a script so the __main__ guard runs."""
    source = tmp_path / "pkg-dir"
    output = tmp_path / "out"
    source.mkdir()
    (source / "package.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "extract-bun-package",
            "--bsdtar",
            "/bin/bsdtar",
            "--package",
            str(source),
            "--out",
            str(output),
        ],
    )

    with pytest.raises(SystemExit, match="0") as excinfo:
        runpy.run_path(
            str(REPO_ROOT / "packages/superset/extract_bun_package.py"),
            run_name="__main__",
        )

    assert excinfo.value.code == 0
    assert (output / "package.json").read_text(encoding="utf-8") == "{}\n"
