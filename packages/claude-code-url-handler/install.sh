#!/bin/sh
set -eu

: "${out:?missing output path}"
: "${infoPlist:?missing Info.plist path}"
: "${claudeExecutable:?missing Claude executable path}"

app_path="$out/Applications/Claude Code URL Handler.app"

if [ ! -f "$infoPlist" ]; then
  echo "Info.plist does not exist: $infoPlist" >&2
  exit 66
fi
if [ ! -x "$claudeExecutable" ]; then
  echo "Claude executable is not executable: $claudeExecutable" >&2
  exit 66
fi

if command -v runHook >/dev/null 2>&1; then
  runHook preInstall
fi
mkdir -p "$app_path/Contents/MacOS"
install -m 0644 "$infoPlist" "$app_path/Contents/Info.plist"
ln -s "$claudeExecutable" "$app_path/Contents/MacOS/claude"
if command -v runHook >/dev/null 2>&1; then
  runHook postInstall
fi
