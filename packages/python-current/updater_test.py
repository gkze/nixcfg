"""CPython discovery stays final-only, source-exact, and publisher-bound."""

import pytest

from lib.nix.models.sources import HashEntry
from lib.tests._updater_helpers import load_repo_module, run_async
from lib.update.updaters import SingleURLHashEntryUpdater, UpdateContext


def _release(version: str = "3.14.10") -> dict:
    return {
        "name": f"Python {version}",
        "is_published": True,
        "pre_release": False,
        "resource_uri": "https://www.python.org/api/v2/downloads/release/1165/",
    }


def _source(version: str = "3.14.10") -> dict:
    return {
        "url": f"https://www.python.org/ftp/python/{version}/Python-{version}.tar.xz",
        "is_source": True,
        "sha256_sum": "a" * 64,
    }


def test_stable_selection_ignores_order_and_nonfinal_entries() -> None:
    module = load_repo_module("packages/python-current/updater.py", "python_selection")
    entries = [
        _release("3.14.9"),
        _release(),
        _release("3.15.1"),
        None,
        _release("3.16.0rc1"),
    ]
    for payload in [entries, list(reversed(entries))]:
        assert module.stable_release(payload) == ("3.14.10", "1165")
    assert module.source_release("3.14.10", [_source()]).version == "3.14.10"


@pytest.mark.parametrize("payload", [None, [], [None], [{"name": "Python 2.7.18"}]])
def test_rejects_missing_final_release(payload: object) -> None:
    module = load_repo_module("packages/python-current/updater.py", "python_missing")
    with pytest.raises((TypeError, ValueError)):
        module.stable_release(payload)


@pytest.mark.parametrize("resource", [None, "https://other.test/release/1/", "invalid"])
def test_rejects_untrusted_release_resource(resource: object) -> None:
    module = load_repo_module("packages/python-current/updater.py", "python_resource")
    release = _release()
    release["resource_uri"] = resource
    with pytest.raises(ValueError, match="release"):
        module.stable_release([release])
    with pytest.raises(ValueError, match="release"):
        module.stable_release([_release(), _release()])


@pytest.mark.parametrize("payload", [None, [], [None], [_source(), _source()]])
def test_rejects_missing_or_ambiguous_archive(payload: object) -> None:
    module = load_repo_module("packages/python-current/updater.py", "python_archive")
    with pytest.raises((TypeError, ValueError)):
        module.source_release("3.14.10", payload)


@pytest.mark.parametrize(
    ("key", "value"),
    [("is_source", False), ("sha256_sum", None), ("sha256_sum", "bad")],
)
def test_rejects_invalid_source_metadata(key: str, value: object) -> None:
    module = load_repo_module("packages/python-current/updater.py", "python_digest")
    source = _source()
    source[key] = value
    with pytest.raises(ValueError, match="source archive metadata"):
        module.source_release("3.14.10", [source])


@pytest.mark.parametrize("owner", ["python-current", "go-toolchain"])
def test_download_digest_matches_publisher(
    monkeypatch: pytest.MonkeyPatch, owner: str
) -> None:
    module = load_repo_module(f"packages/{owner}/updater.py", f"digest_{owner}")
    updater = (
        module.PythonCurrentUpdater
        if owner == "python-current"
        else module.GoToolchainUpdater
    )()
    if owner == "python-current":
        info = module.source_release("3.14.10", [_source()])
    else:
        info = module.stable_source_release([
            {
                "version": "go1.27.3",
                "stable": True,
                "files": [
                    {
                        "filename": "go1.27.3.src.tar.gz",
                        "version": "go1.27.3",
                        "kind": "source",
                        "os": "",
                        "arch": "",
                        "sha256": "a" * 64,
                        "size": 123,
                    }
                ],
            }
        ])
    result = [
        HashEntry.create(
            "sha256",
            "sha256-qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqo=",
            url=info.metadata["url"],
        )
    ]

    async def hashes(*_args, **_kwargs):
        return result

    monkeypatch.setattr(SingleURLHashEntryUpdater, "fetch_hashes", hashes)
    assert (
        run_async(
            updater.fetch_hashes(info, object(), context=UpdateContext(current=None))
        )
        == result
    )
    result[0] = HashEntry.create(
        "sha256",
        "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        url=info.metadata["url"],
    )
    with pytest.raises(ValueError, match="publisher checksum"):
        run_async(
            updater.fetch_hashes(info, object(), context=UpdateContext(current=None))
        )


def test_live_api_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_repo_module("packages/python-current/updater.py", "python_api")
    urls = []

    async def payload(_session: object, url: str, **_kwargs):
        urls.append(url)
        return [_source()] if "release_file" in url else [_release()]

    monkeypatch.setattr(module, "fetch_json", payload)
    info = run_async(
        module.PythonCurrentUpdater().fetch_latest(
            object(), context=UpdateContext(current=None)
        )
    )
    assert info.version == "3.14.10"
    assert urls == [
        "https://www.python.org/api/v2/downloads/release/?is_published=true",
        "https://www.python.org/api/v2/downloads/release_file/?release=1165",
    ]
