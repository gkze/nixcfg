"""Behavioral tests for source-derived Bun toolchain identities."""

import pytest

from lib.nix.models.sources import HashEntry
from lib.update.bun_toolchain import (
    BUN_RELEASE_ASSETS,
    bun_release_url,
    bun_release_urls,
    bun_runtime_hash_entries,
    require_bun_package_manager,
)


@pytest.mark.parametrize(
    ("system", "asset"),
    sorted(BUN_RELEASE_ASSETS.items()),
)
def test_bun_release_url_maps_supported_nix_systems(
    system: str,
    asset: str,
) -> None:
    assert bun_release_url("1.2.3", system) == (
        f"https://github.com/oven-sh/bun/releases/download/bun-v1.2.3/{asset}"
    )


def test_bun_release_urls_preserves_requested_systems() -> None:
    assert bun_release_urls("1.2.3", ("x86_64-linux", "aarch64-darwin")) == {
        "x86_64-linux": (
            "https://github.com/oven-sh/bun/releases/download/"
            "bun-v1.2.3/bun-linux-x64-baseline.zip"
        ),
        "aarch64-darwin": (
            "https://github.com/oven-sh/bun/releases/download/"
            "bun-v1.2.3/bun-darwin-aarch64.zip"
        ),
    }


def test_package_manager_owns_exact_bun_version() -> None:
    assert (
        require_bun_package_manager(
            {"packageManager": "bun@1.3.14"},
            context="Example root manifest",
        )
        == "1.3.14"
    )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "Expected string field 'packageManager'"),
        ({"packageManager": "npm@11.0.0"}, "exact bun@<version>"),
        ({"packageManager": "bun@latest"}, "exact semantic version"),
    ],
)
def test_package_manager_rejects_missing_or_floating_bun(
    payload: object,
    message: str,
) -> None:
    with pytest.raises((TypeError, RuntimeError), match=message):
        require_bun_package_manager(payload, context="Example root manifest")


@pytest.mark.parametrize("version", ["latest", "1.2", "v1.2.3"])
def test_bun_release_url_rejects_non_exact_versions(version: str) -> None:
    with pytest.raises(RuntimeError, match="exact semantic version"):
        bun_release_url(version, "aarch64-darwin")


def test_bun_release_url_rejects_unknown_system() -> None:
    with pytest.raises(RuntimeError, match="no configured release asset"):
        bun_release_url("1.2.3", "riscv64-linux")


def test_runtime_hash_entries_bind_each_platform_to_its_official_url() -> None:
    urls = bun_release_urls("1.2.3", ("aarch64-darwin", "x86_64-linux"))
    hashes = {
        urls["aarch64-darwin"]: "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        urls["x86_64-linux"]: "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
    }

    assert bun_runtime_hash_entries("1.2.3", urls, hashes) == [
        HashEntry.create(
            "bunRuntimeHash",
            hashes[urls["aarch64-darwin"]],
            platform="aarch64-darwin",
            url=urls["aarch64-darwin"],
        ),
        HashEntry.create(
            "bunRuntimeHash",
            hashes[urls["x86_64-linux"]],
            platform="x86_64-linux",
            url=urls["x86_64-linux"],
        ),
    ]


def test_runtime_hash_entries_require_every_requested_platform() -> None:
    with pytest.raises(RuntimeError, match="Missing Bun 1.2.3 runtime hash"):
        bun_runtime_hash_entries("1.2.3", ("aarch64-darwin",), {})
