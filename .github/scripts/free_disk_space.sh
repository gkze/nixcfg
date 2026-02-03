#!/usr/bin/env bash

echo "=== Before cleanup ==="
df -h /

# Keep only the latest Xcode, remove all others - saves ~60-80 GB
latest_xcode=$(ls -d /Applications/Xcode*.app 2>/dev/null | sort -V | tail -1)
for xcode in /Applications/Xcode*.app; do
	[[ "$xcode" != "$latest_xcode" ]] && sudo rm -rf "$xcode"
done

# Remove all simulators - saves ~20-30 GB
sudo rm -rf ~/Library/Developer/CoreSimulator
xcrun simctl delete all 2>/dev/null || true

# Remove Android SDK, .NET, and cached tools - saves ~30-40 GB
sudo rm -rf ~/Library/Android/sdk /usr/local/share/dotnet ~/hostedtoolcache

echo "=== After cleanup ==="
df -h /
