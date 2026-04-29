#!/usr/bin/env bash
set -euo pipefail

app_root="@out@/Applications/Emdash.app/Contents/MacOS"

if [ -z "${SSH_AUTH_SOCK:-}" ]; then
  ssh_auth_sock="$(launchctl getenv SSH_AUTH_SOCK 2>/dev/null || true)"
  if [ -n "$ssh_auth_sock" ]; then
    export SSH_AUTH_SOCK="$ssh_auth_sock"
  fi
fi

exec "$app_root/Emdash" "$@"
