{
  lib,
  pkgs,
  src ? ../..,
}:
let
  homeDirectory = "/Users/test";
  instrumentedCoreutils = pkgs.runCommand "instrumented-coreutils" { } ''
    mkdir -p "$out/bin"
    for command in ${pkgs.coreutils}/bin/*; do
      ln -s "$command" "$out/bin/$(basename "$command")"
    done
    rm "$out/bin/sha256sum"
    cat > "$out/bin/sha256sum" <<'EOF'
    #!${pkgs.runtimeShell}
    printf '%s\n' "$1" >> "$NIXCFG_HASH_CALL_LOG"
    ${pkgs.coreutils}/bin/sha256sum "$@"
    status=$?
    if [ -n "''${NIXCFG_HASH_STARTED:-}" ]; then
      touch "$NIXCFG_HASH_STARTED"
      while [ ! -e "$NIXCFG_HASH_RELEASE" ]; do
        sleep 0.05
      done
    fi
    exit "$status"
    EOF
    chmod +x "$out/bin/sha256sum"
  '';
  instrumentedPerl = pkgs.runCommand "instrumented-perl" { } ''
    mkdir -p "$out/bin"
    cat > "$out/bin/perl" <<'EOF'
    #!${pkgs.runtimeShell}
    ${pkgs.perl}/bin/perl "$@"
    status=$?
    if [ -n "''${NIXCFG_PERL_STARTED:-}" ]; then
      touch "$NIXCFG_PERL_STARTED"
      while [ ! -e "$NIXCFG_PERL_RELEASE" ]; do
        sleep 0.05
      done
    fi
    exit "$status"
    EOF
    chmod +x "$out/bin/perl"
  '';
  testPkgs = pkgs // {
    coreutils = instrumentedCoreutils;
    perl = instrumentedPerl;
  };
  homeModule = import (src + "/home/george/darwin.nix") {
    config = {
      home = { inherit homeDirectory; };
      programs.gpg.homedir = "${homeDirectory}/.gnupg";
      xdg.dataHome = "${homeDirectory}/.local/share";
    };
    lib = lib // {
      hm.dag.entryAfter = _after: script: script;
    };
    pkgs = testPkgs;
  };
  repairAgent = homeModule.launchd.agents.codex-bundled-plugin-repair.config;
  repairProgram = builtins.elemAt repairAgent.ProgramArguments 0;

  assertEq =
    label: expected: actual:
    if expected == actual then
      true
    else
      throw "${label}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";

  launchdChecks = [
    (assertEq "polling interval removed" false (repairAgent ? StartInterval))
    (assertEq "daily fallback schedule" {
      Hour = 4;
      Minute = 0;
    } repairAgent.StartCalendarInterval)
    (assertEq "event-driven repair paths" [
      "${homeDirectory}/Applications/ChatGPT.app"
      "${homeDirectory}/Applications/ChatGPT.app/Contents/Resources/plugins/openai-bundled/.agents/plugins"
      "${homeDirectory}/Applications/ChatGPT.app/Contents/Resources/plugins/openai-bundled/.agents/plugins/marketplace.json"
      "${homeDirectory}/Applications/ChatGPT.app/Contents/Resources/plugins/openai-bundled/plugins/browser/scripts"
      "${homeDirectory}/Applications/ChatGPT.app/Contents/Resources/plugins/openai-bundled/plugins/browser/scripts/browser-client.mjs"
      "${homeDirectory}/Applications/ChatGPT.app/Contents/Resources/plugins/openai-bundled/plugins/chrome/scripts"
      "${homeDirectory}/Applications/ChatGPT.app/Contents/Resources/plugins/openai-bundled/plugins/chrome/scripts/browser-client.mjs"
      "${homeDirectory}/.codex/.tmp/bundled-marketplaces/openai-bundled"
      "${homeDirectory}/.codex/.tmp/bundled-marketplaces/openai-bundled/.agents/plugins"
      "${homeDirectory}/.codex/.tmp/bundled-marketplaces/openai-bundled/.agents/plugins/marketplace.json"
      "${homeDirectory}/.codex/.tmp/bundled-marketplaces/openai-bundled/plugins/browser/scripts"
      "${homeDirectory}/.codex/.tmp/bundled-marketplaces/openai-bundled/plugins/browser/scripts/browser-client.mjs"
      "${homeDirectory}/.codex/.tmp/bundled-marketplaces/openai-bundled/plugins/chrome/scripts"
      "${homeDirectory}/.codex/.tmp/bundled-marketplaces/openai-bundled/plugins/chrome/scripts/browser-client.mjs"
      "${homeDirectory}/.codex/config.toml"
    ] repairAgent.WatchPaths)
  ];
in
builtins.deepSeq launchdChecks (
  pkgs.runCommand "test-codex-bundled-plugin-repair" { } ''
      repair_home="$TMPDIR/home"
      app_root="$repair_home/Applications/ChatGPT.app/Contents/Resources/plugins/openai-bundled"
      tmp_root="$repair_home/.codex/.tmp/bundled-marketplaces/openai-bundled"
      config_file="$repair_home/.codex/config.toml"

      wait_for_file() {
        path=$1
        for _attempt in $(${pkgs.coreutils}/bin/seq 1 200); do
          if [ -e "$path" ]; then
            return 0
          fi
          ${pkgs.coreutils}/bin/sleep 0.05
        done
        return 1
      }

      mkdir -p \
        "$app_root/.agents/plugins" \
        "$app_root/plugins/browser/scripts" \
        "$app_root/plugins/chrome/scripts" \
        "$tmp_root/plugins/browser/scripts" \
        "$tmp_root/plugins/chrome/scripts"

      printf '%s\n' '{"name":"openai-bundled"}' > "$app_root/.agents/plugins/marketplace.json"
      for client in \
        "$app_root/plugins/browser/scripts/browser-client.mjs" \
        "$app_root/plugins/chrome/scripts/browser-client.mjs" \
        "$tmp_root/plugins/browser/scripts/browser-client.mjs" \
        "$tmp_root/plugins/chrome/scripts/browser-client.mjs"
      do
        printf '%s\n' 'generated-browser-client' > "$client"
      done

      mkdir -p "$repair_home/.codex"
      cat > "$config_file" <<'EOF'
    [shell_environment_policy.set]
    NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S = "trusted-existing"
    EOF

      export NIXCFG_HASH_CALL_LOG="$TMPDIR/hash-calls"
      : > "$NIXCFG_HASH_CALL_LOG"
      ${repairProgram} "$repair_home"

      test "$(wc -l < "$NIXCFG_HASH_CALL_LOG")" = 1
      client_hash="$(${pkgs.coreutils}/bin/sha256sum \
        "$app_root/plugins/browser/scripts/browser-client.mjs" \
        | ${pkgs.gawk}/bin/awk '{ print $1 }')"
      ${pkgs.gnugrep}/bin/grep -Fx \
        "NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S = \"trusted-existing,$client_hash\"" \
        "$repair_home/.codex/config.toml"

      manifest_dst="$tmp_root/.agents/plugins/marketplace.json"
      ${pkgs.diffutils}/bin/cmp -s \
        "$app_root/.agents/plugins/marketplace.json" \
        "$manifest_dst"
      ${pkgs.coreutils}/bin/touch --date=@946684800 \
        "$manifest_dst" \
        "$config_file"

      ${repairProgram} "$repair_home"

      test "$(${pkgs.coreutils}/bin/stat --format=%Y "$manifest_dst")" = 946684800
      test "$(${pkgs.coreutils}/bin/stat --format=%Y "$config_file")" = 946684800

      cat > "$config_file" <<'EOF'
    [shell_environment_policy.set]
    NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S = "trusted-existing"
    EOF
      export NIXCFG_PERL_STARTED="$TMPDIR/perl-started"
      export NIXCFG_PERL_RELEASE="$TMPDIR/perl-release"
      rm -f "$NIXCFG_PERL_STARTED" "$NIXCFG_PERL_RELEASE"

      ${repairProgram} "$repair_home" &
      repair_pid=$!
      wait_for_file "$NIXCFG_PERL_STARTED"
      printf '%s\n' '# concurrent-config-edit' >> "$config_file"
      touch "$NIXCFG_PERL_RELEASE"
      wait "$repair_pid"

      ${pkgs.gnugrep}/bin/grep -Fx '# concurrent-config-edit' "$config_file"
      ${pkgs.gnugrep}/bin/grep -Fx \
        'NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S = "trusted-existing"' \
        "$config_file"
      unset NIXCFG_PERL_STARTED NIXCFG_PERL_RELEASE

      client="$app_root/plugins/browser/scripts/browser-client.mjs"
      rm -f \
        "$app_root/plugins/chrome/scripts/browser-client.mjs" \
        "$tmp_root/plugins/browser/scripts/browser-client.mjs" \
        "$tmp_root/plugins/chrome/scripts/browser-client.mjs"
      printf '%s\n' 'original-generated-browser-client' > "$client"
      export NIXCFG_HASH_STARTED="$TMPDIR/hash-started"
      export NIXCFG_HASH_RELEASE="$TMPDIR/hash-release"
      rm -f "$NIXCFG_HASH_STARTED" "$NIXCFG_HASH_RELEASE"

      ${repairProgram} "$repair_home" &
      repair_pid=$!
      wait_for_file "$NIXCFG_HASH_STARTED"
      printf '%s\n' 'replacement-generated-browser-client' > "$client"
      touch "$NIXCFG_HASH_RELEASE"
      wait "$repair_pid"

      ${pkgs.gnugrep}/bin/grep -Fx \
        'NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S = "trusted-existing"' \
        "$config_file"

      touch "$out"
  ''
)
