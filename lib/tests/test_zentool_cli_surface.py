"""Focused pure-Python tests for the zentool CLI surface."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from lib.tests._zen_tooling import load_zen_script_module

if TYPE_CHECKING:
    from types import ModuleType


@pytest.fixture(scope="module")
def zentool() -> ModuleType:
    """Load the zentool script for CLI-surface testing."""
    return load_zen_script_module("zentool", "zentool_cli_surface")


def test_cmd_inspect_workspaces_handles_empty_and_populated_sessions(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Workspace inspection should print the empty and populated branches."""
    lines: list[str] = []
    monkeypatch.setattr(zentool, "_stdout", lines.append)
    monkeypatch.setattr(
        zentool,
        "load_session",
        lambda _profile: (
            Path("/tmp/session"),
            SimpleNamespace(spaces=[]),
        ),
    )

    assert zentool.cmd_inspect_workspaces(SimpleNamespace(profile="default")) == 0
    assert lines == ["No workspaces found."]

    lines.clear()
    monkeypatch.setattr(
        zentool,
        "load_session",
        lambda _profile: (
            Path("/tmp/session"),
            SimpleNamespace(
                spaces=[
                    SimpleNamespace(name="Work", uuid="uuid-1"),
                    SimpleNamespace(name="Play", uuid="uuid-2"),
                ]
            ),
        ),
    )

    assert zentool.cmd_inspect_workspaces(SimpleNamespace(profile="default")) == 0
    assert lines == ["Work [uuid-1]", "Play [uuid-2]"]


def test_cmd_inspect_raw_writes_literal_payload_to_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Literal raw inspection should decode and write the underlying payload."""
    session_path = tmp_path / zentool.SESSION_FILENAME
    payload = {"b": 1, "a": [2]}
    encoded = json.dumps(payload).encode("utf-8")
    size = len(encoded)
    session_path.write_bytes(
        zentool.SESSION_HEADER_PREFIX
        + size.to_bytes(
            zentool.SESSION_HEADER_SIZE - len(zentool.SESSION_HEADER_PREFIX), "little"
        )
        + b"compressed"
    )
    written = tmp_path / "raw.json"
    lines: list[str] = []

    monkeypatch.setattr(zentool, "session_file", lambda _profile: session_path)
    monkeypatch.setattr(zentool.lz4.block, "decompress", lambda _data, _size: encoded)
    monkeypatch.setattr(zentool, "_stdout", lines.append)

    args = SimpleNamespace(profile="default", literal=True, output=str(written))
    assert zentool.cmd_inspect_raw(args) == 0
    assert json.loads(written.read_text(encoding="utf-8")) == payload
    assert lines == [f"Written to {written}"]


def test_cmd_inspect_raw_prints_normalized_session_json(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Normalized raw inspection should print the model dump to stdout."""
    chunks: list[str] = []

    class FakeSession:
        def model_dump(self, *, mode: str, exclude_none: bool) -> dict[str, object]:
            assert mode == "json"
            assert exclude_none is False
            return {"z": 1, "a": None}

    monkeypatch.setattr(zentool, "session_file", lambda _profile: Path("/tmp/session"))
    monkeypatch.setattr(zentool, "read_session", lambda _path: FakeSession())
    monkeypatch.setattr(zentool, "_stdout_raw", chunks.append)

    args = SimpleNamespace(profile="default", literal=False, output=None)
    assert zentool.cmd_inspect_raw(args) == 0
    assert chunks == ['{\n  "a": null,\n  "z": 1\n}\n']


def test_cmd_check_reports_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Session checking should map clean and failing results to exit codes."""
    lines: list[str] = []
    monkeypatch.setattr(zentool, "_stdout", lines.append)
    monkeypatch.setattr(
        zentool,
        "load_session",
        lambda _profile: (Path("/tmp/session"), object()),
    )
    monkeypatch.setattr(
        zentool,
        "load_containers",
        lambda _profile: (Path("/tmp/containers.json"), object()),
    )
    monkeypatch.setattr(zentool, "session_check", lambda _session, _containers: [])

    assert zentool.cmd_check(SimpleNamespace(profile="default")) == 0
    assert lines == ["Session check passed: no structural errors found."]

    lines.clear()
    monkeypatch.setattr(
        zentool,
        "session_check",
        lambda _session, _containers: ["first issue", "second issue"],
    )

    assert zentool.cmd_check(SimpleNamespace(profile="default")) == 1
    assert lines == [
        "Session check found 2 error(s):",
        "  - first issue",
        "  - second issue",
    ]


@pytest.mark.parametrize(
    ("running", "expected_rc", "expected_line"),
    [(True, 0, "running"), (False, 1, "not-running")],
)
def test_cmd_profile_is_running_reports_runtime_state(
    running: bool,
    expected_rc: int,
    expected_line: str,
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Runtime inspection should print warnings and a stable status token."""
    lines: list[str] = []
    warnings: list[object] = []
    state = zentool.ZenRuntimeState(running=running, warnings=("stale-lock",))

    monkeypatch.setattr(zentool, "inspect_zen_runtime", lambda _profile: state)
    monkeypatch.setattr(zentool, "_print_runtime_warnings", warnings.append)
    monkeypatch.setattr(zentool, "_stdout", lines.append)

    assert (
        zentool.cmd_profile_is_running(SimpleNamespace(profile="default"))
        == expected_rc
    )
    assert warnings == [state]
    assert lines == [expected_line]


def test_cmd_profile_path_prints_resolved_directory(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Profile path inspection should print the resolved directory."""
    lines: list[str] = []
    monkeypatch.setattr(
        zentool, "zen_profile_dir", lambda _profile: Path("/tmp/profile")
    )
    monkeypatch.setattr(zentool, "_stdout", lines.append)

    assert zentool.cmd_profile_path(SimpleNamespace(profile="default")) == 0
    assert lines == ["/tmp/profile"]


def test_typer_wrappers_forward_to_existing_namespace_handlers(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Typer command functions should preserve handler namespace payloads."""
    calls: list[tuple[str, SimpleNamespace]] = []

    def _handler(name: str):
        def _inner(args: SimpleNamespace) -> int:
            calls.append((name, args))
            return len(calls)

        return _inner

    for handler_name in (
        "cmd_apply",
        "cmd_check",
        "cmd_diff",
        "cmd_inspect_folders",
        "cmd_inspect_raw",
        "cmd_inspect_tabs",
        "cmd_inspect_workspaces",
        "cmd_profile_is_running",
        "cmd_profile_path",
        "cmd_validate",
    ):
        monkeypatch.setattr(zentool, handler_name, _handler(handler_name))

    root_ctx = SimpleNamespace(obj={"profile": "Root"}, parent=None)
    child_ctx = SimpleNamespace(obj={}, parent=root_ctx)
    assert zentool._context_profile(child_ctx) == "Root"

    assert zentool._typer_validate(config="folders.yaml") == 1
    assert (
        zentool._typer_diff(
            child_ctx,
            profile=None,
            config="folders.yaml",
            asset_dir="assets",
            chrome_source="chrome",
            user_js_source="user.js",
            state=True,
            assets=False,
        )
        == 2
    )
    assert (
        zentool._typer_apply(
            child_ctx,
            profile="Local",
            config="folders.yaml",
            asset_dir="assets",
            chrome_source=None,
            user_js_source=None,
            state=False,
            assets=True,
            yes=True,
        )
        == 3
    )
    assert zentool._typer_check(child_ctx, profile=None) == 4

    inspect_ctx = SimpleNamespace(obj={}, parent=root_ctx)
    zentool._typer_inspect_root(inspect_ctx, profile=None)
    assert inspect_ctx.obj == {"profile": "Root"}
    assert zentool._typer_inspect_folders(inspect_ctx) == 5
    assert zentool._typer_inspect_tabs(inspect_ctx) == 6
    assert zentool._typer_inspect_workspaces(inspect_ctx) == 7
    assert zentool._typer_inspect_raw(inspect_ctx, literal=True, output="raw.json") == 8

    profile_ctx = SimpleNamespace(obj={}, parent=root_ctx)
    zentool._typer_profile_root(profile_ctx, profile="Profile")
    assert profile_ctx.obj == {"profile": "Profile"}
    assert zentool._typer_profile_path(profile_ctx) == 9
    assert zentool._typer_profile_is_running(profile_ctx) == 10

    by_name = dict(calls)
    assert by_name["cmd_validate"].command == "validate"
    assert by_name["cmd_diff"].profile == "Root"
    assert by_name["cmd_apply"].profile == "Local"
    assert by_name["cmd_apply"].yes is True
    assert by_name["cmd_inspect_raw"].literal is True
    assert by_name["cmd_profile_path"].profile == "Profile"


def test_main_routes_typer_commands(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Main should invoke direct, scoped, and nested Typer commands."""
    seen: list[tuple[str, object]] = []

    def check_handler(args: SimpleNamespace) -> int:
        seen.append(("check", args.command))
        return 11

    def apply_handler(args: SimpleNamespace) -> int:
        seen.append(("apply", args.profile, args.state, args.yes))
        return 17

    def nested_handler(args: SimpleNamespace) -> int:
        seen.append(("nested", args.inspect_command))
        return 22

    monkeypatch.setattr(zentool, "cmd_check", check_handler)
    monkeypatch.setattr(zentool, "cmd_apply", apply_handler)
    monkeypatch.setattr(zentool, "cmd_inspect_raw", nested_handler)

    assert zentool.main(["check"]) == 11
    assert (
        zentool.main([
            "apply",
            "--profile",
            "Default (twilight)",
            "--state",
            "--yes",
        ])
        == 17
    )
    assert zentool.main(["inspect", "raw"]) == 22
    assert seen == [
        ("check", "check"),
        ("apply", "Default (twilight)", True, True),
        ("nested", "raw"),
    ]


def test_main_handles_help_errors_and_interrupts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    zentool: ModuleType,
) -> None:
    """Main should cover Typer help, handled errors, and interrupts."""
    stderr_lines: list[str] = []

    assert zentool.main([]) == 0
    assert "usage: zentool" in capsys.readouterr().out.lower()

    monkeypatch.setattr(
        zentool,
        "cmd_check",
        lambda _args: (_ for _ in ()).throw(zentool.ZentoolError("bad state")),
    )
    monkeypatch.setattr(zentool, "_stderr", stderr_lines.append)
    assert zentool.main(["check"]) == 1
    assert stderr_lines == ["Error: bad state"]

    monkeypatch.setattr(
        zentool,
        "cmd_check",
        lambda _args: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    assert zentool.main(["check"]) == 130
    assert stderr_lines[-1] == "Interrupted."


def test_main_maps_typer_interrupt_and_click_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Typer execution should map interrupts and Click errors to stable codes."""
    stderr_lines: list[str] = []
    shown: list[str] = []

    class _Command:
        def __init__(self, exc: BaseException) -> None:
            self.exc = exc

        def main(self, **_kwargs: object) -> object:
            raise self.exc

    class _ClickError(zentool.click.ClickException):
        exit_code = 9

        def show(self, file: object | None = None) -> None:
            _ = file
            shown.append(self.message)

    monkeypatch.setattr(zentool, "_stderr", stderr_lines.append)

    monkeypatch.setattr(
        zentool,
        "get_command",
        lambda _app: _Command(KeyboardInterrupt()),
    )
    assert zentool.main(["check"]) == 130
    assert stderr_lines == ["Interrupted."]

    monkeypatch.setattr(
        zentool,
        "get_command",
        lambda _app: _Command(zentool.click.Abort()),
    )
    assert zentool.main(["check"]) == 130
    assert stderr_lines[-1] == "Interrupted."

    monkeypatch.setattr(
        zentool,
        "get_command",
        lambda _app: _Command(
            zentool.click.exceptions.Exit(zentool.INTERRUPTED_EXIT_CODE)
        ),
    )
    assert zentool.main(["check"]) == 130
    assert stderr_lines[-1] == "Interrupted."

    monkeypatch.setattr(
        zentool,
        "get_command",
        lambda _app: _Command(zentool.click.exceptions.Exit(7)),
    )
    assert zentool.main(["check"]) == 7

    monkeypatch.setattr(
        zentool,
        "get_command",
        lambda _app: _Command(_ClickError("bad click")),
    )
    assert zentool.main(["check"]) == 9
    assert shown == ["bad click"]
