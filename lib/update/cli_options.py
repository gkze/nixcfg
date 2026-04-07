"""Shared CLI option models for update commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict, cast

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
type UpdateOptionValue = str | int | float | bool | None


class _UpdateOptionsInitKwargs(TypedDict, total=False):
    source: str | None
    list_targets: bool
    no_refs: bool
    no_sources: bool
    no_input: bool
    check: bool
    validate: bool
    schema: bool
    sort_by: UpdateSortBy
    json: bool
    verbose: bool
    quiet: bool
    tty: UpdateTTYMode
    zellij_guard: bool | None
    native_only: bool
    http_timeout: int | None
    subprocess_timeout: int | None
    max_nix_builds: int | None
    log_tail_lines: int | None
    render_interval: float | None
    user_agent: str | None
    retries: int | None
    retry_backoff: float | None
    fake_hash: str | None
    deno_platforms: str | None
    pinned_versions: str | None


class UpdateOptionsKwargs(_UpdateOptionsInitKwargs, total=False):
    """Keyword overrides accepted by update CLI compatibility helpers."""

    json_output: bool


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
    def from_mapping(cls, values: UpdateOptionsKwargs) -> UpdateOptions:
        """Build :class:`UpdateOptions` from CLI call parameters."""
        values_map = cast("dict[str, UpdateOptionValue]", dict(values))
        payload = {
            field_name: values_map[field_name]
            for field_name in cls.__dataclass_fields__
            if field_name in values_map
        }
        if "json_output" in values_map:
            payload["json"] = values_map["json_output"]
        return cls(**cast("_UpdateOptionsInitKwargs", payload))


__all__ = ["UpdateOptions", "UpdateOptionsKwargs", "UpdateSortBy", "UpdateTTYMode"]
