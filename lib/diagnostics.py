"""Remove URL credentials from diagnostics without changing request data."""

import re
from urllib.parse import urlsplit, urlunsplit

_URL_START = re.compile(r"https?://", re.IGNORECASE)
# Text that continues a URL across a raw space: a download provider URL may
# contain a literal space in its path, so the query can follow one. Only a
# token that actually carries a query/fragment assignment is absorbed, which
# keeps ordinary prose after a URL ("see https://x.test What?") untouched.
_URL_CONTINUATION = re.compile(r"[ \t]+[^\s<>\"']*[?#][^\s<>\"']*=[^\s<>\"']*")
_DELIMITERS = " \t\r\n<>"
_QUOTES = "\"'"
# A query or fragment assignment is what makes the rest of a token belong to
# the URL; without one, a quote is a closing quote in running text.
_TOKEN_QUERY = re.compile(r"[?#][^\s<>\"']*=")


def _scan_end(message: str, start: int) -> int:
    """Return the end index of the URL candidate that starts at *start*.

    The scan consumes a URL up to the first delimiter. A quote is treated as a
    closing quote - and left out of the candidate - unless the rest of that
    token still belongs to the URL: it carries a query/fragment assignment and
    does not start a second URL. That keeps a URL whose path contains an
    apostrophe in one piece without swallowing the closing quote of a JSON
    string, which would corrupt the surrounding diagnostic text.
    """
    index = start
    length = len(message)
    while index < length:
        character = message[index]
        if character in _DELIMITERS:
            break
        if character in _QUOTES and _quote_closes(message, index):
            break
        index += 1
    return index


def _quote_closes(message: str, index: int) -> bool:
    """Return whether the quote at *index* ends the URL instead of continuing it."""
    length = len(message)
    token_end = index + 1
    while token_end < length and message[token_end] not in _DELIMITERS:
        token_end += 1
    if _URL_START.search(message, index + 1, token_end) is not None:
        return True
    return _TOKEN_QUERY.search(message, index + 1, token_end) is None


def _redact_candidate(candidate: str) -> str:
    """Hide userinfo, query, and fragment while keeping scheme/host/path."""
    try:
        url = urlsplit(candidate)
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


def redact_urls(message: str) -> str:
    """Keep URL identity while hiding userinfo, queries, and fragments in output.

    Query names are not a reliable credential boundary: download providers use
    different signing schemes. Redact every query instead of listing known keys.

    The scan consumes a URL up to the first character that cannot belong to one
    in running text, then folds in a directly following space-separated token
    when that token carries the URL's query. Both rules exist because the
    previous token match ended at the first space or quote and emitted the
    remainder - including ``?token=...`` - verbatim.
    """
    parts: list[str] = []
    position = 0
    for match in _URL_START.finditer(message):
        start = match.start()
        if start < position:
            continue
        end = _scan_end(message, start)
        candidate = message[start:end]
        if "?" not in candidate and "#" not in candidate:
            continuation = _URL_CONTINUATION.match(message, end)
            if continuation is not None:
                end = continuation.end()
                candidate = message[start:end]
        parts.append(message[position:start])
        parts.append(_redact_candidate(candidate))
        position = end
    parts.append(message[position:])
    return "".join(parts)
