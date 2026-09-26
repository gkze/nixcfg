{
  lib,
  src ? ../..,
}:
let
  assertEq =
    label: expected: actual:
    if expected == actual then
      true
    else
      throw "${label}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";

  stubOptions =
    { ... }:
    {
      options = {
        home = {
          homeDirectory = lib.mkOption {
            type = lib.types.str;
            default = "/Users/test";
          };
          sessionPath = lib.mkOption {
            type = lib.types.listOf lib.types.str;
            default = [ ];
          };
        };
        programs = {
          bun.enable = lib.mkOption {
            type = lib.types.bool;
            default = false;
          };
          go.enable = lib.mkOption {
            type = lib.types.bool;
            default = false;
          };
        };
      };
    };

  evalModule =
    module: settings:
    (lib.evalModules {
      modules = [
        stubOptions
        module
        settings
      ];
    }).config.home.sessionPath;

  languageModule = name: import (src + "/modules/home/languages/${name}.nix");
  enabled = name: evalModule (languageModule name) { nixcfg.languages.${name}.enable = true; };
in
lib.all lib.id [
  (assertEq "bun: real bun bin first, then isolated global bins" [
    "$HOME/.bun/bin"
    "$HOME/.bun/install/global/node_modules/.bin"
  ] (enabled "bun"))
  (assertEq "go: unchanged by extraPaths" [ "/Users/test/go/bin" ] (enabled "go"))
  (assertEq "rust: unchanged by extraPaths" [ "$HOME/.cargo/bin" ] (enabled "rust"))
  (assertEq "bun: disabled adds nothing" [ ] (
    evalModule (languageModule "bun") { nixcfg.languages.bun.enable = false; }
  ))
]
