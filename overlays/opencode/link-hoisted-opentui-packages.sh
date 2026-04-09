#!/usr/bin/env bash
set -euo pipefail

root_node_modules="${1:-node_modules}"
package_node_modules="${2:-packages/opencode/node_modules}"
opentui_root="$root_node_modules/@opentui"
target_root="$package_node_modules/@opentui"

if [ ! -d "$opentui_root" ] || [ -d "$target_root" ]; then
  exit 0
fi

chmod u+w "$package_node_modules"
mkdir -p "$target_root"

shopt -s nullglob
for pkg in "$opentui_root"/*; do
  ln -s "../../../../$pkg" "$target_root/$(basename "$pkg")"
done
