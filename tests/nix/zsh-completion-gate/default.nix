{
  pkgs,
  src ? ../../..,
}:
let
  inherit (pkgs) lib;
  # Evaluate the real home module so this check exercises the exact
  # completionInit snippet that ships, not a copied approximation.
  evaluated = lib.evalModules {
    specialArgs = {
      inherit lib pkgs src;
      inherit (pkgs) system;
      slib.srcDirBase = _system: "/work";
    };
    modules = [
      (src + "/modules/home/zsh.nix")
      {
        options = {
          programs.zsh = lib.mkOption {
            type = lib.types.attrsOf lib.types.anything;
            default = { };
          };
          programs.fzf.package = lib.mkOption {
            type = lib.types.package;
            default = pkgs.fzf;
          };
          xdg.configHome = lib.mkOption {
            type = lib.types.str;
            default = "/home/test/.config";
          };
        };
      }
    ];
  };
  completionInit = evaluated.config.programs.zsh.completionInit;
in
pkgs.runCommand "check-test-zsh-completion-gate" { } ''
    export HOME="$NIX_BUILD_TOP/home"
    mkdir -p "$HOME"
    ZDIR="$HOME/.config/zsh"
    mkdir -p "$ZDIR"
    cat > "$ZDIR/gate.zsh" <<'GATE_EOF'
  ${completionInit}
  GATE_EOF
    ZSH=${pkgs.zsh}/bin/zsh

    # 1. Cold start: no dump yet -> full compinit writes dump + fingerprint.
    ZDOTDIR="$ZDIR" $ZSH -f -c 'source $ZDOTDIR/gate.zsh'
    test -s "$ZDIR/.zcompdump"
    test -s "$ZDIR/.zcompdump.fingerprint"
    echo 'ok: cold start wrote dump + fingerprint'

    # 2. Hot start: unchanged content -> compinit -C sources the existing dump
    #    (sentinel proves it) and must not regenerate it.
    printf '# sentinel\nMARKER_HOT=1\n' > "$ZDIR/.zcompdump"
    ZDOTDIR="$ZDIR" $ZSH -f -c 'source $ZDOTDIR/gate.zsh; [[ -n ''${MARKER_HOT:-} ]]'
    grep -q MARKER_HOT "$ZDIR/.zcompdump"
    echo 'ok: hot start sourced the trusted dump without rebuilding'

    # 3. Hot start is read-only-safe: unchanged content requires no writes.
    chmod -R a-w "$ZDIR"
    ZDOTDIR="$ZDIR" $ZSH -f -c 'source $ZDOTDIR/gate.zsh; [[ -n ''${MARKER_HOT:-} ]]'
    chmod -R u+w "$ZDIR"
    echo 'ok: hot start works with a read-only ZDOTDIR'

    # 4. Content change (new completion file) -> exactly one cold rebuild and
    #    a refreshed fingerprint.
    mkdir -p "$ZDIR/extra"
    printf '#compdef fake-tool\n' > "$ZDIR/extra/_fake-tool"
    ZDOTDIR="$ZDIR" $ZSH -f -c 'fpath=($ZDIR/extra $fpath); source $ZDOTDIR/gate.zsh'
    if grep -q MARKER_HOT "$ZDIR/.zcompdump"; then
      echo 'content change did not rebuild the dump' >&2
      exit 1
    fi
    test -s "$ZDIR/.zcompdump.fingerprint"
    echo 'ok: content change ran full compinit and refreshed the fingerprint'

    touch $out
''
