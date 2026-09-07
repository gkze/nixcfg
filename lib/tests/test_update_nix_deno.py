"""Tests for deno dependency hash computation helpers."""

import asyncio

import pytest

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._updater_helpers import collect_events
from lib.update.config import resolve_config
from lib.update.events import (
    CommandResult,
    EventSink,
    StatusInfo,
    StatusKind,
    StatusPayload,
    UpdateEvent,
    UpdateEventKind,
    ignore_event,
)
from lib.update.nix_deno import (
    _build_deno_deps_expr,
    _build_deno_hash_entries,
    _build_deno_temp_entry,
    _compute_deno_deps_hash_for_platform,
    _existing_platform_hashes,
    _PlatformHashContext,
    _process_platform_hash,
    compute_deno_deps_hash,
)
from lib.update.platform_hashes import PreservedPlatformHash


def _collect(operation):
    return asyncio.run(collect_events(operation))


def test_hash_entry_builders_and_payload_helpers() -> None:
    """Build temporary per-platform hash entries and parse value payloads."""
    entries = _build_deno_hash_entries(
        platforms=("x86_64-linux", "aarch64-darwin"),
        active_platform="x86_64-linux",
        existing_hashes={"aarch64-darwin": "sha256-old"},
        computed_hashes={},
        fake_hash="sha256-fake",
    )
    assert entries[0].platform == "x86_64-linux"
    assert entries[0].hash == "sha256-fake"
    assert entries[1].hash == "sha256-old"

    original = SourceEntry(hashes={"x86_64-linux": "sha256-original"}, input="input")
    temp = _build_deno_temp_entry(
        input_name="new-input",
        original_entry=original,
        entries=entries,
    )
    assert temp.input == "new-input"
    assert isinstance(temp.hashes, HashCollection)

    temp_new = _build_deno_temp_entry(
        input_name="new-input", original_entry=None, entries=entries
    )
    assert temp_new.input == "new-input"


def test_build_deno_deps_expr_delegates_to_overlay_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build expression by forwarding source/system to overlay helper."""
    calls: list[tuple[str, str, object]] = []

    def _fake_build_overlay_expr(
        source: str,
        *,
        system: str,
        source_overrides: object,
    ) -> str:
        calls.append((source, system, source_overrides))
        return f"expr:{source}:{system}"

    monkeypatch.setattr(
        "lib.update.nix_deno._build_overlay_expr", _fake_build_overlay_expr
    )
    override = SourceEntry(version="2.0.0", hashes={})
    expr = _build_deno_deps_expr("demo", "x86_64-linux", override)
    assert expr == "expr:demo:x86_64-linux"
    assert calls == [("demo", "x86_64-linux", {"demo": override})]


def test_compute_deno_deps_hash_for_platform_emits_value_and_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capture build/hash drains and surface missing-hash failures."""

    async def _fixed_output_build(
        *_args: object, emit: EventSink = ignore_event, **_kwargs: object
    ) -> object:
        await emit(UpdateEvent.status("demo", "building"))
        return CommandResult(
            args=["nix"], returncode=1, stdout="", stderr="hash mismatch"
        )

    async def _emit_sri(
        *_args: object, emit: EventSink = ignore_event, **_kwargs: object
    ) -> object:
        await emit(UpdateEvent.status("demo", "converting"))
        return "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

    monkeypatch.setattr(
        "lib.update.nix_deno._run_fixed_output_build", _fixed_output_build
    )
    monkeypatch.setattr(
        "lib.update.nix_deno._emit_sri_hash_from_build_result", _emit_sri
    )

    events = _collect(
        lambda emit: _compute_deno_deps_hash_for_platform(
            "demo", "input", "x86_64-linux", emit=emit
        )
    )
    assert len(events) == 2
    assert events[0].kind == UpdateEventKind.STATUS
    assert events[1].kind == UpdateEventKind.STATUS
    assert events.result == (
        "x86_64-linux",
        "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    )


def test_existing_platform_hashes_from_entry_variants() -> None:
    """Read platform hashes from both list and mapping source formats."""
    entries = [
        HashEntry.create("denoDepsHash", "sha256-a", platform="x86_64-linux"),
        HashEntry.create("denoDepsHash", "sha256-b", platform=None),
    ]
    from_entries = SourceEntry(hashes=HashCollection(entries=entries))
    assert _existing_platform_hashes(from_entries) == {"x86_64-linux": "sha256-a"}

    from_mapping = SourceEntry(hashes={"aarch64-darwin": "sha256-c"})
    assert _existing_platform_hashes(from_mapping) == {"aarch64-darwin": "sha256-c"}
    empty_collection = SourceEntry(hashes=HashCollection(entries=[]))
    assert _existing_platform_hashes(empty_collection) == {}
    assert _existing_platform_hashes(None) == {}


def test_process_platform_hash_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Handle success and failure modes while computing platform hashes."""

    async def _compute_ok(
        _source: str,
        _input_name: str,
        platform: str,
        *,
        source_override: SourceEntry,
        config: object,
        emit: EventSink = ignore_event,
    ) -> object:
        assert source_override.input == "input"
        _ = config
        await emit(UpdateEvent.status("demo", f"build {platform}"))
        hash_value = (
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
            if platform == "x86_64-linux"
            else "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
        )
        return (platform, hash_value)

    monkeypatch.setattr(
        "lib.update.nix_deno._compute_deno_deps_hash_for_platform", _compute_ok
    )

    context = _PlatformHashContext(
        source="demo",
        input_name="input",
        platforms=("x86_64-linux", "aarch64-darwin"),
        current_platform="x86_64-linux",
        original_entry=SourceEntry(hashes={}),
        existing_hashes={"aarch64-darwin": "sha256-existing"},
        platform_hashes={},
        failed_platforms=[],
        config=resolve_config(fake_hash="sha256-fake"),
    )

    success_events = _collect(
        lambda emit: _process_platform_hash("x86_64-linux", context=context, emit=emit)
    )
    assert any(
        (event.message or "").startswith("Computing hash") for event in success_events
    )
    assert (
        context.platform_hashes["x86_64-linux"]
        == "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    )

    async def _compute_fail(
        *_args: object, emit: EventSink = ignore_event, **_kwargs: object
    ) -> object:
        msg = "boom"
        raise RuntimeError(msg)
        await emit(UpdateEvent.status("never", "never"))

    monkeypatch.setattr(
        "lib.update.nix_deno._compute_deno_deps_hash_for_platform", _compute_fail
    )

    with pytest.raises(RuntimeError):
        _collect(
            lambda emit: _process_platform_hash(
                "x86_64-linux", context=context, emit=emit
            )
        )

    recovered_events = _collect(
        lambda emit: _process_platform_hash(
            "aarch64-darwin", context=context, emit=emit
        )
    )
    assert [failure.platform for failure in context.failed_platforms] == [
        "aarch64-darwin"
    ]
    assert context.platform_hashes["aarch64-darwin"] == "sha256-existing"
    assert any(
        "preserving existing hash" in (event.message or "")
        for event in recovered_events
    )
    context.existing_hashes = {}
    context.failed_platforms = []
    with pytest.raises(RuntimeError, match="no existing hash is available"):
        _collect(
            lambda emit: _process_platform_hash(
                "aarch64-darwin", context=context, emit=emit
            )
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
                    "hash_build_platforms": ("x86_64-linux", "aarch64-darwin"),
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
        _collect(lambda emit: compute_deno_deps_hash("demo", "input", emit=emit))

    monkeypatch.setattr(
        "lib.update.nix_deno.get_current_nix_platform", lambda: "x86_64-linux"
    )
    monkeypatch.setattr("lib.update.nix_deno.sources_file_for", lambda _name: None)
    with pytest.raises(RuntimeError, match="No sources.json found"):
        _collect(lambda emit: compute_deno_deps_hash("demo", "input", emit=emit))

    monkeypatch.setattr(
        "lib.update.nix_deno.sources_file_for", lambda _name: "dummy-path"
    )
    monkeypatch.setattr(
        "lib.update.nix_deno.load_source_entry",
        lambda _path: SourceEntry(hashes={"aarch64-darwin": "sha256-existing"}),
    )

    async def _process(
        platform_name: str,
        *,
        context: _PlatformHashContext,
        emit: EventSink = ignore_event,
    ) -> object:
        context.platform_hashes[platform_name] = f"sha256-{platform_name}"
        if platform_name == "aarch64-darwin":
            context.failed_platforms.append(
                PreservedPlatformHash(
                    platform=platform_name,
                    hash="sha256-existing",
                    error="boom",
                )
            )
        await emit(UpdateEvent.status("demo", f"processed {platform_name}"))

    monkeypatch.setattr("lib.update.nix_deno._process_platform_hash", _process)

    events = _collect(
        lambda emit: compute_deno_deps_hash(
            "demo", "input", native_only=False, emit=emit
        )
    )
    warning_events = [event for event in events if "Warning:" in (event.message or "")]
    assert len(warning_events) == 1
    assert warning_events[0].message == (
        "Warning: 1 platform(s) failed, preserved existing hashes for: aarch64-darwin"
    )
    assert warning_events[0].payload == StatusPayload(
        operation="compute_hash",
        info=StatusInfo(kind=StatusKind.PARTIAL_HASHES, value="aarch64-darwin"),
    )
    assert events.result.fully_computed is False
    payload = events.result.hashes
    assert isinstance(payload, dict)
    assert payload["x86_64-linux"] == "sha256-x86_64-linux"

    native_events = _collect(
        lambda emit: compute_deno_deps_hash(
            "demo", "input", native_only=True, emit=emit
        )
    )
    native_payload = native_events.result.hashes
    assert isinstance(native_payload, dict)
    assert "x86_64-linux" in native_payload


def test_compute_deno_deps_hash_uses_candidate_override_without_disk_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Candidate metadata must replace the checked-in source during every probe."""
    candidate = SourceEntry(
        version="2.0.0",
        input="candidate-input",
        hashes={"x86_64-linux": "sha256-existing"},
        pins={"runtimeVersion": "2.0.0"},
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "lib.update.nix_deno.get_current_nix_platform",
        lambda: "x86_64-linux",
    )
    monkeypatch.setattr(
        "lib.update.nix_deno.resolve_active_config",
        lambda _config: type(
            "_Cfg",
            (),
            {
                "hash_build_platforms": ("x86_64-linux",),
                "fake_hash": "sha256-fake",
            },
        )(),
    )
    monkeypatch.setattr(
        "lib.update.nix_deno.sources_file_for",
        lambda _source: pytest.fail("candidate probe read checked-in sources.json"),
    )

    async def _process(
        platform_name: str,
        *,
        context: _PlatformHashContext,
        emit: EventSink = ignore_event,
    ) -> object:
        captured["original_entry"] = context.original_entry
        captured["existing_hashes"] = context.existing_hashes
        context.platform_hashes[platform_name] = "sha256-updated"
        await emit(UpdateEvent.status(context.source, "processed candidate"))

    monkeypatch.setattr("lib.update.nix_deno._process_platform_hash", _process)

    events = _collect(
        lambda emit: compute_deno_deps_hash(
            "demo", "candidate-input", source_override=candidate, emit=emit
        )
    )

    assert captured == {
        "existing_hashes": {"x86_64-linux": "sha256-existing"},
        "original_entry": candidate,
    }
    assert events.result.hashes == {"x86_64-linux": "sha256-updated"}
    assert events.result.fully_computed is True
