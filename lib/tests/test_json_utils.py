"""Behavioral tests for shared JSON validation helpers."""

import pytest

from lib import json_utils


def test_as_object_dict_rejects_non_string_keys() -> None:
    """Reject object mappings that cannot satisfy the JSON object contract."""
    with pytest.raises(TypeError, match="Expected string key in payload, got int"):
        json_utils.as_object_dict({1: "value"}, context="payload")


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
