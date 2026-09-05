"""Behavioral contracts for bounded reads from realized flake sources."""

from pathlib import Path

import pytest

from lib.nix.models.flake_lock import FlakeLockNode
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import run_async as _run
from lib.update import locked_source
from lib.update.flake import flake_source_path_expression

_COMMIT = "a" * 40


def _node() -> FlakeLockNode:
    return FlakeLockNode.model_validate({
        "locked": {
            "type": "github",
            "owner": "example",
            "repo": "source",
            "rev": _COMMIT,
            "narHash": "sha256-source",
        }
    })


def test_resolve_locked_source_realizes_and_canonicalizes_the_flake_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Resolve the exact locked node once through the canonical Nix expression."""
    calls: list[tuple[str, float]] = []

    async def _nix_eval_raw(expr: str, *, command_timeout: float) -> str:
        calls.append((expr, command_timeout))
        return f" {tmp_path}\n"

    monkeypatch.setattr(locked_source, "nix_eval_raw", _nix_eval_raw)
    source = _run(
        locked_source.resolve_locked_source(
            _node(),
            context="Example input",
            command_timeout=17,
        )
    )

    assert source == locked_source.LockedSource(
        root=tmp_path.resolve(),
        context="Example input",
    )
    assert len(calls) == 1
    assert calls[0][1] == 17
    assert_nix_ast_equal(calls[0][0], flake_source_path_expression(_node()))


def test_resolve_locked_source_rejects_an_empty_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty Nix result cannot silently become the current directory."""

    async def _nix_eval_raw(_expr: str, *, command_timeout: float) -> str:
        assert command_timeout == 17
        return " \n"

    monkeypatch.setattr(locked_source, "nix_eval_raw", _nix_eval_raw)
    with pytest.raises(RuntimeError, match="resolved to an empty path"):
        _run(
            locked_source.resolve_locked_source(
                _node(),
                context="Example input",
                command_timeout=17,
            )
        )


def test_locked_source_requires_an_absolute_existing_directory(tmp_path: Path) -> None:
    """Reject roots that cannot identify one canonical realized source tree."""
    with pytest.raises(RuntimeError, match="absolute path"):
        locked_source.LockedSource(root=Path("relative"), context="Example input")
    with pytest.raises(RuntimeError, match="path is unavailable"):
        locked_source.LockedSource(
            root=tmp_path / "missing",
            context="Example input",
        )

    source_file = tmp_path / "source"
    source_file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not a directory"):
        locked_source.LockedSource(root=source_file, context="Example input")


@pytest.mark.parametrize(
    "relative_path",
    ["", "\0", ".", "/manifest.json", "../manifest.json", "a/../manifest.json"],
)
def test_locked_source_rejects_unsafe_relative_paths(
    tmp_path: Path,
    relative_path: str,
) -> None:
    """A selected source file must remain a nonempty traversal-free relative path."""
    source = locked_source.LockedSource(root=tmp_path, context="Example input")
    with pytest.raises(RuntimeError, match="manifest path"):
        _run(
            source.read_bytes(
                relative_path,
                max_bytes=10,
                description="manifest",
            )
        )


def test_locked_source_reads_bounded_bytes_and_json(tmp_path: Path) -> None:
    """Read regular source files and allow symlinks that remain within the tree."""
    payload = b'{"version":"1.2.3"}'
    manifest = tmp_path / "metadata" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_bytes(payload)
    (tmp_path / "manifest-link.json").symlink_to(manifest)
    source = locked_source.LockedSource(root=tmp_path, context="Example input")

    assert (
        _run(
            source.read_bytes(
                "manifest-link.json",
                max_bytes=len(payload),
                description="manifest",
            )
        )
        == payload
    )
    assert _run(
        source.read_json(
            "metadata/manifest.json",
            max_bytes=len(payload),
            description="manifest",
        )
    ) == {"version": "1.2.3"}


def test_locked_source_rejects_missing_nonfiles_and_symlink_escapes(
    tmp_path: Path,
) -> None:
    """Only regular files canonically contained by the source may be read."""
    source_root = tmp_path / "source"
    source_root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (source_root / "escape.json").symlink_to(outside)
    (source_root / "directory.json").mkdir()
    source = locked_source.LockedSource(root=source_root, context="Example input")

    with pytest.raises(RuntimeError, match="is unavailable"):
        _run(
            source.read_bytes(
                "missing.json",
                max_bytes=10,
                description="manifest",
            )
        )
    with pytest.raises(RuntimeError, match="escapes the locked source tree"):
        _run(
            source.read_bytes(
                "escape.json",
                max_bytes=10,
                description="manifest",
            )
        )
    with pytest.raises(RuntimeError, match="not a regular file"):
        _run(
            source.read_bytes(
                "directory.json",
                max_bytes=10,
                description="manifest",
            )
        )


def test_locked_source_enforces_declared_and_observed_byte_limits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reject invalid limits, known oversized files, and a post-stat size race."""
    source_file = tmp_path / "manifest.json"
    source_file.write_bytes(b"xx")
    source = locked_source.LockedSource(root=tmp_path, context="Example input")

    with pytest.raises(ValueError, match="max_bytes must be positive"):
        _run(
            source.read_bytes(
                "manifest.json",
                max_bytes=0,
                description="manifest",
            )
        )
    with pytest.raises(RuntimeError, match="exceeds 1 bytes"):
        _run(
            source.read_bytes(
                "manifest.json",
                max_bytes=1,
                description="manifest",
            )
        )

    source_file.write_bytes(b"x")
    monkeypatch.setattr(Path, "read_bytes", lambda _path: b"xx")
    with pytest.raises(RuntimeError, match="exceeds 1 bytes"):
        _run(
            source.read_bytes(
                "manifest.json",
                max_bytes=1,
                description="manifest",
            )
        )


def test_locked_source_wraps_read_and_decode_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Normalize local I/O, UTF-8, and JSON failures with source context."""
    source_file = tmp_path / "manifest.json"
    source_file.write_bytes(b"{}")
    source = locked_source.LockedSource(root=tmp_path, context="Example input")

    def _fail_read(_path: Path) -> bytes:
        msg = "simulated read failure"
        raise PermissionError(msg)

    monkeypatch.setattr(Path, "read_bytes", _fail_read)
    with pytest.raises(RuntimeError, match="manifest could not be read"):
        _run(
            source.read_bytes(
                "manifest.json",
                max_bytes=10,
                description="manifest",
            )
        )

    monkeypatch.undo()
    source_file.write_bytes(b"\xff")
    with pytest.raises(RuntimeError, match="not valid UTF-8"):
        _run(
            source.read_json(
                "manifest.json",
                max_bytes=10,
                description="manifest",
            )
        )
    source_file.write_bytes(b"not JSON")
    with pytest.raises(RuntimeError, match="not valid JSON"):
        _run(
            source.read_json(
                "manifest.json",
                max_bytes=10,
                description="manifest",
            )
        )
