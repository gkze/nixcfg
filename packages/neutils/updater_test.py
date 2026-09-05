"""Focused tests for the neutils updater helpers."""

import asyncio
import io
import re
import tarfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from nix_manipulator import parse
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.let import LetExpression
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.commands.base import CommandResult as LibCommandResult
from lib.nix.commands.base import ProcessDone
from lib.nix.models.flake_lock import FlakeLock
from lib.nix.models.sources import HashEntry, SourceEntry
from lib.tests._assertions import expect_instance, expect_not_none
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._nix_source import nix_file_binding_expr
from lib.tests._updater_helpers import collect_events as _collect_events
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.cli import _runtime_source_policy, _runtime_source_relpaths
from lib.update.events import (
    CommandResult,
    StatusInfo,
    StatusKind,
    UpdateEvent,
    UpdateEventKind,
    expect_artifact_updates,
)
from lib.update.nix_expr import identifier_attr_path
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo

COMMIT = "a" * 40


def _load_module(name: str = "neutils_updater_test") -> ModuleType:
    return load_repo_module("packages/neutils/updater.py", name)


def test_neutils_updater_toolchain_matches_nix_package_selections() -> None:
    """The updater, package hook, and generated dependency builder share one Zig."""
    updater = _load_module().NeutilsUpdater()
    selected = updater.ZIG_TOOLCHAIN_ATTR
    for relative_path, expected_input in (
        ("packages/neutils/default.nix", f"[ {selected}.hook ]"),
        ("packages/neutils/build.zig.zon.nix", f"[ {selected} ]"),
    ):
        assert_nix_ast_equal(
            nix_file_binding_expr(relative_path, "nativeBuildInputs"),
            expected_input,
        )
    package = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/neutils/default.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    assert selected in {
        argument.name
        for argument in package.argument_set
        if isinstance(argument, Identifier)
    }


def _build_archive(
    *,
    include_build_zig_zon: bool = True,
    minimum_zig_version: str | None = "0.15.1",
) -> bytes:
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
        payload = (
            b".{}\n"
            if minimum_zig_version is None
            else (
                f'.{{\n    .minimum_zig_version = "{minimum_zig_version}",\n}}\n'
            ).encode()
        )
        file_info = tarfile.TarInfo(name)
        file_info.size = len(payload)
        archive.addfile(file_info, io.BytesIO(payload))
    return buffer.getvalue()


def _source_entry(version: str, *, commit: str | None = COMMIT) -> SourceEntry:
    return SourceEntry.model_validate({
        "version": version,
        "commit": commit,
        "hashes": [],
    })


def _version_info(version: str = "0.7.2") -> VersionInfo:
    return VersionInfo(version=version, metadata={"commit": COMMIT})


def _expected_flake_attr_expression(
    flake_url: str,
    *attributes: str,
    quoted_indices: tuple[int, ...],
) -> LetExpression:
    """Build the expected flake selection structurally for updater assertions."""
    quoted = set(quoted_indices)
    return LetExpression(
        local_variables=[
            Binding(
                name="flake",
                value=FunctionCall(
                    name=identifier_attr_path("builtins", "getFlake"),
                    argument=StringPrimitive(value=flake_url),
                ),
            ),
        ],
        value=identifier_attr_path(
            "flake",
            *(
                f'"{attribute}"' if index in quoted else attribute
                for index, attribute in enumerate(attributes)
            ),
        ),
    )


def _flake_root_inputs() -> AttributeSet:
    """Parse the clean top-level inputs subtree despite unrelated parser gaps."""
    source = (REPO_ROOT / "flake.nix").read_text(encoding="utf-8")
    parsed = parse(source)
    root_nodes = parsed.node.named_children
    assert len(root_nodes) == 1
    root = root_nodes[0]
    assert root.type == "attrset_expression"

    binding_sets = [
        child for child in root.named_children if child.type == "binding_set"
    ]
    assert len(binding_sets) == 1
    input_bindings = []
    for binding in binding_sets[0].named_children:
        if binding.type != "binding":
            continue
        attrpath = expect_not_none(
            next(
                (child for child in binding.named_children if child.type == "attrpath"),
                None,
            )
        )
        if expect_not_none(attrpath.text) == b"inputs":
            input_bindings.append(binding)

    assert len(input_bindings) == 1
    input_binding = input_bindings[0]
    assert input_binding.has_error is False
    assert len(input_binding.named_children) == 2
    input_value = input_binding.named_children[1]
    assert input_value.type == "attrset_expression"
    assert input_value.has_error is False
    return expect_instance(
        parse_nix_expr(expect_not_none(input_value.text).decode()),
        AttributeSet,
    )


def _select_zig_015(
    monkeypatch: pytest.MonkeyPatch,
    updater,
    *,
    minimum: str = "0.15.1",
) -> None:
    async def _resolve_selected_zig_version(*, platform: str, repo_root: Path) -> str:
        assert platform == "aarch64-darwin"
        assert repo_root == Path("/repo/root")
        return "0.15.2"

    monkeypatch.setattr(
        updater,
        "_resolve_selected_zig_version",
        _resolve_selected_zig_version,
    )

    async def _read_minimum(_build_zig_zon: Path, *, zig_path: str, system: str):
        _ = zig_path
        assert system == "aarch64-darwin"
        yield UpdateEvent.value(updater.name, minimum)

    monkeypatch.setattr(updater, "_read_minimum_zig_version", _read_minimum)


def test_native_zon_helper_is_in_packaged_runtime_source() -> None:
    """Ship and fingerprint the helper alongside the installed updater."""
    root = Path(REPO_ROOT)
    assert Path("packages/neutils/read_minimum_zig_version.zig") in (
        _runtime_source_relpaths(root, _runtime_source_policy(root))
    )


@pytest.mark.parametrize(
    ("system", "zig_target"),
    [
        ("aarch64-darwin", "aarch64-macos"),
        ("aarch64-linux", "aarch64-linux"),
        ("x86_64-linux", "x86_64-linux"),
    ],
)
def test_read_minimum_zig_version_invokes_selected_native_parser(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    system: str,
    zig_target: str,
) -> None:
    """Keep runtime parser execution bounded and scoped to the update workspace."""
    module = _load_module(f"neutils_native_zon_{system}")
    updater = module.NeutilsUpdater()
    manifest = tmp_path / "build.zig.zon"
    status = UpdateEvent.status(updater.name, "parsing ZON")

    async def _run_command(args: list[str], *, options):
        assert args == [
            "/nix/store/zig-tool/bin/zig",
            "run",
            "-target",
            zig_target,
            str(Path(module.__file__).with_name("read_minimum_zig_version.zig")),
            "--cache-dir",
            str(tmp_path / ".zig-cache"),
            "--global-cache-dir",
            str(tmp_path / ".zig-global-cache"),
            "--",
            str(manifest),
        ]
        assert options.command_timeout == updater.config.default_subprocess_timeout
        assert options.config is updater.config
        assert options.allow_failure is False
        yield status
        yield UpdateEvent.value(
            updater.name,
            CommandResult(args=args, returncode=0, stdout="0.15.1\n", stderr=""),
        )

    monkeypatch.setattr(module, "run_command", _run_command)
    events = _run(
        _collect_events(
            updater._read_minimum_zig_version(
                manifest,
                zig_path="/nix/store/zig-tool",
                system=system,
            )
        )
    )
    assert events == [status, UpdateEvent.value(updater.name, "0.15.1")]


def test_native_zon_parser_failure_preserves_the_cause(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A compiler/parser failure must not become a missing-version error."""
    module = _load_module("neutils_native_zon_error")
    updater = module.NeutilsUpdater()
    error = RuntimeError("ZON parse failed: missing minimum_zig_version")

    async def _run_command(_args, *, options):
        assert options.allow_failure is False
        raise error
        yield

    monkeypatch.setattr(module, "run_command", _run_command)
    with pytest.raises(RuntimeError, match="ZON parse failed") as raised:
        _run(
            _collect_events(
                updater._read_minimum_zig_version(
                    tmp_path / "build.zig.zon",
                    zig_path="/nix/store/zig-tool",
                    system="aarch64-darwin",
                )
            )
        )
    assert raised.value is error


@pytest.mark.parametrize("stdout", ["", "0.15.1\n"])
def test_fetch_hashes_rejects_failed_native_parser_before_emitting_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stdout: str,
) -> None:
    """Consume the real command event protocol without trusting failed stdout."""
    module = _load_module("neutils_native_zon_failed_result")
    updater = module.NeutilsUpdater()
    artifact_path = tmp_path / "build.zig.zon.nix"
    artifact_path.write_text("# existing artifact\n", encoding="utf-8")
    stderr = "build.zig.zon:1:1: error: missing minimum_zig_version"
    events: list[UpdateEvent] = []

    async def _fetch_url(*_args, **_kwargs):
        return _build_archive(minimum_zig_version=None)

    async def _run_nix(args, **_kwargs):
        return LibCommandResult(args=args, returncode=0, stdout="0.15.2", stderr="")

    async def _stream_process(args, **_kwargs):
        if args[:2] == ["nix", "build"]:
            assert args[-1].endswith("#pkgs.aarch64-darwin.zig_0_15")
            result = LibCommandResult(
                args=args, returncode=0, stdout="/nix/store/zig-tool\n", stderr=""
            )
        else:
            assert args[:2] == ["/nix/store/zig-tool/bin/zig", "run"]
            result = LibCommandResult(
                args=args, returncode=1, stdout=stdout, stderr=stderr
            )
        yield ProcessDone(result)

    async def _collect() -> None:
        async for event in updater.fetch_hashes(
            _version_info(), object(), context=_source_entry("0.7.2")
        ):
            events.append(event)

    monkeypatch.setattr("lib.update.net.fetch_url", _fetch_url)
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform", lambda: "aarch64-darwin"
    )
    monkeypatch.setattr(module, "run_nix", _run_nix)
    monkeypatch.setattr("lib.update.process.stream_process", _stream_process)
    monkeypatch.setattr("lib.update.paths.updater_dir_for", lambda _name: tmp_path)

    with pytest.raises(RuntimeError, match=re.escape(stderr)) as raised:
        _run(_collect())

    assert "Read neutils minimum_zig_version from ZON failed (exit 1)" in str(
        raised.value
    )
    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.COMMAND_START,
        UpdateEventKind.COMMAND_END,
        UpdateEventKind.COMMAND_START,
        UpdateEventKind.COMMAND_END,
    ]
    parser_result = expect_instance(events[-1].payload, CommandResult)
    assert parser_result.returncode == 1
    assert parser_result.stderr == stderr
    assert artifact_path.read_text(encoding="utf-8") == "# existing artifact\n"


def test_extract_archive_returns_build_zig_zon_path(tmp_path: Path) -> None:
    """Extract the archive and return the discovered build.zig.zon path."""
    module = _load_module("neutils_updater_test_extract")

    path = module.NeutilsUpdater._extract_archive(_build_archive(), tmp_path)

    assert path == tmp_path / "neutils-0.7.2" / "build.zig.zon"
    assert path.read_text(encoding="utf-8") == (
        '.{\n    .minimum_zig_version = "0.15.1",\n}\n'
    )


def test_extract_archive_requires_build_zig_zon(tmp_path: Path) -> None:
    """Fail clearly when the release archive lacks the Zig lockfile."""
    module = _load_module("neutils_updater_test_extract_missing")

    with pytest.raises(RuntimeError, match=r"Could not locate build\.zig\.zon"):
        module.NeutilsUpdater._extract_archive(
            _build_archive(include_build_zig_zon=False),
            tmp_path,
        )


def test_zon2nix_package_expr_uses_the_declared_input_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve the generator through the updater-owned input name."""
    module = _load_module("neutils_updater_zon2nix_input")
    updater = module.NeutilsUpdater()
    monkeypatch.setattr(
        module,
        "_local_flake_url",
        lambda root: f"git+file://{root}?dirty=1",
    )

    assert updater.additional_input_names == ("zon2nix",)
    assert_nix_ast_equal(
        updater._zon2nix_expr(
            platform="aarch64-darwin",
            repo_root=Path("/repo/root"),
        ),
        _expected_flake_attr_expression(
            "git+file:///repo/root?dirty=1",
            "inputs",
            "zon2nix",
            "packages",
            "aarch64-darwin",
            "zon2nix",
            quoted_indices=(1, 3),
        ),
    )


def test_zon2nix_is_an_exact_direct_root_input_with_a_matching_lock() -> None:
    """Keep the generator's flake declaration and direct lock ownership intact."""
    module = _load_module("neutils_updater_zon2nix_root_contract")
    input_name = module.NeutilsUpdater.ZON2NIX_INPUT
    assert module.NeutilsUpdater.additional_input_names == (input_name,)

    inputs = _flake_root_inputs()
    zon2nix = expect_instance(
        expect_binding(inputs.values, input_name).value,
        AttributeSet,
    )
    url = expect_instance(
        expect_binding(zon2nix.values, "url").value,
        StringPrimitive,
    )
    url_match = re.fullmatch(
        r"github:(?P<owner>jcollie)/(?P<repo>zon2nix)/(?P<rev>[0-9a-f]{40})",
        url.value,
    )
    assert url_match is not None

    lock = FlakeLock.from_file(REPO_ROOT / "flake.lock")
    root_inputs = expect_not_none(lock.root_node.inputs)
    node_name = root_inputs.get(input_name)
    assert isinstance(node_name, str)

    node = lock.nodes[node_name]
    original = expect_not_none(node.original)
    locked = expect_not_none(node.locked)
    expected_identity = (
        "github",
        url_match.group("owner"),
        url_match.group("repo"),
        url_match.group("rev"),
    )
    assert node.flake is not False
    assert (
        original.type,
        original.owner,
        original.repo,
        getattr(original, "rev", None),
    ) == expected_identity
    assert (
        locked.type,
        locked.owner,
        locked.repo,
        locked.rev,
    ) == expected_identity


@pytest.mark.parametrize("version", ["0.15.0", "0.15.2"])
def test_zon2nix_flag_follows_the_selected_zig_015_series(version: str) -> None:
    """Pass the generator's explicit mode for the selected Zig series."""
    module = _load_module(f"neutils_updater_zon2nix_flag_{version}")

    assert module.NeutilsUpdater._zon2nix_zig_flag(version) == "--15"


@pytest.mark.parametrize("version", ["0.16.0", "1.15.0", "0.15-dev"])
def test_zon2nix_flag_rejects_an_unowned_zig_series(version: str) -> None:
    """Fail closed instead of silently choosing an incompatible generator mode."""
    module = _load_module(f"neutils_updater_zon2nix_bad_flag_{version}")

    with pytest.raises(RuntimeError, match="must select Zig 0.15.x|exact semantic"):
        module.NeutilsUpdater._zon2nix_zig_flag(version)


@pytest.mark.parametrize("minimum", ["0.14.1", "0.15.1"])
def test_validate_zig_requirement_accepts_supported_minimum(minimum: str) -> None:
    """Allow release minima satisfied by the selected Zig 0.15 series."""
    module = _load_module(f"neutils_updater_zig_minimum_{minimum}")
    assert (
        module.NeutilsUpdater._validate_zig_requirement(
            minimum,
            selected_zig_version="0.15.2",
        )
        == minimum
    )


@pytest.mark.parametrize(
    ("minimum", "match"),
    [
        ("", "must be an exact semantic version"),
        ("0.15", "must be an exact semantic version"),
        ("0.16.0", "newer than package-selected"),
    ],
)
def test_validate_zig_requirement_rejects_unsupported_minimum(
    minimum: str,
    match: str,
) -> None:
    """Fail before generation when release metadata outgrows Zig 0.15."""
    module = _load_module(f"neutils_updater_bad_zig_minimum_{minimum}")
    with pytest.raises(RuntimeError, match=match):
        module.NeutilsUpdater._validate_zig_requirement(
            minimum,
            selected_zig_version="0.15.2",
        )


def test_render_build_zig_zon_rejects_newer_toolchain_before_zon2nix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop before resolving zon2nix if the native parser requires a newer compiler."""
    module = _load_module("neutils_updater_rejects_newer_zig")
    updater = module.NeutilsUpdater()

    async def _fetch_url(*_args, **_kwargs):
        return _build_archive(minimum_zig_version="0.16.0")

    async def _resolve(installable: str, *, expression: bool = False):
        assert expression is False
        assert installable.endswith(".zig_0_15")
        yield UpdateEvent.value(updater.name, "/nix/store/zig-tool")

    monkeypatch.setattr("lib.update.net.fetch_url", _fetch_url)
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform", lambda: "aarch64-darwin"
    )
    monkeypatch.setattr(module, "get_repo_file", lambda _path: Path("/repo/root"))
    monkeypatch.setattr(updater, "_resolve_installable_path", _resolve)
    _select_zig_015(monkeypatch, updater, minimum="0.16.0")

    with pytest.raises(RuntimeError, match="newer than package-selected"):
        _run(
            _collect_events(
                updater._render_build_zig_zon_nix(
                    _version_info("0.8.0"),
                    object(),
                )
            )
        )


def test_resolve_selected_zig_version_reads_the_selected_nix_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compare release metadata with the exact Zig version selected by Nix."""
    module = _load_module("neutils_updater_resolve_zig_version")
    updater = module.NeutilsUpdater()
    repo_root = Path("/repo/root")
    monkeypatch.setattr(
        module,
        "_local_flake_url",
        lambda root: f"git+file://{root}?dirty=1",
    )

    async def _run_nix(args: list[str], *, check: bool):
        assert args == [
            "nix",
            "eval",
            "--impure",
            "--raw",
            "--expr",
            updater._zig_version_expr(
                platform="aarch64-darwin",
                repo_root=repo_root,
            ),
        ]
        assert check is False
        return SimpleNamespace(returncode=0, stdout="0.15.2\n", stderr="")

    monkeypatch.setattr(module, "run_nix", _run_nix)

    assert (
        _run(
            updater._resolve_selected_zig_version(
                platform="aarch64-darwin",
                repo_root=repo_root,
            )
        )
        == "0.15.2"
    )


@pytest.mark.parametrize(
    ("result", "match"),
    [
        (
            SimpleNamespace(returncode=1, stdout="", stderr="zig lookup failed"),
            "zig lookup failed",
        ),
        (
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            "nix eval failed",
        ),
        (
            SimpleNamespace(returncode=0, stdout="0.15-dev\n", stderr=""),
            "must be an exact semantic version",
        ),
    ],
)
def test_resolve_selected_zig_version_rejects_invalid_eval(
    result: SimpleNamespace,
    match: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface failed, empty, or non-version Zig package evaluations."""
    module = _load_module(f"neutils_updater_bad_zig_eval_{match}")
    updater = module.NeutilsUpdater()

    async def _run_nix(_args: list[str], *, check: bool):
        assert check is False
        return result

    monkeypatch.setattr(module, "run_nix", _run_nix)

    with pytest.raises(RuntimeError, match=match):
        _run(
            updater._resolve_selected_zig_version(
                platform="aarch64-darwin",
                repo_root=Path("/repo/root"),
            )
        )


@pytest.mark.parametrize("expression", [False, True])
def test_resolve_installable_path_returns_last_output_path(
    monkeypatch: pytest.MonkeyPatch,
    expression: bool,
) -> None:
    """Use the final non-empty nix build out path from the command result."""
    module = _load_module(f"neutils_updater_test_resolve_installable_{expression}")
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

    events = _run(
        _collect_events(
            updater._resolve_installable_path(
                "flake#tool",
                expression=expression,
            )
        )
    )

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.VALUE,
    ]
    assert events[0].message == "building installable"
    assert events[1].payload == "/nix/store/final"
    expected_command = [
        "nix",
        "build",
        "--no-link",
        "--print-out-paths",
    ]
    if expression:
        expected_command.extend(("--impure", "--expr"))
    expected_command.append("flake#tool")
    assert calls[0][0] == expected_command
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
    info = _version_info()
    installables: list[tuple[str, bool]] = []
    command_calls: list[tuple[list[str], object]] = []

    async def _fetch_url(session, url: str, *, request_timeout=None, config=None):
        assert session is session_obj
        assert url == updater._archive_url(COMMIT)
        assert request_timeout == updater.config.default_timeout
        assert config == updater.config
        return _build_archive()

    async def _resolve(installable: str, *, expression: bool = False):
        installables.append((installable, expression))
        yield UpdateEvent.value(
            updater.name,
            "/nix/store/zon2nix-tool" if expression else "/nix/store/zig-tool",
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

    async def _read_minimum(_build_zig_zon: Path, *, zig_path: str, system: str):
        assert zig_path == "/nix/store/zig-tool"
        assert system == "aarch64-darwin"
        yield UpdateEvent.status(updater.name, "parsing ZON")
        yield UpdateEvent.value(updater.name, "0.15.1")

    monkeypatch.setattr("lib.update.net.fetch_url", _fetch_url)
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform", lambda: "aarch64-darwin"
    )
    monkeypatch.setattr(module, "get_repo_file", lambda _path: Path("/repo/root"))
    monkeypatch.setattr(
        module,
        "_local_flake_url",
        lambda root: f"git+file://{root}?dirty=1",
    )
    monkeypatch.setattr(updater, "_resolve_installable_path", _resolve)
    monkeypatch.setattr(module, "run_command", _run_command)
    _select_zig_015(monkeypatch, updater)
    monkeypatch.setattr(updater, "_read_minimum_zig_version", _read_minimum)

    session_obj = object()
    events = _run(_collect_events(updater._render_build_zig_zon_nix(info, session_obj)))

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.STATUS,
        UpdateEventKind.VALUE,
    ]
    assert [event.message for event in events[:2]] == ["parsing ZON", "running zon2nix"]
    assert events[2].payload == "# rendered\n"
    assert installables[0] == (
        "git+file:///repo/root?dirty=1#pkgs.aarch64-darwin.zig_0_15",
        False,
    )
    assert installables[1][1] is True
    assert_nix_ast_equal(
        installables[1][0],
        _expected_flake_attr_expression(
            "git+file:///repo/root?dirty=1",
            "inputs",
            "zon2nix",
            "packages",
            "aarch64-darwin",
            "zon2nix",
            quoted_indices=(1, 3),
        ),
    )
    assert command_calls[0][0][0] == "/nix/store/zon2nix-tool/bin/zon2nix"
    assert command_calls[0][0][1] == "--15"
    assert command_calls[0][0][3].endswith("/build.zig.zon")
    assert command_calls[0][1].command_timeout == updater._ZON2NIX_TIMEOUT_SECONDS
    assert command_calls[0][1].env["PATH"].startswith("/nix/store/zig-tool/bin:")
    assert command_calls[0][1].env["HOME"].endswith("/.home")
    assert command_calls[0][1].env["XDG_CACHE_HOME"].endswith("/.cache")


@pytest.mark.parametrize("failure_kind", ["exit", "timeout"])
@pytest.mark.parametrize("recover", [False, True])
def test_run_zon2nix_retries_with_bounded_ordered_progress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_kind: str,
    recover: bool,
) -> None:
    """Retry either failure protocol while clearing partial output before each attempt."""
    module = _load_module("neutils_zon2nix_retry_protocol")
    updater = module.NeutilsUpdater()
    output_path = tmp_path / "build.zig.zon.nix"
    output_path.write_text("# stale output\n", encoding="utf-8")
    calls = 0
    timeline: list[UpdateEventKind | str] = []
    retry_events: list[UpdateEvent] = []

    async def _stream_process(args, *, timeout: float, env):
        nonlocal calls
        assert timeout == updater._ZON2NIX_TIMEOUT_SECONDS
        assert env == {"HOME": str(tmp_path)}
        assert not output_path.exists()
        calls += 1
        output_path.write_text("# partial output\n", encoding="utf-8")
        if recover and calls == 2:
            output_path.write_text("# generated\n", encoding="utf-8")
            result = LibCommandResult(args=args, returncode=0, stdout="", stderr="")
        elif failure_kind == "timeout":
            raise TimeoutError
        else:
            result = LibCommandResult(
                args=args,
                returncode=1,
                # Retry classification must consider stdout even with nonempty stderr.
                stdout="err(default): NameServerFailure",
                stderr="dependency fetch failed",
            )
        yield ProcessDone(result)

    async def _sleep(delay: float) -> None:
        assert delay == updater.config.default_retry_backoff
        timeline.append("backoff")

    async def _collect() -> None:
        async for event in updater._run_zon2nix(
            zon2nix_path="/nix/store/zon2nix-tool",
            zig_version_flag="--15",
            build_zig_zon=tmp_path / "build.zig.zon",
            output_path=output_path,
            env={"HOME": str(tmp_path)},
        ):
            timeline.append(event.kind)
            if event.kind is UpdateEventKind.STATUS:
                retry_events.append(event)

    monkeypatch.setattr("lib.update.process.stream_process", _stream_process)
    monkeypatch.setattr(module.asyncio, "sleep", _sleep)

    if recover:
        _run(_collect())
        assert output_path.read_text(encoding="utf-8") == "# generated\n"
    else:
        expected_error = (
            "Command timed out after 180s"
            if failure_kind == "timeout"
            else "dependency fetch failed"
        )
        with pytest.raises(RuntimeError, match=expected_error):
            _run(_collect())

    assert calls == (2 if recover else 3)
    failed_attempt = [UpdateEventKind.COMMAND_START]
    if failure_kind == "exit":
        failed_attempt.append(UpdateEventKind.COMMAND_END)
    last_attempt = (
        [UpdateEventKind.COMMAND_START, UpdateEventKind.COMMAND_END]
        if recover
        else failed_attempt
    )
    assert timeline == (
        [*failed_attempt, UpdateEventKind.STATUS, "backoff"] * (calls - 1)
        + last_attempt
    )
    assert retry_events == [
        UpdateEvent.status(
            updater.name,
            "zon2nix hit a transient fetch failure; retrying...",
            operation="compute_hash",
            status=StatusInfo(kind=StatusKind.RETRY, value=f"attempt {attempt}/3"),
        )
        for attempt in range(2, calls + 1)
    ]


@pytest.mark.parametrize(
    ("stderr", "stdout", "message"),
    [
        ("bad lockfile", "other output", "bad lockfile"),
        ("error: GettingZigDep", "", "error: GettingZigDep"),
        ("", "unsupported ZON", "unsupported ZON"),
        ("", "", "zon2nix failed"),
    ],
)
def test_run_zon2nix_surfaces_permanent_exit_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stderr: str,
    stdout: str,
    message: str,
) -> None:
    """Keep stderr preference and fallback diagnostics for deterministic failures."""
    module = _load_module("neutils_zon2nix_permanent_exit")
    updater = module.NeutilsUpdater()
    events: list[UpdateEvent] = []

    async def _stream_process(args, **_kwargs):
        yield ProcessDone(
            LibCommandResult(args=args, returncode=1, stdout=stdout, stderr=stderr)
        )

    async def _sleep(_delay: float) -> None:
        pytest.fail("A deterministic failure must not be retried")

    async def _collect() -> None:
        async for event in updater._run_zon2nix(
            zon2nix_path="/nix/store/zon2nix-tool",
            zig_version_flag="--15",
            build_zig_zon=tmp_path / "build.zig.zon",
            output_path=tmp_path / "build.zig.zon.nix",
            env={},
        ):
            events.append(event)

    monkeypatch.setattr("lib.update.process.stream_process", _stream_process)
    monkeypatch.setattr(module.asyncio, "sleep", _sleep)
    with pytest.raises(RuntimeError, match=f"^{re.escape(message)}$"):
        _run(_collect())
    assert [event.kind for event in events] == [
        UpdateEventKind.COMMAND_START,
        UpdateEventKind.COMMAND_END,
    ]


def test_render_build_zig_zon_nix_yields_tool_resolution_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass through status events emitted while resolving the Zig and zon2nix tools."""
    module = _load_module("neutils_updater_test_render_resolution_events")
    updater = module.NeutilsUpdater()

    async def _fetch_url(*_args, **_kwargs):
        return _build_archive()

    async def _resolve(installable: str, *, expression: bool = False):
        _ = expression
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

    monkeypatch.setattr("lib.update.net.fetch_url", _fetch_url)
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform", lambda: "aarch64-darwin"
    )
    monkeypatch.setattr(module, "get_repo_file", lambda _path: Path("/repo/root"))
    monkeypatch.setattr(updater, "_resolve_installable_path", _resolve)
    monkeypatch.setattr(module, "run_command", _run_command)
    _select_zig_015(monkeypatch, updater)

    events = _run(
        _collect_events(updater._render_build_zig_zon_nix(_version_info(), object()))
    )

    resolution_messages = [event.message for event in events[:-1]]
    assert resolution_messages[0] == (
        "resolving git+file:///repo/root?dirty=1#pkgs.aarch64-darwin.zig_0_15"
    )
    assert resolution_messages[1].startswith("resolving ")
    assert_nix_ast_equal(
        resolution_messages[1].removeprefix("resolving "),
        _expected_flake_attr_expression(
            "git+file:///repo/root?dirty=1",
            "inputs",
            "zon2nix",
            "packages",
            "aarch64-darwin",
            "zon2nix",
            quoted_indices=(1, 3),
        ),
    )
    assert events[-1].payload == "# rendered\n"


def test_fetch_hashes_requires_package_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail fast when the package directory cannot be located."""
    module = _load_module("neutils_updater_test_missing_pkg_dir")
    updater = module.NeutilsUpdater()

    monkeypatch.setattr("lib.update.paths.updater_dir_for", lambda _name: None)

    with pytest.raises(RuntimeError, match="Package directory not found for neutils"):
        _run(_collect_events(updater.fetch_hashes(_version_info(), object())))


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
        assert_nix_ast_equal(expr, updater._src_expr(COMMIT))
        assert config == updater.config
        yield UpdateEvent.status(name, "hashing src")
        yield UpdateEvent.value(name, "sha256-src")

    monkeypatch.setattr(updater, "_render_build_zig_zon_nix", _render)
    monkeypatch.setattr(
        "lib.update.paths.updater_dir_for",
        lambda _name: REPO_ROOT / "packages" / "neutils",
    )
    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fixed_hash)

    events = _run(_collect_events(updater.fetch_hashes(_version_info(), object())))

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


@pytest.mark.parametrize(
    "transient_error",
    [
        "Command timed out after 180s: zon2nix",
        "Command failed (exit 1): zon2nix\nstderr: err(default): ReadFailed",
    ],
)
def test_fetch_hashes_preserves_existing_artifact_after_current_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transient_error: str,
) -> None:
    """Keep the checked-in artifact after a current-version transient failure."""
    module = _load_module("neutils_updater_test_fetch_hashes_preserve_current")
    updater = module.NeutilsUpdater()
    pkg_dir = tmp_path / "neutils"
    pkg_dir.mkdir()
    artifact_path = pkg_dir / "build.zig.zon.nix"
    artifact_path.write_text("# existing artifact\n", encoding="utf-8")

    async def _render(_info: object, _session: object):
        yield UpdateEvent.status(updater.name, "resolving tools")
        raise RuntimeError(transient_error)

    async def _fixed_hash(name: str, expr: str, *, config=None):
        assert name == updater.name
        assert_nix_ast_equal(expr, updater._src_expr(COMMIT))
        assert config == updater.config
        yield UpdateEvent.value(name, "sha256-src")

    monkeypatch.setattr(updater, "_render_build_zig_zon_nix", _render)
    monkeypatch.setattr("lib.update.paths.updater_dir_for", lambda _name: pkg_dir)
    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fixed_hash)

    events = _run(
        _collect_events(
            updater.fetch_hashes(
                _version_info(),
                object(),
                context=_source_entry("0.7.2"),
            )
        )
    )

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.STATUS,
        UpdateEventKind.STATUS,
        UpdateEventKind.ARTIFACT,
        UpdateEventKind.VALUE,
    ]
    assert events[2].message == (
        "Preserving existing build.zig.zon.nix after transient zon2nix failure."
    )
    artifacts = expect_artifact_updates(events[3].payload)
    assert len(artifacts) == 1
    assert artifacts[0].path == artifact_path
    assert artifacts[0].content == "# existing artifact\n"
    assert events[-1].payload == [HashEntry.create("srcHash", "sha256-src")]


@pytest.mark.parametrize(
    "transient_stderr",
    [
        (
            "err(zig): fetching zig dep: error: unable to connect to "
            "server: ConnectionTimedOut\nerror: GettingZigDep"
        ),
        "err(default): TlsInitializationFailed",
    ],
)
def test_fetch_hashes_retries_transient_zon2nix_failure_before_preserving_current_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transient_stderr: str,
) -> None:
    """Retry zon2nix transport failures before preserving current output."""
    module = _load_module("neutils_updater_test_transient_failure_retry")
    updater = module.NeutilsUpdater()
    pkg_dir = tmp_path / "neutils"
    pkg_dir.mkdir()
    artifact_path = pkg_dir / "build.zig.zon.nix"
    artifact_path.write_text("# existing artifact\n", encoding="utf-8")
    command_calls: list[list[str]] = []
    sleep_delays: list[float] = []

    async def _fetch_url(*_args: object, **_kwargs: object) -> bytes:
        return _build_archive()

    async def _resolve(_installable: str, *, expression: bool = False):
        _ = expression
        yield UpdateEvent.value(updater.name, "/nix/store/tool")

    async def _run_command(args: list[str], *, options: object):
        _ = options
        command_calls.append(args)
        yield UpdateEvent.value(
            updater.name,
            CommandResult(
                args=args,
                returncode=1,
                stdout="",
                stderr=transient_stderr,
            ),
        )

    async def _sleep(delay: float) -> None:
        sleep_delays.append(delay)

    async def _fixed_hash(name: str, expr: str, *, config=None):
        assert name == updater.name
        assert_nix_ast_equal(expr, updater._src_expr(COMMIT))
        assert config == updater.config
        yield UpdateEvent.value(name, "sha256-src")

    monkeypatch.setattr("lib.update.net.fetch_url", _fetch_url)
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform", lambda: "aarch64-darwin"
    )
    monkeypatch.setattr(module, "get_repo_file", lambda _path: Path("/repo/root"))
    monkeypatch.setattr(updater, "_resolve_installable_path", _resolve)
    monkeypatch.setattr(module, "run_command", _run_command)
    _select_zig_015(monkeypatch, updater)
    monkeypatch.setattr(module.asyncio, "sleep", _sleep)
    monkeypatch.setattr("lib.update.paths.updater_dir_for", lambda _name: pkg_dir)
    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fixed_hash)

    events = _run(
        _collect_events(
            updater.fetch_hashes(
                _version_info(),
                object(),
                context=_source_entry("0.7.2"),
            )
        )
    )

    assert len(command_calls) == updater._ZON2NIX_MAX_ATTEMPTS
    assert sleep_delays == [
        updater.config.default_retry_backoff,
        updater.config.default_retry_backoff,
    ]
    status_messages = [
        event.message for event in events if event.kind is UpdateEventKind.STATUS
    ]
    assert (
        status_messages.count("zon2nix hit a transient fetch failure; retrying...") == 2
    )
    assert (
        "Preserving existing build.zig.zon.nix after transient zon2nix failure."
        in status_messages
    )
    artifacts = [
        artifact
        for event in events
        if event.kind is UpdateEventKind.ARTIFACT
        for artifact in expect_artifact_updates(event.payload)
    ]
    assert len(artifacts) == 1
    assert artifacts[0].path == artifact_path
    assert artifacts[0].content == "# existing artifact\n"
    assert events[-1].payload == [HashEntry.create("srcHash", "sha256-src")]


@pytest.mark.parametrize(
    ("context_version", "context_commit", "write_artifact"),
    [
        ("0.7.1", COMMIT, True),
        ("0.7.2", COMMIT, False),
        ("0.7.2", None, True),
        ("0.7.2", "b" * 40, True),
    ],
)
def test_fetch_hashes_rejects_preserve_when_artifact_is_not_current(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    context_version: str,
    context_commit: str | None,
    write_artifact: bool,
) -> None:
    """Do not preserve old or missing generated artifacts after zon2nix failures."""
    module = _load_module("neutils_updater_test_fetch_hashes_preserve_reject")
    updater = module.NeutilsUpdater()
    pkg_dir = tmp_path / "neutils"
    pkg_dir.mkdir()
    artifact_path = pkg_dir / "build.zig.zon.nix"
    if write_artifact:
        artifact_path.write_text("# stale artifact\n", encoding="utf-8")

    async def _render(_info: object, _session: object):
        if False:
            yield UpdateEvent.value(updater.name, "# unreachable\n")
        raise RuntimeError("Command timed out after 180s: zon2nix")

    async def _fixed_hash(name: str, expr: str, *, config=None):
        _ = (name, expr, config)
        raise AssertionError("srcHash computation should not run")
        yield UpdateEvent.value(updater.name, "sha256-src")

    monkeypatch.setattr(updater, "_render_build_zig_zon_nix", _render)
    monkeypatch.setattr("lib.update.paths.updater_dir_for", lambda _name: pkg_dir)
    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fixed_hash)

    with pytest.raises(RuntimeError, match="Command timed out after 180s"):
        _run(
            _collect_events(
                updater.fetch_hashes(
                    _version_info(),
                    object(),
                    context=_source_entry(context_version, commit=context_commit),
                )
            )
        )


@pytest.mark.parametrize("cancelled", [False, True])
def test_run_zon2nix_preserves_stream_exception_and_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cancelled: bool,
) -> None:
    """Reraise permanent errors and cancellation without losing their cause or retrying."""
    module = _load_module("neutils_zon2nix_stream_error")
    updater = module.NeutilsUpdater()
    cause = ValueError("original cause")
    error = (
        asyncio.CancelledError("cancelled")
        if cancelled
        else RuntimeError("zon2nix failed: unsupported zon syntax")
    )
    events: list[UpdateEvent] = []
    status = UpdateEvent.status(updater.name, "starting zon2nix")

    async def _run_command(_args, *, options):
        yield status
        raise error from cause

    async def _sleep(_delay: float) -> None:
        pytest.fail("Permanent errors and cancellation must not be retried")

    async def _collect() -> None:
        async for event in updater._run_zon2nix(
            zon2nix_path="/nix/store/zon2nix-tool",
            zig_version_flag="--15",
            build_zig_zon=tmp_path / "build.zig.zon",
            output_path=tmp_path / "build.zig.zon.nix",
            env={},
        ):
            events.append(event)

    monkeypatch.setattr(module, "run_command", _run_command)
    monkeypatch.setattr(module.asyncio, "sleep", _sleep)
    with pytest.raises(type(error)) as raised:
        _run(_collect())
    assert raised.value is error
    assert raised.value.__cause__ is cause
    assert events == [status]
