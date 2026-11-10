# shellcheck shell=bash
# build-volume-attach.nix assigns buildVolumeBundle, buildVolumeMountPoint,
# buildVolumeName, and buildVolumeMaxSize before this fragment.
# shellcheck disable=SC2154
# Create the Build sparse bundle on first use, then attach it. Attaching an
# image that is already attached is a successful no-op, so this is safe at
# every login and every activation. The final check rejects the one outcome
# hdiutil reports as success but consumers cannot use: a stray directory at
# the mount point makes macOS mount the volume at "<path> 1" instead, and the
# cache environment would then write onto the root volume.
if [ ! -e "$buildVolumeBundle" ]; then
  printf 'creating Build volume %s...\n' "$buildVolumeBundle" >&2
  /usr/bin/hdiutil create -quiet -type SPARSEBUNDLE -fs APFS \
    -volname "$buildVolumeName" -size "$buildVolumeMaxSize" "$buildVolumeBundle"
fi

/usr/bin/hdiutil attach -quiet "$buildVolumeBundle"

buildVolumeCurrentMountPoint="$(
  /usr/sbin/diskutil info -plist "$buildVolumeMountPoint" 2>/dev/null |
    /usr/bin/plutil -extract MountPoint raw -o - - 2>/dev/null || true
)"
if [ "$buildVolumeCurrentMountPoint" != "$buildVolumeMountPoint" ]; then
  printf '%s\n' \
    "error: $buildVolumeBundle is attached but $buildVolumeMountPoint is not its mount point." \
    "Remove any non-volume entry at $buildVolumeMountPoint, detach the image, then rerun." >&2
  exit 1
fi
