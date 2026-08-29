#!/usr/bin/env bash
set -euo pipefail

# Read-only activation gate for the Argus Traycer Host ownership transition.

die() {
  printf 'Traycer Host collision preflight: %s\n' "$1" >&2
  exit 1
}

if [ "$#" -ne 5 ]; then
  die "expected <uid> <home> <applications-directory> <launchctl> <plutil>"
fi

user_uid="$1"
user_home="$2"
applications_directory="$3"
launchctl_bin="$4"
plutil_bin="$5"

case "$user_uid" in
'' | *[!0-9]*) die "the user UID is indeterminate" ;;
esac
case "$user_home" in
/*) ;;
*) die "the user home is indeterminate" ;;
esac
case "$applications_directory" in
/*) ;;
*) die "the Applications directory is indeterminate" ;;
esac
[ -x "$launchctl_bin" ] || die "launchctl is unavailable"
[ -x "$plutil_bin" ] || die "plutil is unavailable"

base_label="ai.traycer.host"
agent_label="ai.traycer.host.agent"
legacy_agents="$applications_directory/Traycer.app/Contents/Library/LaunchAgents"
prior_plist="$user_home/Library/LaunchAgents/$base_label.plist"
legacy_program="Contents/Library/LaunchAgents/Traycer Host.app/Contents/MacOS/Traycer Host"

plist_value() {
  "$plutil_bin" -extract "$2" raw -o - "$1" 2>/dev/null
}

validate_legacy_plist() {
  local plist="$1"
  local expected_label="$2"
  local value

  if [ ! -e "$plist" ]; then
    return 0
  fi
  "$plutil_bin" -lint "$plist" >/dev/null 2>&1 ||
    die "could not determine the legacy $expected_label plist"
  value="$(plist_value "$plist" Label)" ||
    die "could not determine the legacy $expected_label label"
  [ "$value" = "$expected_label" ] ||
    die "the legacy $expected_label plist has an unexpected label"
  value="$(plist_value "$plist" BundleProgram)" ||
    die "could not determine the legacy $expected_label bundle program"
  [ "$value" = "$legacy_program" ] ||
    die "the legacy $expected_label plist has an unexpected bundle program"
  value="$(plist_value "$plist" ProgramArguments.0)" ||
    die "could not determine the legacy $expected_label program"
  [ "$value" = "$legacy_program" ] ||
    die "the legacy $expected_label plist has an unexpected program"
  value="$(plist_value "$plist" ProgramArguments.1)" ||
    die "could not determine the legacy $expected_label argument vector"
  [ "$value" = "$expected_label" ] ||
    die "the legacy $expected_label plist has an unexpected argument vector"
  if plist_value "$plist" ProgramArguments.2 >/dev/null 2>&1; then
    die "the legacy $expected_label plist has an unexpected argument vector"
  fi
}

validate_legacy_plist "$legacy_agents/$base_label.plist" "$base_label"
validate_legacy_plist "$legacy_agents/$agent_label.plist" "$agent_label"

prior_launchctl_program=""
prior_program=""
prior_command=""
prior_vector=""
if [ -e "$prior_plist" ]; then
  "$plutil_bin" -lint "$prior_plist" >/dev/null 2>&1 ||
    die "could not determine the prior Nix supervisor plist"
  prior_label="$(plist_value "$prior_plist" Label)" ||
    die "could not determine the prior Nix supervisor label"
  [ "$prior_label" = "$base_label" ] ||
    die "the prior supervisor plist does not own $base_label"
  prior_launchctl_program="$(plist_value "$prior_plist" ProgramArguments.0)" ||
    die "could not determine the prior Nix supervisor wrapper program"
  [ "$prior_launchctl_program" = /bin/sh ] ||
    die "the prior Nix supervisor does not use the exact Home Manager wrapper program"
  prior_shell_option="$(plist_value "$prior_plist" ProgramArguments.1)" ||
    die "could not determine the prior Nix supervisor wrapper option"
  [ "$prior_shell_option" = -c ] ||
    die "the prior Nix supervisor does not use the exact Home Manager wrapper option"
  prior_command="$(plist_value "$prior_plist" ProgramArguments.2)" ||
    die "could not determine the prior Nix supervisor wrapper command"
  command_prefix="/bin/wait4path /nix/store && exec "
  command_suffix=" host start --service-label $base_label"
  case "$prior_command" in
  "$command_prefix"*"$command_suffix") ;;
  *) die "the prior Nix supervisor does not use the exact Home Manager wrapper command" ;;
  esac
  prior_program="${prior_command#"$command_prefix"}"
  prior_program="${prior_program%"$command_suffix"}"
  if [[ ! $prior_program =~ ^/nix/store/[0-9abcdfghijklmnpqrsvwxyz]{32}-traycer-cli-[0-9A-Za-z+._-]+/bin/traycer$ ]]; then
    die "the prior supervisor wrapper does not contain an exact Traycer CLI Nix store path"
  fi
  if plist_value "$prior_plist" ProgramArguments.3 >/dev/null 2>&1; then
    die "the prior Nix supervisor has an unexpected argument vector"
  fi
  prior_vector="$(printf '%s\n' \
    "$prior_launchctl_program" \
    "$prior_shell_option" \
    "$prior_command")"
fi

user_launch_agents="$user_home/Library/LaunchAgents"
if [ -d "$user_launch_agents" ]; then
  shopt -s dotglob nullglob
  raw_user_plists=("$user_launch_agents"/*.plist)
  shopt -u dotglob nullglob
  for candidate_plist in "${raw_user_plists[@]}"; do
    [ "$candidate_plist" = "$prior_plist" ] && continue
    candidate_label="$(plist_value "$candidate_plist" Label)" || continue
    case "$candidate_label" in
    "$base_label" | "$agent_label")
      die "unexpected raw user LaunchAgent claims $candidate_label at $candidate_plist"
      ;;
    esac
  done
fi

probe_label() {
  local label="$1"
  local output_variable="$2"
  local present_variable="$3"
  local output
  local expected_not_found
  local expected_not_found_variant
  local status

  if output="$(LC_ALL=C "$launchctl_bin" print "gui/$user_uid/$label" 2>&1)"; then
    printf -v "$output_variable" '%s' "$output"
    printf -v "$present_variable" '%s' 1
    return 0
  else
    status=$?
  fi
  expected_not_found="$(printf 'Bad request.\nCould not find service "%s" in domain for user gui: %s' "$label" "$user_uid")"
  expected_not_found_variant="$(printf 'Could not find specified service "%s" in domain for user gui: %s' "$label" "$user_uid")"
  if [ "$status" -eq 113 ] && {
    [ "$output" = "$expected_not_found" ] ||
      [ "$output" = "$expected_not_found_variant" ]
  }; then
    printf -v "$output_variable" '%s' ""
    printf -v "$present_variable" '%s' 0
    return 0
  fi
  die "could not determine whether $label is loaded"
}

base_output=""
base_present=0
agent_output=""
agent_present=0
probe_label "$base_label" base_output base_present
probe_label "$agent_label" agent_output agent_present

reject_smappservice_signals() {
  local label="$1"
  local output="$2"
  case "$output" in
  *"managed_by = com.apple.xpc.ServiceManagement"* | \
    *"path = (submitted by smd."* | \
    *"type = Submitted"* | \
    *"program identifier ="* | \
    *"parent bundle identifier ="* | \
    *"BTM uuid ="*)
    die "$label is still loaded through SMAppService; unregister it with the vendor app before activating"
    ;;
  esac
}

if [ "$agent_present" -eq 1 ]; then
  reject_smappservice_signals "$agent_label" "$agent_output"
  die "$agent_label is loaded outside the declarative Nix supervisor; unregister it before activating"
fi

if [ "$base_present" -eq 1 ]; then
  reject_smappservice_signals "$base_label" "$base_output"
  [ -n "$prior_program" ] ||
    die "$base_label is loaded but there is no exact prior Nix supervisor plist"

  has_exact_top_level_field() {
    local output="$1"
    local key="$2"
    local expected="$3"
    local count=0
    local line
    while IFS= read -r line; do
      case "$line" in
      $'\t'"$key = "*)
        count=$((count + 1))
        [ "$line" = $'\t'"$key = $expected" ] || return 1
        ;;
      esac
    done <<<"$output"
    [ "$count" -eq 1 ]
  }

  IFS= read -r launchctl_header <<<"$base_output"
  [ "$launchctl_header" = "gui/$user_uid/$base_label = {" ] ||
    die "$base_label does not match the exact prior Nix supervisor identity"
  has_exact_top_level_field "$base_output" path "$prior_plist" ||
    die "$base_label does not match the exact prior Nix supervisor plist path"
  has_exact_top_level_field "$base_output" type LaunchAgent ||
    die "$base_label does not match the exact prior Nix supervisor type"
  has_exact_top_level_field "$base_output" program "$prior_launchctl_program" ||
    die "$base_label does not match the exact prior Home Manager wrapper program"

  read_top_level_arguments() {
    local output="$1"
    local in_arguments=0
    local arguments_seen=0
    local line
    local argument
    while IFS= read -r line; do
      if [ "$in_arguments" -eq 0 ]; then
        if [ "$line" = $'\targuments = {' ]; then
          [ "$arguments_seen" -eq 0 ] || return 1
          arguments_seen=1
          in_arguments=1
        fi
        continue
      fi
      if [ "$line" = $'\t}' ]; then
        in_arguments=0
        continue
      fi
      case "$line" in
      $'\t\t'*)
        argument="${line#$'\t\t'}"
        case "$argument" in
        '' | $'\t'*) return 1 ;;
        esac
        printf '%s\n' "$argument"
        ;;
      *) return 1 ;;
      esac
    done <<<"$output"
    [ "$arguments_seen" -eq 1 ] && [ "$in_arguments" -eq 0 ]
  }

  if ! launchctl_vector="$(read_top_level_arguments "$base_output")"; then
    die "$base_label does not match the exact prior Nix supervisor argument vector"
  fi
  [ "$launchctl_vector" = "$prior_vector" ] ||
    die "$base_label does not match the exact prior Nix supervisor argument vector"
fi
