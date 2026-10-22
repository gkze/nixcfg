#!/usr/bin/env zsh
set -euo pipefail

fpath=("$1/share/zsh/site-functions" $fpath)
autoload -Uz compinit
compinit -D

# Capture candidates at the display boundary without requiring a live ZLE.
_describe() { print -rl -- "${(@P)3}"; }

for command in linear linear-cli; do
  words=("$command" issue lis)
  CURRENT=3
  autoload -Uz "_$command"
  "_$command" > "$TMPDIR/linear-zsh-candidates"
  grep -E '^list(:|$)' "$TMPDIR/linear-zsh-candidates"
done
