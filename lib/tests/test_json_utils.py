"""Behavioral tests for shared JSON validation helpers."""

from __future__ import annotations

import pytest

from lib import json_utils


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({1: "value"}, "Expected string key in payload, got int"),
        (object(), "Unsupported JSON value in payload: object"),
    ],
)
def test_coerce_json_value_rejects_non_json_values(
    value: object,
    message: str,
) -> None:
    """Reject values that cannot be represented by the declared JSON type."""
    with pytest.raises(TypeError, match=message):
        json_utils.coerce_json_value(value, context="payload")
