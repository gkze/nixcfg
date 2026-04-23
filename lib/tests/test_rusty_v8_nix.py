"""Guardrails for the shared ``rusty_v8`` Nix builder."""

from __future__ import annotations

from lib.update.paths import REPO_ROOT


def _rusty_v8_nix() -> str:
    return (REPO_ROOT / "lib/rusty-v8.nix").read_text(encoding="utf-8")


def test_shared_rusty_v8_builder_uses_native_source_builds_on_linux() -> None:
    """Keep Linux on the same source-build path as Darwin for supported hosts."""
    text = _rusty_v8_nix()

    assert (
        "if pkgs.stdenv.hostPlatform.isDarwin || pkgs.stdenv.hostPlatform.isLinux then"
        in text
    )
    assert 'RUSTY_V8_ARCHIVE = "${nativeDrv}/lib/librusty_v8.a";' in text
    assert 'RUSTY_V8_PREBUILT_GN_OUT = "${nativeDrv}/share/gn_out";' in text
    assert 'throw "rusty-v8: Linux builds require prebuiltArtifacts"' not in text
