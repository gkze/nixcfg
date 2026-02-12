"""Helpers for normalizing generated Nix expressions."""


def compact_nix_expr(expr: str) -> str:
    """Collapse generated Nix code into a single-line expression."""
    return " ".join(line.strip() for line in expr.splitlines() if line.strip())


__all__ = ["compact_nix_expr"]
