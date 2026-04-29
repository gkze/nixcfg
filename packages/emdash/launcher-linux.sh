#!/usr/bin/env bash
set -euo pipefail

app_root="@out@/share/emdash/linux-unpacked"
exec "$app_root/emdash" "$@"
