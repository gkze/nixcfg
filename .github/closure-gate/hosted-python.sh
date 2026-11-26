#!/bin/bash
# Run one hosted Actions step with the interpreter realize resolves.
#
# `shell: python` on the macOS runner is
# /Library/Frameworks/Python.framework/Versions/Current/bin/python. That
# directory is not on the PATH Nix's launchctl profile leaves behind, so a
# later step whose shell is the bare word python exits "python: command not
# found" after the realize process dies (45788923798). The framework bin and
# the Determinate Nix bin stay in front of PATH, and cachix remains reachable
# through the user profile.
#
# --record-signal runs python as a child. A status in 129..159 is that child
# killed by a signal (macOS SIGBUS is 138). The step records runner_fault so
# the closure-gate retry runs. Any other status records runner_fault=false:
# an empty flag means this wrapper never got to write, which is a lost runner.

set -u

framework="/Library/Frameworks/Python.framework/Versions/Current/bin"
nix_bin="/nix/var/nix/profiles/default/bin"
profile_bin="${HOME:-}/.nix-profile/bin"
PATH="${framework}:${nix_bin}:${profile_bin}:${PATH:-}"
export PATH

record_signal=0
if [[ "${1:-}" == "--record-signal" ]]; then
  record_signal=1
  shift
fi

python=""
if [[ -n "${NIXCFG_PYTHON:-}" && -x "${NIXCFG_PYTHON}" ]]; then
  python="${NIXCFG_PYTHON}"
elif [[ -x "${framework}/python" ]]; then
  python="${framework}/python"
elif command -v python >/dev/null 2>&1; then
  python="$(command -v python)"
elif command -v python3 >/dev/null 2>&1; then
  python="$(command -v python3)"
else
  echo "python: command not found" >&2
  exit 127
fi

write_fault() {
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf 'runner_fault=%s\n' "$1" >> "${GITHUB_OUTPUT}"
  fi
}

if [[ "$record_signal" -eq 1 ]]; then
  set +e
  "$python" "$@"
  status=$?
  if (( status > 128 && status < 160 )); then
    write_fault true
  else
    write_fault false
  fi
  exit "$status"
fi

exec "$python" "$@"
