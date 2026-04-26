"""Focused tests for the crush updater."""

from __future__ import annotations

import asyncio
from types import ModuleType, SimpleNamespace

import pytest

from lib.nix.models.sources import HashEntry
from lib.tests._updater_helpers import collect_events as _collect_events
from lib.tests._updater_helpers import install_fixed_hash_stream, load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.updaters.base import VersionInfo, source_override_env


def _load_module() -> ModuleType:
    return load_repo_module(
        "overlays/crush/updater.py",
        "crush_updater_dedicated_test",
    )


def test_parse_version_triplet_handles_optional_patch() -> None:
    """Parse both major.minor and major.minor.patch tuples."""
    module = _load_module()

    assert module._parse_version_triplet(" 1.26 ") == (1, 26, 0)
    assert module._parse_version_triplet("1.26.3") == (1, 26, 3)


@pytest.mark.parametrize(
    ("version", "message"),
    [
        ("1", "Invalid version tuple"),
        ("1.two.3", "Invalid numeric version tuple"),
    ],
)
def test_parse_version_triplet_rejects_invalid_inputs(
    version: str,
    message: str,
) -> None:
    """Reject malformed and non-numeric version tuples."""
    module = _load_module()

    with pytest.raises(RuntimeError, match=message):
        module._parse_version_triplet(version)


def test_extract_required_go_version_reads_go_directive() -> None:
    """Pick the first go directive from a go.mod payload."""
    module = _load_module()

    go_mod = "module github.com/charmbracelet/crush\n\nrequire x/y v1.2.3\n go 1.27\n"

    assert module._extract_required_go_version(go_mod) == (1, 27, 0)


def test_extract_required_go_version_requires_go_directive() -> None:
    """Fail when go.mod does not declare a toolchain floor."""
    module = _load_module()

    with pytest.raises(
        RuntimeError,
        match="Could not find Go toolchain requirement in crush go.mod",
    ):
        module._extract_required_go_version("module github.com/charmbracelet/crush\n")


def test_extract_required_go_version_skips_blank_go_directive() -> None:
    """Ignore empty go directives and keep scanning for a usable version."""
    module = _load_module()

    go_mod = "module github.com/charmbracelet/crush\n\ngo   \nrequire x/y v1.2.3\ngo 1.25.2\n"

    assert module._extract_required_go_version(go_mod) == (1, 25, 2)


def test_resolve_supported_go_version_falls_back_to_go(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use pkgs.go when pkgs.go_latest is unavailable."""
    module = _load_module()
    updater = module.CrushUpdater()

    calls: list[list[str]] = []

    async def _run_nix(argv: list[str], *, check: bool = True):
        _ = check
        calls.append(argv)
        expr = argv[-1]
        if ".go_latest.version" in expr:
            return SimpleNamespace(returncode=1, stdout="", stderr="missing")
        return SimpleNamespace(returncode=0, stdout="1.26.4\n", stderr="")

    monkeypatch.setattr(module, "run_nix", _run_nix)
    monkeypatch.setattr(module, "get_current_nix_platform", lambda: "aarch64-darwin")

    assert _run(updater._resolve_supported_go_version()) == (1, 26, 4)
    assert len(calls) == 2
    assert ".go_latest.version" in calls[0][-1]
    assert ".go.version" in calls[1][-1]


def test_resolve_supported_go_version_reports_both_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface both nix evaluation failures when no Go attr resolves."""
    module = _load_module()
    updater = module.CrushUpdater()

    async def _run_nix(argv: list[str], *, check: bool = True):
        _ = (argv, check)
        return SimpleNamespace(returncode=1, stdout="", stderr="eval failed")

    monkeypatch.setattr(module, "run_nix", _run_nix)
    monkeypatch.setattr(module, "get_current_nix_platform", lambda: "aarch64-darwin")

    with pytest.raises(
        RuntimeError, match="Failed to evaluate Go toolchain version"
    ) as exc:
        _run(updater._resolve_supported_go_version())

    assert "go_latest: eval failed" in str(exc.value)
    assert "go: eval failed" in str(exc.value)


def test_current_version_requires_package_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail cleanly when the crush package directory cannot be found."""
    module = _load_module()
    updater = module.CrushUpdater()

    def _missing_package(_name: str) -> str:
        msg = "Package directory not found for crush"
        raise RuntimeError(msg)

    monkeypatch.setattr(module, "read_pinned_source_version", _missing_package)

    with pytest.raises(RuntimeError, match="Package directory not found for crush"):
        updater._current_version()


def test_current_version_requires_pinned_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject crush sources entries without a usable version string."""
    module = _load_module()
    updater = module.CrushUpdater()

    def _missing_version(_name: str) -> str:
        msg = "crush sources.json is missing a pinned version"
        raise RuntimeError(msg)

    monkeypatch.setattr(module, "read_pinned_source_version", _missing_version)

    with pytest.raises(
        RuntimeError, match="crush sources.json is missing a pinned version"
    ):
        updater._current_version()


def test_current_version_reads_sources_json_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return the pinned version string from the crush source entry."""
    module = _load_module()
    updater = module.CrushUpdater()
    pinned_version = "fixture-version"

    monkeypatch.setattr(
        module,
        "read_pinned_source_version",
        lambda _name: pinned_version,
    )

    assert updater._current_version() == pinned_version


def test_fetch_latest_rejects_non_mapping_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Release pages must return mapping payloads."""
    module = _load_module()
    updater = module.CrushUpdater()

    monkeypatch.setattr(
        updater,
        "_resolve_supported_go_version",
        lambda: asyncio.sleep(0, result=(1, 26, 0)),
    )
    monkeypatch.setattr(
        module,
        "fetch_github_api_paginated",
        lambda *_a, **_k: asyncio.sleep(0, result=["not-a-dict"]),
    )

    with pytest.raises(TypeError, match="Unexpected release payload type: str"):
        _run(updater.fetch_latest(object()))


def test_fetch_latest_requires_release_tag_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject release payloads that omit tag_name."""
    module = _load_module()
    updater = module.CrushUpdater()

    monkeypatch.setattr(
        updater,
        "_resolve_supported_go_version",
        lambda: asyncio.sleep(0, result=(1, 26, 0)),
    )
    monkeypatch.setattr(
        module,
        "fetch_github_api_paginated",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result=[{"draft": False, "prerelease": False, "tag_name": ""}],
        ),
    )

    with pytest.raises(RuntimeError, match="Missing tag_name in release payload"):
        _run(updater.fetch_latest(object()))


def test_fetch_latest_skips_drafts_and_prereleases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ignore non-stable releases before checking go.mod compatibility."""
    module = _load_module()
    updater = module.CrushUpdater()
    draft_tag = "v9.9.9"
    prerelease_tag = "v9.9.8"
    stable_tag = "v1.2.3"
    stable_version = stable_tag.removeprefix("v")

    fetched_tags: list[str] = []

    monkeypatch.setattr(
        updater,
        "_resolve_supported_go_version",
        lambda: asyncio.sleep(0, result=(1, 26, 0)),
    )
    monkeypatch.setattr(
        module,
        "fetch_github_api_paginated",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result=[
                {"tag_name": draft_tag, "draft": True, "prerelease": False},
                {"tag_name": prerelease_tag, "draft": False, "prerelease": True},
                {"tag_name": stable_tag, "draft": False, "prerelease": False},
            ],
        ),
    )

    async def _fetch_url(_session, url: str, **_kwargs):
        fetched_tags.append(url)
        return b"module github.com/charmbracelet/crush\n\ngo 1.26\n"

    monkeypatch.setattr(module, "fetch_url", _fetch_url)

    latest = _run(updater.fetch_latest(object()))

    assert latest.version == stable_version
    assert latest.metadata.tag == stable_tag
    assert len(fetched_tags) == 1
    assert stable_tag in fetched_tags[0]


def test_fetch_latest_falls_back_to_current_pin_when_all_stable_releases_need_newer_go(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reuse the pinned version when every stable release exceeds the Go floor."""
    module = _load_module()
    updater = module.CrushUpdater()
    current_version = "fixture-current-version"

    monkeypatch.setattr(
        updater,
        "_resolve_supported_go_version",
        lambda: asyncio.sleep(0, result=(1, 26, 0)),
    )
    monkeypatch.setattr(
        module,
        "fetch_github_api_paginated",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result=[{"tag_name": "v0.57.0", "draft": False, "prerelease": False}],
        ),
    )
    monkeypatch.setattr(
        module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result=b"module github.com/charmbracelet/crush\n\ngo 1.26.1\n",
        ),
    )
    monkeypatch.setattr(updater, "_current_version", lambda: current_version)

    latest = _run(updater.fetch_latest(object()))

    assert latest.version == current_version
    assert latest.metadata.tag == f"v{current_version}"


def test_fetch_hashes_computes_src_and_vendor_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compute both source and vendor hashes from mocked build streams."""
    module = _load_module()
    updater = module.CrushUpdater()
    info = VersionInfo(version="fixture-version")

    calls = install_fixed_hash_stream(
        monkeypatch,
        (("building src", "sha256-src"), ("building vendor", "sha256-vendor")),
    )

    events = _run(_collect_events(updater.fetch_hashes(info, object())))

    assert [event.message for event in events[:-1]] == [
        "building src",
        "building vendor",
    ]
    assert calls == [
        {
            "name": "crush",
            "expr": updater._src_expr(info.version),
            "env": None,
            "config": updater.config,
        },
        {
            "name": "crush",
            "expr": module._build_overlay_expr("crush"),
            "env": source_override_env(
                "crush",
                version=info.version,
                src_hash="sha256-src",
                dependency_hash_type="vendorHash",
                dependency_hash=updater.config.fake_hash,
            ),
            "config": updater.config,
        },
    ]
    assert events[-1].payload == [
        HashEntry.create("srcHash", "sha256-src"),
        HashEntry.create("vendorHash", "sha256-vendor"),
    ]
