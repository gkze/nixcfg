#!/usr/bin/env sh
set -eu

if [ "$#" -ne 9 ]; then
  echo "usage: install_zed_nightly_app.sh <out> <tmpdir> <app-version> <patched-src> <magick> <png2icns> <git-bin> <cli-bin> <zed-bin>" >&2
  exit 1
fi

out_path="$1"
tmpdir_path="$2"
app_version="$3"
patched_src="$4"
magick_bin="$5"
png2icns_bin="$6"
git_bin="$7"
cli_bin="$8"
zed_bin="$9"

app_path="$out_path/Applications/Zed Nightly.app"
iconset_dir="$tmpdir_path/Zed Nightly.iconset"

mkdir -p "$app_path/Contents/MacOS" "$app_path/Contents/Resources" "$out_path/bin"

cat >"$app_path/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "https://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>English</string>
  <key>CFBundleDisplayName</key>
  <string>Zed Nightly</string>
  <key>CFBundleExecutable</key>
  <string>zed</string>
  <key>CFBundleIconFile</key>
  <string>Zed Nightly</string>
  <key>CFBundleIdentifier</key>
  <string>dev.zed.Zed-Nightly</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>Zed Nightly</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>${app_version}</string>
  <key>CFBundleVersion</key>
  <string>${app_version}</string>
  <key>CFBundleURLTypes</key>
  <array>
    <dict>
      <key>CFBundleTypeRole</key>
      <string>Editor</string>
      <key>CFBundleURLSchemes</key>
      <array>
        <string>zed</string>
      </array>
    </dict>
  </array>
  <key>LSApplicationCategoryType</key>
  <string>public.app-category.developer-tools</string>
  <key>LSMinimumSystemVersion</key>
  <string>10.15.7</string>
  <key>NSHighResolutionCapable</key>
  <true/>
$(cat "${patched_src}/crates/zed/resources/info/SupportedPlatforms.plist")
$(cat "${patched_src}/crates/zed/resources/info/Permissions.plist")
$(cat "${patched_src}/crates/zed/resources/info/DocumentTypes.plist")
</dict>
</plist>
EOF

mkdir -p "$iconset_dir"
for size in 16 32 64 128 256; do
  "$magick_bin" "${patched_src}/crates/zed/resources/app-icon-nightly.png" \
    -resize "${size}x${size}" "$iconset_dir/${size}.png"
done
cp "${patched_src}/crates/zed/resources/app-icon-nightly.png" "$iconset_dir/512.png"
cp "${patched_src}/crates/zed/resources/app-icon-nightly@2x.png" "$iconset_dir/1024.png"
"$png2icns_bin" "$app_path/Contents/Resources/Zed Nightly.icns" "$iconset_dir"/*.png >/dev/null
cp "${patched_src}/crates/zed/resources/Document.icns" "$app_path/Contents/Resources/Document.icns"

cp "$zed_bin" "$app_path/Contents/MacOS/zed"
ln -s "$git_bin" "$app_path/Contents/MacOS/git"
cp "$cli_bin" "$app_path/Contents/MacOS/cli"
ln -s "$out_path/Applications/Zed Nightly.app/Contents/MacOS/cli" "$out_path/bin/zed"
