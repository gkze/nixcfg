# Behavior of packages/superset/apply-bun-patch.sh, the helper that applies
# Superset's Bun `patchedDependencies` to extracted cache entries. The
# fixture mirrors @xterm/addon-ligatures@0.11.0-beta.310: its published
# package.json has no final newline and the upstream patch's last hunk ends
# at the closing brace.
{ lib, runCommand }:
let
  lines = lib.concatMapStrings (line: line + "\n");
  original = lib.concatStringsSep "\n" [
    "{"
    "  \"main\": \"lib/a.js\","
    "  \"peerDependencies\": {"
    "    \"x\": \"^1\""
    "  }"
    "}"
  ];
  addExports = builtins.toFile "add-exports.patch" (lines [
    "diff --git a/package.json b/package.json"
    "--- a/package.json"
    "+++ b/package.json"
    "@@ -3,4 +3,7 @@"
    "   \"peerDependencies\": {"
    "     \"x\": \"^1\""
    "+  },"
    "+  \"exports\": {"
    "+    \".\": \"./lib/a.js\""
    "   }"
    " }"
  ]);
  expected = builtins.toFile "expected.json" (lines [
    "{"
    "  \"main\": \"lib/a.js\","
    "  \"peerDependencies\": {"
    "    \"x\": \"^1\""
    "  },"
    "  \"exports\": {"
    "    \".\": \"./lib/a.js\""
    "  }"
    "}"
  ]);
  marked = builtins.toFile "marked.patch" (lines [
    "--- a/package.json"
    "+++ b/package.json"
    "@@ -1,6 +1,6 @@"
    " {"
    "-  \"main\": \"lib/a.js\","
    "+  \"main\": \"lib/b.js\","
    "   \"peerDependencies\": {"
    "     \"x\": \"^1\""
    "   }"
    " }"
    "\\ No newline at end of file"
  ]);
  markedExpected = builtins.toFile "marked.json" (
    builtins.replaceStrings [ "lib/a.js" ] [ "lib/b.js" ] original
  );
  fixture = builtins.toFile "package.json" original;
in
runCommand "check-superset-apply-bun-patch" { } ''
  set -euo pipefail
  source ${../../packages/superset/apply-bun-patch.sh}

  fixture() {
    mkdir -p "$1"
    cp ${fixture} "$1/package.json"
    chmod u+w "$1/package.json"
  }

  # GNU patch alone rejects the last hunk at the missing final newline; Bun's
  # line-based application does not.
  fixture plain
  if patch -p1 --batch --forward --fuzz=0 -d plain <${addExports}; then
    echo "fixture no longer reproduces the missing-newline rejection" >&2
    exit 1
  fi

  fixture pkg
  applyBunPatch pkg ${addExports}
  cmp pkg/package.json ${expected}

  # A patch that records the missing newline applies unchanged and leaves
  # the file unterminated.
  fixture marked
  applyBunPatch marked ${marked}
  cmp marked/package.json ${markedExpected}

  # Strict application still fails on real drift.
  fixture drift
  sed -i 's/"\^1"/"^2"/' drift/package.json
  if applyBunPatch drift ${addExports}; then
    echo "drifted context applied" >&2
    exit 1
  fi

  touch "$out"
''
