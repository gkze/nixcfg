{
  lib,
  flakelight,
  src,
}:
let
  system = "aarch64-darwin";
  runCommand = name: attrs: script: { inherit name attrs script; };
  pkgs = {
    inherit runCommand;
    stdenv.hostPlatform.system = system;
  };
  evaluate =
    configurations: darwinSystem:
    (lib.evalModules {
      specialArgs = {
        inherit flakelight;
        moduleArgs = { };
        inputs = {
          nix-darwin.lib = { inherit darwinSystem; };
          example.packages.${system} = [ "example" ];
        };
      };
      modules = [
        (src + "/lib/flakelight-darwin-configurations.nix")
        {
          options = {
            outputs = lib.mkOption { type = lib.types.raw; };
            propagationModule = lib.mkOption {
              type = lib.types.raw;
              default = {
                _fixture = "propagation";
              };
            };
            nixDirAliases = lib.mkOption {
              type = lib.types.attrsOf (lib.types.listOf lib.types.str);
            };
          };
          config.darwinConfigurations = configurations;
        }
      ];
    }).config.outputs;
  lazyOutputs = evaluate {
    workstation = {
      inherit system;
      modules = throw "check discovery forced host modules";
    };
  } (_: throw "check discovery evaluated a Darwin configuration");
  constructedOutputs =
    evaluate
      {
        workstation = {
          inherit system;
          modules = [ { _fixture = "host"; } ];
          specialArgs = {
            hostname = "custom-hostname";
            custom = "preserved";
          };
        };
      }
      (received: {
        inherit pkgs received;
        config.system.build.toplevel = "/nix/store/darwin-system";
      });
  received = constructedOutputs.darwinConfigurations.workstation.received;
  evaluatedConfiguration = {
    inherit pkgs;
    config.system.build.toplevel = "/nix/store/preexisting-system";
  };
  evaluatedOutputs = evaluate {
    workstation = evaluatedConfiguration;
  } (_: throw "an evaluated configuration was evaluated again");
in
assert builtins.attrNames lazyOutputs.checks == [ system ];
assert builtins.attrNames lazyOutputs.checks.${system} == [ "darwin-workstation" ];
assert !(builtins.tryEval lazyOutputs.checks.${system}.darwin-workstation).success;
assert received.system == system;
assert received.specialArgs.hostname == "custom-hostname";
assert received.specialArgs.custom == "preserved";
assert received.specialArgs.inputs'.example.packages == [ "example" ];
assert
  received.modules == [
    { _fixture = "propagation"; }
    { _fixture = "host"; }
  ];
assert
  constructedOutputs.checks.${system}.darwin-workstation == {
    name = "check-darwin-workstation";
    attrs = { };
    script = "echo /nix/store/darwin-system > $out";
  };
assert
  evaluatedOutputs.darwinConfigurations.workstation.config.system.build.toplevel
  == "/nix/store/preexisting-system";
assert
  evaluatedOutputs.checks.${system}.darwin-workstation.script
  == "echo /nix/store/preexisting-system > $out";
true
