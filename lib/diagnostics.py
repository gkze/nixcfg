"""Remove URL credentials from diagnostics without changing request data."""

import re
from urllib.parse import urlsplit, urlunsplit

_HTTP_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def redact_urls(message: str) -> str:
    """Keep URL identity while hiding userinfo, queries, and fragments in output.

    Query names are not a reliable credential boundary: download providers use
    different signing schemes. Redact every query instead of listing known keys.
    """

    def redact(match: re.Match[str]) -> str:
        try:
            url = urlsplit(match.group())
        except ValueError:
            return "REDACTED_URL"
        netloc = url.netloc
        if "@" in netloc:
            netloc = f"REDACTED@{netloc.rsplit('@', 1)[1]}"
        return urlunsplit((
            url.scheme,
            netloc,
            url.path,
            "REDACTED" if url.query else "",
            "REDACTED" if url.fragment else "",
        ))

    return _HTTP_URL.sub(redact, message)
