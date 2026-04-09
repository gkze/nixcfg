"""Shared helpers for post-processing generated Pydantic code."""

from __future__ import annotations

import re

_CONSTR_PATTERN = re.compile(
    r"constr\(\s*pattern=(?P<literal>r?(?:'[^']*'|\"[^\"]*\"))\s*\)"
)


def rewrite_constr_type_hints(code: str) -> str:
    """Rewrite ``constr(pattern=...)`` annotations to ``StringConstraints``."""

    def _replace(match: re.Match[str]) -> str:
        literal = match.group("literal")
        line_start = match.string.rfind("\n", 0, match.start()) + 1
        indent = match.string[line_start : match.start()]
        inner = f"{indent}    "
        return f"Annotated[\n{inner}str,\n{inner}StringConstraints(pattern={literal}),\n{indent}]"

    return _CONSTR_PATTERN.sub(_replace, code)


def normalize_pydantic_imports(code: str) -> str:
    """Replace ``constr`` imports with ``StringConstraints`` when needed."""
    lines: list[str] = []
    for raw_line in code.splitlines():
        line = raw_line
        if raw_line.startswith("from pydantic import ") and "constr" in raw_line:
            names = [
                name.strip()
                for name in raw_line.split("import ", maxsplit=1)[1].split(",")
            ]
            names = [name for name in names if name != "constr"]
            if "StringConstraints" not in names:
                names.append("StringConstraints")
            line = f"from pydantic import {', '.join(sorted(names))}"
        lines.append(line)
    return "\n".join(lines)
