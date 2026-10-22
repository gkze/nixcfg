"""Stable Go discovery is ordered, exact, and checksum-bound."""

from copy import deepcopy

import pytest

from lib.tests._updater_helpers import load_repo_module, run_async
from lib.update.updaters import UpdateContext


def _release(version: str, *, stable: bool = True) -> dict:
    return {
        "version": f"go{version}",
        "stable": stable,
        "files": [
            {
                "filename": f"go{version}.src.tar.gz",
                "version": f"go{version}",
                "kind": "source",
                "os": "",
                "arch": "",
                "sha256": "a" * 64,
                "size": 123,
            }
        ],
    }


def test_selects_greatest_stable_release_independently_of_feed_order() -> None:
    module = load_repo_module("packages/go-toolchain/updater.py", "go_toolchain_test")
    releases = [
        _release("1.26.10"),
        _release("1.28.0", stable=False),
        _release("1.27.3"),
    ]
    for payload in [releases, list(reversed(releases))]:
        info = module.stable_source_release(payload)
        assert info.version == "1.27.3"
        assert info.metadata["url"] == "https://go.dev/dl/go1.27.3.src.tar.gz"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sha256", "bad"),
        ("version", "go1.27.2"),
        ("kind", "archive"),
        ("os", "darwin"),
        ("size", 0),
    ],
)
def test_rejects_inconsistent_source_metadata(field: str, value: object) -> None:
    module = load_repo_module(
        "packages/go-toolchain/updater.py", "go_toolchain_invalid_test"
    )
    release = deepcopy(_release("1.27.3"))
    release["files"][0][field] = value
    with pytest.raises(ValueError, match="source archive metadata"):
        module.stable_source_release([release])


def test_rejects_duplicate_latest_release() -> None:
    module = load_repo_module(
        "packages/go-toolchain/updater.py", "go_toolchain_duplicate_test"
    )
    with pytest.raises(ValueError, match="duplicate"):
        module.stable_source_release([_release("1.27.3"), _release("1.27.3")])


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        [None],
        [{"stable": True, "version": None}],
        [{"stable": True, "version": "go1.28rc1"}],
    ],
)
def test_rejects_missing_or_invalid_stable_releases(payload: object) -> None:
    module = load_repo_module("packages/go-toolchain/updater.py", "go_invalid_feed")
    with pytest.raises((TypeError, ValueError)):
        module.stable_source_release(payload)


@pytest.mark.parametrize("files", [None, [], [{"filename": "other"}]])
def test_rejects_missing_source(files: object) -> None:
    module = load_repo_module("packages/go-toolchain/updater.py", "go_missing_source")
    release = _release("1.27.3")
    release["files"] = files
    with pytest.raises((TypeError, ValueError)):
        module.stable_source_release([release])


def test_official_release_api(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_repo_module("packages/go-toolchain/updater.py", "go_api")
    urls = []

    async def payload(_session: object, url: str, **_kwargs):
        urls.append(url)
        return [_release("1.27.3")]

    monkeypatch.setattr(module, "fetch_json", payload)
    info = run_async(
        module.GoToolchainUpdater().fetch_latest(
            object(), context=UpdateContext(current=None)
        )
    )
    assert info.version == "1.27.3"
    assert urls == ["https://go.dev/dl/?mode=json"]
