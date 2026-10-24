#!/usr/bin/env bash
set -euo pipefail

for command in linear linear-cli; do
  completion="$1/share/bash-completion/completions/$command.bash"
  bash -n "$completion"
  # The generated completion path is only known after installation.
  # shellcheck source=/dev/null
  source "$completion"
  COMP_WORDS=("$command" issue li)
  COMP_CWORD=2
  COMP_TYPE=9
  _clap_complete_linear "$command" li issue
  printf '%s\n' "${COMPREPLY[@]}" | grep -Fx list
done
