"""Vendored Nix JSON schemas and related utilities."""

from pathlib import Path
from typing import TYPE_CHECKING

from lib.schema_codegen.runner import (
    generate_schema_codegen_target,
    verify_schema_codegen_target,
)

from ._fetch import check, fetch
from ._fetch import main as fetch_main

if TYPE_CHECKING:
    from lib.codegen_utils import ProgressReporter

SCHEMA_DIR = Path(__file__).resolve().parent
_CODEGEN_TARGET = "nix-schema-models"


def codegen_main(*, progress: ProgressReporter | None = None) -> None:
    """Generate the declaratively configured Nix schema models."""
    generate_schema_codegen_target(target_name=_CODEGEN_TARGET, progress=progress)


def verify_generated_models(*, progress: ProgressReporter | None = None) -> bool:
    """Return whether the configured Nix schema models are current."""
    return verify_schema_codegen_target(target_name=_CODEGEN_TARGET, progress=progress)


__all__ = [
    "SCHEMA_DIR",
    "check",
    "codegen_main",
    "fetch",
    "fetch_main",
    "verify_generated_models",
]
