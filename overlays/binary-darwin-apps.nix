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
in
{
  agentastic-dev = callDarwinAppPackage "agentastic-dev";
  agentlog = callDarwinAppPackage "agentlog";
  airfoil = callDarwinAppPackage "airfoil";
  antigravity = callDarwinAppPackage "antigravity";
  arc = callDarwinAppPackage "arc";
  ara = callDarwinAppPackage "ara";
  aside = callDarwinAppPackage "aside";
  baseten-switch = callDarwinAppPackage "baseten-switch";
  bb = callDarwinAppPackage "bb";
  buzz = callDarwinAppPackage "buzz";
  claude = callDarwinAppPackage "claude";
  claude-code = callDarwinAppPackage "claude-code";
  claude-code-url-handler = final.callPackage ../packages/claude-code-url-handler { };
  cleanshot = callDarwinAppPackage "cleanshot";
  clearly = callDarwinAppPackage "clearly";
  coast-local = callDarwinAppPackage "coast-local";
  codeedit = callDarwinAppPackage "codeedit";
  cogito = callDarwinAppPackage "cogito";
  comet = callDarwinAppPackage "comet";
  docker-desktop = callDarwinAppPackage "docker-desktop";
  energy = callDarwinAppPackage "energy";
  executor = callDarwinAppPackage "executor";
  factory = callDarwinAppPackage "factory";
  figma = callDarwinAppPackage "figma";
  freelens = callDarwinAppPackage "freelens";
  framer = callDarwinAppPackage "framer";
  gemini = callDarwinAppPackage "gemini";
  ghostty-tip = callDarwinAppPackage "ghostty-tip";
  google-drive = callDarwinAppPackage "google-drive";
  gooeypi = callDarwinAppPackage "gooeypi";
  goose-desktop = callDarwinAppPackage "goose-desktop";
  grok-bot = callDarwinAppPackage "grok-bot";
  grok-build = callDarwinAppPackage "grok-build";
  github-copilot-app = callDarwinAppPackage "github-copilot-app";
  hermes-desktop = callDarwinAppPackage "hermes-desktop";
  hq = callDarwinAppPackage "hq";
  jacq = callDarwinAppPackage "jacq";
  keepingyouawake = callDarwinAppPackage "keepingyouawake";
  linear = callDarwinAppPackage "linear";
  logi-options-plus = callDarwinAppPackage "logi-options-plus";
  loom = callDarwinAppPackage "loom";
  macai = callDarwinAppPackage "macai";
  macfuse = callDarwinAppPackage "macfuse";
  mach-studio = callDarwinAppPackage "mach-studio";
  mole-app = callDarwinAppPackage "mole-app";
  nordvpn = callDarwinAppPackage "nordvpn";
  onepassword = callDarwinAppPackage "onepassword";
  openchamber = callDarwinAppPackage "openchamber";
  pants-preview = callDarwinAppPackage "pants-preview";
  paseo = callDarwinAppPackage "paseo";
  pica = callDarwinAppPackage "pica";
  reflect-open = callDarwinAppPackage "reflect-open";
  screen-studio = callDarwinAppPackage "screen-studio";
  signal-beta = callDarwinAppPackage "signal-beta";
  solo = callDarwinAppPackage "solo";
  spotify = callDarwinAppPackage "spotify";
  superconductor = callDarwinAppPackage "superconductor";
  tailscale-app = callDarwinAppPackage "tailscale-app";
  tembo = callDarwinAppPackage "tembo";
  todoist-desktop = callDarwinAppPackage "todoist-desktop";
  tolaria = callDarwinAppPackage "tolaria";
  unsloth = callDarwinAppPackage "unsloth";
  voiceos = callDarwinAppPackage "voiceos";
  waku = callDarwinAppPackage "waku";
  warp-preview = callDarwinAppPackage "warp-preview";
  wave = callDarwinAppPackage "wave";
  writer-computer = callDarwinAppPackage "writer-computer";
  yaak-beta = callDarwinAppPackage "yaak-beta";
  zeron = callDarwinAppPackage "zeron";
  zo = callDarwinAppPackage "zo";
}
