"""Tests for the Rio source updater."""

import pytest

from lib.nix.models.sources import HashEntry, SourceEntry
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import (
    collect_events,
    install_fixed_hash_stream,
    load_repo_module,
    run_async,
)
from lib.update.constants import placeholder_hash_for
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import UpdateEventKind
from lib.update.nix import _build_fetch_from_github_call, _build_overlay_expr

SRC_HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
CARGO_HASH = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
COMMIT = "c" * 40
RUST_TOOLCHAIN_TOML = b"""[toolchain]
channel = "1.99.1"
profile = "minimal"
components = [ "rustfmt", "clippy" ]
"""


def test_rio_update_uses_latest_source_when_release_has_no_rio_dmg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing vendor DMG must not prevent updating the source-built package."""
    module = load_repo_module("packages/rio/updater.py", "rio_updater_test")
    updater = module.RioUpdater()
    assert updater.get_derivation_validations() == (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}",
            mode="build",
            systems=("aarch64-darwin",),
        ),
    )
    current = SourceEntry(
        version="0.4.12",
        hashes={
            "aarch64-darwin": ("sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC="),
        },
        urls={
            "aarch64-darwin": (
                "https://github.com/raphamorim/rio/releases/download/v0.4.12/rio.dmg"
            ),
        },
    )

    async def _fetch_github(_session: object, path: str, **_kwargs: object) -> object:
        if path.endswith("/releases/latest"):
            return {
                "tag_name": "v0.5.0",
                "assets": [
                    {
                        "name": "Canario-0.0.1.dmg",
                        "browser_download_url": (
                            "https://github.com/raphamorim/rio/releases/download/"
                            "v0.5.0/Canario-0.0.1.dmg"
                        ),
                    },
                ],
            }
        return {"sha": COMMIT}

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        _fetch_github,
    )
    toolchain_urls: list[str] = []

    async def _fetch_url(_session: object, url: str, **_kwargs: object) -> bytes:
        toolchain_urls.append(url)
        return RUST_TOOLCHAIN_TOML

    monkeypatch.setattr(module, "fetch_url", _fetch_url)
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform",
        lambda: "aarch64-darwin",
    )
    calls = install_fixed_hash_stream(
        monkeypatch,
        (
            (None, SRC_HASH),
            (None, CARGO_HASH),
        ),
    )

    events = run_async(
        collect_events(lambda emit: updater.update_stream(current, object(), emit=emit))
    )

    assert toolchain_urls == [
        f"https://raw.githubusercontent.com/raphamorim/rio/{COMMIT}/rust-toolchain.toml"
    ]
    assert len(calls) == 2
    assert_nix_ast_equal(
        str(calls[0]["expr"]),
        _build_fetch_from_github_call(
            "raphamorim",
            "rio",
            rev=COMMIT,
            fetch_submodules=False,
        ),
    )
    assert_nix_ast_equal(
        str(calls[1]["expr"]),
        _build_overlay_expr(
            "rio",
            source_overrides={
                "rio": SourceEntry(
                    version="0.5.0",
                    commit=COMMIT,
                    hashes=[
                        HashEntry.create("srcHash", SRC_HASH),
                        HashEntry.create(
                            "cargoHash", placeholder_hash_for("cargoHash")
                        ),
                    ],
                    pins={"rustToolchain": "1.99.1"},
                )
            },
        ),
    )
    assert calls[1]["env"] is None

    result_events = [event for event in events if event.kind is UpdateEventKind.RESULT]
    assert len(result_events) == 1
    result = result_events[0].payload
    assert isinstance(result, SourceEntry)
    assert result == SourceEntry(
        version="0.5.0",
        commit=COMMIT,
        hashes=[
            HashEntry.create("srcHash", SRC_HASH),
            HashEntry.create("cargoHash", CARGO_HASH),
        ],
        pins={"rustToolchain": "1.99.1"},
    )


def test_rio_reads_exact_stable_channel_from_rust_toolchain_toml() -> None:
    """Rio 0.5.29 pins 1.99.1, newer than nixpkgs' 1.99.0 rustc."""
    module = load_repo_module("packages/rio/updater.py", "rio_updater_channel_test")
    assert (
        module.rust_toolchain_channel(RUST_TOOLCHAIN_TOML, context="fixture")
        == "1.99.1"
    )


@pytest.mark.parametrize(
    "manifest",
    [
        b'[toolchain]\nchannel = "stable"\n',
        b'[toolchain]\nchannel = "nightly-2026-10-10"\n',
        b'[toolchain]\nchannel = "1.99"\n',
        b'[toolchain]\nprofile = "minimal"\n',
        b'channel = "1.99.1"\n',
        b"[toolchain\n",
        b"\xff",
    ],
)
def test_rio_rejects_toolchains_without_an_exact_stable_release(
    manifest: bytes,
) -> None:
    """Only an exact stable release maps onto rust-bin.stable.<version>."""
    module = load_repo_module("packages/rio/updater.py", "rio_updater_reject_test")
    with pytest.raises(ValueError, match="fixture"):
        module.rust_toolchain_channel(manifest, context="fixture")
