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


@pytest.mark.parametrize(
    ("mapping", "message"),
    [
        ({}, "Expected string field 'version' in manifest"),
        ({"version": 1}, "Expected string field 'version' in manifest"),
        ({"version": ""}, "Expected non-empty string field 'version' in manifest"),
    ],
)
def test_get_required_nonempty_str_rejects_missing_wrong_or_empty(
    mapping: dict[str, object], message: str
) -> None:
    """Missing, non-string, and empty fields all fail at the JSON boundary."""
    with pytest.raises(TypeError, match=message):
        json_utils.get_required_nonempty_str(mapping, "version", context="manifest")


def test_get_required_nonempty_str_returns_the_value() -> None:
    """A present non-empty string passes through unchanged."""
    assert (
        json_utils.get_required_nonempty_str(
            {"version": "1.2.3"}, "version", context="manifest"
        )
        == "1.2.3"
    )


@pytest.mark.parametrize(
    ("helper", "value", "message"),
    [
        (json_utils.as_object_dict, [], "Expected JSON object for payload"),
        (json_utils.as_object_list, {}, "Expected JSON array for payload"),
    ],
)
def test_shape_helpers_reject_the_wrong_container(
    helper, value: object, message: str
) -> None:
    """Objects and arrays are not interchangeable at the JSON boundary."""
    with pytest.raises(TypeError, match=message):
        helper(value, context="payload")
