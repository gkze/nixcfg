"""Tests for the repo-local rusty_v8 patch helper scripts."""

from __future__ import annotations

import runpy
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest


def _scripts_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "rusty-v8"


def _load_script(script_name: str) -> dict[str, Any]:
    return cast("dict[str, Any]", runpy.run_path(str(_scripts_dir() / script_name)))


def _write_with_extra_anchor(target: Path, namespace: dict[str, Any]) -> None:
    extra_anchor = namespace.get(
        "_EXTRA_REPLACE_NEEDLE",
        "    if (!rustc_nightly_capability) {\n"
        '      rustflags += [ "--cfg=rust_allocator_no_nightly_capability" ]\n'
        "    }\n",
    )
    target.write_text(
        f"prefix\n{namespace['_NEEDLE']}{extra_anchor}suffix\n", encoding="utf-8"
    )


def _main(namespace: dict[str, Any]) -> Callable[[], int]:
    return cast("Callable[[], int]", namespace["main"])


def test_patch_allocator_build_script_success_and_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patch allocator BUILD files and reject invalid CLI inputs."""
    namespace = _load_script("patch_allocator_build.py")
    target = tmp_path / "BUILD.gn"
    _write_with_extra_anchor(target, namespace)

    monkeypatch.setattr(sys, "argv", ["patch_allocator_build.py", str(target)])
    assert _main(namespace)() == 0
    patched = target.read_text(encoding="utf-8")
    assert 'config("allocator_alwayslink") {' in patched
    assert "legacy shim names" in patched

    monkeypatch.setattr(sys, "argv", ["patch_allocator_build.py"])
    with pytest.raises(SystemExit, match="usage: patch_allocator_build.py"):
        _main(namespace)()

    target.write_text("no anchor\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["patch_allocator_build.py", str(target)])
    with pytest.raises(SystemExit, match="allocator BUILD.gn anchor not found"):
        _main(namespace)()


def test_patch_allocator_build_main_guard_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Execute the allocator patch helper through its CLI entrypoint."""
    script_path = _scripts_dir() / "patch_allocator_build.py"
    namespace = _load_script(script_path.name)
    target = tmp_path / "BUILD.gn"
    _write_with_extra_anchor(target, namespace)

    monkeypatch.setattr(sys, "argv", [str(script_path), str(target)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(script_path), run_name="__main__")
    assert exc.value.code == 0


def test_patch_apple_toolchain_script_success_and_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patch Apple toolchain settings and reject invalid inputs."""
    namespace = _load_script("patch_apple_toolchain_host_build_tools.py")
    target = tmp_path / "toolchain.gni"
    target.write_text(
        f"prefix\n{namespace['_NEEDLE']}{namespace['_UNSUPPORTED_RUST_TOOL_INPUT']}suffix\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys, "argv", ["patch_apple_toolchain_host_build_tools.py", str(target)]
    )
    assert _main(namespace)() == 0
    patched = target.read_text(encoding="utf-8")
    assert "use_lld = false" in patched
    assert "fatal_linker_warnings = false" in patched
    assert "rustc_wrapper_inputs" not in patched

    monkeypatch.setattr(sys, "argv", ["patch_apple_toolchain_host_build_tools.py"])
    with pytest.raises(
        SystemExit,
        match="usage: patch_apple_toolchain_host_build_tools.py",
    ):
        _main(namespace)()

    target.write_text("no anchor\n", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["patch_apple_toolchain_host_build_tools.py", str(target)]
    )
    with pytest.raises(
        SystemExit, match="apple toolchain host-build-tools anchor not found"
    ):
        _main(namespace)()


def test_patch_apple_toolchain_main_guard_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute the Apple toolchain patch helper through its CLI entrypoint."""
    script_path = _scripts_dir() / "patch_apple_toolchain_host_build_tools.py"
    namespace = _load_script(script_path.name)
    target = tmp_path / "toolchain.gni"
    target.write_text(f"prefix\n{namespace['_NEEDLE']}suffix\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [str(script_path), str(target)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(script_path), run_name="__main__")
    assert exc.value.code == 0


@pytest.mark.parametrize(
    "use_lld_line",
    [
        '  use_lld = is_clang && current_os != "zos"\n',
        '  use_lld = is_clang && current_os != "zos" && experimental_linker_path == ""\n',
        '  use_lld   = is_clang && current_os != "zos"\n',
        '  use_lld = is_clang && current_os != "zos" && experimental_linker_path == "" &&\n'
        '            !(is_ios && current_cpu == "arm64e")\n',
    ],
)
def test_patch_compiler_gni_script_success_and_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_lld_line: str,
) -> None:
    """Patch compiler.gni linker settings across pinned Chromium variants."""
    namespace = _load_script("patch_compiler_gni.py")
    target = tmp_path / "compiler.gni"
    target.write_text(f"prefix\n{use_lld_line}suffix\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["patch_compiler_gni.py", str(target)])
    assert _main(namespace)() == 0
    patched = target.read_text(encoding="utf-8")
    assert "use_lld = false" in patched

    monkeypatch.setattr(sys, "argv", ["patch_compiler_gni.py"])
    with pytest.raises(SystemExit, match="usage: patch_compiler_gni.py"):
        _main(namespace)()

    target.write_text("no anchor\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["patch_compiler_gni.py", str(target)])
    with pytest.raises(SystemExit, match="compiler.gni use_lld anchor not found"):
        _main(namespace)()


def test_patch_compiler_gni_main_guard_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute the compiler.gni patch helper through its CLI entrypoint."""
    script_path = _scripts_dir() / "patch_compiler_gni.py"
    target = tmp_path / "compiler.gni"
    target.write_text(
        'prefix\n  use_lld = is_clang && current_os != "zos" &&\n'
        '            current_cpu != "arm64e"\n'
        "suffix\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "argv", [str(script_path), str(target)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(script_path), run_name="__main__")
    assert exc.value.code == 0


def test_patch_compiler_gni_script_patches_build_gn_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strip newer Chromium compiler flags that packaged clangs do not support."""
    namespace = _load_script("patch_compiler_gni.py")
    target = tmp_path / "BUILD.gn"
    target.write_text(
        "prefix\n"
        '          cflags += [ "-fdiagnostics-show-inlining-chain" ]\n'
        '      cflags += [ "/clang:-fdiagnostics-show-inlining-chain" ]\n'
        '  cflags += [ "-fno-lifetime-dse" ]\n'
        '        "-fsanitize-ignore-for-ubsan-feature=array-bounds",\n'
        '      cflags += [ "-fcrash-diagnostics-dir=" + clang_diagnostic_dir ]\n'
        "suffix\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "argv", ["patch_compiler_gni.py", str(target)])
    assert _main(namespace)() == 0
    patched = target.read_text(encoding="utf-8")
    assert "-fdiagnostics-show-inlining-chain" not in patched
    assert "-fno-lifetime-dse" not in patched
    assert "-fsanitize-ignore-for-ubsan-feature" not in patched
    assert "-fcrash-diagnostics-dir=" in patched

    monkeypatch.setattr(sys, "argv", ["patch_compiler_gni.py"])
    with pytest.raises(SystemExit, match="usage: patch_compiler_gni.py"):
        _main(namespace)()

    target.write_text("no unsupported flags\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["patch_compiler_gni.py", str(target)])
    assert _main(namespace)() == 0
    assert target.read_text(encoding="utf-8") == "no unsupported flags\n"


def test_patch_compiler_gni_script_patches_sanitizers_gni_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strip sanitizer flags that packaged clangs do not support."""
    namespace = _load_script("patch_compiler_gni.py")
    target = tmp_path / "sanitizers.gni"
    target.write_text(
        "prefix\n"
        '        "-fsanitize=${invoker.sanitizer}",\n'
        '        "-fsanitize-ignore-for-ubsan-feature=${invoker.sanitizer}",\n'
        "suffix\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "argv", ["patch_compiler_gni.py", str(target)])
    assert _main(namespace)() == 0
    patched = target.read_text(encoding="utf-8")
    assert "-fsanitize-ignore-for-ubsan-feature" not in patched
    assert "-fsanitize=${invoker.sanitizer}" in patched


def test_patch_compiler_gni_main_guard_runs_for_build_gn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute the compiler BUILD.gn patch helper through its CLI entrypoint."""
    script_path = _scripts_dir() / "patch_compiler_gni.py"
    namespace = _load_script(script_path.name)
    target = tmp_path / "BUILD.gn"
    target.write_text(
        f'      cflags += [ "{namespace["_UNSUPPORTED_BUILD_GN_FLAGS"][0]}" ]\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "argv", [str(script_path), str(target)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(script_path), run_name="__main__")
    assert exc.value.code == 0
    assert target.read_text(encoding="utf-8") == ""


def test_patch_stdlib_adler_script_success_noop_and_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normalize older stdlib adler branches while accepting newer V8 sources."""
    namespace = _load_script("patch_stdlib_adler.py")
    target = tmp_path / "BUILD.gn"
    target.write_text(f"prefix\n{namespace['_LEGACY_BLOCK']}suffix\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["patch_stdlib_adler.py", str(target)])
    assert _main(namespace)() == 0
    patched = target.read_text(encoding="utf-8")
    assert 'stdlib_files += [ "adler2" ]' in patched
    assert '"adler"' not in patched

    already_current = 'stdlib_files += [\n      "adler2",\n    ]\n'
    target.write_text(already_current, encoding="utf-8")
    assert _main(namespace)() == 0
    assert target.read_text(encoding="utf-8") == already_current

    monkeypatch.setattr(sys, "argv", ["patch_stdlib_adler.py"])
    with pytest.raises(SystemExit, match="usage: patch_stdlib_adler.py"):
        _main(namespace)()

    target.write_text('stdlib_files += [\n      "adler",\n    ]\n', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["patch_stdlib_adler.py", str(target)])
    with pytest.raises(SystemExit, match="rust stdlib adler selection"):
        _main(namespace)()


def test_patch_stdlib_adler_main_guard_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute the stdlib adler patch helper through its CLI entrypoint."""
    script_path = _scripts_dir() / "patch_stdlib_adler.py"
    namespace = _load_script(script_path.name)
    target = tmp_path / "BUILD.gn"
    target.write_text(str(namespace["_LEGACY_BLOCK"]), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [str(script_path), str(target)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(script_path), run_name="__main__")
    assert exc.value.code == 0
    assert target.read_text(encoding="utf-8") == namespace["_REPLACEMENT"]


def test_patch_build_rs_prebuilt_script_success_and_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patch build.rs prebuilt handling and reject invalid inputs."""
    namespace = _load_script("patch_build_rs_prebuilt.py")
    target = tmp_path / "build.rs"
    target.write_text(
        f"prefix\n{namespace['_ENV_NEEDLE']}\n{namespace['_PREBUILT_NEEDLE']}suffix\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "argv", ["patch_build_rs_prebuilt.py", str(target)])
    assert _main(namespace)() == 0
    patched = target.read_text(encoding="utf-8")
    assert "RUSTY_V8_PREBUILT_GN_OUT" in patched
    assert "build_binding();" in patched

    monkeypatch.setattr(sys, "argv", ["patch_build_rs_prebuilt.py"])
    with pytest.raises(SystemExit, match="usage: patch_build_rs_prebuilt.py"):
        _main(namespace)()

    target.write_text("no env anchor\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["patch_build_rs_prebuilt.py", str(target)])
    with pytest.raises(SystemExit, match="RUSTY_V8_SRC_BINDING_PATH"):
        _main(namespace)()

    target.write_text(str(namespace["_ENV_NEEDLE"]), encoding="utf-8")
    with pytest.raises(SystemExit, match="prebuilt V8 branch"):
        _main(namespace)()


def test_patch_build_rs_prebuilt_main_guard_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute the build.rs prebuilt patch helper through its CLI entrypoint."""
    script_path = _scripts_dir() / "patch_build_rs_prebuilt.py"
    namespace = _load_script(script_path.name)
    target = tmp_path / "build.rs"
    target.write_text(
        f"{namespace['_ENV_NEEDLE']}{namespace['_PREBUILT_NEEDLE']}",
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "argv", [str(script_path), str(target)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(script_path), run_name="__main__")
    assert exc.value.code == 0


def test_patch_whole_archive_script_success_and_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patch Chromium whole-archive handling and reject invalid inputs."""
    namespace = _load_script("patch_whole_archive.py")
    target = tmp_path / "whole_archive.py"
    target.write_text(f"prefix\n{namespace['_NEEDLE']}suffix\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["patch_whole_archive.py", str(target)])
    assert _main(namespace)() == 0
    patched = target.read_text(encoding="utf-8")
    assert "def is_allocator_rlib" in patched
    assert "is_allocator_rlib(x)" in patched

    monkeypatch.setattr(sys, "argv", ["patch_whole_archive.py"])
    with pytest.raises(SystemExit, match="usage: patch_whole_archive.py"):
        _main(namespace)()

    target.write_text("no anchor\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["patch_whole_archive.py", str(target)])
    with pytest.raises(SystemExit, match="whole_archive.py anchor not found"):
        _main(namespace)()


def test_patch_whole_archive_main_guard_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute the whole-archive patch helper through its CLI entrypoint."""
    script_path = _scripts_dir() / "patch_whole_archive.py"
    namespace = _load_script(script_path.name)
    target = tmp_path / "whole_archive.py"
    target.write_text(f"prefix\n{namespace['_NEEDLE']}suffix\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [str(script_path), str(target)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(script_path), run_name="__main__")
    assert exc.value.code == 0
