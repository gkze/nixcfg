args:
import ../opencode-desktop-electron/default.nix (
  args
  // {
    pname = "opencode-desktop-electron-dev";
    sourceHashPackageName = "opencode-desktop-electron";
    opencodeChannel = "dev";
    appName = "OpenCode Electron Dev";
    appId = "ai.opencode.desktop.electron-dev";
    appProtocolScheme = "opencode-electron-dev";
    packageDescription = "OpenCode Desktop Electron local dev app";
  }
)
