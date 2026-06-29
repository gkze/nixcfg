#!/usr/bin/env python3
"""Normalize generated crate2nix output for the checked-in Codex Cargo.nix."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap_repo_import_path() -> None:
    """Add the repository root to ``sys.path`` for direct script execution."""
    if env_root := os.environ.get("REPO_ROOT"):
        sys.path.insert(0, str(Path(env_root).expanduser().resolve()))
        return

    candidates: list[Path] = []
    cwd = Path.cwd().resolve()
    candidates.extend((cwd, *cwd.parents))

    script_path = Path(__file__).resolve()
    for candidate in (script_path.parent, *script_path.parents):
        if candidate not in candidates:
            candidates.append(candidate)

    for candidate in candidates:
        if (candidate / ".root").is_file():
            sys.path.insert(0, str(candidate))
            return

    msg = f"Could not find repo root for {script_path}"
    raise RuntimeError(msg)


_bootstrap_repo_import_path()

from lib.cargo_nix_normalizer import normalize as normalize_cargo_nix  # noqa: E402
from lib.cargo_nix_normalizer_cli import (  # noqa: E402
    resolve_path as resolve_cli_path,
)
from lib.cargo_nix_normalizer_cli import run_normalizer  # noqa: E402
from lib.update.paths import get_repo_root  # noqa: E402

_LOCAL_PATH_PREFIXES = (
    "agent-identity",
    "analytics",
    "ansi-escape",
    "app-server",
    "app-server-client",
    "app-server-protocol",
    "app-server-test-client",
    "apply-patch",
    "arg0",
    "artifacts",
    "async-utils",
    "aws-auth",
    "backend-client",
    "chatgpt",
    "cli",
    "cloud-requirements",
    "cloud-tasks",
    "cloud-tasks-client",
    "cloud-tasks-mock-client",
    "codex-api",
    "codex-backend-openapi-models",
    "codex-client",
    "codex-experimental-api-macros",
    "codex-mcp",
    "code-mode",
    "collaboration-mode-templates",
    "config",
    "connectors",
    "core",
    "core-plugins",
    "core-skills",
    "debug-client",
    "device-key",
    "exec",
    "exec-server",
    "execpolicy",
    "execpolicy-legacy",
    "feedback",
    "features",
    "file-search",
    "git-utils",
    "hooks",
    "install-context",
    "instructions",
    "keyring-store",
    "linux-sandbox",
    "lmstudio",
    "login",
    "mcp-server",
    "model-provider",
    "model-provider-info",
    "models-manager",
    "network-proxy",
    "ollama",
    "otel",
    "package-manager",
    "plugin",
    "process-hardening",
    "protocol",
    "realtime-webrtc",
    "responses-api-proxy",
    "response-debug-context",
    "rmcp-client",
    "rollout",
    "rollout-trace",
    "sandboxing",
    "secrets",
    "shell-command",
    "shell-escalation",
    "skills",
    "state",
    "stdio-to-uds",
    "terminal-detection",
    "test-binary-support",
    "thread-store",
    "tools",
    "test-macros",
    "tui",
    "uds",
    "utils",
    "v8-poc",
    "windows-sandbox-rs",
)


def _resolve_path(path_text: str) -> Path:
    """Resolve one CLI path against the repository root."""
    return resolve_cli_path(path_text, repo_root=get_repo_root())


def normalize(text: str) -> tuple[str, int, bool]:
    """Return normalized Codex Cargo.nix text plus replacement counts."""
    return normalize_cargo_nix(
        text,
        local_path_prefixes=_LOCAL_PATH_PREFIXES,
    )


def main(argv: list[str] | None = None) -> int:
    """Normalize a Codex Cargo.nix file in place and report what changed."""
    return run_normalizer(
        normalize=normalize,
        default_path="packages/codex/Cargo.nix",
        description=__doc__,
        argv=argv,
        repo_root=get_repo_root(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
