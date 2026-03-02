"""Additional tests for shared closure build helper."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

from lib.nix.commands.base import (
    CommandResult,
    NixCommandError,
    ProcessDone,
    ProcessLine,
)
from lib.tests._assertions import check
from lib.update.ci import build_shared_closure as bsc

if TYPE_CHECKING:
    import pytest


def test_parse_dry_run_derivations_covers_section_termination() -> None:
    """Extract only derivations from the 'will be built' section."""
    output = "\n".join([
        "warning: unrelated",
        "these 2 derivations will be built:",
        "  /nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv",
        "  /nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-b.drv",
        "",  # ends section
        "these 1 paths will be fetched",
        "  /nix/store/cccccccccccccccccccccccccccccccc-c.drv",
    ])
    drvs = object.__getattribute__(bsc, "_parse_dry_run_derivations")(output)
    check(
        drvs
        == {
            "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv",
            "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-b.drv",
        }
    )


def test_parse_dry_run_derivations_ends_on_non_indented_line() -> None:
    """Stop parsing when section is followed by a non-indented line."""
    output = "\n".join([
        "these 1 derivations will be built:",
        "  /nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv",
        "summary:",
        "  /nix/store/ignored.drv",
    ])
    drvs = object.__getattribute__(bsc, "_parse_dry_run_derivations")(output)
    check(drvs == {"/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv"})

    stop_immediately = "\n".join([
        "these 1 derivations will be built:",
        "summary line",
    ])
    check(
        object.__getattribute__(bsc, "_parse_dry_run_derivations")(stop_immediately)
        == set()
    )

    keep_section = "\n".join([
        "these 1 derivations will be built:",
        "  just-a-log-line",
        "  /nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv",
    ])
    drvs2 = object.__getattribute__(bsc, "_parse_dry_run_derivations")(keep_section)
    check(drvs2 == {"/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv"})


def test_parse_internal_json_line_and_emit_stream_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Handle valid/invalid internal-json lines and output forwarding."""
    parse = object.__getattribute__(bsc, "_parse_internal_json_line")
    check(parse("plain log") is None)
    check(parse("@nix [1,2,3]") is None)
    check(parse("@nix {not-json}") is None)
    check(parse('@nix {"action":"start"}') == {"action": "start"})

    emit = object.__getattribute__(bsc, "_emit_stream_line")
    emit(ProcessLine(stream="stdout", text="out\n"))
    emit(ProcessLine(stream="stderr", text="err\n"))
    captured = capsys.readouterr()
    check(captured.out == "out\n")
    check(captured.err == "err\n")


def test_build_profiler_ingest_line_and_finalize_defensive_paths() -> None:
    """Exercise ingest early-return branches and finalize interrupted builds."""
    profiler = bsc.BuildProfiler()
    profiler.ingest_line("not-json", now=10.0)
    profiler.ingest_line('@nix {"action":"start","id":"bad","text":"x"}', now=10.0)
    profiler.ingest_line('@nix {"action":"start","id":1,"text":"not a drv"}', now=10.0)
    profiler.ingest_line('@nix {"action":"progress","id":1}', now=10.0)
    profiler.ingest_line('@nix {"action":"stop","id":"bad"}', now=10.0)
    profiler.ingest_line(
        '@nix {"action":"start","id":2,"text":"building '
        "'/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv'" + '"}',
        now=10.0,
    )
    profiler.finalize(now=11.0)
    check(len(profiler.events) == 1)
    check(profiler.events[0].completed is False)


def test_realise_batch_with_profiling_missing_terminal_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise when stream ends without ProcessDone event."""

    async def _stream(*_args: object, **_kwargs: object):
        yield ProcessLine(stream="stderr", text='@nix {"action":"stop","id":1}\n')

    monkeypatch.setattr(bsc, "stream_process", _stream)

    try:
        asyncio.run(
            object.__getattribute__(bsc, "_realise_batch_with_profiling")(
                ["/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv"],
                profiler=bsc.BuildProfiler(),
            )
        )
    except RuntimeError as exc:
        check("without a terminal result" in str(exc))
    else:
        raise AssertionError("expected RuntimeError")


def test_combine_derivation_sets_union_intersection_and_empty() -> None:
    """Combine derivation sets according to selected mode."""
    combine = object.__getattribute__(bsc, "_combine_derivation_sets")
    check(combine([], mode="union") == set())
    check(
        combine(
            [
                {"a", "b"},
                {"b", "c"},
            ],
            mode="union",
        )
        == {"a", "b", "c"}
    )
    check(
        combine(
            [
                {"a", "b"},
                {"b", "c"},
            ],
            mode="intersection",
        )
        == {"b"}
    )


def test_supported_kwargs_filters_to_signature() -> None:
    """Only pass keyword args accepted by callee signature."""

    def _func(a: int, *, keep: str) -> None:
        _ = a
        _ = keep

    filtered = object.__getattribute__(bsc, "_supported_kwargs")(
        _func,
        {"a": 1, "keep": "ok", "drop": True},
    )
    check(filtered == {"a": 1, "keep": "ok"})

    class _NoSig:
        __signature__ = None

    passthrough = object.__getattribute__(bsc, "_supported_kwargs")(
        _NoSig(),
        {"x": 1},
    )
    check(passthrough == {"x": 1})


def test_stream_nix_build_dry_run_timeout_and_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Convert timeout and failed command into expected exceptions."""

    async def _timeout(*_args: object, **_kwargs: object):
        raise TimeoutError
        yield ProcessLine(stream="stdout", text="unreachable")

    monkeypatch.setattr(bsc, "stream_process", _timeout)
    try:
        asyncio.run(
            object.__getattribute__(bsc, "_stream_nix_build_dry_run")(
                ".#demo",
                nix_verbosity=0,
            )
        )
    except NixCommandError as exc:
        check("timed out" in str(exc))
    else:
        raise AssertionError("expected NixCommandError")


def test_stream_nix_build_dry_run_success_and_missing_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parse streamed dry-run output and handle missing terminal event."""

    async def _ok_stream(*_args: object, **_kwargs: object):
        yield ProcessLine(stream="stdout", text="these 1 derivations will be built:\n")
        yield ProcessLine(
            stream="stdout",
            text="  /nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv\n",
        )
        yield ProcessDone(
            result=CommandResult(
                args=["nix"],
                returncode=0,
                stdout=(
                    "these 1 derivations will be built:\n"
                    "  /nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv\n"
                ),
                stderr="",
            )
        )

    monkeypatch.setattr(bsc, "stream_process", _ok_stream)
    drvs = asyncio.run(
        object.__getattribute__(bsc, "_stream_nix_build_dry_run")(
            ".#demo",
            nix_verbosity=0,
        )
    )
    check("/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv" in drvs)

    async def _missing_done(*_args: object, **_kwargs: object):
        yield ProcessLine(stream="stdout", text="just logs\n")

    monkeypatch.setattr(bsc, "stream_process", _missing_done)
    try:
        asyncio.run(
            object.__getattribute__(bsc, "_stream_nix_build_dry_run")(
                ".#demo",
                nix_verbosity=0,
            )
        )
    except RuntimeError as exc:
        check("without a terminal result" in str(exc))
    else:
        raise AssertionError("expected RuntimeError")

    async def _nonzero(*_args: object, **_kwargs: object):
        yield ProcessDone(
            result=CommandResult(args=["nix"], returncode=1, stdout="", stderr="bad")
        )

    monkeypatch.setattr(bsc, "stream_process", _nonzero)
    try:
        asyncio.run(
            object.__getattribute__(bsc, "_stream_nix_build_dry_run")(
                ".#demo",
                nix_verbosity=0,
            )
        )
    except NixCommandError:
        pass
    else:
        raise AssertionError("expected NixCommandError")


def test_write_profile_report_and_summary_logging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persist profile report and log compact summary branches."""
    profiler = bsc.BuildProfiler()
    profiler.events.extend([
        bsc.BuildProfileEvent(
            derivation="/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv",
            duration_seconds=3.2,
            completed=True,
        ),
        bsc.BuildProfileEvent(
            derivation="/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-b.drv",
            duration_seconds=1.1,
            completed=False,
        ),
    ])

    out = tmp_path / "report.json"
    object.__getattribute__(bsc, "_write_profile_report")(
        output_path=out,
        flake_refs=[".#a"],
        derivation_count=2,
        profiler=profiler,
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    check(payload["requested_derivations"] == 2)
    check(payload["completed_derivations"] == 1)
    check(payload["interrupted_derivations"] == 1)
    check(len(payload["all_derivations"]) == 2)

    messages: list[str] = []
    monkeypatch.setattr(bsc.log, "info", lambda msg, *args: messages.append(msg % args))
    object.__getattribute__(bsc, "_log_profile_summary")([])
    check(any("No derivation build events" in msg for msg in messages))


def test_log_profile_summary_nonempty_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Log summary details for completed and interrupted rows."""
    rows = [
        {
            "derivation": "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv",
            "name": "a",
            "seconds": 1.0,
            "occurrences": 1,
            "completed": True,
        },
        {
            "derivation": "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-b.drv",
            "name": "b",
            "seconds": 2.0,
            "occurrences": 1,
            "completed": False,
        },
    ]
    seen: list[str] = []
    monkeypatch.setattr(bsc.log, "info", lambda msg, *args: seen.append(msg % args))
    object.__getattribute__(bsc, "_log_profile_summary")(rows)
    check(any("Profiled 2 derivation(s)" in msg for msg in seen))
    check(any("interrupted" in msg for msg in seen))


def test_realise_batch_with_profiling_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Collect terminal result and profiler events from streamed batch."""
    profiler = bsc.BuildProfiler()

    async def _stream(*_args: object, **_kwargs: object):
        yield ProcessLine(
            stream="stderr",
            text=(
                '@nix {"action":"start","id":1,"text":"building '
                "'/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv'" + '"}\n'
            ),
        )
        yield ProcessLine(stream="stderr", text='@nix {"action":"stop","id":1}\n')
        yield ProcessDone(
            result=CommandResult(args=["nix-store"], returncode=0, stdout="", stderr="")
        )

    monkeypatch.setattr(bsc, "stream_process", _stream)
    result = asyncio.run(
        object.__getattribute__(bsc, "_realise_batch_with_profiling")(
            ["/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv"],
            profiler=profiler,
        )
    )
    check(result.returncode == 0)
    check(len(profiler.events) == 1)


def test_realise_batch_with_profiling_stdout_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handle stdout stream lines without feeding profiler ingest."""
    profiler = bsc.BuildProfiler()

    async def _stream(*_args: object, **_kwargs: object):
        yield ProcessLine(stream="stdout", text="building...\n")
        yield ProcessDone(
            result=CommandResult(args=["nix-store"], returncode=0, stdout="", stderr="")
        )

    monkeypatch.setattr(bsc, "stream_process", _stream)
    result = asyncio.run(
        object.__getattribute__(bsc, "_realise_batch_with_profiling")(
            ["/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv"],
            profiler=profiler,
        )
    )
    check(result.returncode == 0)
    check(profiler.events == [])


def test_build_derivations_uses_run_nix_when_verbose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use run_nix path when nix verbosity forwarding is enabled."""
    captured: dict[str, object] = {}

    async def _run_nix(args: list[str], **kwargs: object) -> CommandResult:
        captured["args"] = list(args)
        captured["kwargs"] = kwargs
        return CommandResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bsc, "run_nix", _run_nix)

    ok = asyncio.run(
        object.__getattribute__(bsc, "_build_derivations")(
            {"/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv"},
            nix_verbosity=1,
        )
    )
    check(ok is True)
    args = captured["args"]
    if not isinstance(args, list):
        raise AssertionError("expected list args")
    check("nix-store" in args)
    check("-v" in args)


def test_build_derivations_uses_profiler_and_async_main_profile_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use profiler build path and write profile report in async main."""
    called: dict[str, object] = {}

    async def _collect(_refs: list[str], **_kwargs: object) -> set[str]:
        return {"/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv"}

    async def _build(_drvs: set[str], **kwargs: object) -> bool:
        called["profiler"] = kwargs.get("profiler")
        return True

    monkeypatch.setattr(bsc, "_collect_derivations", _collect)
    monkeypatch.setattr(bsc, "_build_derivations", _build)
    monkeypatch.setattr(
        bsc, "_log_profile_summary", lambda _events: called.setdefault("logged", True)
    )
    monkeypatch.setattr(
        bsc,
        "_write_profile_report",
        lambda **kwargs: called.setdefault("output_path", kwargs.get("output_path")),
    )

    rc = asyncio.run(
        object.__getattribute__(bsc, "_async_main")(
            flake_refs=[".#a"],
            dry_run=False,
            profile_output=Path("/tmp/profile.json"),
        )
    )
    check(rc == 0)
    check(called.get("profiler") is not None)
    check(called.get("logged") is True)


def test_build_derivations_profiler_path_invokes_realise_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute profiler branch in _build_derivations."""
    calls: list[list[str]] = []

    async def _realise(batch: list[str], *, profiler: object, nix_verbosity: int = 0):
        _ = (profiler, nix_verbosity)
        calls.append(batch)
        return CommandResult(args=["nix-store"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bsc, "_realise_batch_with_profiling", _realise)
    ok = asyncio.run(
        object.__getattribute__(bsc, "_build_derivations")(
            {"/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv"},
            profiler=bsc.BuildProfiler(),
        )
    )
    check(ok is True)
    check(len(calls) == 1)


def test_eval_one_uses_stream_mode_when_verbose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route _eval_one through streamed dry-run path with nix verbosity."""
    seen: dict[str, object] = {}

    async def _stream(ref: str, *, nix_verbosity: int) -> set[str]:
        seen["ref"] = ref
        seen["nix_verbosity"] = nix_verbosity
        return {"/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv"}

    monkeypatch.setattr(bsc, "_stream_nix_build_dry_run", _stream)
    drvs = asyncio.run(
        object.__getattribute__(bsc, "_eval_one")(".#x", nix_verbosity=1)
    )
    check(seen == {"ref": ".#x", "nix_verbosity": 1})
    check(drvs == {"/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv"})


def test_aggregate_profile_events_accumulates_same_derivation() -> None:
    """Aggregate multiple events for the same derivation key."""
    rows = object.__getattribute__(bsc, "_aggregate_profile_events")([
        bsc.BuildProfileEvent(
            derivation="/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv",
            duration_seconds=1.2,
            completed=True,
        ),
        bsc.BuildProfileEvent(
            derivation="/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-a.drv",
            duration_seconds=0.8,
            completed=False,
        ),
    ])
    check(len(rows) == 1)
    check(rows[0]["seconds"] == 2.0)
    check(rows[0]["occurrences"] == 2)
    check(rows[0]["completed"] is False)


def test_supported_kwargs_signature_error_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return copied kwargs when inspect.signature cannot introspect callable."""

    class _Callable:
        def __call__(self, **_kwargs: object) -> None:
            return None

    monkeypatch.setattr(
        bsc.inspect, "signature", lambda _f: (_ for _ in ()).throw(ValueError("nope"))
    )
    kwargs = {"x": 1}
    filtered = object.__getattribute__(bsc, "_supported_kwargs")(_Callable(), kwargs)
    check(filtered == kwargs)
