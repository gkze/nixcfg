"""Behavioral contracts for immutable Electron manifest resolution."""

from pathlib import Path

import pytest

from lib.nix.models.flake_lock import FlakeLockNode
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import run_async as _run
from lib.update import electron_manifest, locked_source
from lib.update.config import resolve_config
from lib.update.flake import flake_source_path_expression
from lib.update.updaters.metadata import FlakeInputMetadata

_COMMIT = "a" * 40


def _node(**locked_updates: object) -> FlakeLockNode:
    locked = {
        "type": "github",
        "owner": "example",
        "repo": "desktop",
        "rev": _COMMIT,
        "narHash": "sha256-source",
        **locked_updates,
    }
    return FlakeLockNode.model_validate({"locked": locked})


def _write_manifest(source_root: Path, payload: bytes) -> None:
    manifest = source_root / "apps" / "desktop" / "package.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_bytes(payload)


def test_fetch_flake_electron_manifest_preserves_locked_source_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Read only the realized immutable source and expose typed metadata."""
    node = _node()
    config = resolve_config()
    _write_manifest(
        tmp_path,
        b'{"version":"1.2.3","devDependencies":{"electron":"42.3.3"}}',
    )
    calls: list[tuple[str, float]] = []

    async def _nix_eval_raw(expr: str, *, command_timeout: float) -> str:
        calls.append((expr, command_timeout))
        return f" {tmp_path}\n"

    monkeypatch.setattr(locked_source, "nix_eval_raw", _nix_eval_raw)
    session = object()
    metadata = _run(
        electron_manifest.fetch_flake_electron_manifest(
            session,
            node=node,
            manifest_path="apps/desktop/package.json",
            dependency_group="devDependencies",
            context="Example desktop flake input",
            config=config,
        )
    )

    assert len(calls) == 1
    assert calls[0][1] == config.default_subprocess_timeout
    assert_nix_ast_equal(
        calls[0][0],
        flake_source_path_expression(node),
    )
    assert metadata == electron_manifest.ElectronManifestMetadata(
        node=node,
        commit=_COMMIT,
        electron_version="42.3.3",
        manifest_path="apps/desktop/package.json",
        manifest_version="1.2.3",
    )
    assert metadata.to_dict() == {
        "node": node,
        "commit": _COMMIT,
        "electronVersion": "42.3.3",
        "manifestPath": "apps/desktop/package.json",
        "manifestVersion": "1.2.3",
    }
    assert FlakeInputMetadata.from_metadata(metadata, context="test") == (
        FlakeInputMetadata(node=node, commit=_COMMIT)
    )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"\xff", "not valid UTF-8"),
        (b"not JSON", "not valid JSON"),
        (b" " * (electron_manifest._MAX_MANIFEST_BYTES + 1), "exceeds"),
    ],
)
def test_fetch_flake_electron_manifest_rejects_invalid_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: bytes,
    message: str,
) -> None:
    """Reject oversized or malformed manifest content before contract parsing."""
    _write_manifest(tmp_path, payload)

    async def _nix_eval_raw(_expr: str, *, command_timeout: float) -> str:
        assert command_timeout == resolve_config().default_subprocess_timeout
        return str(tmp_path)

    monkeypatch.setattr(locked_source, "nix_eval_raw", _nix_eval_raw)
    with pytest.raises(RuntimeError, match=message):
        _run(
            electron_manifest.fetch_flake_electron_manifest(
                object(),
                node=_node(),
                manifest_path="apps/desktop/package.json",
                dependency_group="devDependencies",
                context="Example desktop flake input",
                config=resolve_config(),
            )
        )


@pytest.mark.parametrize(
    "manifest_path",
    [
        "",
        "\0",
        ".",
        "/package.json",
        "../package.json",
        "apps/../package.json",
    ],
)
def test_fetch_flake_electron_manifest_rejects_unsafe_manifest_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    manifest_path: str,
) -> None:
    """Never let updater metadata select content outside the locked source."""

    async def _nix_eval_raw(_expr: str, *, command_timeout: float) -> str:
        assert command_timeout == resolve_config().default_subprocess_timeout
        return str(tmp_path)

    monkeypatch.setattr(locked_source, "nix_eval_raw", _nix_eval_raw)
    with pytest.raises(RuntimeError, match="manifest path"):
        _run(
            electron_manifest.fetch_flake_electron_manifest(
                object(),
                node=_node(),
                manifest_path=manifest_path,
                dependency_group="devDependencies",
                context="Example desktop flake input",
                config=resolve_config(),
            )
        )


@pytest.mark.parametrize(
    ("source_path", "message"),
    [("", "empty path"), ("relative/source", "absolute path")],
)
def test_fetch_flake_electron_manifest_rejects_invalid_source_paths(
    monkeypatch: pytest.MonkeyPatch,
    source_path: str,
    message: str,
) -> None:
    """A Nix evaluation must resolve to one concrete absolute store path."""

    async def _nix_eval_raw(_expr: str, *, command_timeout: float) -> str:
        assert command_timeout == resolve_config().default_subprocess_timeout
        return source_path

    monkeypatch.setattr(locked_source, "nix_eval_raw", _nix_eval_raw)
    with pytest.raises(RuntimeError, match=message):
        _run(
            electron_manifest.fetch_flake_electron_manifest(
                object(),
                node=_node(),
                manifest_path="apps/desktop/package.json",
                dependency_group="devDependencies",
                context="Example desktop flake input",
                config=resolve_config(),
            )
        )


def test_fetch_flake_electron_manifest_rejects_unavailable_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Report a disappeared source path as a manifest-resolution failure."""

    async def _nix_eval_raw(_expr: str, *, command_timeout: float) -> str:
        assert command_timeout == resolve_config().default_subprocess_timeout
        return str(tmp_path / "missing")

    monkeypatch.setattr(locked_source, "nix_eval_raw", _nix_eval_raw)
    with pytest.raises(RuntimeError, match="source path is unavailable"):
        _run(
            electron_manifest.fetch_flake_electron_manifest(
                object(),
                node=_node(),
                manifest_path="apps/desktop/package.json",
                dependency_group="devDependencies",
                context="Example desktop flake input",
                config=resolve_config(),
            )
        )


def test_fetch_flake_electron_manifest_requires_source_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reject a resolved output that is not a source directory."""
    source_file = tmp_path / "source"
    source_file.write_text("not a directory", encoding="utf-8")

    async def _nix_eval_raw(_expr: str, *, command_timeout: float) -> str:
        assert command_timeout == resolve_config().default_subprocess_timeout
        return str(source_file)

    monkeypatch.setattr(locked_source, "nix_eval_raw", _nix_eval_raw)
    with pytest.raises(RuntimeError, match="is not a directory"):
        _run(
            electron_manifest.fetch_flake_electron_manifest(
                object(),
                node=_node(),
                manifest_path="apps/desktop/package.json",
                dependency_group="devDependencies",
                context="Example desktop flake input",
                config=resolve_config(),
            )
        )


def test_fetch_flake_electron_manifest_requires_existing_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Fail with manifest context when the locked source changed shape."""

    async def _nix_eval_raw(_expr: str, *, command_timeout: float) -> str:
        assert command_timeout == resolve_config().default_subprocess_timeout
        return str(tmp_path)

    monkeypatch.setattr(locked_source, "nix_eval_raw", _nix_eval_raw)
    with pytest.raises(RuntimeError, match="manifest is unavailable"):
        _run(
            electron_manifest.fetch_flake_electron_manifest(
                object(),
                node=_node(),
                manifest_path="apps/desktop/package.json",
                dependency_group="devDependencies",
                context="Example desktop flake input",
                config=resolve_config(),
            )
        )


def test_fetch_flake_electron_manifest_requires_regular_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reject a directory even when it occupies the configured manifest path."""
    manifest_dir = tmp_path / "apps" / "desktop" / "package.json"
    manifest_dir.mkdir(parents=True)

    async def _nix_eval_raw(_expr: str, *, command_timeout: float) -> str:
        assert command_timeout == resolve_config().default_subprocess_timeout
        return str(tmp_path)

    monkeypatch.setattr(locked_source, "nix_eval_raw", _nix_eval_raw)
    with pytest.raises(RuntimeError, match="not a regular file"):
        _run(
            electron_manifest.fetch_flake_electron_manifest(
                object(),
                node=_node(),
                manifest_path="apps/desktop/package.json",
                dependency_group="devDependencies",
                context="Example desktop flake input",
                config=resolve_config(),
            )
        )


def test_fetch_flake_electron_manifest_rejects_symlink_escape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A repository symlink cannot redirect manifest reads outside its source."""
    source_root = tmp_path / "source"
    manifest_parent = source_root / "apps" / "desktop"
    manifest_parent.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text(
        '{"version":"1.2.3","devDependencies":{"electron":"42.3.3"}}',
        encoding="utf-8",
    )
    (manifest_parent / "package.json").symlink_to(outside)

    async def _nix_eval_raw(_expr: str, *, command_timeout: float) -> str:
        assert command_timeout == resolve_config().default_subprocess_timeout
        return str(source_root)

    monkeypatch.setattr(locked_source, "nix_eval_raw", _nix_eval_raw)
    with pytest.raises(RuntimeError, match="escapes the locked source tree"):
        _run(
            electron_manifest.fetch_flake_electron_manifest(
                object(),
                node=_node(),
                manifest_path="apps/desktop/package.json",
                dependency_group="devDependencies",
                context="Example desktop flake input",
                config=resolve_config(),
            )
        )


def test_fetch_flake_electron_manifest_wraps_read_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Normalize an I/O race after the manifest path was resolved."""
    _write_manifest(
        tmp_path,
        b'{"version":"1.2.3","devDependencies":{"electron":"42.3.3"}}',
    )

    async def _nix_eval_raw(_expr: str, *, command_timeout: float) -> str:
        assert command_timeout == resolve_config().default_subprocess_timeout
        return str(tmp_path)

    def _fail_read(_path: Path) -> bytes:
        msg = "simulated read failure"
        raise PermissionError(msg)

    monkeypatch.setattr(locked_source, "nix_eval_raw", _nix_eval_raw)
    monkeypatch.setattr(Path, "read_bytes", _fail_read)
    with pytest.raises(RuntimeError, match="manifest could not be read"):
        _run(
            electron_manifest.fetch_flake_electron_manifest(
                object(),
                node=_node(),
                manifest_path="apps/desktop/package.json",
                dependency_group="devDependencies",
                context="Example desktop flake input",
                config=resolve_config(),
            )
        )


def test_electron_manifest_contract_accepts_only_the_declared_exact_shape() -> None:
    """Keep dependency group and exact-version requirements explicit."""
    payload = {
        "version": "0.0.35",
        "dependencies": {"electron": "41.5.0"},
    }

    assert electron_manifest.electron_manifest_contract(
        payload,
        context="T3 desktop manifest",
        dependency_group="dependencies",
    ) == ("0.0.35", "41.5.0")


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"dependencies": {"electron": "41.5.0"}},
        {"version": "1.0.0"},
        {"version": "1.0.0", "dependencies": {}},
        {"version": "1.0.0", "dependencies": {"electron": "^41.5.0"}},
        {"version": "1.0.0", "dependencies": {"electron": "latest"}},
    ],
)
def test_electron_manifest_contract_rejects_malformed_or_ranged_metadata(
    payload: object,
) -> None:
    """Fail closed before malformed manifest metadata reaches source results."""
    with pytest.raises((RuntimeError, TypeError)):
        electron_manifest.electron_manifest_contract(
            payload,
            context="Desktop manifest",
            dependency_group="dependencies",
        )


@pytest.mark.parametrize(
    "node",
    [
        FlakeLockNode(locked=None),
        _node(type="git"),
        _node(owner=None),
        _node(repo=None),
        _node(rev=None),
    ],
)
def test_locked_github_source_requires_complete_github_coordinates(
    node: FlakeLockNode,
) -> None:
    """Reject mutable or incomplete flake source identities."""
    with pytest.raises(RuntimeError, match="complete GitHub source"):
        electron_manifest.locked_github_source(node, context="Desktop input")


def test_locked_github_source_requires_an_immutable_commit() -> None:
    """Do not fetch manifests through a movable branch or abbreviated revision."""
    with pytest.raises(RuntimeError, match="immutable commit"):
        electron_manifest.locked_github_source(
            _node(rev="main"),
            context="Desktop input",
        )
