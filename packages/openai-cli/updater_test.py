"""The openai-cli PTY drain keeps list-activation assertions and anchors them."""

import re
import textwrap
import time
from pathlib import Path

import pytest

from lib.codemods.errors import CodemodError
from lib.nix.models.flake_lock import FlakeLockNode
from lib.tests._updater_helpers import load_repo_module, run_async
from lib.update.updaters import UpdateContext

_COMMIT = "a" * 40
_REF = "v1.51.4"
_ANSI = re.compile(r"\x1b\][^\x07]*?(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]")


def _patch():
    return load_repo_module(
        "packages/openai-cli/patch_pty_wait.py",
        "openai_cli_patch_pty_wait_test",
    )


def _updater():
    return load_repo_module(
        "packages/openai-cli/updater.py",
        "openai_cli_updater_test",
    ).OpenaiCliUpdater()


def _node(
    *,
    owner: str = "openai",
    repo: str = "openai-cli",
    commit: str = _COMMIT,
) -> FlakeLockNode:
    return FlakeLockNode.model_validate({
        "locked": {
            "type": "github",
            "owner": owner,
            "repo": repo,
            "rev": commit,
            "narHash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        },
        "original": {
            "type": "github",
            "owner": owner,
            "repo": repo,
            "ref": _REF,
        },
    })


def _wait(source: str):
    namespace: dict[str, object] = {"ANSI": _ANSI, "time": time}
    exec(textwrap.dedent(source), namespace)  # noqa: S102
    wait = namespace["wait"]
    assert callable(wait)
    return wait


class _Child:
    def __init__(self) -> None:
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode


class _Terminal:
    """Script the read/exit ordering Darwin collapsed into one turn."""

    def __init__(
        self, events: list[tuple[bytes, bool]], *, initial: bytes = b""
    ) -> None:
        self.raw = bytearray(initial)
        self.events = list(events)
        self.calls: list[float] = []
        self.child = _Child()
        self._running = True

    def read(self, duration: float = 0.05) -> None:
        self.calls.append(duration)
        if not self.events:
            return
        payload, exit_now = self.events.pop(0)
        if payload:
            self.raw.extend(payload)
        if exit_now:
            self._running = False
            self.child.returncode = 0

    def running(self) -> bool:
        return self._running

    def text(self) -> str:
        return _ANSI.sub("", self.raw.decode("utf-8", "replace"))


def _layout_source(layout: str) -> str:
    return _patch()._LAYOUTS[layout][0].old


def _patched_source(layout: str) -> str:
    return _patch()._LAYOUTS[layout][0].new


@pytest.mark.parametrize("layout", ["running", "poll"])
def test_exit_with_the_marker_already_buffered_still_fails_upstream(
    layout: str,
) -> None:
    """The unpatched waiter raises after the read that copied the marker."""
    terminal = _Terminal([(b"NEXT_COMMAND\n", True)], initial=b"item_030\n")

    with pytest.raises(AssertionError, match="missing 'NEXT_COMMAND'") as raised:
        _wait(_layout_source(layout))(terminal, "NEXT_COMMAND", timeout=1)

    assert "NEXT_COMMAND" in str(raised.value)
    assert terminal.calls == [0.05]


@pytest.mark.parametrize("layout", ["running", "poll"])
def test_drain_accepts_a_marker_copied_in_the_exit_read(layout: str) -> None:
    """The observed Darwin failure: one read both copies the marker and exits."""
    terminal = _Terminal([(b"NEXT_COMMAND\n", True)], initial=b"item_030\n")

    _wait(_patched_source(layout))(terminal, "NEXT_COMMAND", timeout=1)

    assert terminal.calls == [0.05]


@pytest.mark.parametrize("layout", ["running", "poll"])
def test_drain_reads_a_marker_that_arrives_after_exit_is_visible(layout: str) -> None:
    """Exit can be visible one slice before the final write is readable."""
    terminal = _Terminal(
        [(b"", True), (b"NEXT_COMMAND\n", False)],
        initial=b"item_030\n",
    )

    _wait(_patched_source(layout))(terminal, "NEXT_COMMAND", timeout=1)

    assert terminal.calls == [0.05, 0.02, 0.02, 0.02]


@pytest.mark.parametrize("layout", ["running", "poll"])
def test_drain_still_rejects_exit_when_the_marker_never_arrives(layout: str) -> None:
    """A real missing marker stays a failure, including the buffered tail."""
    terminal = _Terminal([(b"item_030\n", True)])

    with pytest.raises(AssertionError, match="missing 'LATER'") as raised:
        _wait(_patched_source(layout))(terminal, "LATER", timeout=1)

    assert "item_030" in str(raised.value)
    assert terminal.calls == [0.05, 0.02, 0.02]


@pytest.mark.parametrize("layout", ["running", "poll"])
def test_a_live_child_is_not_drained_and_still_times_out(layout: str) -> None:
    """The drain belongs to child exit, not to a process that is still running."""
    terminal = _Terminal([])

    with pytest.raises(AssertionError, match="missing 'NEXT_COMMAND'"):
        _wait(_patched_source(layout))(terminal, "NEXT_COMMAND", timeout=0.01)

    assert terminal.calls
    assert all(duration == 0.05 for duration in terminal.calls)
    assert terminal.running()


@pytest.mark.parametrize("layout", ["running", "poll"])
def test_wait_keeps_the_after_cursor_and_a_marker_already_present(layout: str) -> None:
    """Bytes before ``after`` do not satisfy the marker, and a hit does not read."""
    wait = _wait(_patched_source(layout))
    present = _Terminal([], initial=b"NEXT_COMMAND\n")
    wait(present, "NEXT_COMMAND", timeout=1)
    assert present.calls == []

    skipped = _Terminal([], initial=b"NEXT_COMMAND\n")
    with pytest.raises(AssertionError, match="missing 'NEXT_COMMAND'"):
        wait(skipped, "NEXT_COMMAND", timeout=0.01, after=len(skipped.raw))
    assert skipped.calls
    assert skipped.running()


def test_each_layout_is_one_exact_upstream_waiter() -> None:
    """The two harness generations stay distinct, and the drain is not a third."""
    module = _patch()
    running = module._LAYOUTS["running"][0]
    poll = module._LAYOUTS["poll"][0]

    assert tuple(module._LAYOUTS) == ("running", "poll")
    assert running.relative_path == poll.relative_path == module.HARNESS_PATH
    assert running.old not in running.new
    assert poll.old not in poll.new
    assert running.old not in poll.new
    assert poll.old not in running.new
    assert module._SENTINEL in running.new
    assert module._SENTINEL in poll.new
    assert module._SENTINEL not in running.old
    assert "range(4)" in running.new
    assert "read(0.02)" in running.new
    with pytest.raises(CodemodError, match="found none"):
        module.harness_layout(running.new)


@pytest.mark.parametrize(
    ("layout", "name"),
    [("running", "running"), ("poll", "poll")],
)
def test_patch_tree_rewrites_only_the_matching_layout(
    tmp_path: Path, layout: str, name: str
) -> None:
    """One layout is rewritten in place and a second apply does not write again."""
    module = _patch()
    patch = module._LAYOUTS[layout][0]
    harness = tmp_path / module.HARNESS_PATH
    harness.parent.mkdir()
    original = "prefix\n" + patch.old + "suffix\n"
    harness.write_text(original, encoding="utf-8")

    assert module.harness_layout(original) == name
    assert module.main([str(tmp_path)]) == 0
    rewritten = harness.read_text(encoding="utf-8")
    assert rewritten == "prefix\n" + patch.new + "suffix\n"

    with pytest.raises(
        CodemodError, match="^openai-cli PTY wait drain was already applied$"
    ):
        module.patch_tree(tmp_path)
    assert harness.read_text(encoding="utf-8") == rewritten


def test_patch_tree_leaves_an_unknown_waiter_untouched(tmp_path: Path) -> None:
    """Zero or two layouts fail before any byte of the harness changes."""
    module = _patch()
    harness = tmp_path / module.HARNESS_PATH
    harness.parent.mkdir()
    both = module._LAYOUTS["running"][0].old + module._LAYOUTS["poll"][0].old
    harness.write_text(both, encoding="utf-8")

    with pytest.raises(CodemodError, match="found running, poll"):
        module.harness_layout(both)
    with pytest.raises(CodemodError, match="found running, poll"):
        module.patch_tree(tmp_path)
    assert harness.read_text(encoding="utf-8") == both

    harness.write_text("def wait():\n    pass\n", encoding="utf-8")
    original = harness.read_text(encoding="utf-8")
    with pytest.raises(CodemodError, match="found none"):
        module.patch_tree(tmp_path)
    assert harness.read_text(encoding="utf-8") == original


def test_fetch_latest_accepts_the_refreshed_input_when_the_waiter_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins admit the commit only after the harness text selects one layout."""
    module = load_repo_module(
        "packages/openai-cli/updater.py",
        "openai_cli_updater_accepts",
    )
    updater = module.OpenaiCliUpdater()
    node = _node()
    calls: list[tuple[object, str, object]] = []

    def node_for(input_name: str) -> FlakeLockNode:
        if input_name != "openai-cli":
            msg = input_name
            raise KeyError(msg)
        return node

    monkeypatch.setattr("lib.update.flake.get_flake_input_node", node_for)

    async def fetch(session: object, url: str, *, config: object) -> bytes:
        calls.append((session, url, config))
        return module._PTY_WAIT._LAYOUTS["running"][0].old.encode()

    monkeypatch.setattr(module, "fetch_url", fetch)
    session = object()
    info = run_async(updater.fetch_latest(session, context=UpdateContext(current=None)))

    assert info.version == _REF
    assert info.commit == _COMMIT
    assert calls == [
        (
            session,
            f"https://raw.githubusercontent.com/openai/openai-cli/{_COMMIT}/scripts/image_picker_harness.py",
            updater.config,
        )
    ]


def test_fetch_latest_rejects_a_harness_the_drain_does_not_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anchor drift fails in pins, with the layout error as the cause."""
    module = load_repo_module(
        "packages/openai-cli/updater.py",
        "openai_cli_updater_drifts",
    )
    updater = module.OpenaiCliUpdater()
    monkeypatch.setattr(
        "lib.update.flake.get_flake_input_node",
        lambda _input_name: _node(),
    )

    async def fetch(*_args: object, **_kwargs: object) -> bytes:
        return b"def wait():\n    pass\n"

    monkeypatch.setattr(module, "fetch_url", fetch)

    with pytest.raises(
        RuntimeError,
        match=f"^openai-cli {_REF} PTY harness drifted from the Darwin exit drain$",
    ) as raised:
        run_async(updater.fetch_latest(object(), context=UpdateContext(current=None)))
    assert isinstance(raised.value.__cause__, CodemodError)
    assert "found none" in str(raised.value.__cause__)


def test_fetch_latest_rejects_a_non_utf8_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A binary harness is drift, not a decode that proceeds into the layout check."""
    module = load_repo_module(
        "packages/openai-cli/updater.py",
        "openai_cli_updater_bytes",
    )
    updater = module.OpenaiCliUpdater()
    monkeypatch.setattr(
        "lib.update.flake.get_flake_input_node",
        lambda _input_name: _node(),
    )

    async def fetch(*_args: object, **_kwargs: object) -> bytes:
        return b"\xff"

    monkeypatch.setattr(module, "fetch_url", fetch)

    with pytest.raises(
        RuntimeError,
        match=f"^openai-cli {_REF} PTY harness is not valid UTF-8$",
    ) as raised:
        run_async(updater.fetch_latest(object(), context=UpdateContext(current=None)))
    assert isinstance(raised.value.__cause__, UnicodeDecodeError)


def test_fetch_latest_rejects_a_flake_input_that_is_not_this_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The raw harness URL is never built for a different GitHub source."""
    module = load_repo_module(
        "packages/openai-cli/updater.py",
        "openai_cli_updater_owner",
    )
    updater = module.OpenaiCliUpdater()
    monkeypatch.setattr(
        "lib.update.flake.get_flake_input_node",
        lambda _input_name: _node(owner="example", repo="other"),
    )

    async def fetch(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("harness fetch ran before source identity was checked")

    monkeypatch.setattr(module, "fetch_url", fetch)

    with pytest.raises(RuntimeError, match="must resolve to openai/openai-cli"):
        run_async(updater.fetch_latest(object(), context=UpdateContext(current=None)))
