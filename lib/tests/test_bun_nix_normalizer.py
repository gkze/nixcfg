"""Behavioral contracts for canonical bun2nix output normalization."""

import subprocess
from pathlib import Path

import pytest

from lib.bun_nix_normalizer import normalize_bun_nix, normalize_bun_nix_path
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._nix_source import nix_source_fragment_expr


def test_normalize_bun_nix_runs_deadnix_then_nixfmt(tmp_path: Path) -> None:
    """Generated Bun expressions should use the repository's canonical tool order."""
    calls: list[list[str]] = []

    def _runner(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        path = Path(args[-1])
        if args[0] == "deadnix":
            path.write_text(
                "{ fetchurl, ... }: { inherit fetchurl; }", encoding="utf-8"
            )
        else:
            path.write_text(
                "{ fetchurl, ... }:\n{\n  inherit fetchurl;\n}\n",
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    normalized = normalize_bun_nix(
        "{ fetchgit, fetchurl, ... }: { inherit fetchurl; }",
        runner=_runner,
    )

    assert normalized == "{ fetchurl, ... }:\n{\n  inherit fetchurl;\n}\n"
    assert len(calls) == 2
    assert calls[0][0:3] == ["deadnix", "--edit", "--quiet"]
    assert calls[1][0] == "nixfmt"
    assert calls[0][-1] == calls[1][-1]


def test_normalize_bun_nix_path_uses_the_default_subprocess_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production path should invoke both packaged formatter executables."""
    path = tmp_path / "bun.nix"
    path.write_text("{ fetchurl, ... }: { inherit fetchurl; }", encoding="utf-8")
    calls: list[list[str]] = []

    def _run(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _run)

    normalize_bun_nix_path(path)

    assert [args[0] for args in calls] == ["deadnix", "nixfmt"]


@pytest.mark.parametrize(
    ("stdout", "stderr", "message"),
    [
        ("", "bad syntax", "bad syntax"),
        ("stdout failure", "", "stdout failure"),
        ("", "", "exit 9"),
    ],
)
def test_normalize_bun_nix_reports_formatter_failures(
    tmp_path: Path,
    stdout: str,
    stderr: str,
    message: str,
) -> None:
    """A failed canonicalization command must stop artifact promotion."""
    path = tmp_path / "bun.nix"
    path.write_text("{}", encoding="utf-8")

    def _runner(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 9, stdout=stdout, stderr=stderr)

    with pytest.raises(RuntimeError, match=message):
        normalize_bun_nix_path(path, runner=_runner)


def test_normalize_bun_nix_rejects_a_missing_artifact(tmp_path: Path) -> None:
    """Normalization should fail closed when bun2nix omitted its output."""
    with pytest.raises(RuntimeError, match="not a regular file"):
        normalize_bun_nix_path(tmp_path / "missing.nix", runner=lambda *_a, **_k: None)


def test_nixcfg_runtime_includes_bun_nix_formatters() -> None:
    """Packaged updates must not depend on formatter tools from the user profile."""
    assert_nix_ast_equal(
        nix_source_fragment_expr(
            "packages/nixcfg.nix",
            "        lib.makeBinPath ",
            "\n      }",
        ),
        "[ deadnix flake-edit nix-prefetch-git nixfmt ]",
    )
