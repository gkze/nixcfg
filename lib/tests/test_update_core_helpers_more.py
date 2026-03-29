"""Additional tests for core update helper modules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.nix.models.flake_lock import FlakeLockNode, OriginalRef
from lib.nix.models.sources import SourceEntry, SourcesFile
from lib.update import errors as update_errors
from lib.update import flake as update_flake
from lib.update import io as update_io
from lib.update import paths as update_paths
from lib.update import sources as update_sources


def test_format_exception_handles_empty_message_and_traceback() -> None:
    """Fallback to exception class name when message is empty."""
    exc = Exception()
    message = update_errors.format_exception(exc)
    assert message == "Exception"

    detailed = update_errors.format_exception(exc, include_traceback=True)
    assert detailed.startswith("Exception\n")


def test_atomic_write_text_cleans_up_temp_file_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remove temporary file in finally block when replace fails."""
    target = tmp_path / "data.txt"
    target.write_text("old", encoding="utf-8")

    original_replace = Path.replace

    def _boom(self: Path, target_path: Path) -> Path:
        msg = "replace failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(Path, "replace", _boom)

    with pytest.raises(RuntimeError, match="replace failed"):
        update_io.atomic_write_text(target, "new")

    leftovers = [
        child
        for child in tmp_path.iterdir()
        if child.name.startswith(f".{target.name}.") and child.name.endswith(".tmp")
    ]
    assert leftovers == []
    assert target.read_text(encoding="utf-8") == "old"

    monkeypatch.setattr(Path, "replace", original_replace)


def test_atomic_write_bytes_supports_mkdir_and_replace_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create parent directories and remove temp file when replace fails."""
    target = tmp_path / "nested" / "payload.bin"
    update_io.atomic_write_bytes(target, b"ok", mkdir=True)
    assert target.read_bytes() == b"ok"

    original_replace = Path.replace

    def _boom(self: Path, target_path: Path) -> Path:
        msg = "replace failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(Path, "replace", _boom)

    with pytest.raises(RuntimeError, match="replace failed"):
        update_io.atomic_write_bytes(target, b"new")

    leftovers = [
        child
        for child in target.parent.iterdir()
        if child.name.startswith(f".{target.name}.") and child.name.endswith(".tmp")
    ]
    assert leftovers == []
    assert target.read_bytes() == b"ok"

    monkeypatch.setattr(Path, "replace", original_replace)


def test_flake_helpers_cover_root_without_inputs_and_original_rev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve root input fallback and original.rev version extraction."""
    lock = type(
        "_Lock",
        (),
        {
            "root_node": type("_Root", (), {"inputs": None})(),
            "nodes": {},
        },
    )()
    monkeypatch.setattr(update_flake, "load_flake_lock", lambda: lock)

    assert update_flake.get_root_input_name("nixpkgs") == "nixpkgs"

    version = update_flake.get_flake_input_version(
        FlakeLockNode(
            original=OriginalRef(
                type="github",
                owner="owner",
                repo="repo",
                ref=None,
                rev="deadbeef",
            )
        )
    )
    assert version == "deadbeef"


def test_resolve_repo_root_env_and_nix_store_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve root from REPO_ROOT and nix-store fallback search."""
    env_root = tmp_path / "env-root"
    env_root.mkdir()
    monkeypatch.setenv("REPO_ROOT", str(env_root))
    assert update_paths._resolve_repo_root() == env_root.resolve()
    monkeypatch.delenv("REPO_ROOT", raising=False)

    repo_root = tmp_path / "repo"
    nested = repo_root / "nested" / "work"
    nested.mkdir(parents=True)
    (repo_root / "flake.nix").write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(update_paths, "__file__", "/nix/store/hash/lib/update/paths.py")
    monkeypatch.setattr(update_paths.Path, "cwd", staticmethod(lambda: nested))
    assert update_paths._resolve_repo_root() == repo_root.resolve()

    (repo_root / "flake.nix").unlink()
    assert update_paths._resolve_repo_root() == nested.resolve()


def test_paths_helpers_cover_remaining_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise flat-name edge cases and non-file child branch."""
    assert update_paths._flat_package_file_name(".sources.json", "sources.json") is None

    pkg_root = tmp_path / "packages"
    pkg_root.mkdir()

    class _WeirdChild:
        name = "weird"

        def is_dir(self) -> bool:
            return False

        def is_file(self) -> bool:
            return False

    original_iterdir = Path.iterdir

    def _iterdir(self: Path):
        if self == pkg_root:
            return iter([_WeirdChild()])
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _iterdir)
    mapped = update_paths.package_file_map_in(tmp_path, "sources.json")
    assert mapped == {}

    monkeypatch.setattr(
        update_paths, "package_file_map", lambda _filename: {"demo": Path("/x")}
    )
    assert update_paths.package_file_for("missing", "sources.json") is None
    assert update_paths.sources_file_for("missing") is None


def test_repo_root_proxy_string_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expose readable string representations for the lazy repo-root proxy."""
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    update_paths.get_repo_root.cache_clear()
    assert str(update_paths.REPO_ROOT) == str(tmp_path.resolve())
    assert repr(update_paths.REPO_ROOT) == repr(tmp_path.resolve())
    monkeypatch.delenv("REPO_ROOT", raising=False)


def test_sources_helpers_cover_loading_and_save_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover source map loading and save path-map fallback branches."""
    source_path = tmp_path / "packages" / "demo" / "sources.json"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        json.dumps({
            "hashes": {
                "x86_64-linux": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
            }
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        update_sources, "package_file_map", lambda _name: {"demo": source_path}
    )
    assert update_sources._source_file_map() == {"demo": source_path}
    loaded = update_sources.load_all_sources()
    assert "demo" in loaded.entries

    monkeypatch.setattr(update_sources, "python_source_names", lambda: {"a"})
    monkeypatch.setattr(update_sources, "nix_source_names", lambda: {"a", "b"})
    with pytest.raises(RuntimeError, match="Missing in Python source scan: b"):
        update_sources.validate_source_discovery_consistency()

    writes: list[Path] = []
    monkeypatch.setattr(update_sources, "_source_file_map", dict)
    monkeypatch.setattr(
        update_sources, "package_dir_for", lambda _name: tmp_path / "packages" / "demo"
    )
    monkeypatch.setattr(
        update_sources,
        "_atomic_write_json",
        lambda path, payload: writes.append(path),
    )

    update_sources.save_sources(
        SourcesFile(
            entries={
                "demo": SourceEntry(
                    hashes={
                        "x86_64-linux": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
                    }
                )
            }
        )
    )
    assert writes
    assert writes[0].name == "sources.json"


def test_save_sources_handles_none_path_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip write when mapping lookup returns ``None`` for an entry."""

    class _WeirdMap(dict[str, Path]):
        def __contains__(self, _key: object) -> bool:
            return True

        def get(self, _key: str, _default: object = None) -> Path | None:
            return None

    monkeypatch.setattr(update_sources, "_source_file_map", lambda: _WeirdMap())
    monkeypatch.setattr(update_sources, "package_dir_for", lambda _name: None)

    # Should not raise: entry is seen in __contains__, then skipped in write loop.
    update_sources.save_sources(
        SourcesFile(
            entries={
                "demo": SourceEntry(
                    hashes={
                        "x86_64-linux": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
                    }
                )
            }
        )
    )
