#!/usr/bin/env bash
# Make Cursor's bundled ripgrep usable after nixpkgs' patchPhase.
#
# Nixpkgs chmods or replaces one path chosen from a capped VS Code version:
#   Contents/Resources/app/node_modules/@vscode/ripgrep/bin/rg
#   resources/app/node_modules/@vscode/ripgrep/bin/rg
# Cursor 3.30.8 still ships that package, but only as a resolver. Its
# lib/index.js loads @cursor/ripgrep-$platform-$arch/bin/rg. The Darwin DMG
# for commit 4a44c846d0b817fab060823f93c63ea2861ac192 and the matching Linux
# AppImage both store a regular rg there, with mode 0755, and have no
# @vscode/ripgrep/bin/rg. Earlier Cursor and VS Code builds put the binary at
# the legacy path, under node_modules.asar.unpacked, or in
# @vscode/ripgrep-universal. DMG unpack can drop the executable bit, so every
# regular file in those layouts is marked executable. On Linux, nixpkgs
# replaces the bundled binary with its own ripgrep; the same layouts are
# retargeted. Finding none of them is a layout change, not a successful skip.

set -euo pipefail

shopt -s nullglob

if [[ $# -eq 0 ]]; then
  mode="chmod"
  target=
elif [[ $# -eq 2 && $1 == "--link-to" ]]; then
  mode="link"
  target="$2"
  if [[ ! -x $target ]]; then
    printf 'code-cursor: replacement ripgrep is not executable: %s\n' "$target" >&2
    exit 1
  fi
else
  printf 'usage: chmod-bundled-ripgrep.sh [--link-to TARGET]\n' >&2
  exit 1
fi

candidates=(
  Contents/Resources/app/node_modules/@vscode/ripgrep/bin/rg
  Contents/Resources/app/node_modules.asar.unpacked/@vscode/ripgrep/bin/rg
  Contents/Resources/app/node_modules/@vscode/ripgrep-universal/bin/*/rg
  Contents/Resources/app/node_modules.asar.unpacked/@vscode/ripgrep-universal/bin/*/rg
  Contents/Resources/app/node_modules/@cursor/ripgrep-*/bin/rg
  resources/app/node_modules/@vscode/ripgrep/bin/rg
  resources/app/node_modules.asar.unpacked/@vscode/ripgrep/bin/rg
  resources/app/node_modules/@vscode/ripgrep-universal/bin/*/rg
  resources/app/node_modules.asar.unpacked/@vscode/ripgrep-universal/bin/*/rg
  resources/app/node_modules/@cursor/ripgrep-*/bin/rg
)

found=()
# Bash 3.2 treats "${array[@]}" as unbound when the array is empty under set -u.
if [[ ${#candidates[@]} -gt 0 ]]; then
  for candidate in "${candidates[@]}"; do
    if [[ -f $candidate ]]; then
      found+=("$candidate")
    fi
  done
fi

if [[ ${#found[@]} -eq 0 ]]; then
  printf 'code-cursor: bundled ripgrep was not in a known layout\n' >&2
  find . -path '*/bin/rg' -print >&2 || true
  exit 1
fi

if [[ $mode == "chmod" ]]; then
  chmod +x "${found[@]}"
else
  for candidate in "${found[@]}"; do
    ln -sfn "$target" "$candidate"
  done
fi
