#!/bin/bash
# TEMPORARY diagnostic for Update 43110633449, reverted before review.
# PR update prepare runs this instead of the Darwin prepare shards:
#   0: baseten-switch XCTest SIGTRAP probes (_nixbld1 vs Aqua, lldb, .ips)
#   1: openai-cli 1.52.0 Nix build with per-test JSON timings
#   2: antigravity 2.31.2 extraction + full Nix build on a healthy VM
#   3: openai-cli 1.51.5 Nix build with per-test JSON timings
#   4: baseten-switch Nix builds: baseline and candidate check phases
set -u
PY="$1"
REPO="$GITHUB_WORKSPACE"
SHARD="${NIXCFG_PREPARE_SHARD:-x}"
W=/private/tmp/diag43110633449
rm -rf "$W"
mkdir -p "$W"

echo "::group::env shard=$SHARD"
sw_vers
uname -a
id
sysctl -n hw.ncpu hw.memsize
xcodebuild -version
nix config show 2>/dev/null | grep -E '^(sandbox|sandbox-fallback|cores|max-jobs|system-features) ' || true
df -h / /nix
echo "::endgroup::"

BS_REV=551f1bd5b601f63d5807842feacfcbb82d5c8c16
APPKIT_TESTS=(
  testTrackedStatusMenuRefreshProjectsAndRebuildsAuthAttention
  testUpdateDetailsKeepUsableContentHeightWhenHostedWithoutShowing
  testAvailableMenuItemDispatchesNativeActionToUpdateWindowCallback
  testUnconfiguredDesktopIsDiscoverableAndOpensItsConfiguration
  testCurrentMenuKeepsManualCheckBeforeOpenAppAndNoReleaseMarker
  testReleaseChangesDoNotRebuildTrackedMenuOrChangeRoutingHealth
)

baseten_probes() {
  cd "$W" || return 1
  git clone -q https://github.com/basetenlabs/baseten-switch src || return 1
  cd src && git checkout -q "$BS_REV" || return 1
  for p in nix-managed raw-telemetry-test-clock status-item-appkit-test-setup system-cli-runner-pipe-ownership; do
    patch -p1 --batch -s < "$REPO/packages/baseten-switch/$p.patch" || return 1
  done
  find . -name '*.orig' -delete
  cd mac/BasetenSwitch || return 1
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
  mkdir -p "$W/home" "$W/cc" "$W/mc"
  echo "::group::build tests"
  env HOME="$W/home" CLANG_MODULE_CACHE_PATH="$W/cc" SWIFTPM_MODULECACHE_OVERRIDE="$W/mc" \
    /usr/bin/swift build --build-tests --disable-sandbox --scratch-path "$W/build" 2>&1 | tail -3
  echo "::endgroup::"
  chmod -R a+rwX "$W"
  local base="--skip testUnusableSecondaryReceiptRetainsRecoveryGate --skip testRouterWindowContentUsesNonObscuredLayoutGuide"
  local no_runner="$base --skip testSystemRunner"
  local no_appkit="$base"
  for t in "${APPKIT_TESTS[@]}"; do no_appkit="$no_appkit --skip $t"; done
  local only_status="--filter RuntimeCoordinationTests/testTrackedStatusMenuRefreshProjectsAndRebuildsAuthAttention --filter RuntimeCoordinationTests/testUnsupportedStatusFallsBackWithoutRepeatedProbe --filter StatsTests --filter TrafficTests"
  local only_runner="--filter RuntimeCoordinationTests/testSystemRunner --filter RuntimeCoordinationTests/testUnsupportedStatusFallsBackWithoutRepeatedProbe --filter StatsTests --filter TrafficTests"
  run_case() { # name user swift-test-args...
    local name=$1 user=$2
    shift 2
    cat > "$W/$name.sh" <<EOS
#!/bin/bash
cd "$W/src/mac/BasetenSwitch"
export HOME="$W/home-$name" CFFIXED_USER_HOME="$W/home-$name" TMPDIR="$W/tmp-$name/"
export DEVELOPER_DIR=$DEVELOPER_DIR SDKROOT=$DEVELOPER_DIR/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk
export CLANG_MODULE_CACHE_PATH="$W/cc" SWIFTPM_MODULECACHE_OVERRIDE="$W/mc"
export SWIFT_BACKTRACE="enable=yes,interactive=no,color=no,preset=full,threads=all,registers=all,images=mentioned,limit=60,top=0"
mkdir -p "\$HOME" "\$TMPDIR"
echo "whoami=\$(id -un) manager=\$(launchctl managername 2>&1)"
/usr/bin/swift test --skip-build --disable-sandbox --disable-swift-testing --scratch-path "$W/build" $* 2>&1
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
      for _ in $(seq 1 600); do grep -q "^EXIT=" "$W/$name.out" 2>/dev/null && break; sleep 1; done
      sudo launchctl bootout "system/diag.bsw.$name" 2>/dev/null
    fi
    grep -E "Test Case .*(started|failed)|Executed|Program crashed|Thread [0-9]+ crashed|^ *[0-9]+ .*0x|signal|EXIT=|whoami|error:" "$W/$name.out" | grep -v "passed" | tail -120
    echo "::endgroup::"
    local crashed_in
    crashed_in=$(grep "Test Case .* started" "$W/$name.out" | tail -1)
    echo "RESULT $name: $(grep '^EXIT=' "$W/$name.out" | tail -1) crashes=$(grep -c 'Program crashed' "$W/$name.out") executed=[$(grep 'Executed' "$W/$name.out" | tail -1 | tr -s ' ')] last=[$crashed_in]"
  }
  run_case nixbld-full _nixbld1 $base
  run_case nixbld-full-2 _nixbld1 $base
  run_case nixbld-no-runner _nixbld1 $no_runner
  run_case nixbld-no-appkit _nixbld1 $no_appkit
  run_case nixbld-no-appkit-2 _nixbld1 $no_appkit
  run_case nixbld-only-status _nixbld1 $only_status
  run_case nixbld-only-runner _nixbld1 $only_runner
  run_case aqua-full aqua $base
  run_case aqua-only-status aqua $only_status

  echo "::group::lldb root only-status"
  local bundle
  bundle=$(/bin/ls -d "$W"/build/debug/*.xctest | head -1)
  cat > "$W/lldb.sh" <<EOS
#!/bin/bash
cd "$W/src/mac/BasetenSwitch"
export HOME="$W/home-lldb" CFFIXED_USER_HOME="$W/home-lldb" TMPDIR="$W/tmp-lldb/" DEVELOPER_DIR=$DEVELOPER_DIR SWIFT_BACKTRACE=enable=no
mkdir -p "\$HOME" "\$TMPDIR"
/usr/bin/lldb --batch -o "process handle SIGPIPE -n true -p true -s false" -o run \
  -k "thread backtrace all" -k "register read" -k "image lookup -va \\\$pc" \
  -k "disassemble -s \\\$pc-48 -c 20" -k "thread info" -k "process kill" \
  -- "\$(xcrun --find xctest)" -XCTest "BasetenSwitchTests.RuntimeCoordinationTests/testTrackedStatusMenuRefreshProjectsAndRebuildsAuthAttention,BasetenSwitchTests.RuntimeCoordinationTests/testUnsupportedStatusFallsBackWithoutRepeatedProbe,BasetenSwitchTests.StatsTests,BasetenSwitchTests.TrafficTests" "$bundle" 2>&1
echo "EXIT=\$?"
EOS
  chmod 0755 "$W/lldb.sh"
  sed -e "s#$W/nixbld-full.sh#$W/lldb.sh#; s#diag.bsw.nixbld-full#diag.bsw.lldb#; s#$W/nixbld-full.out#$W/lldb.out#g; s#<string>_nixbld1</string>#<string>root</string>#" "$W/nixbld-full.plist" > "$W/lldb.plist"
  sudo cp "$W/lldb.plist" /Library/LaunchDaemons/diag.bsw.lldb.plist
  sudo launchctl bootstrap system /Library/LaunchDaemons/diag.bsw.lldb.plist
  for _ in $(seq 1 600); do grep -q "^EXIT=" "$W/lldb.out" 2>/dev/null && break; sleep 1; done
  grep -v "Test Case .* passed" "$W/lldb.out" | tail -250
  echo "::endgroup::"

  echo "::group::crash reports"
  sudo find /Library/Logs/DiagnosticReports /Users/runner/Library/Logs/DiagnosticReports /var/root/Library/Logs/DiagnosticReports -newer "$W/build" -type f 2>/dev/null | while read -r f; do
    echo "== $f"
    sudo "$PY" - "$f" <<'EOP'
import json, sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
head, _, body = text.partition("\n")
try:
    report = json.loads(body)
except ValueError:
    print(text[:4000])
    raise SystemExit
for key in ("procName", "exception", "termination", "asi", "asiBacktraces", "faultingThread", "lastExceptionBacktrace", "crashInfo"):
    if key in report:
        print(key, "=", json.dumps(report[key])[:3000])
thread = report.get("threads", [{}])[report.get("faultingThread", 0)]
images = report.get("usedImages", [])
for frame in thread.get("frames", [])[:25]:
    image = images[frame.get("imageIndex", 0)] if images else {}
    print("  frame", image.get("name"), hex(frame.get("imageOffset", 0)), frame.get("symbol"), frame.get("symbolLocation"))
EOP
  done
  echo "::endgroup::"
}

openai_timing() { # version
  local version=$1
  cd "$REPO" || return 1
  "$PY" - "$version" <<'EOP'
import pathlib, re, sys
version = sys.argv[1]
flake = pathlib.Path("flake.nix")
flake.write_text(flake.read_text().replace("github:openai/openai-cli/v1.40.0", f"github:openai/openai-cli/v{version}"))
nix = pathlib.Path("packages/openai-cli/default.nix")
text = nix.read_text()
text = text.replace("    patches = (old.patches or [ ]) ++ [", '    checkFlags = (old.checkFlags or [ ]) ++ [ "-json" "-timeout=40m" ];\n    patches = (old.patches or [ ]) ++ [', 1)
nix.write_text(text)
EOP
  nix flake update openai-cli 2>&1 | tail -2
  echo "::group::vendor hash"
  local got
  got=$(nix build --no-link .#openai-cli.goModules 2>&1 | grep -Eo 'got: +sha256-[A-Za-z0-9+/=]+' | awk '{print $2}' | tail -1)
  echo "vendorHash=$got"
  if [ -n "$got" ]; then
    "$PY" - "$got" <<'EOP'
import json, pathlib, sys
path = pathlib.Path("packages/openai-cli/sources.json")
data = json.loads(path.read_text())
for entry in data["hashes"]:
    if entry["hashType"] == "vendorHash":
        entry["hash"] = sys.argv[1]
path.write_text(json.dumps(data, indent=2) + "\n")
EOP
  fi
  echo "::endgroup::"
  local start end
  start=$(date +%s)
  nix build --no-link -L .#openai-cli > "$W/openai.log" 2>&1
  local rc=$?
  end=$(date +%s)
  echo "openai-cli $version nix build rc=$rc wall=$((end - start))s"
  grep -E "Running phase|completed in|^openai-cli> (ok|FAIL|---|panic)" "$W/openai.log" | head -40
  "$PY" - "$W/openai.log" <<'EOP'
import json, re, sys
events = []
for line in open(sys.argv[1], errors="replace"):
    line = re.sub(r"^openai-cli> ", "", line.rstrip("\n"))
    if line.startswith("{") and '"Action"' in line:
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
pkgs = [e for e in events if e.get("Action") in ("pass", "fail") and not e.get("Test")]
for e in pkgs:
    print("PACKAGE", e.get("Package"), e["Action"], e.get("Elapsed"))
tops = [e for e in events if e.get("Action") in ("pass", "fail", "skip") and e.get("Test") and "/" not in e["Test"] and e.get("Package", "").endswith("cmd/openai")]
print("cmd/openai top-level tests:", len(tops), "sum elapsed", round(sum(e.get("Elapsed", 0) for e in tops), 1))
for e in sorted(tops, key=lambda e: -e.get("Elapsed", 0))[:40]:
    print(f"  {e['Action']:4} {e.get('Elapsed', 0):8.2f} {e['Test']}")
for e in tops:
    if e["Action"] == "fail":
        print("FAILED", e["Test"])
runs = {e["Test"] for e in events if e.get("Action") == "run" and e.get("Test") and "/" not in e["Test"]}
done = {e["Test"] for e in tops}
print("started-but-unfinished:", sorted(runs - done)[:20])
EOP
  grep -E "panic: test timed out|running tests:" -A6 "$W/openai.log" | head -20
}

antigravity_check() {
  cd "$W" || return 1
  curl -fsSL -o ag.dmg https://storage.googleapis.com/antigravity-public/antigravity-hub/2.31.2-6470801290035200/darwin-arm/Antigravity.dmg || return 1
  shasum -a 256 ag.dmg
  (cd "$REPO" && nix shell --inputs-from . nixpkgs#_7zz -c 7zz x -sns- -snld -y "$W/ag.dmg" -o"$W/ag" > "$W/7zz.log" 2>&1)
  echo "7zz rc=$? $(tail -3 "$W/7zz.log" | tr '\n' ' ')"
  "$PY" -c "
import sys
sys.path.insert(0, '$REPO/packages/antigravity')
from pathlib import Path
import resign_bundle as r
m, b = r.discover_inventory(Path('$W/ag/Antigravity.app'))
print('inventory OK', len(m), 'machos', len(b), 'bundles')
"
  cd "$REPO" || return 1
  "$PY" - <<'EOP'
import json, pathlib
path = pathlib.Path("packages/antigravity/sources.json")
data = json.loads(path.read_text())
data["version"] = "2.31.2-6470801290035200"
data["urls"]["aarch64-darwin"] = "https://storage.googleapis.com/antigravity-public/antigravity-hub/2.31.2-6470801290035200/darwin-arm/Antigravity.dmg"
data["hashes"]["aarch64-darwin"] = "sha256-vxCRTGsor7d7u/VTQYMVssnFqj06b3ubJoaCUGA7w+Y="
path.write_text(json.dumps(data, indent=2) + "\n")
EOP
  nix build --no-link -L .#antigravity > "$W/ag-build.log" 2>&1
  echo "antigravity 2.31.2 nix build rc=$?"
  tail -15 "$W/ag-build.log"
  log show --style compact --predicate 'eventMessage CONTAINS "I/O error"' --last 2h 2>/dev/null | grep -c "I/O error" | sed 's/^/kernel I\/O errors: /'
}

baseten_nix_builds() {
  cd "$REPO" || return 1
  build_variant() { # name
    local name=$1 start rc
    start=$(date +%s)
    nix build --no-link -L .#baseten-switch > "$W/bs-$name.log" 2>&1
    rc=$?
    echo "::group::baseten-switch nix build $name"
    grep -E "Test Case .*(failed)|Executed|Program crashed|Thread [0-9]+ crashed|0 \[ra\]|error:|Test Suite .*(passed|failed)" "$W/bs-$name.log" | tail -40
    grep "Test Case .* started" "$W/bs-$name.log" | tail -2
    echo "::endgroup::"
    echo "RESULT bs-$name rc=$rc wall=$(( $(date +%s) - start ))s"
  }
  build_variant baseline
  "$PY" - "${APPKIT_TESTS[@]}" <<'EOP'
import pathlib, sys
path = pathlib.Path("packages/baseten-switch/default.nix")
text = path.read_text()
anchor = "            --skip testRouterWindowContentUsesNonObscuredLayoutGuide\n"
extra = "".join(f" \\\n            --skip {name}" for name in sys.argv[1:])
text = text.replace(anchor, anchor.rstrip("\n") + extra + "\n", 1)
text = text.replace("      ./status-item-appkit-test-setup.patch\n", "", 1)
path.write_text(text)
EOP
  build_variant no-appkit
  "$PY" - <<'EOP'
import pathlib
path = pathlib.Path("packages/baseten-switch/default.nix")
text = path.read_text()
text = text.replace("      ./system-cli-runner-pipe-ownership.patch\n", "", 1)
path.write_text(text)
EOP
  build_variant no-appkit-no-pipe-patch
}

case "$SHARD" in
  0) baseten_probes ;;
  1) openai_timing 1.52.0 ;;
  2) antigravity_check ;;
  3) openai_timing 1.51.5 ;;
  4) baseten_nix_builds ;;
esac
exit 1
