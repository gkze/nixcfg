{ pkgs }:
if !pkgs.stdenv.buildPlatform.isDarwin then
  assert pkgs.bash-dynamic-pipe-heredoc.drvPath == pkgs.bashNonInteractive.drvPath;
  assert pkgs.gnutar-dynamic-pipe-heredoc.drvPath == pkgs.gnutar.drvPath;
  pkgs.runCommand "check-nix-prefetch-git-darwin-heredoc-skipped" { } ''
    touch $out
  ''
else
  let
    gnutarBuilder = pkgs.gnutar-dynamic-pipe-heredoc.dynamicPipeHeredocBuilder;
  in
  assert pkgs.gnutar-dynamic-pipe-heredoc.builder == gnutarBuilder;
  assert pkgs.lib.hasSuffix "/bin/bash" gnutarBuilder;
  pkgs.runCommand "check-nix-prefetch-git-darwin-heredoc"
    {
      nativeBuildInputs = [
        pkgs.coreutils
        pkgs.gnutar-dynamic-pipe-heredoc
      ];
    }
    ''
      unset BASH_COMPAT DIRENV_BASH

      ${pkgs.python3}/bin/python3 \
        ${./fill_pipe_kva.py} \
        "$TMPDIR/pipe-capacity-reduced" &
      pressure_pid=$!
      trap 'kill "$pressure_pid" 2>/dev/null || true' EXIT

      for _ in $(${pkgs.coreutils}/bin/seq 1 200); do
        if [[ -e "$TMPDIR/pipe-capacity-reduced" ]]; then
          break
        fi
        if ! kill -0 "$pressure_pid" 2>/dev/null; then
          wait "$pressure_pid"
          exit 1
        fi
        ${pkgs.coreutils}/bin/sleep 0.05
      done

      if [[ ! -e "$TMPDIR/pipe-capacity-reduced" ]]; then
        echo >&2 "timed out while inducing reduced Darwin pipe capacity"
        exit 1
      fi

      probe_interpreter() {
        local label="$1"
        local interpreter="$2"

        if [[ ! -x "$interpreter" ]]; then
          echo >&2 "could not resolve the packaged interpreter for $label"
          exit 1
        fi

        set +e
        ${pkgs.coreutils}/bin/timeout 5 "$interpreter" -c \
          'read -r payload <<< "$(printf "%0513d" 0)"; test "''${#payload}" -eq 513'
        status=$?
        set -e

        if [[ "$status" -eq 124 ]]; then
          echo >&2 "$label Bash deadlocked writing a medium here-string"
          exit 1
        elif [[ "$status" -ne 0 ]]; then
          echo >&2 "$label Bash heredoc probe failed with status $status"
          exit "$status"
        fi
      }

      probe_interpreter \
        bash-dynamic-pipe-heredoc \
        ${pkgs.bash-dynamic-pipe-heredoc}/bin/bash
      probe_interpreter \
        gnutar-builder \
        ${gnutarBuilder}
      ${pkgs.gnutar-dynamic-pipe-heredoc}/bin/tar --version >/dev/null

      zed_fixture_src="$TMPDIR/zed-fixture-src"
      zed_fixture_resources="$zed_fixture_src/crates/zed/resources"
      zed_fixture_bin="$TMPDIR/zed-fixture-bin"
      mkdir -p "$zed_fixture_resources/info" "$zed_fixture_bin"
      for plist in SupportedPlatforms Permissions DocumentTypes; do
        : > "$zed_fixture_resources/info/$plist.plist"
      done
      touch \
        "$zed_fixture_resources/app-icon-nightly.png" \
        "$zed_fixture_resources/app-icon-nightly@2x.png" \
        "$zed_fixture_resources/Document.icns" \
        "$zed_fixture_bin/cli" \
        "$zed_fixture_bin/zed"

      set +e
      ${pkgs.coreutils}/bin/timeout 5 \
        ${pkgs.bash-dynamic-pipe-heredoc}/bin/bash \
        ${../../../packages/zed-editor-nightly/install_zed_nightly_app.sh} \
        "$TMPDIR/zed-out" \
        "$TMPDIR" \
        0.0.0-test \
        "$zed_fixture_src" \
        ${pkgs.coreutils}/bin/true \
        ${pkgs.coreutils}/bin/true \
        ${pkgs.coreutils}/bin/true \
        "$zed_fixture_bin/cli" \
        "$zed_fixture_bin/zed"
      status=$?
      set -e

      if [[ "$status" -eq 124 ]]; then
        echo >&2 "Zed's Info.plist heredoc deadlocked under reduced pipe capacity"
        exit 1
      elif [[ "$status" -ne 0 ]]; then
        echo >&2 "Zed's installer heredoc probe failed with status $status"
        exit "$status"
      fi
      ${pkgs.gnugrep}/bin/grep -Fq \
        '<string>dev.zed.Zed-Nightly</string>' \
        "$TMPDIR/zed-out/Applications/Zed Nightly.app/Contents/Info.plist"

      direnv_home="$TMPDIR/direnv-home"
      direnv_project="$TMPDIR/direnv-project"
      mkdir -p \
        "$direnv_home/.config/direnv" \
        "$direnv_home/.config/direnv/lib" \
        "$direnv_home/.local/share" \
        "$direnv_project"
      ln -s \
        ${pkgs.nix-direnv}/share/nix-direnv/direnvrc \
        "$direnv_home/.config/direnv/lib/hm-nix-direnv.sh"
      printf '%s\n' \
        '[global]' \
        'bash_path = "${pkgs.bash-dynamic-pipe-heredoc}/bin/bash"' \
        > "$direnv_home/.config/direnv/direnv.toml"
      printf '%s\n' \
        'export NIX_DIRENV_SKIP_VERSION_CHECK=1' \
        'export NIX_DIRENV_FALLBACK_NIX=${pkgs.coreutils}/bin/false' \
        'if ! _nix_direnv_preflight; then return 1; fi' \
        'reload_helper="$(direnv_layout_dir)/bin/nix-direnv-reload"' \
        '[[ -s "$reload_helper" && -x "$reload_helper" ]]' \
        > "$direnv_project/.envrc"

      HOME="$direnv_home" \
        XDG_CONFIG_HOME="$direnv_home/.config" \
        XDG_DATA_HOME="$direnv_home/.local/share" \
        ${pkgs.direnv}/bin/direnv allow "$direnv_project"

      set +e
      HOME="$direnv_home" \
        XDG_CONFIG_HOME="$direnv_home/.config" \
        XDG_DATA_HOME="$direnv_home/.local/share" \
        ${pkgs.coreutils}/bin/timeout 5 \
        ${pkgs.direnv}/bin/direnv exec "$direnv_project" true
      status=$?
      set -e

      if [[ "$status" -eq 124 ]]; then
        echo >&2 "direnv deadlocked in the nix-direnv preflight heredoc"
        exit 1
      elif [[ "$status" -ne 0 ]]; then
        echo >&2 "direnv configured-Bash probe failed with status $status"
        exit "$status"
      fi

      for script in \
        ${pkgs.nix-prefetch-git}/bin/nix-prefetch-git \
        ${pkgs.nix-prefetch-git}/bin/.nix-prefetch-git-wrapped; do
        interpreter="$(${pkgs.gnused}/bin/sed -nE \
          '1s|^#![[:space:]]*([^[:space:]]+).*$|\1|p' \
          "$script")"
        if [[ -z "$interpreter" || ! -x "$interpreter" ]]; then
          echo >&2 "could not resolve the packaged interpreter for $script"
          exit 1
        fi

        probe_interpreter "$script" "$interpreter"
      done

      touch $out
    ''
