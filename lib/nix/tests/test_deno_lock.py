"""Tests for low-level Deno lock resolution helpers."""

from lib.update import deno_lock

URL_TO_CACHE_PATH = deno_lock._url_to_cache_path  # noqa: SLF001
PARSE_NPM_PKG_KEY = deno_lock._parse_npm_pkg_key  # noqa: SLF001


def test_url_to_cache_path_is_stable_for_https_urls() -> None:
    """Verify cache path computation matches Deno cache hashing rules."""
    actual = URL_TO_CACHE_PATH(
        "https://raw.githubusercontent.com/owner/repo/branch/deno.lock"
    )
    expected = (
        "remote/https/raw.githubusercontent.com/"
        "50f48b909f071f7ccf1ca6de27a4402d33390820891594148c442c07802a17c4"
    )
    if actual != expected:
        msg = f"unexpected cache path: {actual!r}"
        raise AssertionError(msg)


def test_url_to_cache_path_rejects_non_https_urls() -> None:
    """Non-HTTPS URLs are unsupported for cache path resolution."""
    expected_prefix = "Expected https URL"
    try:
        URL_TO_CACHE_PATH("file:///tmp/example")
    except ValueError as exc:
        message = str(exc)
        if expected_prefix not in message:
            msg = f"unexpected ValueError message: {message!r}"
            raise AssertionError(msg) from exc
    else:  # pragma: no cover - defensive
        msg = "expected ValueError for non-HTTPS URL"
        raise AssertionError(msg)


def test_parse_npm_pkg_key_handles_scoped_and_peer_qualified_packages() -> None:
    """NPM package keys should normalize scoped and peer-qualified variants."""
    cases = [
        ("left-pad@1.0.0", ("left-pad", "1.0.0")),
        ("@scope/left-pad@1.0.0", ("@scope/left-pad", "1.0.0")),
        ("@scope/left-pad@1.0.0_peer@npm:1", ("@scope/left-pad", "1.0.0")),
    ]
    for package_key, expected in cases:
        actual = PARSE_NPM_PKG_KEY(package_key)
        if actual != expected:
            msg = f"unexpected package key parse for {package_key!r}: {actual!r}"
            raise AssertionError(msg)
