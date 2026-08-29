"""Disable Grok Build's mutable app and renderer updates inside its ASAR."""

import argparse
import plistlib
import re
import shutil
import sys
import tempfile
from pathlib import Path

from lib.asar_integrity import (
    AsarIntegrityError,
    replace_packed_file_preserving_header,
    write_info_plist_hash,
)

MAIN_PATH = "dist-electron/main.js"

_MANIFEST_LOOKUP = (
    b"return process.env.GROK_RENDERER_UPDATE_URL || "
    b"`${this.getRendererBaseUrl()}/manifest.json`;"
)
_MANIFEST_DISABLED = (
    b"return null; /* Nix owns the embedded renderer; mutable OTA is disabled. */"
)
_CACHE_LOOKUP = b"let e = this.rendererUpdateState.cachedVersion;"
_CACHE_DISABLED = b"let e = null; /* Ignore renderer cache. */"
_APP_UPDATER_ENABLE = b"this.autoUpdaterReady = !0"
_APP_UPDATER_DISABLED = b"this.autoUpdaterReady = !1"
_JS_IDENTIFIER = rb"[A-Za-z_$][A-Za-z0-9_$]*"
_JS_ZERO_ARGUMENT_CALL = _JS_IDENTIFIER + rb"(?:\." + _JS_IDENTIFIER + rb")?\(\)"
_JS_DOUBLE_QUOTED_STRING = rb'"(?:\\.|[^"\\])*"'
_QUIT_POLICY_PATTERN = re.compile(
    rb"let\s+(?P<allow>"
    + _JS_IDENTIFIER
    + rb")\s*=\s*!1,\s*(?P<pending>"
    + _JS_IDENTIFIER
    + rb")\s*=\s*!1;\s*(?P<updater>"
    + _JS_IDENTIFIER
    + rb")\.onQuitForUpdate\(\(\)\s*=>\s*\{\s*(?P=allow)\s*=\s*!0;\s*\}\),"
    + rb"\s*(?P<ipc>"
    + _JS_IDENTIFIER
    + rb')\.on\("app:quit-confirm",\s*\(\)\s*=>\s*\{\s*(?P=allow)\s*\|\|\s*'
    + rb"\((?P=pending)\s*=\s*!1,\s*(?P=allow)\s*=\s*!0,\s*(?P<app>"
    + _JS_IDENTIFIER
    + rb")\.quit\(\)\);\s*\}\),\s*(?P=ipc)\.on\("
    + rb'"app:quit-cancel",\s*\(\)\s*=>\s*\{\s*(?P=pending)\s*=\s*!1;\s*\}\),'
    + rb'\s*(?P=app)\.on\("before-quit",\s*\((?P<event>'
    + _JS_IDENTIFIER
    + rb")\)\s*=>\s*\{\s*if\s*\(!(?P=allow)\)\s*\{"
    + rb"\s*if\s*\((?P<auth_store>"
    + _JS_IDENTIFIER
    + rb")\.getState\(\)\.status\s*!==\s*(?P<auth_status>"
    + _JS_IDENTIFIER
    + rb")\.Authenticated\)\s*\{\s*(?P=allow)\s*=\s*!0;\s*return;\s*\}"
    + rb"\s*if\s*\(!(?P=app)\.isPackaged\s*\|\|\s*process\.env\."
    + rb"(?P<dev_server_env>"
    + _JS_IDENTIFIER
    + rb")\?\.trim\(\)\)\s*\{\s*(?P=allow)\s*=\s*!0;\s*return;\s*\}"
    + rb"\s*if\s*\(!(?P<windows>"
    + _JS_IDENTIFIER
    + rb")\.getAllWindows\(\)\.some\(\((?P<window>"
    + _JS_IDENTIFIER
    + rb")\)\s*=>\s*!(?P=window)\.isDestroyed\(\)\)\)\s*\{"
    + rb"\s*(?P=allow)\s*=\s*!0;\s*return;\s*\}\s*"
    + rb"(?P<statement>(?P=event)\.preventDefault\(\),\s*!(?P=pending)\s*&&\s*"
    + rb"\((?P=pending)\s*=\s*!0,\s*(?P<router>"
    + _JS_IDENTIFIER
    + rb')\.sendToRenderer\("app:quit-request"\)\);)\s*\}\s*\}\);'
    + rb"\s*let\s+(?P<cleanup>"
    + _JS_IDENTIFIER
    + rb")\s*=\s*(?P<coordinator>"
    + _JS_IDENTIFIER
    + rb")\(\{\s*cleanup\s*:\s*\(\)\s*=>\s*\{\s*"
    + rb"(?P=updater)\.installPendingUpdateOnQuit\(\),\s*"
    + rb"(?:"
    + _JS_ZERO_ARGUMENT_CALL
    + rb",\s*)*(?P=updater)\.dispose\(\),\s*(?:"
    + _JS_ZERO_ARGUMENT_CALL
    + rb",\s*)*(?P<worker>"
    + _JS_IDENTIFIER
    + rb")\.kill\(\);\s*\}\s*,\s*scheduleExit\s*:\s*\(\)\s*=>\s*\{\s*"
    + rb"let\s+(?P<exit_delay>"
    + _JS_IDENTIFIER
    + rb")\s*=\s*new\s+Promise\(\((?P<timer_event>"
    + _JS_IDENTIFIER
    + rb")\)\s*=>\s*setTimeout\((?P=timer_event),\s*[0-9]+(?:e[0-9]+)?\)\);\s*"
    + rb"Promise\.race\(\[\s*(?P<shutdown>"
    + _JS_IDENTIFIER
    + rb")\.shutdown\(\),\s*(?P=exit_delay)\s*\]\)\.finally\(\(\)\s*=>\s*"
    + rb"(?P=app)\.exit\(\)\);\s*\}\s*,\s*onError\s*:\s*\((?P<error>"
    + _JS_IDENTIFIER
    + rb")\)\s*=>\s*console\.error\(\s*"
    + _JS_DOUBLE_QUOTED_STRING
    + rb"\s*,\s*(?P=error)\s*\)\s*\}\);\s*"
    + rb"(?P=app)\.on\("
    + rb'"will-quit",\s*\((?P<will_event>'
    + _JS_IDENTIFIER
    + rb")\)\s*=>\s*\{\s*(?P=will_event)\.preventDefault\(\),\s*"
    + rb"(?P=cleanup)\(\);\s*\}\)",
    re.DOTALL,
)
_QUIT_REQUEST_CHANNEL = b"app:quit-request"
_QUIT_DIRECT_SUFFIX = b" = !0; /* Nix: allow will-quit cleanup. */"
_SENTRY_INTEGRATION_FILTER = (
    b'...e.filter((e) => e.name !== "Http" && e.name !== "NodeFetch" '
    b'&& e.name !== "Undici"),'
)
_SENTRY_WITHOUT_MINIDUMPS = (
    b'...e.filter((e) => !["Http","NodeFetch","Undici","SentryMinidump"]'
    b".includes(e.name)),"
)


class PatchError(RuntimeError):
    """The vendor archive does not match the expected Grok Build contract."""


def _replace_padded_once(payload: bytes, old: bytes, new: bytes, *, name: str) -> bytes:
    if len(new) > len(old):
        msg = f"{name} replacement is longer than its stable ASAR anchor"
        raise PatchError(msg)
    count = payload.count(old)
    if count != 1:
        msg = f"expected one {name} anchor in Grok main.js, found {count}"
        raise PatchError(msg)
    offset = payload.find(old)
    replacement = new + b" " * (len(old) - len(new))
    return payload[:offset] + replacement + payload[offset + len(old) :]


def _replace_quit_confirmation(payload: bytes) -> bytes:
    """Disable renderer confirmation while preserving the minified quit guard."""
    matches = list(_QUIT_POLICY_PATTERN.finditer(payload))
    if len(matches) != 1:
        msg = (
            "expected one structural quit confirmation policy in Grok main.js, "
            f"found {len(matches)}"
        )
        raise PatchError(msg)
    request_count = payload.count(_QUIT_REQUEST_CHANNEL)
    if request_count != 1:
        msg = (
            "expected one app:quit-request channel in Grok main.js, "
            f"found {request_count}"
        )
        raise PatchError(msg)
    match = matches[0]
    start, end = match.span("statement")
    original = payload[start:end]
    replacement = match.group("allow") + _QUIT_DIRECT_SUFFIX
    if len(replacement) > len(original):
        msg = "quit confirmation replacement is longer than its stable ASAR anchor"
        raise PatchError(msg)
    padded = replacement + b" " * (len(original) - len(replacement))
    return payload[:start] + padded + payload[end:]


def _patch_main_payload(original: bytes) -> bytes:
    patched = _replace_padded_once(
        original,
        _APP_UPDATER_ENABLE,
        _APP_UPDATER_DISABLED,
        name="app updater",
    )
    patched = _replace_padded_once(
        patched,
        _MANIFEST_LOOKUP,
        _MANIFEST_DISABLED,
        name="renderer manifest",
    )
    patched = _replace_padded_once(
        patched,
        _CACHE_LOOKUP,
        _CACHE_DISABLED,
        name="renderer cache",
    )
    patched = _replace_quit_confirmation(patched)
    return _replace_padded_once(
        patched,
        _SENTRY_INTEGRATION_FILTER,
        _SENTRY_WITHOUT_MINIDUMPS,
        name="Sentry minidump integration",
    )


def _patch_asar(asar_path: Path) -> str:
    try:
        return replace_packed_file_preserving_header(
            asar_path,
            MAIN_PATH,
            _patch_main_payload,
        )
    except AsarIntegrityError as exc:
        msg = f"Grok {exc}"
        raise PatchError(msg) from exc


def patch_bundle(asar_path: Path, info_plist_path: Path) -> str:
    """Stage both integrity rewrites before publishing either bundle file."""
    with (
        tempfile.TemporaryDirectory(
            dir=asar_path.parent,
            prefix=f".{asar_path.name}.",
        ) as asar_staging_dir,
        tempfile.TemporaryDirectory(
            dir=info_plist_path.parent,
            prefix=f".{info_plist_path.name}.",
        ) as plist_staging_dir,
    ):
        staged_asar = Path(asar_staging_dir) / asar_path.name
        original_asar = Path(asar_staging_dir) / f"{asar_path.name}.original"
        staged_plist = Path(plist_staging_dir) / info_plist_path.name
        shutil.copy2(asar_path, staged_asar)
        shutil.copy2(asar_path, original_asar)
        shutil.copy2(info_plist_path, staged_plist)

        _patch_asar(staged_asar)
        try:
            digest = write_info_plist_hash(staged_plist, staged_asar)
        except AsarIntegrityError as exc:
            msg = f"Grok {exc}"
            raise PatchError(msg) from exc

        staged_asar.replace(asar_path)
        try:
            staged_plist.replace(info_plist_path)
        except OSError:
            original_asar.replace(asar_path)
            raise
        return digest


def main(argv: list[str] | None = None) -> int:
    """Run the package-local Grok renderer ownership patch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asar_path", type=Path)
    parser.add_argument("info_plist_path", type=Path)
    args = parser.parse_args(argv)
    try:
        digest = patch_bundle(args.asar_path, args.info_plist_path)
    except (OSError, PatchError, plistlib.InvalidFileException) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write(
        f"disabled Grok app and renderer updates; ASAR header SHA256 {digest}\n"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover -- packaged CLI guard
    raise SystemExit(main())
