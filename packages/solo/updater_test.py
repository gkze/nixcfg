"""Solo latest-release discovery must preserve the native artifact identity."""

import pytest

from lib.tests._updater_helpers import load_repo_module, run_async
from lib.update.updaters import UpdateContext


@pytest.mark.parametrize(
    ("version", "url", "valid"),
    [
        (
            "0.10.1",
            "https://releases.soloterm.com/darwin/universal/Solo_0.10.1_universal.app.tar.gz",
            True,
        ),
        (
            "bad",
            "https://releases.soloterm.com/darwin/universal/Solo_bad_universal.app.tar.gz",
            False,
        ),
        ("0.10.1", "https://example.invalid/Solo_0.10.1_universal.app.tar.gz", False),
        (
            "0.10.1",
            "https://releases.soloterm.com/darwin/universal/Solo_0.7.1_universal.app.tar.gz",
            False,
        ),
    ],
)
def test_solo_published_manifest(
    monkeypatch: pytest.MonkeyPatch, version: str, url: str, valid: bool
) -> None:
    module = load_repo_module("packages/solo/updater.py", "solo_latest_test")
    updater = module.SoloUpdater()

    async def fetch(_session: object, endpoint: str, **_kwargs: object):
        assert (
            endpoint
            == "https://soloterm.com/api/v1/updates/manifest?platform=darwin-universal"
        )
        return {"version": version, "url": url}

    monkeypatch.setattr(module, "fetch_json", fetch)
    operation = updater.fetch_latest(object(), context=UpdateContext(current=None))
    if valid:
        result = run_async(operation)
        assert result.version == "0.10.1"
        assert updater.get_download_url("aarch64-darwin", result) == url
    else:
        with pytest.raises(ValueError, match="Solo release manifest"):
            run_async(operation)
