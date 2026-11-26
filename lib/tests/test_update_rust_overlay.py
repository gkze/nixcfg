"""Advancing the root rust-overlay pin to the first commit that ships a stable."""

import asyncio
from collections.abc import Callable, Coroutine
from pathlib import Path

import pytest
from nix_manipulator.expressions.set import AttributeSet

from lib.http_utils import NonRetryableStatusError
from lib.nix.models.flake_lock import FlakeLockNode, LockedRef, OriginalRef
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.update.config import resolve_config
from lib.update.events import UpdateEvent, ignore_event
from lib.update.flake_inputs import input_declaration_text
from lib.update.rust_overlay import (
    _lock_rewritten_pin,
    ensure_root_rust_overlay_has_channel,
)

_UNSET = object()

_OLD = "a" * 40
_NEW = "b" * 40
_NEWER = "c" * 40


def _run[T](awaitable: Coroutine[object, object, T]) -> T:
    return asyncio.run(awaitable)


def _status_error(status: int) -> RuntimeError:
    error = RuntimeError(f"HTTP {status}")
    error.__cause__ = NonRetryableStatusError(status, f"HTTP {status}")
    return error


def _node(
    rev: str = _OLD,
    *,
    owner: str = "oxalica",
    repo: str = "rust-overlay",
    original_rev: object = _UNSET,
    locked_rev: object = _UNSET,
    locked: bool = True,
    original: bool = True,
) -> FlakeLockNode:
    return FlakeLockNode(
        locked=LockedRef(
            type="github",
            owner=owner,
            repo=repo,
            rev=rev if locked_rev is _UNSET else locked_rev,  # type: ignore[arg-type]
            narHash="sha256-x",
        )
        if locked
        else None,
        original=OriginalRef(
            type="github",
            owner=owner,
            repo=repo,
            rev=rev if original_rev is _UNSET else original_rev,  # type: ignore[arg-type]
        )
        if original
        else None,
    )


def _flake(rev: str = _OLD) -> str:
    return (
        "{\n"
        "  inputs.rust-overlay = {\n"
        "    # exact rev\n"
        f'    url = "github:oxalica/rust-overlay/{rev}"; # pin\n'
        '    inputs.nixpkgs.follows = "nixpkgs";\n'
        "  };\n"
        "}\n"
    )


class _GitHub:
    """Scripted GitHub responses for one pin advance."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.apis: list[str] = []
        self.manifest: Callable[[str], bytes] | None = None
        self.commits: list[list[object]] = []
        self.compare: object = {"ahead_by": 1, "behind_by": 0}
        self.updates: list[list[str]] = []

    async def fetch_url(self, _session: object, url: str, **_kwargs: object) -> bytes:
        self.urls.append(url)
        if self.manifest is None:
            raise _status_error(404)
        return self.manifest(url)

    async def fetch_api(
        self, _session: object, api_path: str, **kwargs: object
    ) -> object:
        self.apis.append(api_path)
        if api_path.endswith("/commits"):
            page = int(str(kwargs.get("page", "1")))
            if page > len(self.commits):
                return []
            return self.commits[page - 1]
        return self.compare

    async def update(self, names: list[str], **_kwargs: object) -> None:
        self.updates.append(list(names))


def _install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    github: _GitHub,
    *,
    node: FlakeLockNode | object | None = _UNSET,
    follows: str | None = None,
    flake: str | None = None,
) -> _GitHub:
    resolved = _node() if node is _UNSET else node
    (tmp_path / "flake.nix").write_text(
        flake if flake is not None else _flake(), encoding="utf-8"
    )
    monkeypatch.setattr("lib.update.rust_overlay.get_repo_root", lambda: tmp_path)
    monkeypatch.setattr("lib.update.rust_overlay.load_flake_lock", lambda: object())
    monkeypatch.setattr(
        "lib.update.rust_overlay.resolve_root_input_node",
        lambda _lock, _name: (resolved, follows),
    )
    monkeypatch.setattr("lib.update.rust_overlay.fetch_url", github.fetch_url)
    monkeypatch.setattr("lib.update.rust_overlay.fetch_github_api", github.fetch_api)
    monkeypatch.setattr("lib.update.rust_overlay.update_flake_inputs", github.update)
    monkeypatch.setattr("lib.update.rust_overlay.invalidate_flake_lock", lambda: None)
    return github


def _ensure(
    channel: str = "1.100.0",
) -> Coroutine[object, object, tuple[None, list[UpdateEvent]]]:
    async def _collect() -> tuple[None, list[UpdateEvent]]:
        events: list[UpdateEvent] = []

        async def emit(event: UpdateEvent) -> None:
            events.append(event)

        await ensure_root_rust_overlay_has_channel(
            object(),  # type: ignore[arg-type]
            channel,
            source="zed-editor-nightly",
            emit=emit,
            config=resolve_config(),
        )
        return None, events

    return _collect()


def test_present_channel_does_not_move_the_pin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A manifest that already exists at the pin is not a reason to move it."""
    github = _GitHub()
    github.manifest = lambda _url: b"manifest"
    _install(monkeypatch, tmp_path, github)

    _run(_ensure())

    assert github.apis == []
    assert github.updates == []
    assert (tmp_path / "flake.nix").read_text(encoding="utf-8") == _flake()


def test_missing_channel_advances_to_the_oldest_manifest_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The pin becomes the first commit that contains the stable, not HEAD."""
    github = _GitHub()
    github.commits = [[{"sha": _NEWER}, {"sha": "d" * 40}], [{"sha": _NEW}]]
    _install(monkeypatch, tmp_path, github)
    monkeypatch.setattr("lib.update.rust_overlay._PAGE_SIZE", 2)

    _result, events = _run(_ensure())

    assert github.updates == [["rust-overlay"]]
    text = (tmp_path / "flake.nix").read_text(encoding="utf-8")
    declaration = input_declaration_text(text, "rust-overlay")
    assert declaration is not None
    overlay = expect_instance(parse_nix_expr(declaration), AttributeSet)
    assert_nix_ast_equal(
        expect_binding(overlay.values, "url").value,
        f'"github:oxalica/rust-overlay/{_NEW}"',
    )
    assert "# exact rev" in text
    assert "# pin" in text
    assert events[0].message == f"Advancing rust-overlay to {_NEW} for stable 1.100.0"
    assert any(path.endswith("/commits") for path in github.apis)
    assert any("/compare/" in path for path in github.apis)


def test_non_exact_channel_is_rejected_before_any_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A floating toolchain has no single stable manifest to pin."""
    github = _install(monkeypatch, tmp_path, _GitHub())

    with pytest.raises(RuntimeError, match="not an exact stable release"):
        _run(_ensure("stable"))

    assert github.urls == []


@pytest.mark.parametrize(
    ("node", "follows"),
    [
        (_node(), "nixpkgs"),
        (None, None),
        (_node(locked=False), None),
        (_node(original=False), None),
        (_node(owner="other"), None),
        (_node(repo="other"), None),
        (_node(locked_rev=None), None),
        (_node(original_rev=None), None),
        (_node(rev=_NEW, original_rev=_OLD), None),
        (_node(rev="g" * 40), None),
    ],
)
def test_pin_must_be_an_exact_oxalica_rev(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    node: FlakeLockNode | None,
    follows: str | None,
) -> None:
    """A follows, fork, or unlocked input cannot be advanced in place."""
    _install(monkeypatch, tmp_path, _GitHub(), node=node, follows=follows)

    with pytest.raises(RuntimeError, match="not an exact rev pin"):
        _run(_ensure())


def test_url_must_match_the_locked_rev(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The flake URL and the lock rev are one pin; a split is not advanced."""
    _install(monkeypatch, tmp_path, _GitHub(), flake="{ inputs = { }; }\n")
    with pytest.raises(RuntimeError, match="not the locked oxalica rev pin"):
        _run(_ensure())

    _install(monkeypatch, tmp_path, _GitHub(), flake=_flake(_NEW))
    with pytest.raises(RuntimeError, match="not the locked oxalica rev pin"):
        _run(_ensure())

    commented = _flake(_NEW).replace(
        "# exact rev",
        f"# github:oxalica/rust-overlay/{_OLD}",
    )
    github = _GitHub()
    _install(monkeypatch, tmp_path, github, flake=commented)
    with pytest.raises(RuntimeError, match="not the locked oxalica rev pin"):
        _run(_ensure())
    assert github.urls == []
    assert github.apis == []


def test_manifest_transport_errors_propagate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only a 404 means the channel is absent."""
    github = _GitHub()

    async def _fetch(_session: object, url: str, **_kwargs: object) -> bytes:
        github.urls.append(url)
        raise RuntimeError("connection reset")

    _install(monkeypatch, tmp_path, github)
    monkeypatch.setattr("lib.update.rust_overlay.fetch_url", _fetch)
    with pytest.raises(RuntimeError, match="connection reset"):
        _run(_ensure())

    async def _status_on_self(_session: object, url: str, **_kwargs: object) -> bytes:
        error = RuntimeError("missing")
        error.status = 404  # type: ignore[attr-defined]
        raise error

    monkeypatch.setattr("lib.update.rust_overlay.fetch_url", _status_on_self)
    github.commits = []
    with pytest.raises(RuntimeError, match="no commit"):
        _run(_ensure())


def test_deep_status_chain_without_a_status_is_a_transport_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A long cause chain that never names a status is not treated as a 404."""
    github = _GitHub()

    async def _fetch(_session: object, _url: str, **_kwargs: object) -> bytes:
        current = RuntimeError("deep")
        head = current
        for _ in range(7):
            nxt = RuntimeError("deeper")
            current.__cause__ = nxt
            current = nxt
        raise head

    _install(monkeypatch, tmp_path, github)
    monkeypatch.setattr("lib.update.rust_overlay.fetch_url", _fetch)

    with pytest.raises(RuntimeError, match="deep"):
        _run(_ensure())


def test_history_must_be_a_complete_commit_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A truncated or malformed history is not an oldest commit."""
    github = _GitHub()
    github.compare = {}
    _install(monkeypatch, tmp_path, github)
    monkeypatch.setattr("lib.update.rust_overlay._PAGE_SIZE", 1)
    monkeypatch.setattr("lib.update.rust_overlay._MAX_PAGES", 1)
    github.commits = [[{"sha": _NEW}]]
    with pytest.raises(RuntimeError, match="exceeded 1 pages"):
        _run(_ensure())

    github.commits = []
    with pytest.raises(RuntimeError, match="no commit"):
        _run(_ensure())

    async def _not_a_list(_session: object, api_path: str, **_kwargs: object) -> object:
        github.apis.append(api_path)
        return {"message": "nope"}

    monkeypatch.setattr("lib.update.rust_overlay.fetch_github_api", _not_a_list)
    with pytest.raises(TypeError, match="was not a list"):
        _run(_ensure())

    github.commits = [[{"nope": "x"}]]
    monkeypatch.setattr("lib.update.rust_overlay.fetch_github_api", github.fetch_api)
    monkeypatch.setattr("lib.update.rust_overlay._MAX_PAGES", 20)
    with pytest.raises(RuntimeError, match="has no revision"):
        _run(_ensure())

    github.commits = [["nope"]]
    with pytest.raises(TypeError, match="malformed"):
        _run(_ensure())


def test_introducing_commit_must_be_a_descendant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Moving to an ancestor or a diverged commit would drop shipped stables."""
    github = _GitHub()
    github.commits = [[{"sha": _NEW}]]
    _install(monkeypatch, tmp_path, github)

    github.compare = {"ahead_by": 0, "behind_by": 1}
    with pytest.raises(RuntimeError, match="not a descendant"):
        _run(_ensure())

    github.compare = {"ahead_by": 2, "behind_by": 1}
    with pytest.raises(RuntimeError, match="not a descendant"):
        _run(_ensure())

    github.compare = []
    with pytest.raises(TypeError, match="not an object"):
        _run(_ensure())

    github.compare = {"ahead_by": "1", "behind_by": 0}
    with pytest.raises(TypeError, match="ahead_by and behind_by"):
        _run(_ensure())

    github.compare = {"ahead_by": 1, "behind_by": "0"}
    with pytest.raises(TypeError, match="ahead_by and behind_by"):
        _run(_ensure())

    assert (tmp_path / "flake.nix").read_text(encoding="utf-8") == _flake()
    assert github.updates == []


def test_same_revision_with_a_missing_manifest_is_not_rewritten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A commit that touches the path but does not contain the file is not a pin."""
    github = _GitHub()
    github.commits = [[{"sha": _OLD}]]
    _install(monkeypatch, tmp_path, github)

    with pytest.raises(RuntimeError, match="does not contain it"):
        _run(_ensure())

    assert github.updates == []


def test_unchanged_url_is_not_locked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A rewrite that leaves the URL in place does not refresh the lock."""
    _install(monkeypatch, tmp_path, _GitHub())

    with pytest.raises(RuntimeError, match="did not move"):
        _run(
            _lock_rewritten_pin(
                "zed-editor-nightly",
                _OLD,
                emit=ignore_event,
                config=resolve_config(),
            )
        )


def test_lock_failure_restores_the_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed lock refresh puts the previous rev back into flake.nix."""
    github = _GitHub()

    async def _fail(_names: list[str], **_kwargs: object) -> None:
        raise RuntimeError("nix failed")

    _install(monkeypatch, tmp_path, github)
    monkeypatch.setattr("lib.update.rust_overlay.update_flake_inputs", _fail)
    github.commits = [[{"sha": _NEW}]]

    with pytest.raises(RuntimeError, match="nix failed"):
        _run(_ensure())

    assert (tmp_path / "flake.nix").read_text(encoding="utf-8") == _flake()


def test_lock_failure_reports_when_the_url_cannot_be_restored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cleanup failure stays attached to the lock error and names the unrestored file."""
    github = _GitHub()
    github.commits = [[{"sha": _NEW}]]
    _install(monkeypatch, tmp_path, github)
    writes = {"count": 0}
    real_write = (tmp_path / "flake.nix").write_text

    def _write(text: str) -> None:
        writes["count"] += 1
        if writes["count"] == 2:
            raise OSError("disk full")
        real_write(text, encoding="utf-8")

    async def _fail(_names: list[str], **_kwargs: object) -> None:
        raise RuntimeError("nix failed")

    monkeypatch.setattr("lib.update.rust_overlay._write_flake", _write)
    monkeypatch.setattr("lib.update.rust_overlay.update_flake_inputs", _fail)

    with pytest.raises(RuntimeError, match="could not be restored") as raised:
        _run(_ensure())

    assert isinstance(raised.value.__cause__, RuntimeError)
    assert "nix failed" in str(raised.value.__cause__)
    assert _NEW in (tmp_path / "flake.nix").read_text(encoding="utf-8")


def test_server_error_is_not_a_missing_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A 500 while probing the manifest does not advance the pin."""
    github = _GitHub()

    async def _fetch(_session: object, url: str, **_kwargs: object) -> bytes:
        github.urls.append(url)
        raise _status_error(500)

    _install(monkeypatch, tmp_path, github)
    monkeypatch.setattr("lib.update.rust_overlay.fetch_url", _fetch)

    with pytest.raises(RuntimeError, match="HTTP 500"):
        _run(_ensure())

    assert github.apis == []
