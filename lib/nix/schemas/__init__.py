"""Vendored Nix JSON schemas and related utilities."""

from pathlib import Path

from ._codegen import main as codegen_main
from ._fetch import check, fetch
from ._fetch import main as fetch_main

SCHEMA_DIR = Path(__file__).resolve().parent

__all__ = ["SCHEMA_DIR", "check", "codegen_main", "fetch", "fetch_main"]
