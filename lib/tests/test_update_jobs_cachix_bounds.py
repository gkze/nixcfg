"""Bound every Cachix call in flush and prove recovered paths are substitutable.

40048292663 warm-rust (4) sat 3h in ``cachix daemon stop``: cachix builds its
HTTP manager with ``responseTimeoutNone``, so nothing in flush may wait on it
without a deadline.
"""

import http.server
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from unittest.mock import Mock

import pytest

from lib.update.ci import jobs

_BUILT = "/nix/store/84hwiwgd4asz2jpyxp0w1lyhb8wkdg5d-rust_title_bar-0.1.0"
_FOD = "/nix/store/ph3arvv0hh8ll8yz4r6v6y4mpmhrk5qr-cargo-package-flate2-1.1.9"


def _budget(seconds: float = 10_000.0) -> jobs._FlushBudget:
    return jobs._FlushBudget(seconds)


def test_run_reports_a_killed_child_as_a_timeout() -> None:
    started = time.monotonic()
    result = jobs._run(
        sys.executable,
        "-c",
        "import sys, time; print('partial', flush=True); time.sleep(30)",
        check=False,
        capture=True,
        timeout=0.5,
    )
    assert time.monotonic() - started < 10
    assert result.returncode == jobs._TIMEOUT_RETURNCODE
    assert result.stdout == "partial\n"
    assert result.stderr == "timed out after 0s"
    with pytest.raises(subprocess.CalledProcessError) as error:
        jobs._run(sys.executable, "-c", "import time; time.sleep(30)", timeout=0.2)
    assert error.value.returncode == jobs._TIMEOUT_RETURNCODE


def test_timeout_text_decodes_partial_output() -> None:
    assert jobs._timeout_text(b"caf\xc3\xa9\xff") == "café\ufffd"
    assert jobs._timeout_text(None) == ""
    assert jobs._timeout_text("text") == "text"


def test_flush_budget_caps_each_call_and_fails_once_spent() -> None:
    now = [100.0]
    budget = jobs._FlushBudget(60.0, clock=lambda: now[0])
    assert budget.timeout(300.0) == 60.0
    assert budget.timeout(5.0) == 5.0
    now[0] = 150.0
    assert budget.timeout(300.0) == 10.0
    now[0] = 160.0
    with pytest.raises(jobs.CachixFlushError, match="budget exhausted"):
        budget.timeout(1.0)


def test_cachix_stop_timeout_defaults_and_accepts_a_floor_bounded_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NIXCFG_CACHIX_STOP_TIMEOUT_SECONDS", raising=False)
    assert jobs._cachix_stop_timeout_seconds() == jobs._CACHIX_STOP_TIMEOUT_SECONDS
    monkeypatch.setenv("NIXCFG_CACHIX_STOP_TIMEOUT_SECONDS", "20")
    assert jobs._cachix_stop_timeout_seconds() == 20.0
    monkeypatch.setenv("NIXCFG_CACHIX_STOP_TIMEOUT_SECONDS", "0")
    assert jobs._cachix_stop_timeout_seconds() == 1.0


def test_prefetched_push_is_killed_at_its_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cachix = tmp_path / "cachix"
    cachix.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
    cachix.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    log = StringIO()
    started = time.monotonic()
    with pytest.raises(subprocess.CalledProcessError) as error:
        jobs._push_prefetched_paths([_FOD], log, tmp_path, timeout=0.3)
    assert time.monotonic() - started < 10
    assert error.value.returncode == jobs._TIMEOUT_RETURNCODE
    assert "Cachix publication timed out" in log.getvalue()


def test_cachix_push_chunks_and_retries_until_one_chunk_exhausts_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs, "_CACHIX_PUSH_CHUNK", 2)
    outcomes = iter([0, jobs._TIMEOUT_RETURNCODE, 0, 1, 1, 1])
    run = Mock(
        side_effect=lambda *args, **_kwargs: subprocess.CompletedProcess(
            args, next(outcomes), "", "stalled"
        )
    )
    monkeypatch.setattr(jobs, "_run", run)
    paths = ("/nix/store/a", "/nix/store/b", "/nix/store/c", "/nix/store/d", "/e")
    log = StringIO()
    result = jobs._cachix_push(paths, "probe", log, _budget())
    assert result.returncode == 1
    chunks = [call.args[3:] for call in run.call_args_list]
    assert chunks == [
        ("/nix/store/a", "/nix/store/b"),
        ("/nix/store/c", "/nix/store/d"),
        ("/nix/store/c", "/nix/store/d"),
        ("/e",),
        ("/e",),
        ("/e",),
    ]
    assert all(
        call.kwargs["timeout"] == jobs._CACHIX_PUSH_TIMEOUT_SECONDS
        for call in run.call_args_list
    )
    assert "cachix push probe chunk=1 attempt=1/3 paths=2 returncode=124" in (
        log.getvalue()
    )
    assert "chunk=2 attempt=3/3 paths=1 returncode=1" in log.getvalue()
    assert jobs._cachix_push((), "empty", log, _budget()).returncode == 0


def test_ultimate_store_paths_keeps_only_locally_built_outputs() -> None:
    """Nix prints the path, padding, a tab, then ``ultimate``/``ca:``/sigs."""
    listing = (
        f"{_BUILT}     \tultimate\n"
        f"{_FOD}\tultimate ca:fixed:r:sha256:0abc\n"
        "/nix/store/2h2rzk1a5jbd9hljrdw2x8vhm204p9zf-cargo-src-subtle-2.6.1"
        "\tcache.nixos.org-1:c2ln\n"
        "/nix/store/flz0x6gyby15i19yb7ysdr02bsx3jf6q-rust_title_bar-0.1.0.drv"
        "\tultimate\n"
        "relative-path\tultimate\n"
        f"{_BUILT}\tultimate\n"
        "/nix/store/w8wgf78sv5hrd35iayk60lqglyplrvn5-title_bar\n"
        "\n"
    )
    assert jobs._ultimate_store_paths(listing) == (_BUILT, _FOD)


class _Narinfo(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:
        agent = self.headers.get("User-Agent", "")
        if agent != jobs._NARINFO_USER_AGENT:
            self.send_response(403)
        elif self.path.startswith("/present"):
            self.send_response(200)
        else:
            self.send_response(404)
        self.end_headers()

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture
def narinfo_server() -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Narinfo)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_narinfo_status_reports_http_codes_with_the_flush_user_agent(
    narinfo_server: str,
) -> None:
    assert jobs._narinfo_status(f"{narinfo_server}/present.narinfo", 5.0) == 200
    assert jobs._narinfo_status(f"{narinfo_server}/absent.narinfo", 5.0) == 404


def test_path_substitutable_requires_a_200_from_gkze_or_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(jobs.time, "sleep", sleeps.append)
    digest = jobs._ultimate_store_paths(f"{_FOD}\tultimate")[0][11:43]
    gkze = f"https://gkze.cachix.org/{digest}.narinfo"
    upstream = f"https://cache.nixos.org/{digest}.narinfo"

    def answers(table: dict[str, list[int | OSError]]) -> list[str]:
        asked: list[str] = []

        def status(url: str, timeout: float) -> int:
            assert timeout == jobs._NARINFO_TIMEOUT_SECONDS
            asked.append(url)
            answer = table[url].pop(0)
            if isinstance(answer, OSError):
                raise answer
            return answer

        monkeypatch.setattr(jobs, "_narinfo_status", status)
        return asked

    asked = answers({gkze: [200]})
    assert jobs._unsubstitutable_paths((_FOD,), _budget()) == ()
    assert asked == [gkze]
    asked = answers({gkze: [404], upstream: [200]})
    assert jobs._unsubstitutable_paths((_FOD,), _budget()) == ()
    assert asked == [gkze, upstream]
    asked = answers({gkze: [OSError("reset"), 403, 503], upstream: [404]})
    assert jobs._unsubstitutable_paths((_FOD,), _budget()) == (_FOD,)
    assert asked == [gkze, gkze, gkze, upstream]
    assert sleeps == [1, 2]


def test_recovery_with_no_locally_built_paths_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jobs,
        "_run",
        lambda *args, **_kwargs: subprocess.CompletedProcess(args, 0, "", ""),
    )
    log = StringIO()
    jobs._recover_undrained_daemon_paths(log, _budget())
    assert "republishing 0 locally built paths" in log.getvalue()


def _daemon_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pid: str) -> None:
    daemon_dir = tmp_path / "daemon"
    daemon_dir.mkdir(exist_ok=True)
    (daemon_dir / "daemon.pid").write_text(pid)
    monkeypatch.setenv("CACHIX_DAEMON_DIR", str(daemon_dir))


def test_terminate_cachix_daemon_skips_missing_or_foreign_pids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = StringIO()
    monkeypatch.delenv("CACHIX_DAEMON_DIR", raising=False)
    jobs._terminate_cachix_daemon(log)
    _daemon_pid(tmp_path, monkeypatch, "not-a-pid")
    jobs._terminate_cachix_daemon(log)
    assert log.getvalue().count("no readable daemon.pid") == 2
    with subprocess.Popen([
        sys.executable,
        "-c",
        "import time; time.sleep(30)",
    ]) as other:
        _daemon_pid(tmp_path, monkeypatch, f"{other.pid}\n")
        jobs._terminate_cachix_daemon(log)
        assert other.poll() is None
        other.kill()
    assert f"Cachix daemon pid={other.pid} already exited" in log.getvalue()


def test_terminate_cachix_daemon_kills_a_daemon_that_ignores_sigterm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs, "_CACHIX_DAEMON_EXIT_GRACE_SECONDS", 0.5)
    ready = tmp_path / "ready"
    with subprocess.Popen([  # noqa: S603 -- fixed test interpreter argv
        sys.executable,
        "-c",
        "import pathlib, signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "pathlib.Path(sys.argv[1]).write_text('')\n"
        "time.sleep(30)\n",
        str(ready),
        "cachix",
    ]) as daemon:
        while not ready.exists():
            time.sleep(0.01)
        _daemon_pid(tmp_path, monkeypatch, str(daemon.pid))
        reaper = threading.Thread(target=daemon.wait)
        reaper.start()
        log = StringIO()
        jobs._terminate_cachix_daemon(log)
        reaper.join(timeout=10)
    assert daemon.returncode == -signal.SIGKILL
    assert f"killed wedged Cachix daemon pid={daemon.pid}" in log.getvalue()


def test_terminate_cachix_daemon_reports_a_daemon_it_cannot_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _daemon_pid(tmp_path, monkeypatch, "4242")
    monkeypatch.setattr(
        jobs,
        "_run",
        lambda *args, **_kwargs: subprocess.CompletedProcess(
            args, 0, "cachix daemon run\n", ""
        ),
    )

    def deny(_pid: int, _signal: int) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(jobs.os, "kill", deny)
    log = StringIO()
    jobs._terminate_cachix_daemon(log)
    assert "cannot signal Cachix daemon pid=4242" in log.getvalue()
