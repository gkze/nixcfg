"""Enforce Nix-owned update and local-engine policy in Mach Studio's ASAR."""

import argparse
import plistlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from lib.asar_integrity import (
    AsarIntegrityError,
    packed_file_paths,
    patch_bundle_integrity,
    read_packed_file,
    replace_packed_file,
)

_ENABLED_GATE = b"let t=e.disabled===!0||e.app.isPackaged===!1;"
_DISABLED_GATE = b"let t=!0/* Updates are managed by Nix. */;   "


@dataclass(frozen=True, slots=True)
class _EngineGeneration:
    """One audited release generation's minified engine-provisioning identifiers."""

    resolve: bytes
    args: bytes
    error: bytes
    serve: bytes
    wheel_catch: bool = True


_ENGINE_GENERATIONS: tuple[_EngineGeneration, ...] = (
    # Identifiers observed in each audited Mach Studio release generation,
    # oldest first. A new generation is added only after reviewing its DMG.
    _EngineGeneration(resolve=b"BO", args=b"sLt", error=b"_Lt", serve=b"Mg"),
    _EngineGeneration(resolve=b"xx", args=b"x8", error=b"E8", serve=b"bx"),
    # The 1.53 wheel shape dropped the trailing catch block.
    _EngineGeneration(
        resolve=b"pO", args=b"GFt", error=b"tIt", serve=b"zg", wheel_catch=False
    ),
    _EngineGeneration(resolve=b"yP", args=b"o8t", error=b"g8t", serve=b"yg"),
)


def _engine_source_open(generation: _EngineGeneration) -> bytes:
    """Return one generation's fail-open source provisioning shape."""
    return (
        b"let i;try{i=(await "
        + generation.resolve
        + b"({context:this.context,cwd:e.managedRoot,environment:"
        b"this.options.environment,onLog:this.options.onLog,candidatesOverride:"
        b"this.options.uvCandidatesOverride})).binaryPath}catch(e){this.options.onLog?."
        b"(`[local-moe] cannot resolve host uv to install local_moe_engine: "
        b"${e.message}\\n`);return}try{await this.runCommand(i,[`pip`,`install`,"
        b"`--python`,e.pythonExecutable,`--editable`,`${r}[dev,dflash]`,"
        + generation.args
        + b"],"
        b"e.managedRoot,void 0,t)}catch(e){if("
        + generation.error
        + b"(e))throw e;this.options.onLog?."
        b'(`[local-moe] uv pip install -e "${r}[dev,dflash]" '
        b"failed: ${e.message}\\n`)}"
    )


def _engine_source_closed(generation: _EngineGeneration) -> bytes:
    """Return the matching fail-closed shape that validates the bundled source."""
    return (
        b"let i=(await "
        + generation.resolve
        + b"({context:this.context,cwd:e.managedRoot,environment:"
        b"this.options.environment,onLog:this.options.onLog,candidatesOverride:"
        b"this.options.uvCandidatesOverride})).binaryPath;await this.runCommand(i,"
        b"[`pip`,`install`,`--python`,e.pythonExecutable,`--editable`,"
        b"`${r}[dev,dflash]`," + generation.args + b"],e.managedRoot,"
        b"void 0,t);await this.runCommand(e.pythonExecutable,[`-c`,"
        b"`import mach,pathlib,sys;sys.exit(not pathlib.Path(mach.__file__).resolve()"
        b".is_relative_to(pathlib.Path(sys.argv[1]).resolve()))`,r],e.managedRoot,"
        b"void 0,t)"
    )


def _engine_wheel_open(generation: _EngineGeneration) -> bytes:
    """Return one generation's fail-open bundled-wheel provisioning shape."""
    wheel_open = (
        b"let r;try{r=(await "
        + generation.resolve
        + b"({context:this.context,cwd:e.managedRoot,environment:"
        b"this.options.environment,onLog:this.options.onLog,candidatesOverride:"
        b"this.options.uvCandidatesOverride})).binaryPath}catch(e){this.options.onLog?."
        b"(`[local-moe] cannot resolve host uv to install bundled local_moe_engine "
        b"wheel: ${e.message}\\n`);return}try{await this.runCommand(r,[`pip`,"
        b"`install`,`--python`,e.pythonExecutable,`${n}[dflash]`,"
        + generation.args
        + b"],e.managedRoot,void 0,t),await "
        + generation.serve
        + b"(e.localMoeServeExecutable)||this.options.onLog?.(`[local-moe] bundled wheel"
        b" install completed, but mach-serve was not created at "
        b"${e.localMoeServeExecutable}.\\n`)}"
    )
    if not generation.wheel_catch:
        return wheel_open
    return wheel_open + (
        b"catch(e){if(" + generation.error + b"(e))throw e;this.options.onLog?.("
        b'`[local-moe] uv pip install "${n}[dflash]" failed: ${e.message}\\n`)}'
    )


def _engine_wheel_closed(wheel_open: bytes) -> bytes:
    """Propagate bundled-wheel provisioning failures instead of logging them."""
    return wheel_open.replace(
        b"uv to install bundled",
        b"uv install bundled",
        1,
    ).replace(b";return}", b";throw e}", 1)


(
    _FAIL_OPEN_ENGINE_INSTALL_SOURCE,
    _FAIL_OPEN_ENGINE_INSTALL_WHEEL,
    _FAIL_CLOSED_ENGINE_INSTALL_SOURCE,
    _FAIL_OPEN_ENGINE_INSTALL_SOURCE_147,
    _FAIL_OPEN_ENGINE_INSTALL_WHEEL_147,
    _FAIL_CLOSED_ENGINE_INSTALL_SOURCE_147,
    _FAIL_OPEN_ENGINE_INSTALL_SOURCE_153,
    _FAIL_OPEN_ENGINE_INSTALL_WHEEL_153,
    _FAIL_CLOSED_ENGINE_INSTALL_SOURCE_153,
    _FAIL_OPEN_ENGINE_INSTALL_SOURCE_163,
    _FAIL_OPEN_ENGINE_INSTALL_WHEEL_163,
    _FAIL_CLOSED_ENGINE_INSTALL_SOURCE_163,
) = tuple(
    anchor
    for generation in _ENGINE_GENERATIONS
    for anchor in (
        _engine_source_open(generation),
        _engine_wheel_open(generation),
        _engine_source_closed(generation),
    )
)

_ENGINE_SOURCE_SHAPES = (
    (_FAIL_OPEN_ENGINE_INSTALL_SOURCE, _FAIL_CLOSED_ENGINE_INSTALL_SOURCE),
    (_FAIL_OPEN_ENGINE_INSTALL_SOURCE_147, _FAIL_CLOSED_ENGINE_INSTALL_SOURCE_147),
    (_FAIL_OPEN_ENGINE_INSTALL_SOURCE_153, _FAIL_CLOSED_ENGINE_INSTALL_SOURCE_153),
    (_FAIL_OPEN_ENGINE_INSTALL_SOURCE_163, _FAIL_CLOSED_ENGINE_INSTALL_SOURCE_163),
)
_ENGINE_WHEEL_SHAPES = (
    _FAIL_OPEN_ENGINE_INSTALL_WHEEL,
    _FAIL_OPEN_ENGINE_INSTALL_WHEEL_147,
    _FAIL_OPEN_ENGINE_INSTALL_WHEEL_153,
    _FAIL_OPEN_ENGINE_INSTALL_WHEEL_163,
)

_WHEEL_REINSTALL_DESCRIPTION = (
    b"Wipes the `.venv/` directory and rebuilds it from the bundled lockfile and "
    b"wheel. Use this when local models fail to start with an engine-unavailable "
    b"error."
)
_SOURCE_REINSTALL_DESCRIPTION = (
    b"Wipes `.venv/` and rebuilds it from the bundled lockfile and local engine "
    b"source. Use this when local models fail to start with an engine-unavailable "
    b"error."
)
_WHEEL_MISSING_DESCRIPTION = (
    b"Engine wheel not bundled. Reinstall Mach Studio from a recent build."
)
_SOURCE_READY_DESCRIPTION = (
    b"Packaged local_moe_engine source is bundled for managed provisioning"
)
_WHEEL_TITLE = b"Bundled wheel"
_SOURCE_TITLE = b"Engine source"
_RENDERER_VENDOR_INVENTORY = (
    (_WHEEL_REINSTALL_DESCRIPTION, 1),
    (_WHEEL_MISSING_DESCRIPTION, 1),
    (_WHEEL_TITLE, 3),
)
_RENDERER_PATH_PATTERN = re.compile(r"^dist/assets/index-[^/]+\.js$")
_MAIN_POLICY_DIRECTORY = "dist-electron"


class PatchError(RuntimeError):
    """Mach Studio's updater no longer matches the audited policy anchor."""


def _replace_exact(
    payload: bytes,
    old: bytes,
    new: bytes,
    *,
    label: str,
    count: int = 1,
) -> bytes:
    actual_count = payload.count(old)
    if actual_count != count:
        msg = f"expected {count} Mach Studio {label} anchor(s), found {actual_count}"
        raise PatchError(msg)
    return payload.replace(old, new)


def patch_main(payload: bytes) -> bytes:
    """Fail closed on updater or local-engine provisioning drift."""
    patched = _replace_exact(
        payload,
        _ENABLED_GATE,
        _DISABLED_GATE,
        label="updater policy",
    )
    matched = False
    for source_open, source_closed in _ENGINE_SOURCE_SHAPES:
        if source_open not in patched:
            continue
        pad = len(source_open) - len(source_closed)
        patched = _replace_exact(
            patched,
            source_open,
            source_closed + b" " * pad,
            label="local-engine source provisioning",
        )
        matched = True
        break
    for wheel_open in _ENGINE_WHEEL_SHAPES:
        if wheel_open not in patched:
            continue
        wheel_closed = _engine_wheel_closed(wheel_open)
        pad = len(wheel_open) - len(wheel_closed)
        if pad > 0:
            wheel_closed = wheel_closed + b" " * pad
        patched = _replace_exact(
            patched,
            wheel_open,
            wheel_closed,
            label="local-engine wheel provisioning",
        )
        matched = True
        break
    if not matched:
        msg = "expected 1 Mach Studio local-engine provisioning anchor(s), found 0"
        raise PatchError(msg)
    return patched


def patch_renderer(payload: bytes) -> bytes:
    """Describe the reviewed release's source-backed local engine."""
    patched = _replace_exact(
        payload,
        _WHEEL_REINSTALL_DESCRIPTION,
        _SOURCE_REINSTALL_DESCRIPTION,
        label="engine reinstall description",
    )
    patched = _replace_exact(
        patched,
        _WHEEL_MISSING_DESCRIPTION,
        _SOURCE_READY_DESCRIPTION,
        label="engine source description",
    )
    return _replace_exact(
        patched,
        _WHEEL_TITLE,
        _SOURCE_TITLE,
        label="engine source title",
        count=3,
    )


def resolve_main_policy_path(asar_path: Path) -> str:
    """Find the sole Electron main-process chunk carrying the updater gate."""
    matches: list[str] = []
    for relative_path in packed_file_paths(asar_path):
        directory, _, file_name = relative_path.rpartition("/")
        if directory != _MAIN_POLICY_DIRECTORY or not file_name.endswith(".js"):
            continue
        if _ENABLED_GATE in read_packed_file(asar_path, relative_path):
            matches.append(relative_path)
    if len(matches) != 1:
        rendered = ", ".join(matches) if matches else "none"
        msg = (
            "expected exactly one Mach Studio main-policy chunk with the updater "
            f"gate, found {len(matches)}: {rendered}"
        )
        raise PatchError(msg)
    return matches[0]


def resolve_renderer_path(asar_path: Path) -> str:
    """Find the sole fingerprinted renderer asset with the vendor inventory."""
    matches: list[str] = []
    for relative_path in packed_file_paths(asar_path):
        if _RENDERER_PATH_PATTERN.fullmatch(relative_path) is None:
            continue
        payload = read_packed_file(asar_path, relative_path)
        if all(
            payload.count(anchor) == expected_count
            for anchor, expected_count in _RENDERER_VENDOR_INVENTORY
        ):
            matches.append(relative_path)
    if len(matches) != 1:
        rendered = ", ".join(matches) if matches else "none"
        msg = (
            "expected exactly one Mach Studio renderer asset with the complete "
            f"vendor inventory, found {len(matches)}: {rendered}"
        )
        raise PatchError(msg)
    return matches[0]


def patch_bundle(asar_path: Path, info_plist_path: Path) -> str:
    """Apply reviewed Mach Studio policy and refresh ASAR integrity."""
    engine_source = asar_path.parent / "vendor/local_moe_engine"
    if not engine_source.is_dir():
        msg = (
            f"Mach Studio packaged local_moe_engine source is missing: {engine_source}"
        )
        raise PatchError(msg)
    main_policy_path = resolve_main_policy_path(asar_path)
    renderer_path = resolve_renderer_path(asar_path)
    main_payload = read_packed_file(asar_path, main_policy_path)
    renderer_payload = read_packed_file(asar_path, renderer_path)
    patch_main(main_payload)
    patch_renderer(renderer_payload)

    def patch_archive(staged: Path) -> str:
        replace_packed_file(staged, main_policy_path, patch_main)
        return replace_packed_file(staged, renderer_path, patch_renderer)

    return patch_bundle_integrity(asar_path, info_plist_path, patch_archive)


def main(argv: list[str] | None = None) -> int:
    """Run the package-local Mach Studio policy patch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asar_path", type=Path)
    parser.add_argument("info_plist_path", type=Path)
    args = parser.parse_args(argv)
    try:
        digest = patch_bundle(
            args.asar_path,
            args.info_plist_path,
        )
    except (
        AsarIntegrityError,
        OSError,
        PatchError,
        plistlib.InvalidFileException,
    ) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write(f"enforced Mach Studio policy; ASAR header SHA256 {digest}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover -- packaged CLI guard
    raise SystemExit(main())
