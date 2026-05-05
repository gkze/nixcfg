"""Focused tests for the neutils updater helpers."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from types import ModuleType

import pytest

from lib.nix.models.sources import HashEntry
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import collect_events as _collect_events
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import (
    CommandResult,
    UpdateEvent,
    UpdateEventKind,
    expect_artifact_updates,
)
from lib.update.paths import REPO_ROOT
from lib.update.updaters.base import VersionInfo


def _load_module(name: str = "neutils_updater_test") -> ModuleType:
    return load_repo_module("packages/neutils/updater.py", name)


def _build_archive(*, include_build_zig_zon: bool = True) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("neutils-0.7.2/")
        info.type = tarfile.DIRTYPE
        archive.addfile(info)
        name = (
            "neutils-0.7.2/build.zig.zon"
            if include_build_zig_zon
            else "neutils-0.7.2/README.md"
        )
        payload = b".{}\n"
        file_info = tarfile.TarInfo(name)
        file_info.size = len(payload)
        archive.addfile(file_info, io.BytesIO(payload))
    return buffer.getvalue()


def test_extract_archive_returns_build_zig_zon_path(tmp_path: Path) -> None:
    """Extract the archive and return the discovered build.zig.zon path."""
    module = _load_module("neutils_updater_test_extract")

    path = module.NeutilsUpdater._extract_archive(_build_archive(), tmp_path)

    assert path == tmp_path / "neutils-0.7.2" / "build.zig.zon"
    assert path.read_text(encoding="utf-8") == ".{}\n"


def test_extract_archive_requires_build_zig_zon(tmp_path: Path) -> None:
    """Fail clearly when the release archive lacks the Zig lockfile."""
    module = _load_module("neutils_updater_test_extract_missing")

    with pytest.raises(RuntimeError, match=r"Could not locate build\.zig\.zon"):
        module.NeutilsUpdater._extract_archive(
            _build_archive(include_build_zig_zon=False),
            tmp_path,
        )


def test_resolve_installable_path_returns_last_output_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the final non-empty nix build out path from the command result."""
    module = _load_module("neutils_updater_test_resolve_installable")
    updater = module.NeutilsUpdater()
    calls: list[tuple[list[str], object]] = []

    async def _run_command(args: list[str], *, options):
        calls.append((args, options))
        yield UpdateEvent.status(updater.name, "building installable")
        yield UpdateEvent.value(
            updater.name,
            CommandResult(
                args=args,
                returncode=0,
                stdout="\n/nix/store/old\n\n/nix/store/final\n",
                stderr="",
            ),
        )

    monkeypatch.setattr(module, "run_command", _run_command)

    events = _run(_collect_events(updater._resolve_installable_path("flake#tool")))

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.VALUE,
    ]
    assert events[0].message == "building installable"
    assert events[1].payload == "/nix/store/final"
    assert calls[0][0] == [
        "nix",
        "build",
        "--no-link",
        "--print-out-paths",
        "flake#tool",
    ]
    assert calls[0][1].source == updater.name
    assert calls[0][1].config == updater.config


@pytest.mark.parametrize(
    ("result", "match"),
    [
        (
            CommandResult(
                args=["nix", "build"],
                returncode=1,
                stdout="",
                stderr="boom",
            ),
            "boom",
        ),
        (
            CommandResult(
                args=["nix", "build"],
                returncode=0,
                stdout="\n \n",
                stderr="",
            ),
            "nix build returned no out path",
        ),
    ],
)
def test_resolve_installable_path_rejects_bad_command_results(
    monkeypatch: pytest.MonkeyPatch,
    result: CommandResult,
    match: str,
) -> None:
    """Surface command failures and empty nix build outputs clearly."""
    module = _load_module(f"neutils_updater_test_resolve_bad_{result.returncode}")
    updater = module.NeutilsUpdater()

    async def _run_command(_args: list[str], *, options):
        _ = options
        yield UpdateEvent.value(updater.name, result)

    monkeypatch.setattr(module, "run_command", _run_command)

    with pytest.raises(RuntimeError, match=match):
        _run(_collect_events(updater._resolve_installable_path("flake#tool")))


def test_render_build_zig_zon_nix_renders_artifact_with_resolved_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fetch the archive, resolve tool paths, run zon2nix, and return the file content."""
    module = _load_module("neutils_updater_test_render")
    updater = module.NeutilsUpdater()
    info = VersionInfo(version="0.7.2")
    installables: list[str] = []
    command_calls: list[tuple[list[str], object]] = []

    async def _fetch_url(session, url: str, *, request_timeout=None, config=None):
        assert session is session_obj
        assert url == updater._archive_url("0.7.2")
        assert request_timeout == updater.config.default_timeout
        assert config == updater.config
        return _build_archive()

    async def _resolve(installable: str):
        installables.append(installable)
        yield UpdateEvent.value(
            updater.name,
            "/nix/store/zig-tool"
            if installable.endswith("zig_0_15")
            else "/nix/store/zon2nix-tool",
        )

    async def _run_command(args: list[str], *, options):
        command_calls.append((args, options))
        output_arg = next(arg for arg in args if arg.startswith("--nix="))
        Path(output_arg.removeprefix("--nix=")).write_text(
            "# rendered\n", encoding="utf-8"
        )
        yield UpdateEvent.status(updater.name, "running zon2nix")
        yield UpdateEvent.value(
            updater.name,
            CommandResult(args=args, returncode=0, stdout="ok", stderr=""),
        )

    monkeypatch.setattr(module, "fetch_url", _fetch_url)
    monkeypatch.setattr(module, "get_current_nix_platform", lambda: "aarch64-darwin")
    monkeypatch.setattr(module, "get_repo_file", lambda _path: Path("/repo/root"))
    monkeypatch.setattr(updater, "_resolve_installable_path", _resolve)
    monkeypatch.setattr(module, "run_command", _run_command)

    session_obj = object()
    events = _run(_collect_events(updater._render_build_zig_zon_nix(info, session_obj)))

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.VALUE,
    ]
    assert events[0].message == "running zon2nix"
    assert events[1].payload == "# rendered\n"
    assert installables == [
        "path:/repo/root#pkgs.aarch64-darwin.zig_0_15",
        updater._ZON2NIX_FLAKE,
    ]
    assert command_calls[0][0][0] == "/nix/store/zon2nix-tool/bin/zon2nix"
    assert command_calls[0][0][2].endswith("/build.zig.zon")
    assert command_calls[0][1].env["PATH"].startswith("/nix/store/zig-tool/bin:")
    assert command_calls[0][1].env["HOME"].endswith("/.home")
    assert command_calls[0][1].env["XDG_CACHE_HOME"].endswith("/.cache")


def test_render_build_zig_zon_nix_surfaces_zon2nix_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise the zon2nix stderr when the helper command fails."""
    module = _load_module("neutils_updater_test_render_failure")
    updater = module.NeutilsUpdater()

    async def _fetch_url(*_args, **_kwargs):
        return _build_archive()

    async def _resolve(_installable: str):
        yield UpdateEvent.value(updater.name, "/nix/store/tool")

    async def _run_command(args: list[str], *, options):
        _ = (args, options)
        yield UpdateEvent.value(
            updater.name,
            CommandResult(
                args=["zon2nix"], returncode=1, stdout="", stderr="bad lockfile"
            ),
        )

    monkeypatch.setattr(module, "fetch_url", _fetch_url)
    monkeypatch.setattr(module, "get_current_nix_platform", lambda: "aarch64-darwin")
    monkeypatch.setattr(module, "get_repo_file", lambda _path: Path("/repo/root"))
    monkeypatch.setattr(updater, "_resolve_installable_path", _resolve)
    monkeypatch.setattr(module, "run_command", _run_command)

    with pytest.raises(RuntimeError, match="bad lockfile"):
        _run(
            _collect_events(
                updater._render_build_zig_zon_nix(
                    VersionInfo(version="0.7.2"), object()
                )
            )
        )


@pytest.mark.parametrize(
    "transient_stderr",
    [
        "err(default): NameServerFailure",
        "err(default): HttpConnectionClosing",
    ],
)
def test_render_build_zig_zon_nix_retries_transient_zon2nix_failure(
    monkeypatch: pytest.MonkeyPatch,
    transient_stderr: str,
) -> None:
    """Retry transient Zig package fetch failures before surfacing the artifact."""
    module = _load_module("neutils_updater_test_render_retry")
    updater = module.NeutilsUpdater()
    command_calls: list[list[str]] = []
    sleep_delays: list[float] = []

    async def _fetch_url(*_args, **_kwargs):
        return _build_archive()

    async def _resolve(_installable: str):
        yield UpdateEvent.value(updater.name, "/nix/store/tool")

    async def _run_command(args: list[str], *, options):
        _ = options
        command_calls.append(args)
        if len(command_calls) == 1:
            yield UpdateEvent.value(
                updater.name,
                CommandResult(
                    args=args,
                    returncode=1,
                    stdout="",
                    stderr=transient_stderr,
                ),
            )
            return
        output_arg = next(arg for arg in args if arg.startswith("--nix="))
        Path(output_arg.removeprefix("--nix=")).write_text(
            "# rendered after retry\n", encoding="utf-8"
        )
        yield UpdateEvent.value(
            updater.name,
            CommandResult(args=args, returncode=0, stdout="ok", stderr=""),
        )

    async def _sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr(module, "fetch_url", _fetch_url)
    monkeypatch.setattr(module, "get_current_nix_platform", lambda: "aarch64-darwin")
    monkeypatch.setattr(module, "get_repo_file", lambda _path: Path("/repo/root"))
    monkeypatch.setattr(updater, "_resolve_installable_path", _resolve)
    monkeypatch.setattr(module, "run_command", _run_command)
    monkeypatch.setattr(module.asyncio, "sleep", _sleep)

    events = _run(
        _collect_events(
            updater._render_build_zig_zon_nix(VersionInfo(version="0.7.2"), object())
        )
    )

    assert len(command_calls) == 2
    assert sleep_delays == [updater.config.default_retry_backoff]
    assert [
        event.message for event in events if event.kind is UpdateEventKind.STATUS
    ] == ["zon2nix hit a transient fetch failure; retrying..."]
    assert events[-1].payload == "# rendered after retry\n"


def test_render_build_zig_zon_nix_yields_tool_resolution_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass through status events emitted while resolving the Zig and zon2nix tools."""
    module = _load_module("neutils_updater_test_render_resolution_events")
    updater = module.NeutilsUpdater()

    async def _fetch_url(*_args, **_kwargs):
        return _build_archive()

    async def _resolve(installable: str):
        yield UpdateEvent.status(updater.name, f"resolving {installable}")
        yield UpdateEvent.value(
            updater.name,
            "/nix/store/zig-tool"
            if installable.endswith("zig_0_15")
            else "/nix/store/zon2nix-tool",
        )

    async def _run_command(args: list[str], *, options):
        _ = options
        output_arg = next(arg for arg in args if arg.startswith("--nix="))
        Path(output_arg.removeprefix("--nix=")).write_text(
            "# rendered\n", encoding="utf-8"
        )
        yield UpdateEvent.value(
            updater.name,
            CommandResult(args=args, returncode=0, stdout="ok", stderr=""),
        )

    monkeypatch.setattr(module, "fetch_url", _fetch_url)
    monkeypatch.setattr(module, "get_current_nix_platform", lambda: "aarch64-darwin")
    monkeypatch.setattr(module, "get_repo_file", lambda _path: Path("/repo/root"))
    monkeypatch.setattr(updater, "_resolve_installable_path", _resolve)
    monkeypatch.setattr(module, "run_command", _run_command)

    events = _run(
        _collect_events(
            updater._render_build_zig_zon_nix(VersionInfo(version="0.7.2"), object())
        )
    )

    assert [event.message for event in events[:-1]] == [
        "resolving path:/repo/root#pkgs.aarch64-darwin.zig_0_15",
        f"resolving {updater._ZON2NIX_FLAKE}",
    ]
    assert events[-1].payload == "# rendered\n"


def test_fetch_hashes_requires_package_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail fast when the package directory cannot be located."""
    module = _load_module("neutils_updater_test_missing_pkg_dir")
    updater = module.NeutilsUpdater()

    monkeypatch.setattr(module, "package_dir_for", lambda _name: None)

    with pytest.raises(RuntimeError, match="Package directory not found for neutils"):
        _run(
            _collect_events(
                updater.fetch_hashes(VersionInfo(version="0.7.2"), object())
            )
        )


def test_fetch_hashes_emits_generated_artifact_and_src_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emit the generated Zig materialization before the source hash value."""
    module = _load_module("neutils_updater_test_fetch_hashes")
    updater = module.NeutilsUpdater()

    async def _render(_info: object, _session: object):
        yield UpdateEvent.status(updater.name, "rendering artifact")
        yield UpdateEvent.value(updater.name, "# generated\n")

    async def _fixed_hash(name: str, expr: str, *, config=None):
        assert name == updater.name
        assert_nix_ast_equal(expr, updater._src_expr("0.7.2"))
        assert config == updater.config
        yield UpdateEvent.status(name, "hashing src")
        yield UpdateEvent.value(name, "sha256-src")

    monkeypatch.setattr(updater, "_render_build_zig_zon_nix", _render)
    monkeypatch.setattr(
        module, "package_dir_for", lambda _name: REPO_ROOT / "packages" / "neutils"
    )
    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)

    events = _run(
        _collect_events(updater.fetch_hashes(VersionInfo(version="0.7.2"), object()))
    )

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.STATUS,
        UpdateEventKind.ARTIFACT,
        UpdateEventKind.STATUS,
        UpdateEventKind.VALUE,
    ]
    assert events[0].message == "Refreshing build.zig.zon.nix..."
    artifacts = expect_artifact_updates(events[2].payload)
    assert len(artifacts) == 1
    assert artifacts[0].path == REPO_ROOT / "packages" / "neutils" / "build.zig.zon.nix"
    assert artifacts[0].content == "# generated\n"
    assert events[-1].payload == [HashEntry.create("srcHash", "sha256-src")]
