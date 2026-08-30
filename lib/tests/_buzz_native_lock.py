"""Test helpers for Buzz's updater-owned native runtime lock."""

import json
import shlex
from functools import cache

from lib.update.paths import REPO_ROOT


@cache
def buzz_native_lock() -> dict[str, object]:
    """Load the same structured lock consumed by the Buzz Nix derivations."""
    payload = json.loads(
        (REPO_ROOT / "packages/buzz/native-lock.json").read_text(encoding="utf-8")
    )
    assert isinstance(payload, dict)
    return payload


def buzz_native_lock_string(section: str, key: str) -> str:
    """Return one required string from the updater-owned native lock."""
    section_value = buzz_native_lock()[section]
    assert isinstance(section_value, dict)
    value = section_value[key]
    assert isinstance(value, str)
    return value


def render_buzz_native_lock_interpolations(source: str) -> str:
    """Materialize Nix interpolations needed to execute embedded test scripts."""
    values = {
        "buzzVersion": buzz_native_lock_string("buzz", "version"),
        "llamaCppCommit": buzz_native_lock_string("llamaCpp", "commit"),
        "meshLlmCommit": buzz_native_lock_string("meshLlm", "commit"),
        "meshLlmVersion": buzz_native_lock_string("meshLlm", "version"),
        "skippyAbi": buzz_native_lock_string("meshLlm", "skippyAbi"),
    }
    rendered = source
    for name, value in values.items():
        rendered = rendered.replace(f"${{{name}}}", value)
        rendered = rendered.replace(
            f"${{builtins.toJSON {name}}}",
            json.dumps(value),
        )
        rendered = rendered.replace(
            f"${{lib.escapeShellArg {name}}}",
            shlex.quote(value),
        )
    return rendered.replace(
        "${skippyAbiTuple}",
        ", ".join(values["skippyAbi"].split(".")),
    )
