"""Tests for maintained Python helpers under modules/."""

from __future__ import annotations

import os
import runpy
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT


def _load_module(relative_path: str, module_name: str):
    return load_module_from_path(REPO_ROOT / relative_path, module_name)


def test_languages_package_init_loads_cleanly() -> None:
    """The languages helper package should stay importable."""
    module = _load_module("modules/home/languages/__init__.py", "languages_init_test")

    assert module.__doc__ == "Language helpers for home modules."


def test_ptpython_build_style_and_configure(monkeypatch) -> None:
    """Ptpython config should build and install the Catppuccin style."""
    module = _load_module("modules/home/languages/ptpython.py", "ptpython_test")
    style_calls: list[object] = []

    class _FakeStylesModule:
        @staticmethod
        def style_from_pygments_cls(style_cls: object) -> object:
            style_calls.append(style_cls)
            return {"style": style_cls}

    class _FakePygmentsModule:
        FrappeStyle = object()

    def _fake_import_module(name: str) -> object:
        if name == "catppuccin.extras.pygments":
            return _FakePygmentsModule
        assert name == "prompt_toolkit.styles"
        return _FakeStylesModule

    class _FakeRepl:
        def __init__(self) -> None:
            self.show_signature = False
            self.show_docstring = False
            self.enable_auto_suggest = False
            self.vi_mode = False
            self.calls: list[tuple[str, str, object | None]] = []

        def install_ui_colorscheme(self, name: str, style: object) -> None:
            self.calls.append(("install_ui", name, style))

        def use_ui_colorscheme(self, name: str) -> None:
            self.calls.append(("use_ui", name, None))

        def install_code_colorscheme(self, name: str, style: object) -> None:
            self.calls.append(("install_code", name, style))

        def use_code_colorscheme(self, name: str) -> None:
            self.calls.append(("use_code", name, None))

    monkeypatch.setattr(module.importlib, "import_module", _fake_import_module)
    repl = _FakeRepl()

    style = module._build_style()
    module.configure(repl)

    assert style == {"style": _FakePygmentsModule.FrappeStyle}
    assert style_calls == [
        _FakePygmentsModule.FrappeStyle,
        _FakePygmentsModule.FrappeStyle,
    ]
    assert repl.show_signature is True
    assert repl.show_docstring is True
    assert repl.enable_auto_suggest is True
    assert repl.vi_mode is True
    assert repl.calls == [
        ("install_ui", module.STYLE_NAME, style),
        ("use_ui", module.STYLE_NAME, None),
        ("install_code", module.STYLE_NAME, style),
        ("use_code", module.STYLE_NAME, None),
    ]


def test_git_delta_cache_helpers_cover_env_and_tree_edge_cases(
    monkeypatch, tmp_path: Path
) -> None:
    """Cache helper utilities should handle env fallbacks and stat errors."""
    module = _load_module("modules/home/git_delta_cache.py", "git_delta_cache_test")

    monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/cache-home")
    assert module._xdg_home("XDG_CACHE_HOME", ".cache") == Path("/tmp/cache-home")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path)
    assert module._xdg_home("XDG_CONFIG_HOME", ".config") == tmp_path / ".config"

    reference = tmp_path / "reference.bin"
    reference.write_text("ref", encoding="utf-8")
    regular_file = tmp_path / "newer.txt"
    regular_file.write_text("demo", encoding="utf-8")
    os.utime(
        regular_file,
        ns=(reference.stat().st_mtime_ns + 10, reference.stat().st_mtime_ns + 10),
    )
    assert module._tree_has_newer_regular_files(tmp_path, reference) is True
    assert (
        module._tree_has_newer_regular_files(tmp_path, tmp_path / "missing.bin") is True
    )

    class _BrokenEntry:
        def lstat(self) -> object:
            raise OSError("broken")

    class _RegularEntry:
        def __init__(self, mtime_ns: int) -> None:
            self._mtime_ns = mtime_ns

        def lstat(self) -> object:
            return SimpleNamespace(st_mode=stat.S_IFREG, st_mtime_ns=self._mtime_ns)

    class _Reference:
        def stat(self) -> object:
            return SimpleNamespace(st_mtime_ns=5)

    class _DirectoryWithEntries:
        def rglob(self, _pattern: str) -> list[object]:
            return [_BrokenEntry(), _RegularEntry(4)]

    class _DirectoryWithError:
        def rglob(self, _pattern: str) -> list[object]:
            raise OSError("scan failed")

    assert (
        module._tree_has_newer_regular_files(_DirectoryWithEntries(), _Reference())
        is False
    )
    assert (
        module._tree_has_newer_regular_files(_DirectoryWithError(), _Reference())
        is False
    )


def test_git_delta_cache_needs_rebuild_and_rebuild_cache(
    monkeypatch, tmp_path: Path
) -> None:
    """Cache rebuild decisions should honor missing files and newer theme/syntax trees."""
    module = _load_module(
        "modules/home/git_delta_cache.py", "git_delta_cache_rebuild_test"
    )
    cache_dir = tmp_path / "cache" / "bat"
    config_dir = tmp_path / "config" / "bat"
    cache_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)

    assert module._needs_rebuild(cache_dir, config_dir) is True

    (cache_dir / "themes.bin").write_text("themes", encoding="utf-8")
    (cache_dir / "syntaxes.bin").write_text("syntaxes", encoding="utf-8")
    (config_dir / "themes").mkdir()
    (config_dir / "syntaxes").mkdir()

    seen: list[Path] = []

    def _fake_tree_has_newer_regular_files(directory: Path, reference: Path) -> bool:
        seen.append(directory)
        return directory.name == "themes"

    monkeypatch.setattr(
        module,
        "_tree_has_newer_regular_files",
        _fake_tree_has_newer_regular_files,
    )
    assert module._needs_rebuild(cache_dir, config_dir) is True

    monkeypatch.setattr(
        module,
        "_tree_has_newer_regular_files",
        lambda _directory, _reference: False,
    )
    assert module._needs_rebuild(cache_dir, config_dir) is False
    assert seen == [config_dir / "themes"]

    captured: dict[str, object] = {}
    monkeypatch.setattr(module, "BAT", "/bin/bat")
    tmp_root = tmp_path / "tmp"
    tmp_root.mkdir()
    monkeypatch.setenv("TMPDIR", str(tmp_root))

    def _fake_run(args: list[str], **kwargs: object) -> object:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", _fake_run)
    cache_home = tmp_path / "cache-home"
    module._rebuild_cache(cache_home)

    assert (cache_home / "bat").is_dir()
    assert captured["args"] == ["/bin/bat", "cache", "--build"]
    kwargs = captured["kwargs"]
    assert kwargs["check"] is False
    assert kwargs["env"]["XDG_CACHE_HOME"] == str(cache_home)
    assert kwargs["stderr"] is module.subprocess.DEVNULL
    assert kwargs["stdout"] is module.subprocess.DEVNULL


def test_git_delta_cache_main_rebuilds_and_execs(monkeypatch, tmp_path: Path) -> None:
    """Main should rebuild when needed, then replace the process with delta."""
    module = _load_module(
        "modules/home/git_delta_cache.py", "git_delta_cache_main_test"
    )
    cache_home = tmp_path / "cache-home"
    config_home = tmp_path / "config-home"
    seen: dict[str, object] = {}

    def _fake_xdg_home(env_name: str, fallback: str) -> Path:
        assert fallback in {".cache", ".config"}
        return cache_home if env_name == "XDG_CACHE_HOME" else config_home

    monkeypatch.setattr(module, "_xdg_home", _fake_xdg_home)
    monkeypatch.setattr(module, "_needs_rebuild", lambda _cache_dir, _config_dir: True)
    monkeypatch.setattr(
        module, "_rebuild_cache", lambda path: seen.setdefault("rebuild", path)
    )
    monkeypatch.setattr(module, "DELTA", "/bin/delta")
    monkeypatch.setattr(
        module.os,
        "execv",
        lambda executable, argv: seen.setdefault("execv", (executable, argv)),
    )

    module.main(["--side-by-side"])

    assert seen == {
        "rebuild": cache_home,
        "execv": ("/bin/delta", ["/bin/delta", "--side-by-side"]),
    }


def test_git_delta_cache_main_guard_executes_without_rebuild(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Running the wrapper as __main__ should still exec delta with argv passthrough."""
    cache_home = tmp_path / "cache-home"
    config_home = tmp_path / "config-home"
    (cache_home / "bat").mkdir(parents=True)
    (config_home / "bat").mkdir(parents=True)
    for name in ("themes.bin", "syntaxes.bin"):
        (cache_home / "bat" / name).write_text(name, encoding="utf-8")

    calls: dict[str, object] = {}
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setattr(
        os,
        "execv",
        lambda executable, argv: calls.setdefault("execv", (executable, argv)),
    )
    monkeypatch.setattr(sys, "argv", ["git_delta_cache.py", "--raw"])

    runpy.run_path(
        str(REPO_ROOT / "modules/home/git_delta_cache.py"),
        run_name="__main__",
        init_globals={"DELTA": "/bin/delta"},
    )

    assert calls["execv"] == ("@DELTA@", ["@DELTA@", "--raw"])
