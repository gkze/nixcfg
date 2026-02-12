"""lib.nix.commands: Typed async subprocess wrappers for Nix CLI."""

from ._json import as_model_list, as_model_mapping, run_nix_json
from .base import (
    CommandResult,
    HashMismatchError,
    NixCommandError,
    ProcessDone,
    ProcessLine,
    run_nix,
    stream_nix,
    stream_process,
)
from .build import nix_build, nix_build_dry_run
from .derivation import nix_derivation_show
from .eval import nix_eval_json, nix_eval_raw, nix_eval_typed
from .flake import nix_flake_lock_update, nix_flake_metadata, nix_flake_show
from .hash import nix_hash_convert, nix_prefetch_url
from .path_info import nix_path_info
from .store import nix_store_query_references, nix_store_realise

__all__ = [
    "CommandResult",
    "HashMismatchError",
    "NixCommandError",
    "ProcessDone",
    "ProcessLine",
    "as_model_list",
    "as_model_mapping",
    "nix_build",
    "nix_build_dry_run",
    "nix_derivation_show",
    "nix_eval_json",
    "nix_eval_raw",
    "nix_eval_typed",
    "nix_flake_lock_update",
    "nix_flake_metadata",
    "nix_flake_show",
    "nix_hash_convert",
    "nix_path_info",
    "nix_prefetch_url",
    "nix_store_query_references",
    "nix_store_realise",
    "run_nix",
    "run_nix_json",
    "stream_nix",
    "stream_process",
]
