"""Tests for deno dependency hash computation helpers."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._assertions import check
from lib.update.events import CommandResult, UpdateEvent, UpdateEventKind
from lib.update.nix_deno import (
    _build_deno_deps_expr,
    _build_deno_hash_entries,
    _build_deno_temp_entry,
    _build_source_override_env,
    _compute_deno_deps_hash_for_platform,
    _existing_platform_hashes,
    _process_platform_hash,
    _try_platform_hash_event,
    compute_deno_deps_hash,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _collect(stream: AsyncIterator[UpdateEvent]) -> list[UpdateEvent]:
    async def _run() -> list[UpdateEvent]:
        items: list[UpdateEvent] = []
        async for item in stream:
            items.append(item)
        return items

    return asyncio.run(_run())


def test_hash_entry_builders_and_payload_helpers() -> None:
    """Build temporary per-platform hash entries and parse value payloads."""
    entries = _build_deno_hash_entries(
        platforms=("x86_64-linux", "aarch64-darwin"),
        active_platform="x86_64-linux",
        existing_hashes={"aarch64-darwin": "sha256-old"},
        computed_hashes={},
        fake_hash="sha256-fake",
    )
    check(entries[0].platform == "x86_64-linux")
    check(entries[0].hash == "sha256-fake")
    check(entries[1].hash == "sha256-old")

    original = SourceEntry(hashes={"x86_64-linux": "sha256-original"}, input="input")
    temp = _build_deno_temp_entry(
        input_name="new-input",
        original_entry=original,
        entries=entries,
    )
    check(temp.input == "new-input")
    check(isinstance(temp.hashes, HashCollection))

    temp_new = _build_deno_temp_entry(
        input_name="new-input", original_entry=None, entries=entries
    )
    check(temp_new.input == "new-input")

    value_event = UpdateEvent.value("demo", ("x86_64-linux", "sha256-abc"))
    check(_try_platform_hash_event(value_event) == ("x86_64-linux", "sha256-abc"))
    check(_try_platform_hash_event(UpdateEvent.status("demo", "x")) is None)
    check(_try_platform_hash_event(UpdateEvent.value("demo", ("x", 1))) is None)
    check(_try_platform_hash_event(UpdateEvent.value("demo", ("x", "y", "z"))) is None)

    env = _build_source_override_env("demo", temp_new)
    payload = json.loads(env["UPDATE_SOURCE_OVERRIDES_JSON"])
    check("demo" in payload)


def test_build_deno_deps_expr_delegates_to_overlay_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build expression by forwarding source/system to overlay helper."""
    calls: list[tuple[str, str]] = []

    def _fake_build_overlay_expr(source: str, *, system: str) -> str:
        calls.append((source, system))
        return f"expr:{source}:{system}"

    monkeypatch.setattr(
        "lib.update.nix_deno._build_overlay_expr", _fake_build_overlay_expr
    )
    expr = _build_deno_deps_expr("demo", "x86_64-linux")
    check(expr == "expr:demo:x86_64-linux")
    check(calls == [("demo", "x86_64-linux")])


def test_compute_deno_deps_hash_for_platform_emits_value_and_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capture build/hash drains and surface missing-hash failures."""

    async def _fixed_output_build(
        *_args: object, **_kwargs: object
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.status("demo", "building")
        yield UpdateEvent.value(
            "demo",
            CommandResult(
                args=["nix"], returncode=1, stdout="", stderr="hash mismatch"
            ),
        )

    async def _emit_sri(
        *_args: object, **_kwargs: object
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.status("demo", "converting")
        yield UpdateEvent.value(
            "demo",
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        )

    monkeypatch.setattr(
        "lib.update.nix_deno._run_fixed_output_build", _fixed_output_build
    )
    monkeypatch.setattr(
        "lib.update.nix_deno._emit_sri_hash_from_build_result", _emit_sri
    )

    events = _collect(
        _compute_deno_deps_hash_for_platform(
            "demo",
            "input",
            "x86_64-linux",
        )
    )
    check(len(events) == 3)
    check(events[0].kind == UpdateEventKind.STATUS)
    check(events[1].kind == UpdateEventKind.STATUS)
    check(events[2].kind == UpdateEventKind.VALUE)
    check(
        events[2].payload
        == ("x86_64-linux", "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    )

    async def _emit_none(
        *_args: object, **_kwargs: object
    ) -> AsyncIterator[UpdateEvent]:
        if False:
            yield UpdateEvent.status("demo", "never")
        return

    monkeypatch.setattr(
        "lib.update.nix_deno._emit_sri_hash_from_build_result", _emit_none
    )
    with pytest.raises(RuntimeError, match="Hash conversion failed"):
        _collect(
            _compute_deno_deps_hash_for_platform(
                "demo",
                "input",
                "x86_64-linux",
            )
        )


def test_existing_platform_hashes_from_entry_variants() -> None:
    """Read platform hashes from both list and mapping source formats."""
    entries = [
        HashEntry.create("denoDepsHash", "sha256-a", platform="x86_64-linux"),
        HashEntry.create("denoDepsHash", "sha256-b", platform=None),
    ]
    from_entries = SourceEntry(hashes=HashCollection(entries=entries))
    check(_existing_platform_hashes(from_entries) == {"x86_64-linux": "sha256-a"})

    from_mapping = SourceEntry(hashes={"aarch64-darwin": "sha256-c"})
    check(_existing_platform_hashes(from_mapping) == {"aarch64-darwin": "sha256-c"})
    empty_collection = SourceEntry(hashes=HashCollection(entries=[]))
    check(_existing_platform_hashes(empty_collection) == {})
    check(_existing_platform_hashes(None) == {})


def test_process_platform_hash_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Handle success and failure modes while computing platform hashes."""

    async def _compute_ok(
        _source: str,
        _input_name: str,
        platform: str,
        *,
        env: dict[str, str],
        config: object,
    ) -> AsyncIterator[UpdateEvent]:
        _ = (env, config)
        yield UpdateEvent.status("demo", f"build {platform}")
        hash_value = (
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
            if platform == "x86_64-linux"
            else "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
        )
        yield UpdateEvent.value("demo", (platform, hash_value))

    monkeypatch.setattr(
        "lib.update.nix_deno._compute_deno_deps_hash_for_platform", _compute_ok
    )

    context = type("_Context", (), {})()
    context.source = "demo"
    context.input_name = "input"
    context.platforms = ("x86_64-linux", "aarch64-darwin")
    context.current_platform = "x86_64-linux"
    context.original_entry = SourceEntry(hashes={})
    context.existing_hashes = {"aarch64-darwin": "sha256-existing"}
    context.platform_hashes = {}
    context.failed_platforms = []
    context.config = type("_Cfg", (), {"fake_hash": "sha256-fake"})()

    success_events = _collect(_process_platform_hash("x86_64-linux", context=context))
    check(
        any(
            (event.message or "").startswith("Computing hash")
            for event in success_events
        )
    )
    check(
        context.platform_hashes["x86_64-linux"]
        == "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    )

    async def _compute_fail(
        *_args: object, **_kwargs: object
    ) -> AsyncIterator[UpdateEvent]:
        msg = "boom"
        raise RuntimeError(msg)
        yield UpdateEvent.status("never", "never")

    monkeypatch.setattr(
        "lib.update.nix_deno._compute_deno_deps_hash_for_platform", _compute_fail
    )

    with pytest.raises(RuntimeError):
        _collect(_process_platform_hash("x86_64-linux", context=context))

    recovered_events = _collect(
        _process_platform_hash("aarch64-darwin", context=context)
    )
    check("aarch64-darwin" in context.failed_platforms)
    check(context.platform_hashes["aarch64-darwin"] == "sha256-existing")
    check(
        any(
            "preserving existing hash" in (event.message or "")
            for event in recovered_events
        )
    )

    context.existing_hashes = {}
    context.failed_platforms = []
    no_existing_events = _collect(
        _process_platform_hash("aarch64-darwin", context=context)
    )
    check(
        any("no existing hash" in (event.message or "") for event in no_existing_events)
    )


def test_compute_deno_deps_hash_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate platform guards, source lookup, and final hash emission."""
    monkeypatch.setattr(
        "lib.update.nix_deno.get_current_nix_platform", lambda: "x86_64-linux"
    )
    monkeypatch.setattr(
        "lib.update.nix_deno.resolve_active_config",
        lambda cfg: (
            cfg
            or type(
                "_Cfg",
                (),
                {
                    "deno_deps_platforms": ("x86_64-linux", "aarch64-darwin"),
                    "fake_hash": "sha256-fake",
                },
            )()
        ),
    )

    # unsupported current platform
    monkeypatch.setattr(
        "lib.update.nix_deno.get_current_nix_platform", lambda: "arm-linux"
    )
    with pytest.raises(RuntimeError, match="not in supported platforms"):
        _collect(compute_deno_deps_hash("demo", "input"))

    monkeypatch.setattr(
        "lib.update.nix_deno.get_current_nix_platform", lambda: "x86_64-linux"
    )
    monkeypatch.setattr("lib.update.nix_deno.sources_file_for", lambda _name: None)
    with pytest.raises(RuntimeError, match="No sources.json found"):
        _collect(compute_deno_deps_hash("demo", "input"))

    monkeypatch.setattr(
        "lib.update.nix_deno.sources_file_for", lambda _name: "dummy-path"
    )
    monkeypatch.setattr(
        "lib.update.nix_deno.load_source_entry",
        lambda _path: SourceEntry(hashes={"aarch64-darwin": "sha256-existing"}),
    )

    async def _process(
        platform_name: str, *, context: object
    ) -> AsyncIterator[UpdateEvent]:
        context.platform_hashes[platform_name] = f"sha256-{platform_name}"
        if platform_name == "aarch64-darwin":
            context.failed_platforms.append(platform_name)
        yield UpdateEvent.status("demo", f"processed {platform_name}")

    monkeypatch.setattr("lib.update.nix_deno._process_platform_hash", _process)

    events = _collect(compute_deno_deps_hash("demo", "input", native_only=False))
    check(any("Warning:" in (event.message or "") for event in events))
    final = events[-1]
    check(final.kind == UpdateEventKind.VALUE)
    payload = final.payload
    check(isinstance(payload, dict))
    check(payload["x86_64-linux"] == "sha256-x86_64-linux")

    native_events = _collect(compute_deno_deps_hash("demo", "input", native_only=True))
    native_payload = native_events[-1].payload
    check(isinstance(native_payload, dict))
    check("x86_64-linux" in native_payload)
