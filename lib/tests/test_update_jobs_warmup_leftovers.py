"""Keep best-effort warmup pushes scoped to locally available receipts."""

import subprocess
from io import StringIO
from unittest.mock import Mock

import pytest

from lib.update.ci import jobs


@pytest.mark.parametrize("all_missing", [False, True], ids=["partial", "all-missing"])
def test_warmup_leftovers_skip_missing_receipts_without_losing_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    all_missing: bool,
) -> None:
    missing = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-missing-warmup"
    first = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-first-warmup"
    second = "/nix/store/cccccccccccccccccccccccccccccccc-second-warmup"
    paths = (missing, first, second)
    present = () if all_missing else (first, second)
    monkeypatch.setattr(jobs, "_store_path_exists", lambda path: path in present)
    push = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(jobs, "_run", push)
    log = StringIO()

    jobs._push_warmup_leftovers(paths, log, jobs._FlushBudget(10_000.0))

    skipped = paths if all_missing else (missing,)
    diagnostic = "warmup leftovers missing locally; skipping push: " + ", ".join(
        skipped
    )
    assert diagnostic in log.getvalue()
    assert capsys.readouterr().err == log.getvalue()
    if all_missing:
        push.assert_not_called()
        assert "pushing " not in log.getvalue()
    else:
        push.assert_called_once_with(
            "cachix",
            "push",
            "gkze",
            *present,
            check=False,
            capture=True,
            timeout=jobs._CACHIX_PUSH_TIMEOUT_SECONDS,
        )
        assert "pushing 2 warmup leftovers without gkze fail-close" in log.getvalue()
        assert "cachix push warmup-leftovers returncode=0" in log.getvalue()
