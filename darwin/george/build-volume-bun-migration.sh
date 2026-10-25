# shellcheck shell=bash
# buildVolumeBunInstall is assigned by build-volume.nix before this fragment.
# shellcheck disable=SC2154
# Only an unmanaged ~/.bun directory needs migration. Existing Home Manager
# links are already adopted, so a detached Build volume must not block unrelated
# activations. Refuse to displace populated data with an empty or unreadable
# target; the owning Nix module supplies the current volume path.
if [ -d "$HOME/.bun" ] && [ ! -L "$HOME/.bun" ]; then
  if [ ! -d "$buildVolumeBunInstall" ]; then
    buildVolumeTargetState=empty
  elif ! buildVolumeTargetEntries="$(/bin/ls -A "$buildVolumeBunInstall" 2>/dev/null)"; then
    printf 'error: cannot inspect Build Bun target: %s\n' "$buildVolumeBunInstall" >&2
    exit 1
  elif [ -z "$buildVolumeTargetEntries" ]; then
    buildVolumeTargetState=empty
  fi

  if [ "${buildVolumeTargetState:-}" = empty ]; then
    if ! buildVolumeSourceEntries="$(/bin/ls -A "$HOME/.bun" 2>/dev/null)"; then
      printf 'error: cannot inspect existing Bun directory: %s\n' "$HOME/.bun" >&2
      exit 1
    fi
    if [ -n "$buildVolumeSourceEntries" ]; then
      printf '%s\n' \
        "error: refusing to replace $HOME/.bun with empty $buildVolumeBunInstall." \
        "Copy the existing data to Build, verify the global CLIs, then rerun activation:" \
        "  /usr/bin/ditto \"$HOME/.bun\" \"$buildVolumeBunInstall\"" \
        "Keep $HOME/.bun until the copied installation works." >&2
      exit 1
    fi
  fi
fi
