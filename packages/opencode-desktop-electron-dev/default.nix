args:
import ../opencode-desktop-electron/default.nix (
  args
  // {
    pname = "opencode-desktop-electron-dev";
    sourceHashPackageName = "opencode-desktop-electron";
    opencodeChannel = "dev";
    appName = "OpenCode Dev";
    appId = "ai.opencode.desktop.dev";
    appProtocolScheme = "opencode";
    packageDescription = "OpenCode Desktop local dev app";
  }
)
