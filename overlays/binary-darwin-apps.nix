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
  antigravity = callDarwinAppPackage "antigravity";
  arc = callDarwinAppPackage "arc";
  claude = callDarwinAppPackage "claude";
  claude-code = callDarwinAppPackage "claude-code";
  cleanshot = callDarwinAppPackage "cleanshot";
  codeedit = callDarwinAppPackage "codeedit";
  comet = callDarwinAppPackage "comet";
  docker-desktop = callDarwinAppPackage "docker-desktop";
  figma = callDarwinAppPackage "figma";
  freelens = callDarwinAppPackage "freelens";
  framer = callDarwinAppPackage "framer";
  ghostty-tip = callDarwinAppPackage "ghostty-tip";
  google-drive = callDarwinAppPackage "google-drive";
  keepingyouawake = callDarwinAppPackage "keepingyouawake";
  linear = callDarwinAppPackage "linear";
  lm-studio = callDarwinAppPackage "lm-studio";
  logi-options-plus = callDarwinAppPackage "logi-options-plus";
  loom = callDarwinAppPackage "loom";
  macai = callDarwinAppPackage "macai";
  macfuse = callDarwinAppPackage "macfuse";
  mole-app = callDarwinAppPackage "mole-app";
  nordvpn = callDarwinAppPackage "nordvpn";
  onepassword = callDarwinAppPackage "onepassword";
  pants-preview = callDarwinAppPackage "pants-preview";
  rio = callDarwinAppPackage "rio";
  signal-beta = callDarwinAppPackage "signal-beta";
  spotify = callDarwinAppPackage "spotify";
  tailscale-app = callDarwinAppPackage "tailscale-app";
  warp-preview = callDarwinAppPackage "warp-preview";
  wave = callDarwinAppPackage "wave";
  yaak-beta = callDarwinAppPackage "yaak-beta";
}
