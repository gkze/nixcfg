"""Additional tests for update config/constants/events helpers."""

from __future__ import annotations

import asyncio
import builtins
from typing import TYPE_CHECKING

import pytest

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.update import constants
from lib.update.artifacts import GeneratedArtifact
from lib.update.config import (
    DEFAULT_CONFIG,
    UpdateConfig,
    default_max_nix_builds,
    env_bool,
    hash_build_platforms_for,
    resolve_active_config,
    resolve_config,
)
from lib.update.events import (
    CapturedValue,
    CommandResult,
    GatheredValues,
    UpdateEvent,
    UpdateEventKind,
    ValueDrain,
    capture_stream_value,
    drain_value_events,
    expect_artifact_updates,
    expect_command_result,
    expect_hash_mapping,
    expect_source_entry,
    expect_source_hashes,
    expect_str,
    gather_event_streams,
    is_nix_build_command,
    require_value,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def test_default_max_nix_builds_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compute default max jobs for known and unknown CPU counts."""
    monkeypatch.setattr("os.cpu_count", lambda: None)
    assert default_max_nix_builds() == 4

    monkeypatch.setattr("os.cpu_count", lambda: 10)
    assert default_max_nix_builds() == 7

    monkeypatch.setattr("os.cpu_count", lambda: 1)
    assert default_max_nix_builds() == 1


def test_env_bool_truthy_falsy_and_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Interpret environment booleans consistently and safely."""
    monkeypatch.setenv("UPDATE_BOOL_TEST", "yes")
    assert env_bool("UPDATE_BOOL_TEST", default=False) is True

    monkeypatch.setenv("UPDATE_BOOL_TEST", "0")
    assert env_bool("UPDATE_BOOL_TEST", default=True) is False

    monkeypatch.setenv("UPDATE_BOOL_TEST", "maybe")
    assert env_bool("UPDATE_BOOL_TEST", default=True) is True

    monkeypatch.delenv("UPDATE_BOOL_TEST", raising=False)
    assert env_bool("UPDATE_BOOL_TEST", default=False) is False


def test_resolve_config_normalizes_aliases_and_bounds() -> None:
    """Normalize deno platform aliases and bounded numeric fields."""
    cfg = resolve_config(
        deno_platforms="x86_64-linux, aarch64-darwin",
        retries=-5,
        log_tail_lines=0,
        max_nix_builds=0,
    )
    assert cfg.default_retries == 0
    assert cfg.default_log_tail_lines == 1
    assert cfg.max_nix_builds == 1
    assert cfg.deno_deps_platforms == ("x86_64-linux", "aarch64-darwin")


def test_resolve_config_accepts_hash_build_platform_alias() -> None:
    """Allow the generalized hash platform alias to override Deno targets."""
    cfg = resolve_config(hash_build_platforms=("aarch64-linux",))
    assert cfg.deno_deps_platforms == ("aarch64-linux",)
    assert cfg.hash_build_platforms == ("aarch64-linux",)


def test_hash_build_platforms_for_accepts_real_and_legacy_configs() -> None:
    """Read canonical platforms from UpdateConfig and legacy config doubles."""
    cfg = resolve_config(hash_build_platforms=("aarch64-linux",))
    assert hash_build_platforms_for(cfg) == ("aarch64-linux",)

    legacy_cfg = type("_LegacyCfg", (), {"deno_deps_platforms": ["x86_64-linux"]})()
    assert hash_build_platforms_for(legacy_cfg) == ("x86_64-linux",)


def test_hash_build_platforms_for_rejects_invalid_legacy_shapes() -> None:
    """Reject malformed legacy platform overrides with clear errors."""
    missing_cfg = type("_MissingCfg", (), {})()
    with pytest.raises(TypeError, match="Expected hash-build platform list/tuple"):
        hash_build_platforms_for(missing_cfg)

    bad_item_cfg = type(
        "_BadItemCfg", (), {"deno_deps_platforms": ["x86_64-linux", 1]}
    )()
    with pytest.raises(TypeError, match="Hash-build platforms must be strings"):
        hash_build_platforms_for(bad_item_cfg)


def test_resolve_active_config_and_default_config_reference() -> None:
    """Prefer explicit config and otherwise return the global default."""
    custom = UpdateConfig(
        default_timeout=1,
        default_subprocess_timeout=2,
        default_log_tail_lines=3,
        default_render_interval=0.1,
        default_user_agent="ua",
        default_retries=4,
        default_retry_backoff=0.5,
        fake_hash="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        max_nix_builds=2,
        deno_deps_platforms=("x86_64-linux",),
    )
    assert resolve_active_config(custom) is custom
    assert resolve_active_config(None) is DEFAULT_CONFIG


def test_resolve_active_config_reloads_when_env_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recompute the default config when UPDATE_* env overrides change."""
    monkeypatch.setenv("UPDATE_HTTP_TIMEOUT", "99")
    assert resolve_active_config(None).default_timeout == 99
    monkeypatch.delenv("UPDATE_HTTP_TIMEOUT", raising=False)


def test_resolve_config_ignores_none_overrides() -> None:
    """Keep environment defaults when explicit overrides are None."""
    cfg = resolve_config(http_timeout=None, retries=None, hash_build_platforms=None)
    assert cfg == DEFAULT_CONFIG


def test_resolve_timeout_alias_success_and_errors() -> None:
    """Handle timeout alias conversion and invalid argument combinations."""
    kwargs: dict[str, object] = {"timeout": 2}
    resolved = constants.resolve_timeout_alias(
        named_timeout=None,
        named_timeout_label="request_timeout",
        kwargs=kwargs,
    )
    assert resolved == 2.0
    assert kwargs == {}

    with pytest.raises(TypeError, match="Pass only one"):
        constants.resolve_timeout_alias(
            named_timeout=1.0,
            named_timeout_label="request_timeout",
            kwargs={"timeout": 2.0},
        )

    with pytest.raises(TypeError, match="timeout must be a number"):
        constants.resolve_timeout_alias(
            named_timeout=None,
            named_timeout_label="request_timeout",
            kwargs={"timeout": "oops"},
        )

    with pytest.raises(TypeError, match=r"Unexpected keyword argument\(s\): extra"):
        constants.resolve_timeout_alias(
            named_timeout=None,
            named_timeout_label="request_timeout",
            kwargs={"extra": True},
        )


def test_update_event_expect_helpers_and_type_guards() -> None:
    """Validate payload conversion helpers for common event payloads."""
    cmd = CommandResult(args=["nix"], returncode=0, stdout="", stderr="")
    assert expect_command_result(cmd) is cmd
    with pytest.raises(TypeError, match="Expected CommandResult payload"):
        expect_command_result("x")

    assert expect_str("ok") == "ok"
    with pytest.raises(TypeError, match="Expected string payload"):
        expect_str(1)

    mapping = {"sha256": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}
    assert expect_hash_mapping(mapping) == mapping
    with pytest.raises(TypeError, match="Expected hash mapping payload"):
        expect_hash_mapping({"a": 1})

    entries = [
        HashEntry.create(
            "sha256", "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        )
    ]
    assert expect_source_hashes(mapping) == mapping
    assert expect_source_hashes(entries) == entries
    with pytest.raises(TypeError, match="Expected SourceHashes payload"):
        expect_source_hashes(["bad"])
    with pytest.raises(TypeError, match="Expected SourceHashes payload"):
        expect_source_hashes("bad")

    source_entry = SourceEntry(hashes=HashCollection.from_value(mapping))
    assert expect_source_entry(source_entry) is source_entry
    with pytest.raises(TypeError, match="Expected SourceEntry payload"):
        expect_source_entry("bad")

    artifact = GeneratedArtifact.text("demo.txt", "content\n")
    assert expect_artifact_updates([artifact]) == [artifact]
    with pytest.raises(TypeError, match="Expected GeneratedArtifact list payload"):
        expect_artifact_updates([artifact, "bad"])
    with pytest.raises(TypeError, match="Expected GeneratedArtifact list payload"):
        expect_artifact_updates("bad")

    assert is_nix_build_command(["nix", "build", "foo"]) is True
    assert is_nix_build_command(["nix", "eval"]) is False
    assert is_nix_build_command(None) is False

    plain_status = UpdateEvent.status("demo", "working")
    assert plain_status.payload is None

    typed_status = UpdateEvent.status(
        "demo",
        "working",
        operation="compute_hash",
    )
    assert typed_status.payload == {"operation": "compute_hash"}


def _collect_async[T](stream: AsyncIterator[T]) -> list[T]:
    async def _run() -> list[T]:
        result: list[T] = []
        async for item in stream:
            result.append(item)
        return result

    return asyncio.run(_run())


def test_drain_value_events_and_require_value() -> None:
    """Capture VALUE events while forwarding non-value events."""

    async def _events() -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.status("demo", "working")
        yield UpdateEvent.value("demo", "value")

    drain = ValueDrain[str]()
    forwarded = _collect_async(drain_value_events(_events(), drain, parse=expect_str))
    assert [event.kind for event in forwarded] == [UpdateEventKind.STATUS]
    assert require_value(drain, "missing") == "value"

    with pytest.raises(RuntimeError, match="missing payload"):

        async def _missing_value() -> AsyncIterator[UpdateEvent]:
            yield UpdateEvent(source="demo", kind=UpdateEventKind.VALUE, payload=None)

        _collect_async(
            drain_value_events(_missing_value(), ValueDrain(), parse=expect_str)
        )

    with pytest.raises(RuntimeError, match="need value"):
        require_value(ValueDrain[str](), "need value")


def test_capture_stream_value_emits_wrapper() -> None:
    """Wrap final captured VALUE payload in CapturedValue."""

    async def _events() -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.status("demo", "one")
        yield UpdateEvent.value("demo", "captured")

    items = _collect_async(capture_stream_value(_events(), error="missing"))
    assert isinstance(items[0], UpdateEvent)
    wrapped = items[1]
    if not isinstance(wrapped, CapturedValue):
        raise AssertionError
    assert wrapped.captured == "captured"


def test_gather_event_streams_success_and_errors() -> None:
    """Gather VALUE payloads while forwarding non-value events."""

    async def _stream_ok(name: str, payload: str) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.status(name, f"start-{name}")
        yield UpdateEvent.value(name, payload)

    items = _collect_async(
        gather_event_streams({"a": _stream_ok("a", "1"), "b": _stream_ok("b", "2")})
    )
    statuses = [item for item in items if isinstance(item, UpdateEvent)]
    assert len(statuses) == 2
    gathered = [item for item in items if isinstance(item, GatheredValues)][0]
    assert gathered.values == {"a": "1", "b": "2"}

    with pytest.raises(RuntimeError, match="missing payload"):

        async def _missing_payload() -> AsyncIterator[UpdateEvent]:
            yield UpdateEvent(source="x", kind=UpdateEventKind.VALUE, payload=None)

        _collect_async(gather_event_streams({"x": _missing_payload()}))

    with pytest.raises(RuntimeError, match="boom") as exc_info:

        async def _boom() -> AsyncIterator[UpdateEvent]:
            msg = "boom"
            raise RuntimeError(msg)
            yield UpdateEvent.status("never", "never")

        _collect_async(gather_event_streams({"x": _boom()}))

    assert "event stream key: 'x'" in "\n".join(exc_info.value.__notes__)

    with pytest.raises(RuntimeError, match="Multiple event streams failed") as exc_info:

        async def _boom_one() -> AsyncIterator[UpdateEvent]:
            msg = "first"
            raise RuntimeError(msg)
            yield UpdateEvent.status("never", "never")

        async def _boom_two() -> AsyncIterator[UpdateEvent]:
            msg = "second"
            raise RuntimeError(msg)
            yield UpdateEvent.status("never", "never")

        _collect_async(gather_event_streams({"a": _boom_one(), "b": _boom_two()}))

    notes = "\n".join(exc_info.value.__notes__)
    assert "'a': RuntimeError('first')" in notes
    assert "'b': RuntimeError('second')" in notes


def test_gather_event_streams_cancels_siblings_after_error() -> None:
    """Cancel in-flight sibling streams once one stream fails."""
    cancelled = False

    async def _slow_stream() -> AsyncIterator[UpdateEvent]:
        nonlocal cancelled
        try:
            await asyncio.sleep(1)
            yield UpdateEvent.value("slow", "late")
        except asyncio.CancelledError:
            cancelled = True
            raise

    async def _boom() -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.status("boom", "starting")
        msg = "boom"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="boom"):
        _collect_async(gather_event_streams({"slow": _slow_stream(), "boom": _boom()}))

    assert cancelled is True


def test_gather_event_streams_reraises_without_add_note_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-raise a single stream error even if add_note is unavailable."""
    original_hasattr = builtins.hasattr

    def _fake_hasattr(obj: object, name: str) -> bool:
        if isinstance(obj, RuntimeError) and name == "add_note":
            return False
        return original_hasattr(obj, name)

    monkeypatch.setattr("builtins.hasattr", _fake_hasattr)

    async def _boom() -> AsyncIterator[UpdateEvent]:
        msg = "boom"
        raise RuntimeError(msg)
        yield UpdateEvent.status("never", "never")

    with pytest.raises(RuntimeError, match="boom") as exc_info:
        _collect_async(gather_event_streams({"x": _boom()}))

    assert not getattr(exc_info.value, "__notes__", [])


def test_update_event_status_includes_structured_fields() -> None:
    """Preserve structured status metadata on status events."""
    event = UpdateEvent.status(
        "demo",
        "working",
        operation="compute_hash",
        status="computing_hash",
        detail="linux",
    )
    assert event.payload == {
        "operation": "compute_hash",
        "status": "computing_hash",
        "detail": "linux",
    }
    status_only = UpdateEvent.status(
        "demo",
        "done",
        status="updated",
        detail={"version": "1.2.3"},
    )
    assert status_only.payload == {
        "status": "updated",
        "detail": {"version": "1.2.3"},
    }
