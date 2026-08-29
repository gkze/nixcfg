"""Enforce Nix-owned update and local-engine policy in Mach Studio's ASAR."""

import argparse
import hashlib
import plistlib
import sys
from functools import partial
from pathlib import Path

from lib.asar_integrity import (
    AsarIntegrityError,
    read_packed_file,
    replace_packed_file,
    write_info_plist_hash,
)

MAIN_PATH = "dist-electron/main.js"
RENDERER_PATH = "dist/assets/index-BeczixRy.js"
REVIEWED_MAIN_SHA256 = (
    "61561bb24a99c8e6aeb197df8e2a37c00f703e4cf1c53a1841362833d19cb60f"
)
REVIEWED_RENDERER_SHA256 = (
    "dce52b93639d873a0391126660a189fe481005ec8228f2d63a23266eb3605f5c"
)
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


def patch_main(
    payload: bytes,
    *,
    expected_sha256: str = REVIEWED_MAIN_SHA256,
) -> bytes:
    """Fail closed on updater or local-engine provisioning drift."""
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        msg = (
            "Mach Studio packed main SHA-256 drifted: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )
        raise PatchError(msg)
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


def patch_renderer(
    payload: bytes,
    *,
    expected_sha256: str = REVIEWED_RENDERER_SHA256,
) -> bytes:
    """Describe the reviewed release's source-backed local engine."""
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        msg = (
            "Mach Studio packed renderer SHA-256 drifted: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )
        raise PatchError(msg)
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


def patch_bundle(
    asar_path: Path,
    info_plist_path: Path,
    *,
    expected_main_sha256: str = REVIEWED_MAIN_SHA256,
    expected_renderer_sha256: str = REVIEWED_RENDERER_SHA256,
) -> str:
    """Apply reviewed Mach Studio policy and refresh ASAR integrity."""
    engine_source = asar_path.parent / "vendor/local_moe_engine"
    if not engine_source.is_dir():
        msg = (
            f"Mach Studio packaged local_moe_engine source is missing: {engine_source}"
        )
        raise PatchError(msg)
    main_payload = read_packed_file(asar_path, MAIN_PATH)
    renderer_payload = read_packed_file(asar_path, RENDERER_PATH)
    patch_main(main_payload, expected_sha256=expected_main_sha256)
    patch_renderer(renderer_payload, expected_sha256=expected_renderer_sha256)

    replace_packed_file(
        asar_path,
        MAIN_PATH,
        partial(patch_main, expected_sha256=expected_main_sha256),
    )
    digest = replace_packed_file(
        asar_path,
        RENDERER_PATH,
        partial(patch_renderer, expected_sha256=expected_renderer_sha256),
    )
    write_info_plist_hash(info_plist_path, asar_path)
    return digest


def main(
    argv: list[str] | None = None,
    *,
    expected_main_sha256: str = REVIEWED_MAIN_SHA256,
    expected_renderer_sha256: str = REVIEWED_RENDERER_SHA256,
) -> int:
    """Run the package-local Mach Studio policy patch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asar_path", type=Path)
    parser.add_argument("info_plist_path", type=Path)
    args = parser.parse_args(argv)
    try:
        digest = patch_bundle(
            args.asar_path,
            args.info_plist_path,
            expected_main_sha256=expected_main_sha256,
            expected_renderer_sha256=expected_renderer_sha256,
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
