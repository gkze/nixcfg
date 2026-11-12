#!/bin/bash
# TEMPORARY diagnostic for Update 43110633449, round 2, reverted before review.
# PR update prepare runs this instead of the Darwin prepare shards:
#   0: NSWindow.init trap: _nixbld1 vs root vs Aqua, no backtracer, .ips
#   1: openai-cli 1.52.0 Nix build with the committed checkFlags
#   2: committed baseten-switch Nix build (second VM)
#   3: openai-cli at its pinned version
#   4: committed baseten-switch Nix build
set -u
PY="$1"
REPO="$GITHUB_WORKSPACE"
SHARD="${NIXCFG_PREPARE_SHARD:-x}"
W=/private/tmp/diag43110633449
rm -rf "$W"
mkdir -p "$W"

echo "::group::env shard=$SHARD"
sw_vers
id
sysctl -n hw.ncpu hw.memsize
nix config show 2>/dev/null | grep -E '^(sandbox|cores|max-jobs) ' || true
echo "::endgroup::"

window_probes() {
  cd "$W" || return 1
  git clone -q https://github.com/basetenlabs/baseten-switch src || return 1
  cd src && git checkout -q 551f1bd5b601f63d5807842feacfcbb82d5c8c16 || return 1
  for p in nix-managed raw-telemetry-test-clock; do
    patch -p1 --batch -s < "$REPO/packages/baseten-switch/$p.patch" || return 1
  done
  find . -name '*.orig' -delete
  cd mac/BasetenSwitch || return 1
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
  mkdir -p "$W/home" "$W/cc" "$W/mc"
  env HOME="$W/home" CLANG_MODULE_CACHE_PATH="$W/cc" SWIFTPM_MODULECACHE_OVERRIDE="$W/mc" \
    /usr/bin/swift build --build-tests --disable-sandbox --scratch-path "$W/build" 2>&1 | tail -2
  chmod -R a+rwX "$W"
  local bundle xctest marker
  bundle=$(/bin/ls -d "$W"/build/debug/*.xctest | head -1)
  xctest=$(xcrun --find xctest)
  marker="$W/marker"
  touch "$marker"
  local window="BasetenSwitchTests.UpdatePresentationTests/testUpdateWindowCentersUsingItsFinalContentSizeWithoutShowing"
  run_case() { # name user filter
    local name=$1 user=$2 filter=$3
    cat > "$W/$name.sh" <<EOS
#!/bin/bash
export HOME="$W/home-$name" CFFIXED_USER_HOME="$W/home-$name" TMPDIR="$W/tmp-$name/" DEVELOPER_DIR=$DEVELOPER_DIR SWIFT_BACKTRACE=enable=no
mkdir -p "\$HOME" "\$TMPDIR"
echo "whoami=\$(id -un)"
"$xctest" -XCTest "$filter" "$bundle" 2>&1
echo "EXIT=\$?"
EOS
    chmod 0755 "$W/$name.sh"
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
    echo "::group::case $name"
    grep -v "Test Case .* passed" "$W/$name.out" | tail -40
    echo "::endgroup::"
    echo "RESULT $name: $(grep '^EXIT=' "$W/$name.out" | tail -1) $(grep -E 'Executed [0-9]+ test' "$W/$name.out" | tail -1)"
  }
  run_case nixbld-window _nixbld1 "$window"
  run_case root-window root "$window"
  run_case aqua-window aqua "$window"
  sleep 20
  echo "::group::crash reports"
  sudo find /Library/Logs/DiagnosticReports /Users/runner/Library/Logs/DiagnosticReports /var/root/Library/Logs/DiagnosticReports -newer "$marker" -type f 2>/dev/null | while read -r f; do
    echo "== $f"
    sudo "$PY" - "$f" <<'EOP'
import json, sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
head, _, body = text.partition("\n")
try:
    report = json.loads(body)
except ValueError:
    print(text[:6000])
    raise SystemExit
for key in ("procName", "userID", "exception", "termination", "asi", "asiBacktraces", "faultingThread", "crashInfo"):
    if key in report:
        print(key, "=", json.dumps(report[key])[:3000])
thread = report.get("threads", [{}])[report.get("faultingThread", 0)]
images = report.get("usedImages", [])
for frame in thread.get("frames", [])[:30]:
    image = images[frame.get("imageIndex", 0)] if images else {}
    print("  frame", image.get("name"), hex(frame.get("imageOffset", 0)), frame.get("symbol"), "+", frame.get("symbolLocation"))
EOP
  done
  echo "::endgroup::"
}

nix_build() { # attr label
  local start rc
  start=$(date +%s)
  nix build --no-link -L "$1" > "$W/$2.log" 2>&1
  rc=$?
  echo "::group::$2 tail"
  grep -E "Program crashed|Thread [0-9]+ crashed|Executed [0-9]+ test|error:|^[a-z-]+> (ok|FAIL|PASS)|checkPhase completed|Running phase: (check|install)Phase" "$W/$2.log" | tail -30
  echo "::endgroup::"
  echo "RESULT $2 rc=$rc wall=$(( $(date +%s) - start ))s"
}

openai_candidate() {
  cd "$REPO" || return 1
  sed -i '' 's#github:openai/openai-cli/v1.40.0#github:openai/openai-cli/v1.52.0#' flake.nix
  nix flake update openai-cli 2>&1 | tail -1
  "$PY" - <<'EOP'
import json, pathlib
path = pathlib.Path("packages/openai-cli/sources.json")
data = json.loads(path.read_text())
for entry in data["hashes"]:
    if entry["hashType"] == "vendorHash":
        entry["hash"] = "sha256-XIqKtgNdziLrr8vxG05Re2WeGQf9LXF5jKZS2cqELvE="
path.write_text(json.dumps(data, indent=2) + "\n")
EOP
  nix_build .#openai-cli openai-cli-1.52.0
}

case "$SHARD" in
  0) window_probes ;;
  1) openai_candidate ;;
  2) cd "$REPO" && nix_build .#baseten-switch baseten-switch-vm2 ;;
  3) cd "$REPO" && nix_build .#openai-cli openai-cli-pinned ;;
  4) cd "$REPO" && nix_build .#baseten-switch baseten-switch-vm1 ;;
esac
exit 1
