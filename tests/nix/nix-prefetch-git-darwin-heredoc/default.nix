{ pkgs }:
if !pkgs.stdenv.hostPlatform.isDarwin then
  pkgs.runCommand "check-nix-prefetch-git-darwin-heredoc-skipped" { } ''
    touch $out
  ''
else
  pkgs.runCommand "check-nix-prefetch-git-darwin-heredoc"
    {
      nativeBuildInputs = [ pkgs.coreutils ];
    }
    ''
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

        set +e
        ${pkgs.coreutils}/bin/timeout 5 "$interpreter" -c \
          'read -r payload <<< "$(printf "%0513d" 0)"; test "''${#payload}" -eq 513'
        status=$?
        set -e

        if [[ "$status" -eq 124 ]]; then
          echo >&2 "$script Bash deadlocked writing a medium here-string"
          exit 1
        elif [[ "$status" -ne 0 ]]; then
          echo >&2 "$script Bash heredoc probe failed with status $status"
          exit "$status"
        fi
      done

      touch $out
    ''
