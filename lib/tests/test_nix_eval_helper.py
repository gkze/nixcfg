"""Focused contracts for the shared test-only Nix evaluator."""

import subprocess

import pytest
from nix_manipulator.expressions.primitive import Primitive

from lib.tests import _nix_eval


def test_nix_eval_result_preserves_evaluator_traces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Callers that need evaluation traces should use the centralized boundary."""
    calls: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(
        _nix_eval.shutil,
        "which",
        lambda name: "/test/bin/nix-instantiate" if name == "nix-instantiate" else None,
    )

    def run(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"assertion":true}',
            stderr="trace: normalized-once\n",
        )

    result = _nix_eval.nix_eval_result(Primitive(value=True), raw=True, run=run)

    assert result.stdout == '{"assertion":true}'
    assert result.stderr == "trace: normalized-once\n"
    assert calls == [
        (
            [
                "/test/bin/nix-instantiate",
                "--eval",
                "--strict",
                "--raw",
                "--expr",
                "true",
            ],
            {
                "check": True,
                "capture_output": True,
                "cwd": _nix_eval.REPO_ROOT,
                "text": True,
                "timeout": _nix_eval._NIX_EVAL_TIMEOUT_SECONDS,
            },
        )
    ]


def test_nix_eval_result_falls_back_to_the_nix_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use `nix eval` when the legacy evaluator executable is unavailable."""
    monkeypatch.setattr(
        _nix_eval.shutil,
        "which",
        lambda name: "/test/bin/nix" if name == "nix" else None,
    )
    commands: list[list[str]] = []

    def run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="true", stderr="")

    _nix_eval.nix_eval_result(Primitive(value=True), raw=False, run=run)

    assert commands == [
        [
            "/test/bin/nix",
            "eval",
            "--impure",
            "--json",
            "--expr",
            "true",
        ]
    ]


def test_nix_eval_result_rejects_an_environment_without_nix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail before invoking a runner when neither evaluator executable exists."""
    monkeypatch.setattr(_nix_eval.shutil, "which", lambda _name: None)

    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        pytest.fail("runner must not be called")

    with pytest.raises(AssertionError):
        _nix_eval.nix_eval_result(
            Primitive(value=True),
            raw=True,
            run=run,
        )
