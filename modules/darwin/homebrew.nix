{
  config,
  inputs ? { },
  lib,
  pkgs,
  primaryUser ? null,
  ...
}:
let
  inherit (lib)
    mkEnableOption
    mkIf
    mkOption
    optionalAttrs
    types
    ;

  # nix-homebrew currently hardcodes Ruby 3.4, but the repo-pinned Homebrew
  # source requires Ruby 4.0. Keep the patch local while advancing the tested
  # brew/tap tuple explicitly.
  brewSource =
    if
      builtins.hasAttr "nix-homebrew" inputs
      && builtins.hasAttr "inputs" inputs."nix-homebrew"
      && builtins.hasAttr "brew-src" inputs."nix-homebrew".inputs
    then
      inputs."nix-homebrew".inputs.brew-src
    else
      null;

  brewVersion =
    if brewSource != null && brewSource ? rev then
      "HEAD-${builtins.substring 0 7 brewSource.rev}"
    else
      "HEAD";

  patchedBrewPackage =
    if brewSource == null then
      null
    else
      pkgs.runCommandLocal "brew-${brewVersion}-nixcfg-patched" { } ''
        cp -r "${brewSource}" "$out"
        chmod u+w "$out" "$out/Library/Homebrew" "$out/Library/Homebrew/cmd"

        substituteInPlace "$out/Library/Homebrew/cmd/update.sh" \
          --replace-fail 'for DIR in "''${HOMEBREW_REPOSITORY}"' "for DIR in "

        ruby_sh="$out/Library/Homebrew/utils/ruby.sh"
        if [[ ! -e "$ruby_sh" ]]; then
          printf 'Expected Homebrew ruby helper at %s\n' "$ruby_sh" >&2
          exit 1
        fi
        if ! grep -q 'setup-ruby-path' "$ruby_sh"; then
          printf 'Expected setup-ruby-path in %s\n' "$ruby_sh" >&2
          exit 1
        fi
        chmod u+w "$ruby_sh"
        printf '%s\n' 'setup-ruby-path() { export HOMEBREW_RUBY_PATH="${pkgs.ruby_4_0}/bin/ruby"; }' >>"$ruby_sh"

        brew_sh="$out/Library/Homebrew/brew.sh"
        chmod u+w "$brew_sh"
        sed -i -e 's/^HOMEBREW_VERSION=.*/HOMEBREW_VERSION="${brewVersion}"/g' "$brew_sh"
        sed -i -e 's/^GIT_REVISION=.*/GIT_REVISION=""; HOMEBREW_VERSION="${brewVersion}"/g' "$brew_sh"
      '';

  defaultTaps =
    (optionalAttrs (builtins.hasAttr "homebrew-core" inputs) {
      "homebrew/homebrew-core" = inputs."homebrew-core";
    })
    // (optionalAttrs (builtins.hasAttr "homebrew-cask" inputs) {
      "homebrew/homebrew-cask" = inputs."homebrew-cask";
    })
    // (optionalAttrs (builtins.hasAttr "pantsbuild-tap" inputs) {
      "pantsbuild/homebrew-tap" = inputs."pantsbuild-tap";
    });

  cfg = config.nixcfg.darwin.homebrew;
  resolvedUser = if cfg.user != null then cfg.user else primaryUser;
in
{
  options.nixcfg.darwin.homebrew = {
    enable = mkEnableOption "managed nix-homebrew configuration" // {
      default = true;
    };

    user = mkOption {
      type = types.nullOr types.str;
      default = primaryUser;
      description = "User that owns the Homebrew installation.";
    };

    enableRosetta = mkOption {
      type = types.bool;
      default = true;
      description = "Enable Rosetta support in nix-homebrew on Apple Silicon.";
    };

    taps = mkOption {
      type = types.attrsOf types.anything;
      default = defaultTaps;
      description = "Tap attrset passed directly to nix-homebrew.taps.";
    };

    mutableTaps = mkOption {
      type = types.bool;
      default = false;
      description = "Whether Homebrew taps may be modified outside nix-homebrew.";
    };
  };

  config = mkIf cfg.enable {
    assertions = [
      {
        assertion = resolvedUser != null;
        message = "nixcfg.darwin.homebrew.user must be set (or provide primaryUser via lib.mkSystem/lib.mkDarwinHost).";
      }
    ];

    nix-homebrew = {
      enable = true;
      inherit (cfg)
        enableRosetta
        mutableTaps
        taps
        ;
      user = resolvedUser;
    }
    // optionalAttrs (patchedBrewPackage != null) {
      package = patchedBrewPackage;
      patchBrew = false;
    };
  };
}
