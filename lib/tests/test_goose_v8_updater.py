"""Tests for the goose-v8 updater overlay surface."""

from __future__ import annotations

import tomllib

import pytest

from lib.nix.models.sources import HashEntry
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import collect_events as _collect_events
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import UpdateEventKind
from lib.update.nix import _build_fetchgit_call
from lib.update.updaters.base import VersionInfo


def _load_module(module_name: str = "goose_v8_updater_test"):
    return load_repo_module("overlays/goose-v8/updater.py", module_name)


def test_goose_v8_helper_urls_and_src_expr_shape() -> None:
    """Build the recursive fetchgit expression and release asset URLs."""
    module = _load_module("goose_v8_updater_test_helpers")

    src_expr = module.GooseV8Updater._src_expr("abcdef123456")
    archive_url = module.GooseV8Updater._archive_url("v999.0.0", "x86_64-linux")
    binding_url = module.GooseV8Updater._binding_url("999.0.0", "x86_64-linux")

    assert_nix_ast_equal(
        src_expr,
        _build_fetchgit_call(
            "https://github.com/jh-block/rusty_v8.git",
            "abcdef123456",
            fetch_submodules=True,
        ),
    )
    assert archive_url == (
        "https://github.com/denoland/rusty_v8/releases/download/"
        "v999.0.0/librusty_v8_release_x86_64-unknown-linux-gnu.a.gz"
    )
    assert binding_url == (
        "https://github.com/denoland/rusty_v8/releases/download/"
        "v999.0.0/src_binding_release_x86_64-unknown-linux-gnu.rs"
    )


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (None, False),
        (
            type("Current", (), {"version": "deadbeef", "hashes": None})(),
            False,
        ),
        (
            type(
                "Current",
                (),
                {
                    "version": "cafebabe",
                    "hashes": type("Hashes", (), {"entries": None})(),
                },
            )(),
            False,
        ),
        (
            type(
                "Current",
                (),
                {
                    "version": "cafebabe",
                    "hashes": type(
                        "Hashes",
                        (),
                        {
                            "entries": [
                                HashEntry.create(
                                    "srcHash",
                                    "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                                ),
                                HashEntry.create(
                                    "rustyV8ArchiveHash",
                                    "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                                    platform="x86_64-linux",
                                ),
                            ]
                        },
                    )(),
                },
            )(),
            False,
        ),
        (
            type(
                "Current",
                (),
                {
                    "version": "cafebabe",
                    "hashes": type(
                        "Hashes",
                        (),
                        {
                            "entries": [
                                HashEntry.create(
                                    "srcHash",
                                    "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                                ),
                                HashEntry.create(
                                    "rustyV8ArchiveHash",
                                    "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                                    platform="x86_64-linux",
                                ),
                                HashEntry.create(
                                    "rustyV8BindingHash",
                                    "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
                                    platform="x86_64-linux",
                                ),
                            ]
                        },
                    )(),
                },
            )(),
            True,
        ),
    ],
)
def test_goose_v8_is_latest_requires_matching_version_and_hash_set(
    current, expected: bool
) -> None:
    """Treat the pin as current only when version and required hashes all exist."""
    module = _load_module(f"goose_v8_updater_test_is_latest_{expected}")
    updater = module.GooseV8Updater()

    assert (
        _run(updater._is_latest(current, VersionInfo(version="cafebabe"))) is expected
    )


def test_goose_v8_fetch_hashes_success_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compute the recursive source hash and both release asset hashes."""
    module = _load_module("goose_v8_updater_test_hashes")
    updater = module.GooseV8Updater()
    info = VersionInfo(version="cafebabe")
    fixed_hash_calls: list[tuple[str, str, object]] = []
    fetched_urls: list[tuple[object, str, object, object]] = []
    url_hash_batches: list[list[str]] = []

    async def _fixed_hash(name: str, expr: str, *, config=None):
        fixed_hash_calls.append((name, expr, config))
        yield module.UpdateEvent.status(name, "building src")
        yield module.UpdateEvent.value(
            name, "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        )

    async def _fetch_url(session, url: str, *, request_timeout=None, config=None):
        fetched_urls.append((session, url, request_timeout, config))
        return b'[package]\nversion = "999.0.0"\n'

    async def _url_hashes(name: str, urls):
        batch = list(urls)
        url_hash_batches.append(batch)
        yield module.UpdateEvent.status(name, "hashing release assets")
        yield module.UpdateEvent.value(
            name,
            {
                batch[0]: "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                batch[1]: "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
            },
        )

    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)
    monkeypatch.setattr(module, "fetch_url", _fetch_url)
    monkeypatch.setattr(module, "compute_url_hashes", _url_hashes)

    session = object()
    events = _run(_collect_events(updater.fetch_hashes(info, session)))

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.STATUS,
        UpdateEventKind.VALUE,
    ]
    assert [event.message for event in events[:-1]] == [
        "building src",
        "hashing release assets",
    ]
    assert fixed_hash_calls == [
        ("goose-v8", updater._src_expr("cafebabe"), updater.config),
    ]
    assert fetched_urls == [
        (
            session,
            "https://raw.githubusercontent.com/jh-block/rusty_v8/cafebabe/Cargo.toml",
            updater.config.default_timeout,
            updater.config,
        )
    ]
    assert url_hash_batches == [
        [
            "https://github.com/denoland/rusty_v8/releases/download/v999.0.0/librusty_v8_release_x86_64-unknown-linux-gnu.a.gz",
            "https://github.com/denoland/rusty_v8/releases/download/v999.0.0/src_binding_release_x86_64-unknown-linux-gnu.rs",
        ]
    ]
    assert events[-1].payload == [
        HashEntry.create(
            "srcHash",
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        ),
        HashEntry.create(
            "rustyV8ArchiveHash",
            "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
            platform="x86_64-linux",
            url="https://github.com/denoland/rusty_v8/releases/download/v999.0.0/librusty_v8_release_x86_64-unknown-linux-gnu.a.gz",
        ),
        HashEntry.create(
            "rustyV8BindingHash",
            "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
            platform="x86_64-linux",
            url="https://github.com/denoland/rusty_v8/releases/download/v999.0.0/src_binding_release_x86_64-unknown-linux-gnu.rs",
        ),
    ]


def test_goose_v8_fetch_hashes_rejects_non_string_src_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail fast when the captured source hash has the wrong type."""
    module = _load_module("goose_v8_updater_test_bad_src_type")
    updater = module.GooseV8Updater()

    async def _bad_src_hash(_name: str, _expr: str, *, config=None):
        _ = config
        yield module.UpdateEvent.value("goose-v8", {"hash": "sha256-src"})

    monkeypatch.setattr(module, "compute_fixed_output_hash", _bad_src_hash)

    with pytest.raises(TypeError, match="Expected src hash string, got dict"):
        _run(
            _collect_events(
                updater.fetch_hashes(VersionInfo(version="cafebabe"), object())
            )
        )


@pytest.mark.parametrize(
    ("cargo_toml", "expected_exception", "match"),
    [
        (b"not valid toml", tomllib.TOMLDecodeError, "Expected '=' after a key"),
        (b'[package]\nname = "rusty_v8"\n', KeyError, "version"),
    ],
)
def test_goose_v8_fetch_hashes_rejects_bad_cargo_toml_or_missing_version(
    monkeypatch: pytest.MonkeyPatch,
    cargo_toml: bytes,
    expected_exception: type[Exception],
    match: str,
) -> None:
    """Surface TOML parse and version extraction failures from Cargo.toml."""
    module = _load_module(f"goose_v8_updater_test_cargo_{expected_exception.__name__}")
    updater = module.GooseV8Updater()

    async def _fixed_hash(_name: str, _expr: str, *, config=None):
        _ = config
        yield module.UpdateEvent.value(
            "goose-v8",
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        )

    async def _fetch_url(_session, _url: str, *, request_timeout=None, config=None):
        _ = (request_timeout, config)
        return cargo_toml

    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)
    monkeypatch.setattr(module, "fetch_url", _fetch_url)

    with pytest.raises(expected_exception, match=match):
        _run(
            _collect_events(
                updater.fetch_hashes(VersionInfo(version="cafebabe"), object())
            )
        )


def test_goose_v8_fetch_hashes_rejects_missing_asset_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise when the hashed asset mapping omits a required release URL."""
    module = _load_module("goose_v8_updater_test_missing_asset_hash")
    updater = module.GooseV8Updater()

    async def _fixed_hash(_name: str, _expr: str, *, config=None):
        _ = config
        yield module.UpdateEvent.value(
            "goose-v8",
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        )

    async def _fetch_url(_session, _url: str, *, request_timeout=None, config=None):
        _ = (request_timeout, config)
        return b'[package]\nversion = "999.0.0"\n'

    async def _url_hashes(name: str, urls):
        batch = list(urls)
        yield module.UpdateEvent.value(
            name,
            {
                batch[0]: "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
            },
        )

    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)
    monkeypatch.setattr(module, "fetch_url", _fetch_url)
    monkeypatch.setattr(module, "compute_url_hashes", _url_hashes)

    with pytest.raises(
        KeyError, match="src_binding_release_x86_64-unknown-linux-gnu\\.rs"
    ):
        _run(
            _collect_events(
                updater.fetch_hashes(VersionInfo(version="cafebabe"), object())
            )
        )


def test_goose_v8_fetch_hashes_requires_asset_hash_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise when the asset hash stream emits no final VALUE payload."""
    module = _load_module("goose_v8_updater_test_missing_asset_capture")
    updater = module.GooseV8Updater()

    async def _fixed_hash(_name: str, _expr: str, *, config=None):
        _ = config
        yield module.UpdateEvent.value(
            "goose-v8",
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        )

    async def _fetch_url(_session, _url: str, *, request_timeout=None, config=None):
        _ = (request_timeout, config)
        return b'[package]\nversion = "999.0.0"\n'

    async def _missing_assets(name: str, _urls):
        yield module.UpdateEvent.status(name, "hashing release assets")

    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)
    monkeypatch.setattr(module, "fetch_url", _fetch_url)
    monkeypatch.setattr(module, "compute_url_hashes", _missing_assets)

    with pytest.raises(RuntimeError, match="Missing prebuilt rusty_v8 hash output"):
        _run(
            _collect_events(
                updater.fetch_hashes(VersionInfo(version="cafebabe"), object())
            )
        )


def test_goose_v8_fetch_hashes_handles_missing_wrapped_asset_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover the fallback path when the wrapped asset capture yields nothing."""
    module = _load_module("goose_v8_updater_test_missing_wrapped_asset_capture")
    updater = module.GooseV8Updater()

    async def _fixed_hash(_name: str, _expr: str, *, config=None):
        _ = config
        yield module.UpdateEvent.value(
            "goose-v8",
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        )

    async def _fetch_url(_session, _url: str, *, request_timeout=None, config=None):
        _ = (request_timeout, config)
        return b'[package]\nversion = "999.0.0"\n'

    async def _url_hashes(name: str, _urls):
        yield module.UpdateEvent.status(name, "hashing release assets")

    async def _capture_selectively(events, *, error: str):
        async for _event in events:
            pass
        if error == "Missing srcHash output":
            yield module.CapturedValue(
                "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            )

    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)
    monkeypatch.setattr(module, "fetch_url", _fetch_url)
    monkeypatch.setattr(module, "compute_url_hashes", _url_hashes)
    monkeypatch.setattr(module, "capture_stream_value", _capture_selectively)

    assert (
        _run(
            _collect_events(
                updater.fetch_hashes(VersionInfo(version="cafebabe"), object())
            )
        )
        == []
    )


def test_goose_v8_fetch_hashes_requires_src_hash_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise when the source hash stream emits no final VALUE payload."""
    module = _load_module("goose_v8_updater_test_missing_src_capture")
    updater = module.GooseV8Updater()

    async def _fixed_hash(_name: str, _expr: str, *, config=None):
        _ = config
        yield module.UpdateEvent.status("goose-v8", "building src")

    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)

    with pytest.raises(RuntimeError, match="Missing srcHash output"):
        _run(
            _collect_events(
                updater.fetch_hashes(VersionInfo(version="cafebabe"), object())
            )
        )
