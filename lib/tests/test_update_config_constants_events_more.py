"""Additional tests for update config/constants/events helpers."""

import asyncio

import pytest

from lib.nix.models.sources import HashCollection, SourceEntry
from lib.tests._updater_helpers import collect_events
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
    CommandResult,
    StatusInfo,
    StatusKind,
    StatusPayload,
    UpdateEvent,
    expect_artifact_updates,
    expect_command_result,
    expect_source_entry,
    gather_results,
    is_nix_build_command,
)


def test_default_max_nix_builds_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep implicit build fan-out disabled regardless of local CPU count."""
    monkeypatch.setattr("os.cpu_count", lambda: None)
    assert default_max_nix_builds() == 1

    monkeypatch.setattr("os.cpu_count", lambda: 10)
    assert default_max_nix_builds() == 1

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


def test_resolve_config_normalizes_legacy_platform_aliases_and_bounds() -> None:
    """Normalize legacy platform aliases and bounded numeric fields."""
    cfg = resolve_config(
        deno_platforms="x86_64-linux, aarch64-darwin",
        retries=-5,
        log_tail_lines=0,
        max_nix_builds=0,
    )
    assert cfg.default_retries == 0
    assert cfg.default_log_tail_lines == 1
    assert cfg.max_nix_builds == 1
    assert cfg.hash_build_platforms == ("x86_64-linux", "aarch64-darwin")
    assert cfg.deno_deps_platforms == ("x86_64-linux", "aarch64-darwin")


def test_resolve_config_accepts_hash_build_platform_alias() -> None:
    """Allow the generalized hash platform alias to override Deno targets."""
    cfg = resolve_config(hash_build_platforms=("aarch64-linux",))
    assert cfg.deno_deps_platforms == ("aarch64-linux",)
    assert cfg.hash_build_platforms == ("aarch64-linux",)


def test_resolve_config_canonical_platforms_win_over_legacy_aliases() -> None:
    """Prefer canonical platform overrides over legacy aliases."""
    cfg = resolve_config(
        hash_build_platforms=("aarch64-linux",),
        deno_deps_platforms=("x86_64-linux",),
    )
    assert cfg.hash_build_platforms == ("aarch64-linux",)


def test_resolve_config_canonical_platforms_win_over_legacy_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit canonical overrides should not inherit legacy env values."""
    monkeypatch.setenv("UPDATE_DENO_DEPS_PLATFORMS", "x86_64-linux")

    cfg = resolve_config(hash_build_platforms=("aarch64-linux",))

    assert cfg.hash_build_platforms == ("aarch64-linux",)


def test_resolve_config_accepts_legacy_platform_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use legacy Deno platform env when the canonical env is absent."""
    monkeypatch.setenv("UPDATE_DENO_DEPS_PLATFORMS", "x86_64-linux")
    monkeypatch.delenv("UPDATE_HASH_BUILD_PLATFORMS", raising=False)

    cfg = resolve_config()

    assert cfg.hash_build_platforms == ("x86_64-linux",)


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
        hash_build_platforms=("x86_64-linux",),
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

    mapping = {"sha256": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}
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
    assert typed_status.payload == StatusPayload(operation="compute_hash")


def test_gather_results_keeps_typed_results_and_real_progress() -> None:
    """Concurrent operations return values separately from awaited progress."""

    async def run():
        async def operations(emit):
            async def one(name):
                await emit(UpdateEvent.status(name, "started"))
                return f"hash:{name}"

            return await gather_results({name: one(name) for name in ("a", "b")})

        return await collect_events(operations)

    captured = asyncio.run(run())
    assert captured.result == {"a": "hash:a", "b": "hash:b"}
    assert [event.source for event in captured] == ["a", "b"]


def test_gather_results_cancels_siblings_and_preserves_failure() -> None:
    """A failed operation cancels and awaits a blocked sibling before returning."""
    cancelled = []

    async def run():
        started = asyncio.Event()

        async def slow():
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                cancelled.append("slow")

        async def fail():
            await started.wait()
            raise RuntimeError("hash failed")

        return await gather_results({"slow": slow(), "broken": fail()})

    with pytest.raises(RuntimeError, match="hash failed") as caught:
        asyncio.run(run())
    assert cancelled == ["slow"]
    assert "update operation key: 'broken'" in caught.value.__notes__


def test_gather_results_reports_simultaneous_failures() -> None:
    """Independent errors retain their causes when tasks fail together."""

    async def run():
        async def fail(message):
            raise RuntimeError(message)

        return await gather_results({"a": fail("first"), "b": fail("second")})

    with pytest.raises(RuntimeError, match="first.*second") as caught:
        asyncio.run(run())
    assert isinstance(caught.value.__cause__, ExceptionGroup)


def test_sink_backpressure_and_caller_cancellation() -> None:
    """An awaited bounded sink pauses producers and cancellation joins cleanup."""

    async def run():
        queue = asyncio.Queue(maxsize=1)
        started = asyncio.Event()
        finished = []

        async def produce():
            try:
                await queue.put(UpdateEvent.status("demo", "first"))
                started.set()
                await queue.put(UpdateEvent.status("demo", "second"))
                raise AssertionError("bounded sink did not apply backpressure")
            finally:
                finished.append(True)

        task = asyncio.create_task(gather_results({"demo": produce()}))
        await started.wait()
        assert queue.qsize() == 1
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished == [True]

    asyncio.run(run())


def test_update_event_status_includes_structured_fields() -> None:
    """Preserve structured status metadata on status events."""
    event = UpdateEvent.status(
        "demo",
        "working",
        operation="compute_hash",
        status=StatusInfo(kind=StatusKind.COMPUTING_HASH, value="linux"),
    )
    assert event.payload == StatusPayload(
        operation="compute_hash",
        info=StatusInfo(kind=StatusKind.COMPUTING_HASH, value="linux"),
    )
    status_only = UpdateEvent.status(
        "demo",
        "done",
        status=StatusInfo(kind=StatusKind.UPDATED, value="1.2.3"),
    )
    assert status_only.payload == StatusPayload(
        info=StatusInfo(kind=StatusKind.UPDATED, value="1.2.3"),
    )
    assert UpdateEvent.status("demo", "plain").payload is None
