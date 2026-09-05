"""Enforce Nix-owned update and local-engine policy in Mach Studio's ASAR."""

import argparse
import plistlib
import re
import sys
from pathlib import Path

from lib.asar_integrity import (
    AsarIntegrityError,
    packed_file_paths,
    read_packed_file,
    replace_packed_file,
    write_info_plist_hash,
)

MAIN_PATH = "dist-electron/main.js"
_RENDERER_PATH_PATTERN = re.compile(r"^dist/assets/index-[^/]+\.js$")
_ENABLED_GATE = b"let t=e.disabled===!0||e.app.isPackaged===!1;"
_DISABLED_GATE = b"let t=!0/* Updates are managed by Nix. */;   "
_FAIL_OPEN_ENGINE_INSTALL = (
    b"let i;try{i=(await xx({context:this.context,cwd:e.managedRoot,"
    b"environment:this.options.environment,onLog:this.options.onLog,"
    b"candidatesOverride:this.options.uvCandidatesOverride})).binaryPath}"
    b"catch(e){this.options.onLog?.(`[local-moe] cannot resolve host uv to "
    b"install local_moe_engine: ${e.message}\\n`);return}try{await this."
    b"runCommand(i,[`pip`,`install`,`--python`,e.pythonExecutable,`--editable`,"
    b"`${r}[dev,dflash]`,x8],e.managedRoot,void 0,t)}catch(e){if(E8(e))throw e;"
    b'this.options.onLog?.(`[local-moe] uv pip install -e "${r}[dev,dflash]" '
    b"failed: ${e.message}\\n`)}"
)
_FAIL_CLOSED_ENGINE_INSTALL = (
    b"let i=(await xx({context:this.context,cwd:e.managedRoot,environment:this."
    b"options.environment,onLog:this.options.onLog,candidatesOverride:this."
    b"options.uvCandidatesOverride})).binaryPath;await this.runCommand(i,[`pip`,"
    b"`install`,`--python`,e.pythonExecutable,`--editable`,`${r}[dev,dflash]`,"
    b"x8],e.managedRoot,void 0,t);await this.runCommand(e.pythonExecutable,[`-c`,"
    b"`import mach,pathlib,sys;sys.exit(not pathlib.Path(mach.__file__).resolve()"
    b".is_relative_to(pathlib.Path(sys.argv[1]).resolve()))`,r],e.managedRoot,"
    b"void 0,t)"
).ljust(len(_FAIL_OPEN_ENGINE_INSTALL), b" ")
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
    return _replace_exact(
        patched,
        _FAIL_OPEN_ENGINE_INSTALL,
        _FAIL_CLOSED_ENGINE_INSTALL,
        label="local-engine provisioning",
    )


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
    renderer_path = resolve_renderer_path(asar_path)
    main_payload = read_packed_file(asar_path, MAIN_PATH)
    renderer_payload = read_packed_file(asar_path, renderer_path)
    patch_main(main_payload)
    patch_renderer(renderer_payload)

    replace_packed_file(
        asar_path,
        MAIN_PATH,
        patch_main,
    )
    digest = replace_packed_file(
        asar_path,
        renderer_path,
        patch_renderer,
    )
    write_info_plist_hash(info_plist_path, asar_path)
    return digest


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
