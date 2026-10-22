"""Release identity and dependency coupling for the current AWS CLI."""

from unittest.mock import AsyncMock

import pytest

from lib.nix.models.sources import HashEntry
from lib.tests._updater_helpers import load_repo_module, run_async
from lib.update.updaters import UpdateContext, VersionInfo

MODULE = load_repo_module(
    "packages/awscli2-current/updater.py", "awscli2_current_tests"
)
COMMIT = "a" * 40
HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def test_literal_release_version() -> None:
    assert MODULE.release_version(b"import os\n__version__ = '2.38.4'\n") == "2.38.4"


@pytest.mark.parametrize(
    "source",
    [
        b"pass",
        b"__version__ = 2",
        b"__version__ = '1.9.0'",
        b"__version__ = '2.38.4rc1'",
        b"__version__ = '2.038.4'",
        b"__version__ = lookup()",
        b"__version__ = '2.38.4'\n__version__ = lookup()",
    ],
)
def test_ambiguous_or_unreleased_version_rejected(source: bytes) -> None:
    with pytest.raises(ValueError, match="literal stable version"):
        MODULE.release_version(source)


def dependencies(*requirements: str) -> bytes:
    return ("[project]\ndependencies = " + repr(list(requirements))).encode()


def test_exact_crt_requirement() -> None:
    assert (
        MODULE.crt_version(dependencies("colorama>=0.2.5", "awscrt==0.38.0"))
        == "0.38.0"
    )


@pytest.mark.parametrize(
    "requirements",
    [
        [],
        ["awscrt==0.38.0", "awscrt==0.37.0"],
        ["awscrt[extra]==0.38.0"],
        ["awscrt==0.38.0; python_version < '3.15'"],
        ["awscrt @ https://example.com/source.tar.gz"],
        ["awscrt>=0.38.0"],
        ["awscrt==0.38.0,!=0.37.0"],
        ["awscrt==0.38.*"],
    ],
)
def test_nonexact_crt_requirement_rejected(requirements: list[str]) -> None:
    with pytest.raises(ValueError, match="awscrt"):
        MODULE.crt_version(dependencies(*requirements))


def test_localstub_pin_ignores_comments_and_other_test_tools() -> None:
    assert (
        MODULE.localstub_version(b"# test-only\n\npytest==8.4.0\nlocalstub==0.0.3\n")
        == "0.0.3"
    )


def test_localstub_must_be_exactly_pinned() -> None:
    with pytest.raises(ValueError, match="localstub"):
        MODULE.localstub_version(b"localstub>=0.0.3")


def test_release_uses_tagged_dependency_source(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = AsyncMock(
        side_effect=[
            b"__version__ = '2.38.4'",
            dependencies("awscrt==0.38.0"),
            b"localstub==0.0.3",
        ]
    )
    resolve = AsyncMock(return_value=COMMIT)
    monkeypatch.setattr(MODULE, "fetch_url", fetch)
    updater = MODULE.AwsCli2CurrentUpdater()
    monkeypatch.setattr(updater, "_resolve_release_tag_commit", resolve)
    info = run_async(updater.fetch_latest(None, context=UpdateContext(current=None)))
    assert info.version == "2.38.4"
    assert info.commit == COMMIT
    assert info.metadata["awscrtVersion"] == "0.38.0"
    assert info.metadata["localstubVersion"] == "0.0.3"
    resolve.assert_awaited_once_with(None, "2.38.4")
    assert fetch.await_args_list[1].args[1].endswith(f"/{COMMIT}/pyproject.toml")
    assert (
        fetch
        .await_args_list[2]
        .args[1]
        .endswith(f"/{COMMIT}/requirements-test-blackbox.txt")
    )


def test_missing_release_tag_is_not_replaced_by_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = AsyncMock(return_value=b"__version__ = '2.38.5'")
    monkeypatch.setattr(MODULE, "fetch_url", fetch)
    updater = MODULE.AwsCli2CurrentUpdater()
    monkeypatch.setattr(
        updater,
        "_resolve_release_tag_commit",
        AsyncMock(side_effect=RuntimeError("missing tag")),
    )
    with pytest.raises(RuntimeError, match="missing tag"):
        run_async(updater.fetch_latest(None, context=UpdateContext(current=None)))
    assert fetch.await_count == 1


def test_hashes_and_result_keep_crt_with_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    source = AsyncMock(return_value=HASH)
    url = "https://files.pythonhosted.org/packages/source/a/awscrt/awscrt-0.38.0.tar.gz"
    localstub_url = "https://files.pythonhosted.org/packages/source/l/localstub/localstub-0.0.3.tar.gz"
    runtime = AsyncMock(return_value={url: HASH, localstub_url: HASH})
    monkeypatch.setattr(MODULE, "compute_fixed_output_hash", source)
    monkeypatch.setattr(MODULE, "compute_url_hashes", runtime)
    updater = MODULE.AwsCli2CurrentUpdater()
    info = VersionInfo(
        version="2.38.4",
        metadata={
            "commit": COMMIT,
            "awscrtVersion": "0.38.0",
            "localstubVersion": "0.0.3",
        },
    )
    hashes = run_async(
        updater.fetch_hashes(info, None, context=UpdateContext(current=None))
    )
    assert hashes == [
        HashEntry.create("srcHash", HASH),
        HashEntry.create("sha256", HASH, url=url),
        HashEntry.create("sha256", HASH, url=localstub_url),
    ]
    result = updater.build_result(info, hashes)
    assert result.version == "2.38.4"
    assert result.commit == COMMIT
    assert result.pins == {"awscrtVersion": "0.38.0", "localstubVersion": "0.0.3"}
    assert result.hashes.entries == hashes
    assert runtime.await_args.args[1] == [url, localstub_url]


@pytest.mark.parametrize("metadata", [{}, {"commit": "v2.38.4"}, {"commit": COMMIT}])
def test_incomplete_identity_rejected(metadata: dict[str, str]) -> None:
    with pytest.raises((RuntimeError, TypeError)):
        MODULE.AwsCli2CurrentUpdater().build_result(
            VersionInfo(version="2.38.4", metadata=metadata), []
        )
