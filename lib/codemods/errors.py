"""Shared exceptions for source codemod helpers."""


class CodemodError(RuntimeError):
    """A source codemod could not be applied safely."""
