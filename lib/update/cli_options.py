"""Shared CLI option models for update commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

UpdateSortBy = Literal[
    "name",
    "type",
    "classification",
    "source",
    "input",
    "ref",
    "version",
    "rev",
    "commit",
    "touches",
    "writes",
]

UpdateTTYMode = Literal["auto", "force", "off", "full"]


@dataclass(frozen=True)
class UpdateOptions:
    """Typed options for the update CLI."""

    source: str | None = None
    list_targets: bool = False
    no_refs: bool = False
    no_sources: bool = False
    no_input: bool = False
    check: bool = False
    validate: bool = False
    schema: bool = False
    sort_by: UpdateSortBy = "name"
    json: bool = False
    verbose: bool = False
    quiet: bool = False
    tty: UpdateTTYMode = "auto"
    zellij_guard: bool | None = None
    native_only: bool = False
    http_timeout: int | None = None
    subprocess_timeout: int | None = None
    max_nix_builds: int | None = None
    log_tail_lines: int | None = None
    render_interval: float | None = None
    user_agent: str | None = None
    retries: int | None = None
    retry_backoff: float | None = None
    fake_hash: str | None = None
    deno_platforms: str | None = None
    pinned_versions: str | None = None

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> UpdateOptions:
        """Build :class:`UpdateOptions` from CLI call parameters."""
        payload: dict[str, object] = {
            field_name: values[field_name]
            for field_name in cls.__dataclass_fields__
            if field_name in values
        }
        if "json_output" in values:
            payload["json"] = values["json_output"]
        return cls(**cast("dict[str, Any]", payload))


__all__ = ["UpdateOptions", "UpdateSortBy", "UpdateTTYMode"]
