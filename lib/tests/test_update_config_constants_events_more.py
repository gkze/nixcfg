"""Additional tests for update config/constants/events helpers."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._assertions import check
from lib.update import constants
from lib.update.artifacts import GeneratedArtifact
from lib.update.config import (
    DEFAULT_CONFIG,
    UpdateConfig,
    default_max_nix_builds,
    env_bool,
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
    check(default_max_nix_builds() == 4)

    monkeypatch.setattr("os.cpu_count", lambda: 10)
    check(default_max_nix_builds() == 7)

    monkeypatch.setattr("os.cpu_count", lambda: 1)
    check(default_max_nix_builds() == 1)


def test_env_bool_truthy_falsy_and_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Interpret environment booleans consistently and safely."""
    monkeypatch.setenv("UPDATE_BOOL_TEST", "yes")
    check(env_bool("UPDATE_BOOL_TEST", default=False) is True)

    monkeypatch.setenv("UPDATE_BOOL_TEST", "0")
    check(env_bool("UPDATE_BOOL_TEST", default=True) is False)

    monkeypatch.setenv("UPDATE_BOOL_TEST", "maybe")
    check(env_bool("UPDATE_BOOL_TEST", default=True) is True)

    monkeypatch.delenv("UPDATE_BOOL_TEST", raising=False)
    check(env_bool("UPDATE_BOOL_TEST", default=False) is False)


def test_resolve_config_normalizes_aliases_and_bounds() -> None:
    """Normalize deno platform aliases and bounded numeric fields."""
    cfg = resolve_config(
        deno_platforms="x86_64-linux, aarch64-darwin",
        retries=-5,
        log_tail_lines=0,
        max_nix_builds=0,
    )
    check(cfg.default_retries == 0)
    check(cfg.default_log_tail_lines == 1)
    check(cfg.max_nix_builds == 1)
    check(cfg.deno_deps_platforms == ("x86_64-linux", "aarch64-darwin"))


def test_resolve_config_accepts_hash_build_platform_alias() -> None:
    """Allow the generalized hash platform alias to override Deno targets."""
    cfg = resolve_config(hash_build_platforms=("aarch64-linux",))
    check(cfg.deno_deps_platforms == ("aarch64-linux",))
    check(cfg.hash_build_platforms == ("aarch64-linux",))


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
    check(resolve_active_config(custom) is custom)
    check(resolve_active_config(None) is DEFAULT_CONFIG)


def test_resolve_timeout_alias_success_and_errors() -> None:
    """Handle timeout alias conversion and invalid argument combinations."""
    kwargs: dict[str, object] = {"timeout": 2}
    resolved = constants.resolve_timeout_alias(
        named_timeout=None,
        named_timeout_label="request_timeout",
        kwargs=kwargs,
    )
    check(resolved == 2.0)
    check(kwargs == {})

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
    check(expect_command_result(cmd) is cmd)
    with pytest.raises(TypeError, match="Expected CommandResult payload"):
        expect_command_result("x")

    check(expect_str("ok") == "ok")
    with pytest.raises(TypeError, match="Expected string payload"):
        expect_str(1)

    mapping = {"sha256": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}
    check(expect_hash_mapping(mapping) == mapping)
    with pytest.raises(TypeError, match="Expected hash mapping payload"):
        expect_hash_mapping({"a": 1})

    entries = [
        HashEntry.create(
            "sha256", "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        )
    ]
    check(expect_source_hashes(mapping) == mapping)
    check(expect_source_hashes(entries) == entries)
    with pytest.raises(TypeError, match="Expected SourceHashes payload"):
        expect_source_hashes(["bad"])
    with pytest.raises(TypeError, match="Expected SourceHashes payload"):
        expect_source_hashes("bad")

    source_entry = SourceEntry(hashes=HashCollection.from_value(mapping))
    check(expect_source_entry(source_entry) is source_entry)
    with pytest.raises(TypeError, match="Expected SourceEntry payload"):
        expect_source_entry("bad")

    artifact = GeneratedArtifact.text("demo.txt", "content\n")
    check(expect_artifact_updates([artifact]) == [artifact])
    with pytest.raises(TypeError, match="Expected GeneratedArtifact list payload"):
        expect_artifact_updates([artifact, "bad"])
    with pytest.raises(TypeError, match="Expected GeneratedArtifact list payload"):
        expect_artifact_updates("bad")

    check(is_nix_build_command(["nix", "build", "foo"]) is True)
    check(is_nix_build_command(["nix", "eval"]) is False)
    check(is_nix_build_command(None) is False)

    plain_status = UpdateEvent.status("demo", "working")
    check(plain_status.payload is None)

    typed_status = UpdateEvent.status(
        "demo",
        "working",
        operation="compute_hash",
    )
    check(typed_status.payload == {"operation": "compute_hash"})


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
    check([event.kind for event in forwarded] == [UpdateEventKind.STATUS])
    check(require_value(drain, "missing") == "value")

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
    check(isinstance(items[0], UpdateEvent))
    wrapped = items[1]
    if not isinstance(wrapped, CapturedValue):
        raise AssertionError
    check(wrapped.captured == "captured")


def test_gather_event_streams_success_and_errors() -> None:
    """Gather VALUE payloads while forwarding non-value events."""

    async def _stream_ok(name: str, payload: str) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.status(name, f"start-{name}")
        yield UpdateEvent.value(name, payload)

    items = _collect_async(
        gather_event_streams({"a": _stream_ok("a", "1"), "b": _stream_ok("b", "2")})
    )
    statuses = [item for item in items if isinstance(item, UpdateEvent)]
    check(len(statuses) == 2)
    gathered = [item for item in items if isinstance(item, GatheredValues)][0]
    check(gathered.values == {"a": "1", "b": "2"})

    with pytest.raises(RuntimeError, match="missing payload"):

        async def _missing_payload() -> AsyncIterator[UpdateEvent]:
            yield UpdateEvent(source="x", kind=UpdateEventKind.VALUE, payload=None)

        _collect_async(gather_event_streams({"x": _missing_payload()}))

    with pytest.raises(RuntimeError, match="boom"):

        async def _boom() -> AsyncIterator[UpdateEvent]:
            msg = "boom"
            raise RuntimeError(msg)
            yield UpdateEvent.status("never", "never")

        _collect_async(gather_event_streams({"x": _boom()}))
