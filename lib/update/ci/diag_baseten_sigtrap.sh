#!/bin/bash
# TEMPORARY diagnostic (not for merge): reproduce the baseten-switch XCTest
# SIGTRAP from Update 41275217088 on a hosted macos-15 runner and capture a
# backtrace. Compares the runner's Aqua session against a system-bootstrap
# _nixbld process (the nix-daemon build context).
set -u
W=/private/tmp/bsw
rm -rf "$W"; mkdir -p "$W"; cd "$W" || exit 1
echo "::group::env"
sw_vers; uname -a; id; xcodebuild -version; swift --version; sysctl -n hw.ncpu
launchctl print gui/$(id -u) 2>/dev/null | head -5
echo "::endgroup::"
git clone -q https://github.com/basetenlabs/baseten-switch src && cd src && git checkout -q 551f1bd5b601f63d5807842feacfcbb82d5c8c16 || exit 1
for p in nix-managed.patch raw-telemetry-test-clock.patch; do
  patch -p1 --batch < "$GITHUB_WORKSPACE/packages/baseten-switch/$p" || exit 1
done
cd mac/BasetenSwitch
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
mkdir -p "$W/home" "$W/cc" "$W/mc"
echo "::group::build tests"
env HOME="$W/home" CLANG_MODULE_CACHE_PATH="$W/cc" SWIFTPM_MODULECACHE_OVERRIDE="$W/mc" \
  swift build --build-tests --disable-sandbox --scratch-path "$W/build" 2>&1 | tail -5
echo "::endgroup::"
BUNDLE=$(/bin/ls -d "$W"/build/debug/*.xctest | head -1)
XCTEST=$(xcrun --find xctest)
echo "bundle=$BUNDLE xctest=$XCTEST"
chmod -R a+rwX "$W"
PAIR="BasetenSwitchTests.RuntimeCoordinationTests/testTrackedStatusMenuRefreshProjectsAndRebuildsAuthAttention,BasetenSwitchTests.RuntimeCoordinationTests/testUnsupportedStatusFallsBackWithoutRepeatedProbe,BasetenSwitchTests.RuntimeCoordinationTests/testUnusableSecondaryReceiptRetainsRecoveryGate"
ALONE="BasetenSwitchTests.RuntimeCoordinationTests/testUnsupportedStatusFallsBackWithoutRepeatedProbe,BasetenSwitchTests.RuntimeCoordinationTests/testUnusableSecondaryReceiptRetainsRecoveryGate"
UPD="BasetenSwitchTests.UpdatePresentationTests"
BT="enable=yes,interactive=no,symbolicate=full,threads=all,sanitize=no,color=no"
run_case() { # name user filter
  local name=$1 user=$2 filter=$3
  cat > "$W/$name.sh" <<EOS
#!/bin/bash
cd "$W/src/mac/BasetenSwitch"
export HOME="$W/home-$name" CFFIXED_USER_HOME="$W/home-$name" TMPDIR="$W/tmp-$name/" DEVELOPER_DIR=$DEVELOPER_DIR SWIFT_BACKTRACE="$BT"
mkdir -p "\$HOME" "\$TMPDIR"
echo "whoami=\$(id) ppid=\$PPID"
launchctl managername 2>&1
"$XCTEST" -XCTest "$filter" "$BUNDLE" 2>&1
echo "EXIT=\$?"
EOS
  chmod 0755 "$W/$name.sh"
  echo "::group::case $name ($user)"
  if [ "$user" = aqua ]; then
    "$W/$name.sh" > "$W/$name.out" 2>&1
  else
    cat > "$W/$name.plist" <<EOP
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>diag.bsw.$name</string>
<key>UserName</key><string>$user</string>
<key>ProgramArguments</key><array><string>$W/$name.sh</string></array>
<key>StandardOutPath</key><string>$W/$name.out</string>
<key>StandardErrorPath</key><string>$W/$name.out</string>
<key>RunAtLoad</key><true/>
</dict></plist>
EOP
    sudo cp "$W/$name.plist" "/Library/LaunchDaemons/diag.bsw.$name.plist"
    sudo launchctl bootstrap system "/Library/LaunchDaemons/diag.bsw.$name.plist"
    for _ in $(seq 1 300); do grep -q "^EXIT=" "$W/$name.out" 2>/dev/null && break; sleep 1; done
    sudo launchctl bootout "system/diag.bsw.$name" 2>/dev/null
  fi
  cat "$W/$name.out"
  echo "::endgroup::"
  echo "RESULT $name: $(grep '^EXIT=' "$W/$name.out" | tail -1) $(grep -c 'signal\|Crash\|Fatal' "$W/$name.out")"
}
run_case aqua-pair aqua "$PAIR"
run_case daemon-pair _nixbld1 "$PAIR"
run_case daemon-alone _nixbld1 "$ALONE"
run_case daemon-root-pair root "$PAIR"
run_case daemon-updates _nixbld1 "$UPD"
run_case daemon-all _nixbld1 "BasetenSwitchTests"
echo "::group::lldb daemon-pair"
cat > "$W/lldb-pair.sh" <<EOS
#!/bin/bash
cd "$W/src/mac/BasetenSwitch"
export HOME="$W/home-lldb" CFFIXED_USER_HOME="$W/home-lldb" TMPDIR="$W/tmp-lldb/" DEVELOPER_DIR=$DEVELOPER_DIR
mkdir -p "\$HOME" "\$TMPDIR"
/usr/bin/lldb --batch -o "process handle SIGPIPE -n true -p true -s false" -o run -k "thread backtrace all" -k "register read" -k "disassemble -p -c 6" -k "process kill" -- "$XCTEST" -XCTest "$PAIR" "$BUNDLE" 2>&1
echo "EXIT=\$?"
EOS
chmod 0755 "$W/lldb-pair.sh"
sed -e "s#$W/daemon-pair.sh#$W/lldb-pair.sh#; s#diag.bsw.daemon-pair#diag.bsw.lldb#; s#$W/daemon-pair.out#$W/lldb.out#g; s#<string>_nixbld1</string>#<string>root</string>#" "$W/daemon-pair.plist" > "$W/lldb.plist"
sudo cp "$W/lldb.plist" /Library/LaunchDaemons/diag.bsw.lldb.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/diag.bsw.lldb.plist
for _ in $(seq 1 300); do grep -q "^EXIT=" "$W/lldb.out" 2>/dev/null && break; sleep 1; done
cat "$W/lldb.out"
echo "::endgroup::"
echo "::group::crash reports"
sudo find /Library/Logs/DiagnosticReports /Users/runner/Library/Logs/DiagnosticReports /var/db/diagnostics -maxdepth 2 -newer "$W/build" -name '*xctest*' 2>/dev/null | while read -r f; do echo "== $f"; sudo head -c 60000 "$f"; echo; done
echo "::endgroup::"
exit 1
