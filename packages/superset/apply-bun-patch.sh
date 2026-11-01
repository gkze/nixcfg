# shellcheck shell=bash
# Apply one Bun `patchedDependencies` patch to an extracted package tree.
#
# Bun applies these patches line by line, so a context line matches whether
# or not the published file ends in a newline. GNU patch compares the newline
# too: @xterm/addon-ligatures@0.11.0-beta.310 ships package.json without one,
# so its upstream patch fails at the closing brace under GNU patch alone.
# Terminate such targets first unless the patch itself records a missing
# newline, then apply strictly (no fuzz), so any real drift still fails.
applyBunPatch() {
  local root=$1 patchFile=$2 target
  if ! grep -q '^\\ No newline at end of file' "$patchFile"; then
    while IFS= read -r target; do
      [ -n "$target" ] || continue
      if [ -f "$root/$target" ] && [ -s "$root/$target" ] &&
        [ -n "$(tail -c 1 "$root/$target")" ]; then
        printf '\n' >>"$root/$target"
      fi
    done < <(sed -n 's|^+++ b/||p' "$patchFile")
  fi
  patch -p1 --batch --forward --fuzz=0 --no-backup-if-mismatch \
    -d "$root" <"$patchFile"
}
