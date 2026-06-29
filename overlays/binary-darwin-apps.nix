{
  final,
  sources,
  ...
}:
let
  callDarwinAppPackage =
    name:
    final.callPackage ../packages/${name} {
      selfSource = sources.${name};
    };
in
{
  airfoil = callDarwinAppPackage "airfoil";
  agentastic-dev = callDarwinAppPackage "agentastic-dev";
  antigravity = callDarwinAppPackage "antigravity";
  arc = callDarwinAppPackage "arc";
  ara = callDarwinAppPackage "ara";
  claude = callDarwinAppPackage "claude";
  claude-code = callDarwinAppPackage "claude-code";
  cleanshot = callDarwinAppPackage "cleanshot";
  codeedit = callDarwinAppPackage "codeedit";
  cogito = callDarwinAppPackage "cogito";
  comet = callDarwinAppPackage "comet";
  docker-desktop = callDarwinAppPackage "docker-desktop";
  figma = callDarwinAppPackage "figma";
  freelens = callDarwinAppPackage "freelens";
  framer = callDarwinAppPackage "framer";
  ghostty-tip = callDarwinAppPackage "ghostty-tip";
  google-drive = callDarwinAppPackage "google-drive";
  goose-desktop = callDarwinAppPackage "goose-desktop";
  jacq = callDarwinAppPackage "jacq";
  keepingyouawake = callDarwinAppPackage "keepingyouawake";
  linear = callDarwinAppPackage "linear";
  logi-options-plus = callDarwinAppPackage "logi-options-plus";
  loom = callDarwinAppPackage "loom";
  macai = callDarwinAppPackage "macai";
  macfuse = callDarwinAppPackage "macfuse";
  mole-app = callDarwinAppPackage "mole-app";
  nordvpn = callDarwinAppPackage "nordvpn";
  onepassword = callDarwinAppPackage "onepassword";
  pants-preview = callDarwinAppPackage "pants-preview";
  pica = callDarwinAppPackage "pica";
  rio = callDarwinAppPackage "rio";
  signal-beta = callDarwinAppPackage "signal-beta";
  solo = callDarwinAppPackage "solo";
  spotify = callDarwinAppPackage "spotify";
  superconductor = callDarwinAppPackage "superconductor";
  tailscale-app = callDarwinAppPackage "tailscale-app";
  todoist-desktop = callDarwinAppPackage "todoist-desktop";
  tolaria = callDarwinAppPackage "tolaria";
  warp-preview = callDarwinAppPackage "warp-preview";
  wave = callDarwinAppPackage "wave";
  yaak-beta = callDarwinAppPackage "yaak-beta";
}
