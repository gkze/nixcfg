#!/bin/bash
# TEMPORARY diagnostic (not for merge): does initializing AppKit avoid the
# SkyLight CGSConnectionByID assert in NSStatusBar.statusItem on hosted
# macos-15, in the runner's Aqua session and in system-domain launchd daemons
# (root, _nixbld1 = nix builder)? Also NSWindow creation.
set -u
sw_vers; xcodebuild -version
W=/private/tmp/cgs; rm -rf "$W"; mkdir -p "$W"
cat > "$W/cgs.swift" <<'EOF'
import AppKit
setvbuf(stdout, nil, _IONBF, 0)
let args = Array(CommandLine.arguments.dropFirst())
let mode = args.first ?? "bare"
print("mode=\(args) uid=\(getuid())")
switch mode {
case "app": _ = NSApplication.shared
case "app-prohibited": _ = NSApplication.shared; NSApp.setActivationPolicy(.prohibited)
case "finish": _ = NSApplication.shared; NSApp.setActivationPolicy(.accessory); NSApp.finishLaunching()
default: break
}
print("CGSession=\(CGSessionCopyCurrentDictionary() != nil)")
if args.contains("window") {
    let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 100, height: 100), styleMask: [.titled], backing: .buffered, defer: false)
    print("window ok \(w.frame)")
} else {
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    print("statusItem ok button=\(item.button != nil)")
}
print("DONE")
EOF
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun swiftc -O "$W/cgs.swift" -o "$W/cgs" || exit 1
sudo chmod -R 0777 "$W"
for user in aqua root _nixbld1; do
  for a in "bare" "app" "app-prohibited" "finish" "bare window" "app window"; do
    name="$user-${a// /-}"
    if [ "$user" = aqua ]; then
      "$W/cgs" $a > "$W/$name.out" 2>&1; echo "EXIT=$?" >> "$W/$name.out"
    else
      printf '#!/bin/bash\n"%s/cgs" %s > "%s/%s.out" 2>&1\necho "EXIT=$?" >> "%s/%s.out"\n' "$W" "$a" "$W" "$name" "$W" "$name" > "$W/$name.sh"
      chmod 0755 "$W/$name.sh"
      cat > "$W/$name.plist" <<EOP
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>diag.cgs.$name</string>
<key>UserName</key><string>$user</string>
<key>ProgramArguments</key><array><string>$W/$name.sh</string></array>
<key>RunAtLoad</key><true/>
</dict></plist>
EOP
      sudo cp "$W/$name.plist" "/Library/LaunchDaemons/diag.cgs.$name.plist"
      sudo launchctl bootstrap system "/Library/LaunchDaemons/diag.cgs.$name.plist"
      for _ in $(seq 1 60); do grep -q '^EXIT=' "$W/$name.out" 2>/dev/null && break; sleep 1; done
      sudo launchctl bootout "system/diag.cgs.$name" 2>/dev/null || true
    fi
    echo "::group::$name"; cat "$W/$name.out"; echo "::endgroup::"
    echo "RESULT $name: $(grep -E '^(EXIT=|CGSession=|statusItem ok|window ok)' "$W/$name.out" | tr '\n' ' ') $(grep -m1 -o 'Assertion failed.*' "$W/$name.out")"
  done
done
exit 1
