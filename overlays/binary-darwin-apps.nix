{
  final,
  inputs,
  outputs,
  sources,
  ...
}:
let
  callDarwinAppPackage =
    name:
    final.callPackage ../packages/${name} {
      inherit inputs outputs;
      selfSource = sources.${name};
    };

  # SelfSource-backed binary Darwin apps exported through the shared helper.
  # Keep one name per entry so additions and renames stay single-string.
  selfSourceDarwinAppNames = [
    "agentastic-dev"
    "agentlog"
    "airfoil"
    "antigravity"
    "ara"
    "arc"
    "aside"
    "baseten-switch"
    "bb"
    "buzz"
    "capy"
    "claude"
    "claude-code"
    "cleanshot"
    "clearly"
    "coast-local"
    "codeedit"
    "cogito"
    "comet"
    "docker-desktop"
    "energy"
    "executor"
    "factory"
    "figma"
    "framer"
    "freelens"
    "gemini"
    "ghostty-tip"
    "github-copilot-app"
    "gooeypi"
    "google-drive"
    "goose-desktop"
    "grok-bot"
    "grok-build"
    "hermes-desktop"
    "hq"
    "jacq"
    "keepingyouawake"
    "linear"
    "logi-options-plus"
    "loom"
    "macai"
    "macfuse"
    "mach-studio"
    "mole-app"
    "nordvpn"
    "onepassword"
    "openchamber"
    "pants-preview"
    "paseo"
    "pica"
    "reflect-open"
    "screen-studio"
    "signal-beta"
    "solo"
    "spotify"
    "superconductor"
    "tailscale-app"
    "tembo"
    "todoist-desktop"
    "tolaria"
    "unsloth"
    "voiceos"
    "waku"
    "warp-preview"
    "wave"
    "writer-computer"
    "yaak-beta"
    "zeron"
    "zo"
  ];
in
builtins.listToAttrs (
  map (name: {
    inherit name;
    value = callDarwinAppPackage name;
  }) selfSourceDarwinAppNames
)
// {
  # Not selfSource-backed; wired explicitly.
  claude-code-url-handler = final.callPackage ../packages/claude-code-url-handler { };
}
