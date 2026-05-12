args:
import ../opencode-desktop/default.nix (
  args
  // {
    pname = "opencode-desktop-dev";
    sourceHashPackageName = "opencode-desktop";
    opencodeChannel = "dev";
    appName = "OpenCode Desktop Dev";
    appId = "ai.opencode.desktop.dev";
    appProtocolScheme = "opencode";
    packageDescription = "OpenCode Desktop local dev app";
  }
)
