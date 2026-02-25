"""Shared assertion helpers used by tests."""

from __future__ import annotations


def check(condition: object, message: object | None = None) -> None:
    """Fail the test when *condition* is false."""
    if condition:
        return
    if message is None:
        raise AssertionError
    raise AssertionError(message)


def expect_not_none[T](value: T | None, message: object | None = None) -> T:
    """Return *value* when present, otherwise fail the test."""
    if value is not None:
        return value
    if message is None:
        raise AssertionError
    raise AssertionError(message)


def expect_instance[T](value: object, expected_type: type[T]) -> T:
    """Return *value* cast to *expected_type*, or fail the test."""
    if isinstance(value, expected_type):
        return value
    msg = f"expected {expected_type.__name__}, got {type(value).__name__}"
    raise AssertionError(msg)
