#!/usr/bin/env python3
"""Normalize generated crate2nix output for the checked-in Codex Cargo.nix."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


sys.path.insert(0, str(_repo_root()))

from lib.cargo_nix_normalizer import normalize as normalize_cargo_nix  # noqa: E402

_LOCAL_PATH_PREFIXES = (
    "ansi-escape",
    "app-server",
    "app-server-client",
    "app-server-protocol",
    "app-server-test-client",
    "apply-patch",
    "arg0",
    "artifacts",
    "async-utils",
    "backend-client",
    "chatgpt",
    "cli",
    "cloud-requirements",
    "cloud-tasks",
    "cloud-tasks-client",
    "codex-api",
    "codex-backend-openapi-models",
    "codex-client",
    "codex-experimental-api-macros",
    "config",
    "core",
    "debug-client",
    "exec",
    "execpolicy",
    "execpolicy-legacy",
    "feedback",
    "file-search",
    "hooks",
    "keyring-store",
    "linux-sandbox",
    "lmstudio",
    "login",
    "mcp-server",
    "network-proxy",
    "ollama",
    "otel",
    "package-manager",
    "process-hardening",
    "protocol",
    "responses-api-proxy",
    "rmcp-client",
    "secrets",
    "shell-command",
    "shell-escalation",
    "skills",
    "state",
    "stdio-to-uds",
    "test-macros",
    "tui",
    "utils",
    "windows-sandbox-rs",
)


def normalize(text: str) -> tuple[str, int, bool]:
    """Return normalized Codex Cargo.nix text plus replacement counts."""
    return normalize_cargo_nix(
        text,
        local_path_prefixes=_LOCAL_PATH_PREFIXES,
    )


def main() -> int:
    """Normalize a Codex Cargo.nix file in place and report what changed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default="packages/codex/Cargo.nix")
    args = parser.parse_args()

    path = Path(args.path)
    original = path.read_text()
    normalized, path_rewrites, added_root_src = normalize(original)

    if normalized != original:
        path.write_text(normalized)

    status = []
    status.append("added rootSrc" if added_root_src else "rootSrc already present")
    status.append(f"rewrote {path_rewrites} source path(s)")
    status.append("updated file" if normalized != original else "no content change")
    sys.stdout.write(f"{path}: " + ", ".join(status) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
