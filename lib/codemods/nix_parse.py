"""Parse Nix that the pinned parser rejects only for trailing formal commas.

nix-manipulator rejects ``{ a, }: a``, which crate2nix and nixfmt both emit.
This module is the single owner of that workaround.
"""

import re
from typing import TYPE_CHECKING, Final

from nix_manipulator import parse

if TYPE_CHECKING:
    from nix_manipulator.expressions.source_code import NixSourceCode

_TRAILING_FORMAL_COMMA_PATTERN: Final = re.compile(
    r",(?=\s*}(?:@[A-Za-z_][A-Za-z0-9_'-]*)?\s*:)",
)


def strip_trailing_formal_commas(text: str) -> str:
    """Drop the trailing comma before each formal set's closing ``}:``."""
    return _TRAILING_FORMAL_COMMA_PATTERN.sub("", text)


def parse_nix(source: str) -> tuple[NixSourceCode, bool]:
    """Parse *source*, retrying without trailing formal commas.

    Returns the parse and whether it is verbatim, i.e. whether rebuilding the
    tree can reproduce *source*. A retried parse has lost those commas.
    """
    parsed = parse(source)
    if not parsed.contains_error:
        return parsed, True
    return parse(strip_trailing_formal_commas(source)), False


__all__ = ["parse_nix", "strip_trailing_formal_commas"]
