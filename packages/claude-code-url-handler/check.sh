#!/bin/sh
set -eu

: "${out:?missing output path}"
: "${claudeExecutable:?missing Claude executable path}"

app="$out/Applications/Claude Code URL Handler.app"
executable="$app/Contents/MacOS/claude"

if command -v runHook >/dev/null 2>&1; then
  runHook preInstallCheck
fi
test -L "$executable"
test "$(readlink "$executable")" = "$claudeExecutable"
test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$app/Contents/Info.plist")" = \
  "com.anthropic.claude-code-url-handler"
test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleURLTypes:0:CFBundleURLSchemes:0' "$app/Contents/Info.plist")" = \
  "claude-cli"
if command -v runHook >/dev/null 2>&1; then
  runHook postInstallCheck
fi
