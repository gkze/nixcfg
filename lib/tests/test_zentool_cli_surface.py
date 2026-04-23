"""Focused pure-Python tests for the zentool CLI surface."""

from __future__ import annotations

import argparse
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
    monkeypatch.setattr(zentool, "session_check", lambda _session: [])

    assert zentool.cmd_check(SimpleNamespace(profile="default")) == 0
    assert lines == ["Session check passed: no structural errors found."]

    lines.clear()
    monkeypatch.setattr(
        zentool,
        "session_check",
        lambda _session: ["first issue", "second issue"],
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


def test_add_profile_argument_sets_expected_option_shape(zentool: ModuleType) -> None:
    """The shared profile argument should keep its documented flag surface."""
    parser = argparse.ArgumentParser()

    zentool._add_profile_argument(parser)

    assert parser.parse_args([]).profile is None
    assert parser.parse_args(["--profile", "Work"]).profile == "Work"
    action = next(action for action in parser._actions if action.dest == "profile")
    assert "-p" in action.option_strings
    assert "--profile" in action.option_strings
    assert action.help == zentool.PROFILE_HELP


def test_add_scoped_operation_arguments_sets_shared_defaults(
    zentool: ModuleType,
) -> None:
    """Diff/apply parsers should share the expected profile/config/asset flags."""
    parser = argparse.ArgumentParser()

    zentool._add_scoped_operation_arguments(parser)

    defaults = parser.parse_args([])
    assert defaults.profile is None
    assert defaults.config == str(zentool.DEFAULT_YAML)
    assert defaults.asset_dir == str(zentool.DEFAULT_CONFIG_DIR)
    assert defaults.chrome_source is None
    assert defaults.user_js_source is None
    assert defaults.state is False
    assert defaults.assets is False

    parsed = parser.parse_args([
        "-p",
        "Work",
        "--state",
        "--assets",
        "--chrome-source",
        "/tmp/chrome",
        "--user-js-source",
        "/tmp/user.js",
    ])
    assert parsed.profile == "Work"
    assert parsed.state is True
    assert parsed.assets is True
    assert parsed.chrome_source == "/tmp/chrome"
    assert parsed.user_js_source == "/tmp/user.js"


def test_build_parser_parses_nested_cli_shapes(zentool: ModuleType) -> None:
    """The public parser should expose the targeted inspect/profile/main flags."""
    parser = zentool.build_parser()

    inspect_raw = parser.parse_args([
        "inspect",
        "raw",
        "--literal",
        "--output",
        "session.json",
    ])
    assert inspect_raw.command == "inspect"
    assert inspect_raw.inspect_command == "raw"
    assert inspect_raw.literal is True
    assert inspect_raw.output == "session.json"
    assert inspect_raw.profile is None

    profile_running = parser.parse_args(["profile", "--profile", "Work", "is-running"])
    assert profile_running.command == "profile"
    assert profile_running.profile_command == "is-running"
    assert profile_running.profile == "Work"

    check_args = parser.parse_args(["check", "-p", "Default"])
    assert check_args.command == "check"
    assert check_args.profile == "Default"


def test_dispatch_nested_command_routes_known_nested_commands(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Nested dispatch should map inspect/profile verbs to handlers."""
    parser = zentool.build_parser()
    inspect_handler = object()
    profile_handler = object()
    monkeypatch.setattr(zentool, "cmd_inspect_raw", inspect_handler)
    monkeypatch.setattr(zentool, "cmd_profile_is_running", profile_handler)

    assert (
        zentool._dispatch_nested_command(
            parser,
            SimpleNamespace(command="inspect", inspect_command="raw"),
        )
        is inspect_handler
    )
    assert (
        zentool._dispatch_nested_command(
            parser,
            SimpleNamespace(command="profile", profile_command="is-running"),
        )
        is profile_handler
    )
    assert (
        zentool._dispatch_nested_command(
            parser,
            SimpleNamespace(command="check"),
        )
        is None
    )


def test_dispatch_nested_command_requests_help_when_nested_command_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Missing nested verbs should bounce back through parser help parsing."""
    calls: list[list[str]] = []

    class FakeParser:
        def parse_args(self, argv: list[str]) -> None:
            calls.append(argv)

    parser = FakeParser()

    assert (
        zentool._dispatch_nested_command(
            parser,
            SimpleNamespace(command="inspect", inspect_command=None),
        )
        is None
    )
    assert (
        zentool._dispatch_nested_command(
            parser,
            SimpleNamespace(command="profile", profile_command=None),
        )
        is None
    )
    assert calls == [["inspect", "-h"], ["profile", "-h"]]


def test_main_routes_direct_and_nested_commands(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Main should invoke direct commands and nested fallback dispatch."""
    parser = zentool.build_parser()
    seen: list[tuple[str, object]] = []

    def direct_handler(args: argparse.Namespace) -> int:
        seen.append(("check", args.command))
        return 11

    def nested_handler(args: argparse.Namespace) -> int:
        seen.append(("nested", args.inspect_command))
        return 22

    monkeypatch.setattr(zentool, "build_parser", lambda: parser)
    monkeypatch.setattr(zentool, "cmd_check", direct_handler)
    monkeypatch.setattr(
        zentool,
        "_dispatch_nested_command",
        lambda _parser, args: nested_handler if args.command == "inspect" else None,
    )

    assert zentool.main(["check"]) == 11
    assert zentool.main(["inspect", "raw"]) == 22
    assert seen == [("check", "check"), ("nested", "raw")]


def test_main_handles_help_errors_interrupts_and_missing_nested_handler(
    monkeypatch: pytest.MonkeyPatch,
    zentool: ModuleType,
) -> None:
    """Main should cover no-command, handled errors, interrupts, and help fallback."""
    stderr_lines: list[str] = []

    class FakeParser:
        def __init__(self, args: argparse.Namespace) -> None:
            self.args = args
            self.help_calls = 0

        def parse_args(self, _argv: object = None) -> argparse.Namespace:
            return self.args

        def print_help(self) -> None:
            self.help_calls += 1

    parser = FakeParser(argparse.Namespace(command=None))
    monkeypatch.setattr(zentool, "build_parser", lambda: parser)
    assert zentool.main([]) == 0
    assert parser.help_calls == 1

    parser = FakeParser(argparse.Namespace(command="check"))
    monkeypatch.setattr(zentool, "build_parser", lambda: parser)
    monkeypatch.setattr(
        zentool,
        "cmd_check",
        lambda _args: (_ for _ in ()).throw(zentool.ZentoolError("bad state")),
    )
    monkeypatch.setattr(zentool, "_stderr", stderr_lines.append)
    assert zentool.main(["check"]) == 1
    assert stderr_lines == ["Error: bad state"]

    parser = FakeParser(argparse.Namespace(command="check"))
    monkeypatch.setattr(zentool, "build_parser", lambda: parser)
    monkeypatch.setattr(
        zentool,
        "cmd_check",
        lambda _args: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    assert zentool.main(["check"]) == 130
    assert stderr_lines[-1] == "Interrupted."

    parser = FakeParser(argparse.Namespace(command="inspect", inspect_command="raw"))
    monkeypatch.setattr(zentool, "build_parser", lambda: parser)
    monkeypatch.setattr(zentool, "_dispatch_nested_command", lambda *_args: None)
    assert zentool.main(["inspect", "raw"]) == 0
    assert parser.help_calls == 1
