"""Focused branch-closure tests for high-coverage update helpers."""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from lib.nix.commands.base import CommandResult as LibCommandResult
from lib.nix.commands.base import NixCommandError, ProcessDone, ProcessLine
from lib.nix.models.sources import HashEntry
from lib.update.events import (
    CommandResult,
    GatheredValues,
    UpdateEvent,
    UpdateEventKind,
    expect_source_hashes,
)
from lib.update.process import NixBuildOptions, RunCommandOptions, StreamCommandOptions
from lib.update.ui_state import OperationKind, OperationState, _set_operation_status

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _collect(stream: AsyncIterator[UpdateEvent]) -> list[UpdateEvent]:
    async def _run() -> list[UpdateEvent]:
        items: list[UpdateEvent] = []
        async for item in stream:
            items.append(item)
        return items

    return asyncio.run(_run())


def test_deno_lock_known_version_skips_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avoid unexpected-version warning for supported lock versions."""
    from lib.update import deno_lock

    lock_file = tmp_path / "deno.lock"
    lock_file.write_text(
        json.dumps({"version": "5", "jsr": {}, "npm": {}}), encoding="utf-8"
    )

    async def _resolve_jsr(_lock_jsr: dict[str, dict[str, object]]) -> list[object]:
        return []

    monkeypatch.setattr("lib.update.deno_lock._resolve_all_jsr", _resolve_jsr)
    monkeypatch.setattr("lib.update.deno_lock._resolve_all_npm", lambda _lock_npm: [])
    manifest = asyncio.run(deno_lock.resolve_deno_deps(lock_file))
    assert manifest.lock_version == "5"
    assert "Unexpected deno.lock version" not in caplog.text


def test_events_expect_source_hashes_rejects_mixed_list() -> None:
    """Reject list payloads that mix ``HashEntry`` and non-entries."""
    good = HashEntry.create(
        "sha256", "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    )
    with pytest.raises(TypeError, match="Expected SourceHashes payload"):
        _ = expect_source_hashes([good, "bad"])


def test_compute_drv_fingerprint_without_store_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accept raw drvPath output without a store-path prefix."""
    from lib.update.nix import compute_drv_fingerprint

    async def _run_command(
        *_args: object, **_kwargs: object
    ) -> AsyncIterator[UpdateEvent]:
        result = CommandResult(
            args=["nix"],
            returncode=0,
            stdout="abc123-demo.drv",
            stderr="",
        )
        yield UpdateEvent(
            source="demo", kind=UpdateEventKind.COMMAND_END, payload=result
        )
        yield UpdateEvent.value("demo", result)

    monkeypatch.setattr("lib.update.nix.run_command", _run_command)
    assert asyncio.run(compute_drv_fingerprint("demo")) == "abc123"


def test_nix_cargo_yields_passthrough_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forward non-gather events from ``gather_event_streams``."""
    from lib.update.nix_cargo import compute_import_cargo_lock_output_hashes
    from lib.update.updaters.base import CargoLockGitDep

    async def _fake_gather(_streams: object) -> AsyncIterator[object]:
        yield UpdateEvent.status("demo", "prefetching")
        yield GatheredValues(values={"crate-1.0.0": "sha256-demo"})

    monkeypatch.setattr("lib.update.nix_cargo.gather_event_streams", _fake_gather)
    monkeypatch.setattr(
        "lib.update.nix_cargo.get_flake_input_node",
        lambda _name: type(
            "_Node",
            (),
            {"locked": type("_L", (), {"owner": "o", "repo": "r", "rev": "v"})()},
        )(),
    )

    class _Session:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr("lib.update.nix_cargo.aiohttp.ClientSession", _Session)
    monkeypatch.setattr(
        "lib.update.nix_cargo.fetch_url",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result=(
                b'[[package]]\nname = "crate"\nversion = "1.0.0"\n'
                b'source = "git+https://github.com/a/b?x#deadbeef"\n'
            ),
        ),
    )

    deps = [
        CargoLockGitDep(git_dep="crate-1.0.0", hash_type="sha256", match_name="crate")
    ]
    events = _collect(
        compute_import_cargo_lock_output_hashes(
            "demo",
            "input",
            lockfile_path="Cargo.lock",
            git_deps=deps,
        )
    )
    assert any(event.kind == UpdateEventKind.STATUS for event in events)


def test_stream_command_timeout_override_and_empty_sanitized_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use explicit timeout and skip empty sanitized lines."""
    from lib.update.process import stream_command

    captured: dict[str, object] = {}

    async def _stream_process(
        _args: list[str],
        *,
        timeout: float,
        env: object,
    ) -> AsyncIterator[ProcessLine | ProcessDone]:
        _ = env
        captured["timeout"] = timeout
        yield ProcessLine("stdout", "\x1b[31m\x1b[0m\n")
        yield ProcessDone(
            LibCommandResult(args=["echo", "x"], returncode=0, stdout="", stderr="")
        )

    monkeypatch.setattr("lib.update.process.stream_process", _stream_process)
    events = _collect(
        stream_command(
            ["echo", "x"],
            options=StreamCommandOptions(source="demo", command_timeout=2.5),
        )
    )
    assert captured["timeout"] == 2.5
    assert [event.kind for event in events] == [
        UpdateEventKind.COMMAND_START,
        UpdateEventKind.COMMAND_END,
    ]


def test_run_nix_build_without_verbose(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not include ``--verbose`` when verbose mode is disabled."""
    from lib.update.process import run_nix_build

    captured: dict[str, object] = {}

    async def _run_command(
        args: list[str], *, options: RunCommandOptions
    ) -> AsyncIterator[UpdateEvent]:
        captured["args"] = args
        yield UpdateEvent.status(options.source, "ok")

    monkeypatch.setattr("lib.update.process.run_command", _run_command)
    _ = _collect(
        run_nix_build(
            "pkgs.hello", options=NixBuildOptions(source="demo", verbose=False)
        )
    )
    args = captured["args"]
    assert isinstance(args, list)
    assert "--verbose" not in args


def test_validate_source_discovery_consistency_missing_in_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report sources present in Nix but missing from Python scan."""
    from lib.update.sources import validate_source_discovery_consistency

    monkeypatch.setattr("lib.update.sources.python_source_names", lambda: {"a"})
    monkeypatch.setattr("lib.update.sources.nix_source_names", lambda: {"a", "b"})
    with pytest.raises(RuntimeError, match="Missing in Python source scan: b"):
        validate_source_discovery_consistency()


def test_ui_state_set_status_preserves_message_when_none() -> None:
    """Keep existing message when no replacement/clear flag is provided."""
    op = OperationState(kind=OperationKind.CHECK_VERSION, label="Checking")
    op.message = "old"
    _set_operation_status(op, "success", message=None, clear_message=False)
    assert op.message == "old"


def test_packaging_source_policy_parser_and_python_audit_edges() -> None:
    """Cover source-modification policy helpers without scanning the repo."""
    from lib.codemods import packaging_source_policy as policy
    from lib.codemods.errors import CodemodError
    from lib.update.paths import REPO_ROOT

    assert policy._rewrite_function_formals_for_parser("let x = 1; in x") == (
        "let x = 1; in x"
    )
    assert policy._rewrite_function_formals_for_parser("{\n  lib,\n") == "{\n  lib,\n"
    assert policy._rewrite_function_formals_for_parser("{\n}:\nnull\n") == (
        "{\n}:\nnull\n"
    )
    malformed_header = "{\n    lib,\n}:\nnull\n"
    assert policy._rewrite_function_formals_for_parser(malformed_header) == (
        malformed_header
    )

    multiline_formals = (
        "{\n"
        "  lib,\n"
        "    # attached to lib\n"
        "  stdenv,\n"
        "}:\n"
        'stdenv.mkDerivation { pname = "demo"; }\n'
    )
    rewritten = policy._rewrite_function_formals_for_parser(multiline_formals)
    assert rewritten.startswith("{ lib\n    # attached to lib\n, stdenv\n}:")
    assert policy.parse_nix_expr_for_policy(
        '{\n  lib,\n  stdenv,\n}:\nstdenv.mkDerivation { pname = "demo"; }\n',
        context="demo.nix",
    )
    with pytest.raises(CodemodError, match="Unable to parse Nix source"):
        policy.parse_nix_expr_for_policy("{", context="bad.nix")

    assert (
        policy.NixSubstituteAudit._command_from(
            ["substituteInPlace file " + chr(92)],
            0,
        )
        == "substituteInPlace file"
    )

    audit = policy.PythonRewriteAudit(allowed_sites=())
    assigned: dict[str, list[tuple[int, int, int, int, str]]] = {}
    audit._record_assignment(None, (), assigned)
    audit._record_assignment(ast.Constant(1), [ast.Name("plain")], assigned)
    assert assigned == {}

    target = ast.parse("(left, [right, obj.attr]) = value").body[0]
    assert isinstance(target, ast.Assign)
    assert audit._assigned_names(target.targets[0]) == ("left", "right")

    module = ast.parse(
        "def rewrite(path, pattern):\n"
        "    text = pattern.replace('old', 'new')\n"
        "    left, [right, obj.attr] = pattern.subn('x', 'y')\n"
        "    annotated: str = pattern.sub('a', 'b')\n"
        "    path.write_text(text)\n"
        "    path.write_text(left)\n"
        "    path.write_text(annotated)\n"
        "    path.write_text(pattern.replace('c', 'd'))\n"
        "    path.write_text('literal')\n"
    )
    function = module.body[0]
    assert isinstance(function, ast.FunctionDef)
    sites = audit._function_sites(REPO_ROOT / "packages/demo/updater.py", function)
    site_names = {site[-1] for site in sites}
    assert {"replace", "sub", "subn"} <= site_names

    plain_call = ast.parse("func(value)").body[0]
    assert isinstance(plain_call, ast.Expr)
    assert isinstance(plain_call.value, ast.Call)
    assert audit._write_text_payload_name(plain_call.value) is None


def test_text_codemod_noop_write(tmp_path: Path) -> None:
    """Exercise text codemod no-op writes."""
    from lib.codemods.text import regex_replace_file_exactly

    path = tmp_path / "demo.txt"
    path.write_text("abc\n", encoding="utf-8")
    assert not regex_replace_file_exactly(path, r"abc", "abc")
    assert path.read_text(encoding="utf-8") == "abc\n"


def test_small_module_helper_edges() -> None:
    """Cover tiny URL and assignment helper branches."""
    from lib.nix.models.sources import _merge_drv_hash
    from lib.tests._updater_helpers import load_repo_module
    from lib.update import crate2nix
    from lib.update.updaters import factories
    from lib.update.updaters.vendor_feeds import SparkleAppcastItem

    patch_compiler = load_repo_module(
        "lib/rusty-v8/patch_compiler_gni.py",
        "patch_compiler_gni_branch_more",
    )
    assert patch_compiler._assignment_indent("use_lld_extra = true", "use_lld") is None
    assert patch_compiler._assignment_indent("use_lld true", "use_lld") is None

    assert _merge_drv_hash(None, "drv-new", baseline="drv-old") is None
    assert _merge_drv_hash("drv-old", None, baseline="drv-new") is None
    assert (
        _merge_drv_hash("drv-current", "drv-old", baseline="drv-old") == "drv-current"
    )

    repo_installable = f"path:{Path(crate2nix.REPO_ROOT).resolve()}#demo"
    assert crate2nix._local_flake_installable(repo_installable).endswith("#demo")
    assert crate2nix._local_flake_installable("github:owner/repo#demo") == (
        "github:owner/repo#demo"
    )

    assert (
        factories._render_asset_name(
            lambda version, platform_value: f"{version}-{platform_value}",
            version="1.2.3",
            platform_value="arm64",
        )
        == "1.2.3-arm64"
    )
    assert (
        factories._sparkle_item_version(
            SparkleAppcastItem("100", None, "https://example.com/app.zip"),
            version_field="short_or_version",
        )
        == "100"
    )
    assert (
        factories._sparkle_item_version(
            SparkleAppcastItem("100", "1.0", "https://example.com/app.zip"),
            version_field="invalid",
        )
        is None
    )

    orbstack = load_repo_module(
        "overlays/orbstack/updater.py",
        "orbstack_updater_branch_more",
    )
    assert orbstack._download_url("1.2.3-456", "aarch64-darwin", "arm64") == (
        "https://cdn-updates.orbstack.dev/arm64/OrbStack_v1.2.3_456_arm64.dmg"
    )
    tolaria = load_repo_module(
        "packages/tolaria/updater.py",
        "tolaria_updater_branch_more",
    )
    assert tolaria._download_url("2026.1.5", "aarch64-darwin", "Silicon") == (
        "https://github.com/refactoringhq/tolaria/releases/download/v2026-01-05/"
        "Tolaria_2026.1.5_macOS_Silicon.app.tar.gz"
    )


def test_crate2nix_compat_missing_hash_patch(tmp_path: Path) -> None:
    """The compatibility shim should synthesize absent crate-hashes output."""
    from lib.update import crate2nix, crate2nix_compat

    target = crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo-crate2nix-src",
        cargo_nix=Path("packages/demo/Cargo.nix"),
        crate_hashes=Path("packages/demo/crate-hashes.json"),
        normalizer_path=Path("packages/demo/normalize_cargo_nix.py"),
        supported_platforms=("x86_64-linux",),
    )

    assert not crate2nix_compat.patch_installed_crate2nix_target(
        SimpleNamespace(TARGETS={}),
        "missing",
    )

    class FakeCrate2Nix:
        RefreshResult = crate2nix.RefreshResult

        def __init__(self) -> None:
            self.TARGETS = {"demo": target}
            self.write_hashes = True
            self.generated_args: list[list[str]] = []

        def _build_patched_src(self, _target: object) -> Path:
            patched = tmp_path / "patched"
            patched.mkdir(exist_ok=True)
            (patched / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
            return patched

        def _load_normalizer(self, _path: Path):
            return lambda text: (text + "normalized-root\n", 0, False)

        def _crate2nix_cargo_home(self) -> Path:
            return tmp_path / "cargo-home"

        def _run_crate2nix_generate(
            self,
            args: list[str],
            *,
            env: dict[str, str],
            generated_outputs: tuple[Path, Path],
        ) -> None:
            _ = env
            self.generated_args.append(args)
            cargo_nix, crate_hashes = generated_outputs
            cargo_nix.write_text("generated-cargo\n", encoding="utf-8")
            if self.write_hashes:
                crate_hashes.write_text('{"crate":"hash"}\n', encoding="utf-8")

        def _stabilize_generated_root_src_paths(
            self,
            cargo_text: str,
            *,
            patched_src: Path,
            generated_cargo: Path,
        ) -> str:
            _ = (patched_src, generated_cargo)
            return cargo_text.replace("generated", "stable")

        def _stabilize_generated_command_comment(
            self,
            _target: object,
            cargo_text: str,
        ) -> str:
            return cargo_text + "command-comment\n"

        def _normalize_trailing_newline(self, text: str) -> str:
            return text.rstrip("\n") + "\n"

        def _normalize_json_text(self, text: str) -> str:
            payload = json.loads(text)
            return json.dumps(payload, sort_keys=True) + "\n"

    fake = FakeCrate2Nix()
    assert crate2nix_compat.patch_installed_crate2nix_missing_hashes(fake)
    first = fake._refresh_target(target)
    assert first.cargo_nix == "stable-cargo\nnormalized-root\ncommand-comment\n"
    assert first.crate_hashes == '{"crate": "hash"}\n'
    assert fake.generated_args[0][-1] == "--default-features"

    fake.write_hashes = False
    second = fake._refresh_target(target)
    assert second.crate_hashes == "{}\n"


def test_command_materialized_artifacts_failure_and_missing_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Materialized artifact commands should fail clearly and still restore files."""
    from lib.update.generated_artifact_commands import (
        stream_command_materialized_artifacts,
    )

    async def _empty_inner() -> AsyncIterator[UpdateEvent]:
        for event in ():
            yield event

    async def _missing_artifact_command(
        args: list[str],
        *,
        options: object,
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.status("demo", "running")
        yield UpdateEvent.value(
            "demo",
            CommandResult(args=args, returncode=0, stdout="", stderr=""),
        )

    monkeypatch.setattr(
        "lib.update.generated_artifact_commands._run_command",
        _missing_artifact_command,
    )
    with pytest.raises(RuntimeError, match="Generated artifact was not produced"):
        _collect(
            stream_command_materialized_artifacts(
                "demo",
                args=["refresh"],
                artifact_paths=("generated.txt",),
                inner=_empty_inner(),
                dry_run=False,
                repo_root=tmp_path,
            )
        )
    assert not (tmp_path / "generated.txt").exists()

    async def _failing_command(
        args: list[str],
        *,
        options: object,
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.value(
            "demo",
            CommandResult(args=args, returncode=2, stdout="", stderr="bad command"),
        )

    monkeypatch.setattr(
        "lib.update.generated_artifact_commands._run_command",
        _failing_command,
    )
    with pytest.raises(RuntimeError, match="bad command"):
        _collect(
            stream_command_materialized_artifacts(
                "demo",
                args=["refresh"],
                artifact_paths=("generated.txt",),
                inner=_empty_inner(),
                dry_run=False,
                repo_root=tmp_path,
            )
        )
    assert not (tmp_path / "generated.txt").exists()

    from lib.update import generated_artifact_commands as artifact_commands

    with pytest.raises(RuntimeError, match=r"Refresh demo failed \(exit 1\)$"):
        artifact_commands._raise_failed_command(
            "Refresh demo",
            CommandResult(args=["refresh"], returncode=1, stdout="", stderr=""),
        )


def test_deno_lock_retry_classifier_and_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise retry classification plus non-retry and exhausted loops."""
    import aiohttp

    from lib.update import deno_lock

    request_info = cast("Any", SimpleNamespace(real_url="https://example.invalid"))
    retryable_response = aiohttp.ClientResponseError(
        request_info=request_info,
        history=(),
        status=429,
    )
    non_retryable_response = aiohttp.ClientResponseError(
        request_info=request_info,
        history=(),
        status=404,
    )
    assert deno_lock._is_retryable_jsr_fetch_error(TimeoutError())
    assert deno_lock._is_retryable_jsr_fetch_error(retryable_response)
    assert not deno_lock._is_retryable_jsr_fetch_error(non_retryable_response)
    assert deno_lock._is_retryable_jsr_fetch_error(aiohttp.ClientError("network"))

    class RaisingClient:
        def get(self, _url: str) -> object:
            raise ValueError("permanent")

    with pytest.raises(ValueError, match="permanent"):
        asyncio.run(
            deno_lock._fetch_jsr_bytes(
                cast("Any", RaisingClient()),
                "https://example.invalid/@scope/pkg",
                context="demo",
            )
        )

    monkeypatch.setattr(deno_lock, "JSR_FETCH_ATTEMPTS", 0)
    with pytest.raises(RuntimeError, match="Exhausted retries fetching demo"):
        asyncio.run(
            deno_lock._fetch_jsr_bytes(
                cast("Any", RaisingClient()),
                "https://example.invalid/@scope/pkg",
                context="demo",
            )
        )


def test_compute_sri_hash_reraises_non_retryable_prefetch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-transient nix-prefetch-url errors should not be retried."""
    from lib.update import process as update_process
    from lib.update.config import resolve_config

    calls = 0

    async def _prefetch_url(_url: str, *, name: str | None = None) -> str:
        nonlocal calls
        _ = name
        calls += 1
        raise NixCommandError(
            LibCommandResult(
                args=["nix-prefetch-url"],
                returncode=1,
                stdout="",
                stderr="permanent failure",
            ),
            "permanent failure",
        )

    monkeypatch.setattr(update_process, "libnix_prefetch_url", _prefetch_url)
    monkeypatch.setattr(
        update_process,
        "resolve_active_config",
        lambda _config: resolve_config(retries=3, retry_backoff=0),
    )

    with pytest.raises(NixCommandError, match="permanent failure"):
        _collect(
            update_process.compute_sri_hash(
                "demo",
                "https://example.com/archive.tar.gz",
            )
        )
    assert calls == 1


def test_update_cli_reexec_and_entrypoint_short_circuit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover update CLI checkout re-exec argument construction."""
    from lib.update import cli as update_cli

    captured: dict[str, object] = {}
    monkeypatch.setattr(update_cli.sys, "argv", ["nixcfg", "update", "demo"])
    monkeypatch.setattr(update_cli, "get_repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(
        update_cli, "_update_library_matches_checkout", lambda _root: False
    )
    monkeypatch.delenv(update_cli._REEXEC_ENV, raising=False)
    monkeypatch.setattr(update_cli.shutil, "which", lambda _name: "/bin/nix")
    monkeypatch.setattr(
        update_cli.os, "chdir", lambda path: captured.setdefault("cwd", path)
    )

    def _execvpe(file: str, args: list[str], env: dict[str, str]) -> None:
        captured["file"] = file
        captured["args"] = args
        captured["env"] = env

    monkeypatch.setattr(update_cli.os, "execvpe", _execvpe)

    with pytest.raises(AssertionError, match="unreachable"):
        update_cli._maybe_reexec_checkout_update()

    assert captured["cwd"] == Path("/repo")
    assert captured["file"] == "/bin/nix"
    assert captured["args"] == [
        "/bin/nix",
        "run",
        ".#nixcfg",
        "--",
        "update",
        "demo",
    ]
    assert cast("dict[str, str]", captured["env"])[update_cli._REEXEC_ENV] == "1"

    monkeypatch.setattr(update_cli, "_maybe_reexec_checkout_update", lambda: 42)
    assert update_cli.run_update_command() == 42


def test_ui_consumer_cancels_pending_delayed_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sentinel shutdown should cancel a pending delayed TTY render task."""
    import time

    from lib.tests import test_update_ui_modules as ui_tests
    from lib.update import ui_consumer as ui_consumer_module

    consumer, queue = ui_tests._consumer(
        monkeypatch, is_tty=True, render_interval=999.0
    )
    renderer = ui_tests._renderer(consumer)
    renderer.last_render = time.monotonic()
    original_sleep = asyncio.sleep

    async def _run() -> None:
        sleep_started = asyncio.Event()
        never_complete = asyncio.Future[None]()

        async def _sleep(_delay: float) -> None:
            sleep_started.set()
            await never_complete

        monkeypatch.setattr(ui_consumer_module.asyncio, "sleep", _sleep)

        task = asyncio.create_task(consumer.run())
        await queue.put(UpdateEvent.status("demo", "Checking demo (current: 1.0)"))
        await asyncio.wait_for(sleep_started.wait(), timeout=1)
        await queue.put(UpdateEvent.status("demo", "Checking demo (current: 1.1)"))
        await original_sleep(0)
        await queue.put(None)
        await task

    asyncio.run(_run())
    assert renderer.finalized


def test_ui_consumer_delayed_render_can_run_without_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A due delayed render should render immediately without sleeping."""
    from lib.tests import test_update_ui_modules as ui_tests
    from lib.update import ui_consumer as ui_consumer_module

    consumer, queue = ui_tests._consumer(monkeypatch, is_tty=True, render_interval=10.0)
    renderer = ui_tests._renderer(consumer)
    renderer.last_render = 10_000_000.0
    original_create_task = asyncio.create_task
    original_sleep = asyncio.sleep
    scheduled: list[object] = []
    sleep_calls: list[float] = []

    class DoneTask:
        def done(self) -> bool:
            return True

        def cancel(self) -> None:
            return None

    def _create_task(coro: object) -> DoneTask:
        scheduled.append(coro)
        return DoneTask()

    async def _sleep(delay: float) -> None:
        sleep_calls.append(delay)

    async def _run() -> None:
        task = original_create_task(consumer.run())
        monkeypatch.setattr(ui_consumer_module.asyncio, "create_task", _create_task)
        await queue.put(UpdateEvent.status("demo", "Checking demo (current: 1.0)"))
        await original_sleep(0)
        await queue.put(None)
        await task
        assert len(scheduled) == 1
        monkeypatch.setattr(ui_consumer_module.asyncio, "sleep", _sleep)
        renderer.last_render = 0.0
        await cast("Any", scheduled[0])

    asyncio.run(_run())
    assert sleep_calls == []
    assert renderer.finalized


def test_linear_cli_and_superconductor_branch_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover package updater retry classification and zero-attempt branches."""
    from lib.tests._updater_helpers import load_repo_module
    from lib.update.updaters.base import VersionInfo

    linear = load_repo_module(
        "packages/linear-cli/updater.py",
        "linear_cli_updater_branch_more",
    )
    fake_aiohttp_error = type(
        "FakeAiohttpError",
        (Exception,),
        {"__module__": "aiohttp.client_exceptions"},
    )
    assert linear._is_transient_deno_manifest_error(fake_aiohttp_error("temporary"))

    updater = linear.LinearCliUpdater()
    monkeypatch.setattr(linear, "DENO_MANIFEST_ATTEMPTS", 0)
    monkeypatch.setattr(linear.LinearCliUpdater, "PLATFORMS", {"x86_64-linux": "x64"})

    async def _resolve_deno_version() -> str:
        return "2.3.4"

    async def _compute_url_hashes(
        name: str,
        urls: object,
    ) -> AsyncIterator[UpdateEvent]:
        url_list = list(cast("Any", urls))
        yield UpdateEvent.value(name, {url_list[0]: "sha256-demo"})

    monkeypatch.setattr(updater, "_resolve_deno_version", _resolve_deno_version)
    monkeypatch.setattr(linear, "compute_url_hashes", _compute_url_hashes)

    events = _collect(updater.fetch_hashes(VersionInfo("1.0.0"), object()))
    assert events[-1].kind is UpdateEventKind.VALUE
    entries = cast("list[HashEntry]", events[-1].payload)
    assert entries == [
        HashEntry.create(
            "sha256",
            "sha256-demo",
            platform="x86_64-linux",
            url="https://dl.deno.land/release/v2.3.4/denort-x64.zip",
        )
    ]

    superconductor = load_repo_module(
        "packages/superconductor/updater.py",
        "superconductor_updater_branch_more",
    )
    assert (
        asyncio.run(
            superconductor.SuperconductorUpdater()._is_latest(None, VersionInfo("x"))
        )
        is False
    )


def test_nixcfg_command_tree_ignores_non_group_nodes() -> None:
    """Command tree rendering should no-op for commands without children."""
    from rich.tree import Tree

    import nixcfg

    tree = Tree("root")
    command = SimpleNamespace(
        hidden=False,
        help=None,
        short_help=None,
        get_short_help_str=lambda: "",
    )

    nixcfg._add_command_nodes(tree, command)
    assert tree.children == []


def test_zentool_favicon_parser_profile_and_extra_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover favicon parser, running-profile, and tab-extra edge helpers."""
    from lib.tests._zen_tooling import load_zentool_module

    zentool = load_zentool_module("zentool_misc_branch_more")

    parser = zentool.FaviconLinkParser()
    parser.feed(
        "<span></span>"
        "<link rel='stylesheet' href='/style.css'>"
        "<link rel='icon'>"
        "<link rel='shortcut icon' href='/icon.png'>"
    )
    assert parser.hrefs == ["/icon.png"]

    monkeypatch.setattr(zentool, "ZEN_PROFILES", tmp_path / "missing-profiles")
    assert zentool.zen_running_profile_dirs() == []

    extra_tab = zentool.SessionTab(zenSyncId="extra")
    assert zentool._tab_extra(extra_tab) == {}

    class ExtraFailure:
        @property
        def model_extra(self) -> None:
            return None

        def __setattr__(self, _name: str, _value: object) -> None:
            return None

    with pytest.raises(zentool.ZenFoldersError, match="Unable to initialize"):
        zentool._tab_extra(cast("Any", ExtraFailure()))

    assert zentool._origin_url("about:blank") is None
    assert zentool._content_type_from_url("https://example.com/favicon.png") == (
        "image/png"
    )
    assert (
        zentool._image_data_url("https://example.com/favicon.txt", b"x", None) is None
    )
    assert zentool._image_data_url(
        "https://example.com/favicon.bin",
        b"x",
        "image/png",
    ).startswith("data:image/png;base64,")


def test_zentool_favicon_fetch_and_resolution_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover favicon fetch, discovery, data URL, and resolver-cache edges."""
    from lib.tests._zen_tooling import load_zentool_module

    zentool = load_zentool_module("zentool_misc_favicon_more")

    with pytest.raises(zentool.ZenFoldersError, match="unsupported favicon URL scheme"):
        zentool._read_url_bytes("file:///tmp/icon.png", timeout=1.0, max_bytes=1)

    class Headers:
        def __init__(self, content_type: str) -> None:
            self._content_type = content_type

        def get_content_type(self) -> str:
            return self._content_type

    class Response:
        def __init__(self, data: bytes, content_type: str) -> None:
            self._data = data
            self.headers = Headers(content_type)

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

        def read(self, _size: int) -> bytes:
            return self._data

    monkeypatch.setattr(
        zentool.urllib.request,
        "urlopen",
        lambda _request, *, timeout: Response(b"xxx", "image/png"),
    )
    with pytest.raises(zentool.ZenFoldersError, match="response too large"):
        zentool._read_url_bytes(
            "https://example.com/icon.png",
            timeout=1.0,
            max_bytes=2,
        )

    monkeypatch.setattr(
        zentool.urllib.request,
        "urlopen",
        lambda _request, *, timeout: Response(b"x", "image/png"),
    )
    assert zentool._read_url_bytes(
        "https://example.com/icon.png",
        timeout=1.0,
        max_bytes=2,
    ) == (b"x", "image/png")

    monkeypatch.setattr(
        zentool,
        "_read_url_bytes",
        lambda _url, *, timeout, max_bytes: (
            b"<link rel='icon' href='/icon.png'>",
            "text/html",
        ),
    )
    assert zentool._discover_favicon_urls("https://example.com/page") == [
        "https://example.com/icon.png"
    ]
    monkeypatch.setattr(
        zentool,
        "_read_url_bytes",
        lambda _url, *, timeout, max_bytes: (b"{}", "application/json"),
    )
    assert zentool._discover_favicon_urls("https://example.com/page") == []

    def _raise_zen_error(*_args: object, **_kwargs: object) -> tuple[bytes, str]:
        raise zentool.ZenFoldersError("network")

    monkeypatch.setattr(zentool, "_read_url_bytes", _raise_zen_error)
    assert zentool._discover_favicon_urls("https://example.com/page") == []
    assert zentool._fetch_image_data_url("https://example.com/icon.png") is None
    monkeypatch.setattr(
        zentool,
        "_read_url_bytes",
        lambda _url, *, timeout, max_bytes: (b"x", "image/png"),
    )
    assert zentool._fetch_image_data_url("https://example.com/icon.png") == (
        "data:image/png;base64,eA=="
    )

    assert zentool.resolve_favicon_data_url("about:blank") is None
    monkeypatch.setattr(
        zentool,
        "_discover_favicon_urls",
        lambda _url: ["data:image/png;base64,cached"],
    )
    assert zentool.resolve_favicon_data_url("https://example.com/page") == (
        "data:image/png;base64,cached"
    )
    monkeypatch.setattr(zentool, "_discover_favicon_urls", lambda _url: [])
    monkeypatch.setattr(zentool, "_fetch_image_data_url", lambda _url: None)
    assert zentool.resolve_favicon_data_url("https://example.com/page") is None

    calls: list[str] = []
    monkeypatch.setattr(
        zentool,
        "resolve_favicon_data_url",
        lambda url: calls.append(url) or "data:image/png;base64,cache",
    )
    resolver = zentool.FaviconResolver()
    assert resolver("https://example.com") == "data:image/png;base64,cache"
    assert resolver("https://example.com") == "data:image/png;base64,cache"
    assert calls == ["https://example.com"]


def test_zentool_tab_image_resolution_edges() -> None:
    """Cover tab image clearing and unresolved favicon candidate paths."""
    from lib.tests._zen_tooling import (
        load_zentool_module,
        make_session_entry,
        make_session_tab,
    )

    zentool = load_zentool_module("zentool_misc_tab_image_more")

    image_tab = make_session_tab(
        zentool,
        entries=[make_session_entry(zentool, url="https://old.example", title="Old")],
        image="data:image/png;base64,old",
    )
    zentool.maybe_resolve_tab_image(
        image_tab,
        desired_url="",
        previous_url="",
        reset_runtime_entry=False,
        favicon_resolver=lambda _url: pytest.fail("blank URL should not resolve"),
    )
    assert zentool.tab_image(image_tab) is None

    cross_origin = make_session_tab(
        zentool,
        entries=[make_session_entry(zentool, url="https://old.example", title="Old")],
        image="data:image/png;base64,old",
    )
    zentool.maybe_resolve_tab_image(
        cross_origin,
        desired_url="https://new.example",
        previous_url="https://old.example",
        reset_runtime_entry=True,
        favicon_resolver=None,
    )
    assert zentool.tab_image(cross_origin) is None

    unresolved = make_session_tab(
        zentool,
        entries=[make_session_entry(zentool, url="https://new.example", title="New")],
    )
    zentool.maybe_resolve_tab_image(
        unresolved,
        desired_url="https://new.example",
        previous_url="",
        reset_runtime_entry=False,
        favicon_resolver=lambda _url: None,
    )
    assert zentool.tab_image(unresolved) is None

    no_candidates = zentool.SessionState(
        tabs=[
            make_session_tab(zentool, entries=[], sync_id="blank"),
            make_session_tab(
                zentool,
                entries=[make_session_entry(zentool, url="https://has-image.example")],
                sync_id="has-image",
                image="data:image/png;base64,old",
            ),
        ]
    )
    assert (
        zentool.resolve_missing_tab_images(
            no_candidates,
            lambda _url: pytest.fail("no candidate should resolve"),
        )
        == 0
    )

    unresolved_session = zentool.SessionState(
        tabs=[
            make_session_tab(
                zentool,
                entries=[make_session_entry(zentool, url="https://missing.example")],
                sync_id="missing-image",
            )
        ]
    )
    logs: list[str] = []
    assert (
        zentool.resolve_missing_tab_images(
            unresolved_session,
            lambda _url: None,
            log=logs.append,
        )
        == 0
    )
    assert logs[-1] == "Resolved favicon images for 0 of 1 tab(s)."


def test_zentool_state_apply_plan_runtime_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover apply-plan fallback for runtime-only session changes."""
    from lib.tests._zen_tooling import load_zentool_module

    zentool = load_zentool_module("zentool_misc_apply_plan_more")

    current = zentool.SessionState()
    desired = zentool.SessionState(tabs=[zentool.SessionTab(zenSyncId="runtime")])
    containers = zentool.ContainerState()
    container_plan = zentool.ContainerPlan(state=containers, context_ids_by_key={})

    class EmptyDiff:
        def __bool__(self) -> bool:
            return False

        def pretty(self) -> str:
            return ""

    monkeypatch.setattr(zentool, "require_zen_closed", lambda _profile: None)
    monkeypatch.setattr(zentool, "_print_runtime_warnings", lambda _runtime: None)
    monkeypatch.setattr(
        zentool,
        "load_session",
        lambda _profile: (tmp_path / "zen-sessions.jsonlz4", current),
    )
    monkeypatch.setattr(
        zentool,
        "load_containers",
        lambda _profile: (tmp_path / "containers.json", containers),
    )
    monkeypatch.setattr(zentool, "load_config", lambda _path: zentool.ZenConfig())
    monkeypatch.setattr(
        zentool,
        "build_desired_containers",
        lambda _containers, _config, _session: container_plan,
    )
    monkeypatch.setattr(
        zentool,
        "build_desired_state",
        lambda _session, _config, _container_plan: desired,
    )
    monkeypatch.setattr(zentool, "snapshot", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(zentool, "DeepDiff", lambda *_args, **_kwargs: EmptyDiff())

    plan = zentool.build_state_apply_plan(
        SimpleNamespace(
            profile=None,
            config=str(tmp_path / "folders.yaml"),
            resolve_favicons=False,
        )
    )
    assert plan.diff_text == "Updated runtime session fields."
