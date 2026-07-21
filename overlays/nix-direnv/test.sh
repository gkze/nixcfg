#!/usr/bin/env bash

# The functions in this harness override callbacks invoked by sourced nix-direnv code.
# shellcheck disable=SC2329

set -euo pipefail

direnvrc=$1
direnv_bin=$2
retry_integration_envrc=$3
# The built package path is supplied by the Nix test.
# shellcheck disable=SC1090
source "$direnvrc"

calls=0
_nix() {
  calls=$((calls + 1))

  local -a expected=(
    build
    --out-link
    "$TMPDIR/flake-inputs/input"
    /nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-first
    /nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-second
  )
  local -a actual=("$@")

  [[ ${#actual[@]} -eq ${#expected[@]} ]]

  local index
  for index in "${!expected[@]}"; do
    [[ ${actual[$index]} == "${expected[$index]}" ]]
  done
}

archive_json='{"path":"/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-first","inputs":{"duplicate":{"path":"/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-first"},"second":{"path":"/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-second"}}}'
_nix_add_flake_input_gcroots "$TMPDIR/flake-inputs/" "$archive_json"
[[ $calls -eq 1 ]]

_nix_add_flake_input_gcroots "$TMPDIR/flake-inputs/" '{"inputs":{}}'
[[ $calls -eq 1 ]]

_nix() {
  return 23
}

if _nix_add_flake_input_gcroots "$TMPDIR/flake-inputs/" "$archive_json"; then
  echo "expected a failed batch build to propagate" >&2
  exit 1
fi

test_layout=
failure_mode=
info_log=
warning_log=
imports=0
imported_profile_rc=
watched_file=
retry_watch_was_present=0

_nix_direnv_preflight() {
  return 0
}

watch_file() {
  local file
  for file in "$@"; do
    if [[ $file == "$test_layout/flake-retry" && -e $file ]]; then
      retry_watch_was_present=1
    fi
  done
}

direnv_layout_dir() {
  printf '%s\n' "$test_layout"
}

_nix_argsum_suffix() {
  return 0
}

_nix_direnv_watches() {
  local -n result=$1
  # Initialize the caller's array through the nameref.
  # shellcheck disable=SC2034
  result=("$watched_file")
}

_nix_direnv_info() {
  info_log+="$*"$'\n'
}

_nix_direnv_warning() {
  warning_log+="$*"$'\n'
}

_nix_import_env() {
  imports=$((imports + 1))
  imported_profile_rc=$1
}

_nix() {
  case $1 in
  print-dev-env)
    mkdir -p "$(dirname "$3")"
    touch "$3"
    printf 'export NIX_DIRENV_TEST=1\n'
    ;;
  flake)
    if [[ $failure_mode == archive ]]; then
      return 22
    fi
    printf '{"path":"/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-first"}\n'
    ;;
  build)
    if [[ $3 == "$test_layout/flake-inputs/"* && $failure_mode == batch ]]; then
      return 23
    fi
    if [[ $3 == "$test_layout/flake-profile" && $failure_mode == profile ]]; then
      return 24
    fi
    ln -sfn "$4" "$3"
    ;;
  *)
    echo "unexpected nix invocation: $*" >&2
    return 1
    ;;
  esac
}

assert_failed_renewal_preserves_previous_cache() {
  failure_mode=$1
  test_layout="$TMPDIR/use-flake-$failure_mode"
  watched_file="$TMPDIR/flake-$failure_mode/flake.nix"
  info_log=
  warning_log=
  imports=0
  imported_profile_rc=
  retry_watch_was_present=0
  unset NIX_DIRENV_DID_FALLBACK
  mkdir -p "$(dirname "$watched_file")" "$test_layout/flake-inputs"
  touch "$test_layout/flake-profile"
  printf 'export NIX_DIRENV_PREVIOUS=1\n' >"$test_layout/flake-profile.rc"
  touch "$test_layout/flake-inputs/previous-input" "$watched_file"
  touch -t 200001010000 "$test_layout/flake-profile.rc"
  touch -t 203001010000 "$watched_file"

  if ! use_flake "path:$(dirname "$watched_file")"; then
    echo "expected $failure_mode failure to use the previous cache" >&2
    exit 1
  fi

  [[ $imports -eq 1 ]]
  [[ $imported_profile_rc == "$test_layout/flake-profile.rc" ]]
  [[ ${NIX_DIRENV_DID_FALLBACK:-} == 1 ]]
  [[ $info_log != *"Renewed cache"* ]]
  [[ $warning_log == *"Falling back to previous environment"* ]]
  [[ -e $test_layout/flake-profile ]]
  [[ $(<"$test_layout/flake-profile.rc") == "export NIX_DIRENV_PREVIOUS=1" ]]
  [[ -e $test_layout/flake-inputs/previous-input ]]
  # The watched retry file changes from present to absent, so direnv reruns
  # the environment automatically on the next prompt.
  [[ $retry_watch_was_present -eq 1 ]]
  [[ ! -e $test_layout/flake-retry ]]
}

assert_failed_renewal_preserves_previous_cache archive
assert_failed_renewal_preserves_previous_cache batch
assert_failed_renewal_preserves_previous_cache profile

assert_successful_renewal_replaces_previous_cache() {
  failure_mode=
  test_layout="$TMPDIR/use-flake-success"
  watched_file="$TMPDIR/flake-success/flake.nix"
  info_log=
  warning_log=
  imports=0
  imported_profile_rc=
  retry_watch_was_present=0
  unset NIX_DIRENV_DID_FALLBACK
  mkdir -p "$(dirname "$watched_file")" "$test_layout/flake-inputs"
  touch "$test_layout/flake-profile"
  printf 'export NIX_DIRENV_PREVIOUS=1\n' >"$test_layout/flake-profile.rc"
  touch "$test_layout/flake-inputs/previous-input" "$watched_file"
  touch -t 200001010000 "$test_layout/flake-profile.rc"
  touch -t 203001010000 "$watched_file"

  use_flake "path:$(dirname "$watched_file")"

  [[ $imports -eq 1 ]]
  [[ $imported_profile_rc == "$test_layout/flake-profile.rc" ]]
  [[ -z ${NIX_DIRENV_DID_FALLBACK:-} ]]
  [[ $info_log == *"Renewed cache"* ]]
  [[ -z $warning_log ]]
  [[ $(<"$test_layout/flake-profile.rc") == "export NIX_DIRENV_TEST=1" ]]
  [[ ! -e $test_layout/flake-inputs/previous-input ]]
  if ! compgen -G "$test_layout/flake-inputs/input-*" >/dev/null; then
    echo "successful renewal did not retain its new input roots" >&2
    exit 1
  fi
  [[ -e $test_layout/flake-retry ]]
}

assert_successful_renewal_replaces_previous_cache

assert_initial_failure_schedules_retry() {
  failure_mode=archive
  test_layout="$TMPDIR/use-flake-initial"
  watched_file="$TMPDIR/flake-initial/flake.nix"
  info_log=
  warning_log=
  imports=0
  imported_profile_rc=
  retry_watch_was_present=0
  unset NIX_DIRENV_DID_FALLBACK
  mkdir -p "$(dirname "$watched_file")"
  touch "$watched_file"

  if use_flake "path:$(dirname "$watched_file")"; then
    echo "expected an initial archive failure without a fallback cache" >&2
    exit 1
  fi

  [[ $imports -eq 0 ]]
  [[ -z ${NIX_DIRENV_DID_FALLBACK:-} ]]
  [[ $retry_watch_was_present -eq 1 ]]
  [[ ! -e $test_layout/flake-retry ]]
  if compgen -G "$test_layout/flake-profile*" >/dev/null; then
    echo "initial archive failure left a partial profile" >&2
    exit 1
  fi
}

assert_initial_failure_schedules_retry

(
  integration_dir="$TMPDIR/direnv-retry-integration"
  mkdir -p "$integration_dir/home" "$integration_dir/xdg"
  cp "$retry_integration_envrc" "$integration_dir/.envrc"
  touch "$integration_dir/flake.nix"

  export HOME="$integration_dir/home"
  export XDG_DATA_HOME="$integration_dir/xdg"
  export NIX_DIRENVRC="$direnvrc"
  cd "$integration_dir"

  "$direnv_bin" allow .
  first_export=$("$direnv_bin" export bash 2>first.err)
  eval "$first_export"
  read -r first_count <evaluations

  second_export=$("$direnv_bin" export bash 2>second.err)
  eval "$second_export"
  read -r second_count <evaluations

  if [[ $first_count != 1 || $second_count != 2 ]]; then
    echo "expected automatic retry across consecutive direnv exports; got $first_count -> $second_count" >&2
    exit 1
  fi
)
