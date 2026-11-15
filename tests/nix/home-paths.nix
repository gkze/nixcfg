{
  lib,
  src ? ../..,
}:
# The `nixcfg.paths` owner has two platform behaviours worth freezing: on Darwin
# it must refuse to compete with `modules/darwin/paths.nix` for PATH, and
# everywhere else it stays the Home Manager PATH writer. Assertion messages are
# the observable failure surface, so the checks below count failing assertions
# instead of re-deriving the malformed-entry rule.
let
  inherit (import ./lib.nix) assertEq;

  slib = import (src + "/lib/lib.nix") {
    inherit lib src;
    inputs = { };
    outputs = { };
    pkgsFor = { };
  };

  evalPaths =
    {
      system,
      user ? [ ],
      sessionPath ? [ ],
    }:
    (lib.evalModules {
      specialArgs = {
        inherit slib;
        pkgs = {
          stdenv.hostPlatform.system = system;
        };
      };
      modules = [
        {
          # Home Manager and nix-darwin both collect module assertions here.
          options.assertions = lib.mkOption {
            type = lib.types.listOf lib.types.unspecified;
            default = [ ];
          };
          options.home = {
            homeDirectory = lib.mkOption {
              type = lib.types.str;
              default = "/Users/test";
            };
            sessionPath = lib.mkOption {
              type = lib.types.listOf lib.types.str;
              default = [ ];
            };
          };
        }
        (src + "/modules/home/paths.nix")
        {
          nixcfg.paths.user = user;
          home.sessionPath = sessionPath;
        }
      ];
    }).config;

  failedAssertions =
    config:
    map (assertion: assertion.message) (lib.filter (assertion: !assertion.assertion) config.assertions);

  darwin =
    user: sessionPath:
    evalPaths {
      system = "aarch64-darwin";
      inherit user sessionPath;
    };
  linux =
    user:
    evalPaths {
      system = "x86_64-linux";
      inherit user;
    };
in
lib.all lib.id [
  (assertEq "darwin: the darwin PATH owner keeps home.sessionPath empty" [ ]
    (darwin [ "$HOME/.local/bin" "/custom/bin" ] [ ]).home.sessionPath
  )
  (assertEq "darwin: user entries stay as authored" [
    "$HOME/.local/bin"
  ] (darwin [ "$HOME/.local/bin" ] [ ]).nixcfg.paths.user)
  (assertEq "darwin: a second PATH source is rejected" 1 (
    builtins.length (failedAssertions (darwin [ ] [ "/extra/bin" ]))
  ))
  (assertEq "darwin: well-formed unique entries pass every assertion" [ ] (
    failedAssertions (darwin [ "$HOME/.local/bin" "/custom/bin" ] [ ])
  ))
  (assertEq "darwin: repeated entries are rejected" 1 (
    builtins.length (failedAssertions (darwin [ "/dup/bin" "/dup/bin" ] [ ]))
  ))
  (assertEq "darwin: relative entries are rejected" 1 (
    builtins.length (failedAssertions (darwin [ "relative/bin" ] [ ]))
  ))
  (assertEq "darwin: trailing-slash entries are rejected" 1 (
    builtins.length (failedAssertions (darwin [ "/custom/bin/" ] [ ]))
  ))
  (assertEq "non-darwin: home.sessionPath carries the same list verbatim" [
    "$HOME/.bin"
  ] (linux [ "$HOME/.bin" ]).home.sessionPath)
  (assertEq "non-darwin: a trailing-slash entry is still rejected" 1 (
    builtins.length (failedAssertions (linux [ "/custom/bin/" ]))
  ))
]
