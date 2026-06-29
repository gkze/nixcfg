"""Shared exceptions for source codemod helpers."""

from __future__ import annotations


class CodemodError(RuntimeError):
    """A source codemod could not be applied safely."""
