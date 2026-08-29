"""Make Executor's updater and mutable service ownership fail closed under Nix."""

import argparse
from dataclasses import dataclass
from pathlib import Path

from lib.exact_text_patch import ExactTextPatch, plan_exact_text_patches

_MAIN_SOURCE = Path("apps/desktop/src/main/index.ts")
_SERVICE_SOURCE = Path("apps/desktop/src/main/service.ts")
_CRASH_SCREEN_SOURCE = Path("apps/desktop/src/main/crash-screen.ts")
_CLI_SOURCE = Path("apps/cli/src/main.ts")
_UPDATE_CHECK_SOURCE = Path("packages/core/api/src/update-check.ts")
_PATCH_SENTINEL = 'const NIX_MANAGED_MESSAGE = "Updates are managed by Nix.";'
_CLI_COMPILED_BINARY_MESSAGE = (
    r"            `\`${command}\` requires the compiled "
    r"\`executor\` binary so the OS can run it directly.`,"
    "\n"
)
_CLI_DEV_CHECKOUT_MESSAGE = (
    r"            `In a dev checkout, run \`${cliPrefix} daemon run --foreground\` "
    "instead.`,\n"
)


@dataclass(frozen=True, slots=True)
class _SourcePatch:
    path: Path
    old: str
    new: str


_PATCHES = (
    _SourcePatch(
        _MAIN_SOURCE,
        """import updater from "electron-updater";
const { autoUpdater } = updater;
""",
        """import updater from "electron-updater";
const { autoUpdater } = updater;
const NIX_MANAGED = true;
const NIX_MANAGED_MESSAGE = "Updates are managed by Nix.";
""",
    ),
    _SourcePatch(
        _MAIN_SOURCE,
        """const ensureSupervisedConnection = async (): Promise<SidecarConnection | null> => {
  // 1. Already running → attach.
""",
        """const ensureSupervisedConnection = async (): Promise<SidecarConnection | null> => {
  if (NIX_MANAGED) return null;
  // 1. Already running → attach.
""",
    ),
    _SourcePatch(
        _MAIN_SOURCE,
        """    "executor:service:set-enabled",
    async (_evt, enabled: unknown): Promise<boolean> => {
      if (typeof enabled !== "boolean") return false;
""",
        """    "executor:service:set-enabled",
    async (_evt, enabled: unknown): Promise<boolean> => {
      if (NIX_MANAGED) return false;
      if (typeof enabled !== "boolean") return false;
""",
    ),
    _SourcePatch(
        _MAIN_SOURCE,
        (
            '  ipcMain.handle("executor:server:rotate-token", '
            "async (): Promise<DesktopServerConnection> => {"
            """
    rotateServerToken();
    if (connection?.supervisedDaemon) {
      const previous = connection;
      await restartSupervisedService();
      const active = (await waitForSupervisedAttach(15_000)) ?? previous;
      connection = active;
      installBearerAuthHeader(active.baseUrl, active.authToken);
      const window = liveMainWindow();
      if (window) await window.loadURL(webUrlForConnection(active));
      return toDesktopServerConnection(active);
    }
    return restartSidecarAndReload();
  });
"""
        ),
        (
            '  ipcMain.handle("executor:server:rotate-token", '
            "async (): Promise<DesktopServerConnection> => {"
            """
    if (NIX_MANAGED && connection?.supervisedDaemon) {
      throw new Error("Nix-managed Executor cannot rotate a supervised daemon token from the app.");
    }
    rotateServerToken();
    if (connection?.supervisedDaemon) {
      const previous = connection;
      await restartSupervisedService();
      const active = (await waitForSupervisedAttach(15_000)) ?? previous;
      connection = active;
      installBearerAuthHeader(active.baseUrl, active.authToken);
      const window = liveMainWindow();
      if (window) await window.loadURL(webUrlForConnection(active));
      return toDesktopServerConnection(active);
    }
    return restartSidecarAndReload();
  });
"""
        ),
    ),
    _SourcePatch(
        _MAIN_SOURCE,
        """  ipcMain.handle("executor:state:reset", async (): Promise<boolean> => {
    if (!(await confirmResetState())) return false;
    if (connection) {
      await stopConnection(connection);
      connection = null;
    }
    const { backupDir } = resetExecutorState();
    await restartSidecarAndReload();
    await announceBackup(backupDir);
    return true;
  });
""",
        """  ipcMain.handle("executor:state:reset", async (): Promise<boolean> => {
    if (NIX_MANAGED) {
      throw new Error("Nix-managed Executor cannot reset data from the app.");
    }
    if (!(await confirmResetState())) return false;
    if (connection) {
      await stopConnection(connection);
      connection = null;
    }
    const { backupDir } = resetExecutorState();
    await restartSidecarAndReload();
    await announceBackup(backupDir);
    return true;
  });
""",
    ),
    _SourcePatch(
        _MAIN_SOURCE,
        """    buttons: ["Quit", "Reset data and retry…"],
""",
        """    buttons: NIX_MANAGED ? ["Quit"] : ["Quit", "Reset data and retry…"],
""",
    ),
    _SourcePatch(
        _MAIN_SOURCE,
        """  const retryAfterReset = response === 1 && (await confirmResetState());
""",
        """  const retryAfterReset =
    !NIX_MANAGED && response === 1 && (await confirmResetState());
""",
    ),
    _SourcePatch(
        _MAIN_SOURCE,
        """  ipcMain.handle(UPDATE_INSTALL_CHANNEL, async (): Promise<void> => {
    const version = "version" in updateStatus ? updateStatus.version : "";
""",
        """  ipcMain.handle(UPDATE_INSTALL_CHANNEL, async (): Promise<void> => {
    if (NIX_MANAGED) return;
    const version = "version" in updateStatus ? updateStatus.version : "";
""",
    ),
    _SourcePatch(
        _MAIN_SOURCE,
        """const applyFakeUpdateFromEnv = () => {
  // Never honor the fake seam in a packaged build: a stray env var would seed a
""",
        """const applyFakeUpdateFromEnv = () => {
  if (NIX_MANAGED) return;
  // Never honor the fake seam in a packaged build: a stray env var would seed a
""",
    ),
    _SourcePatch(
        _MAIN_SOURCE,
        """const promptInstallUpdate = async (version: string) => {
  if (updateDialogOpen) return;
""",
        """const promptInstallUpdate = async (version: string) => {
  if (NIX_MANAGED) return;
  if (updateDialogOpen) return;
""",
    ),
    _SourcePatch(
        _MAIN_SOURCE,
        """const setupAutoUpdater = () => {
  if (!app.isPackaged) return;
""",
        """const setupAutoUpdater = () => {
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = false;
  if (NIX_MANAGED) return;
  if (!app.isPackaged) return;
""",
    ),
    _SourcePatch(
        _MAIN_SOURCE,
        """const runUpdateCheck = async ({ alertOnFail, trigger }: UpdateCheckOptions) => {
  if (!app.isPackaged) {
""",
        """const runUpdateCheck = async ({ alertOnFail, trigger }: UpdateCheckOptions) => {
  if (NIX_MANAGED) {
    if (alertOnFail) {
      await dialog.showMessageBox({
        type: "info",
        title: "Updates managed by Nix",
        message: NIX_MANAGED_MESSAGE,
      });
    }
    return;
  }
  if (!app.isPackaged) {
""",
    ),
    _SourcePatch(
        _MAIN_SOURCE,
        """const handleFatalSidecarFailure = async (error: unknown) => {
  showCrashScreen();
  if (app.isPackaged) {
""",
        """const handleFatalSidecarFailure = async (error: unknown) => {
  showCrashScreen();
  if (app.isPackaged && !NIX_MANAGED) {
""",
    ),
    _SourcePatch(
        _MAIN_SOURCE,
        """      {
        label: "Check for Updates…",
        click: () => void runUpdateCheck({ alertOnFail: true, trigger: "manual" }),
      },
""",
        """      {
        label: "Updates managed by Nix",
        enabled: false,
      },
""",
    ),
    _SourcePatch(
        _SERVICE_SOURCE,
        """const serviceLog = log.scope("service");
const execFileAsync = promisify(execFile);
""",
        """const serviceLog = log.scope("service");
const execFileAsync = promisify(execFile);
const NIX_MANAGED = true;
""",
    ),
    _SourcePatch(
        _SERVICE_SOURCE,
        """export const supervisedServiceStatus = async (): Promise<SupervisedServiceStatus> => {
  if (!executorAvailable()) return { supported: false, registered: false, running: false };
""",
        """export const supervisedServiceStatus = async (): Promise<SupervisedServiceStatus> => {
  if (NIX_MANAGED) return { supported: false, registered: false, running: false };
  if (!executorAvailable()) return { supported: false, registered: false, running: false };
""",
    ),
    _SourcePatch(
        _SERVICE_SOURCE,
        """export const installSupervisedService = async (opts: InstallOptions): Promise<void> => {
  if (!executorAvailable()) {
""",
        """export const installSupervisedService = async (opts: InstallOptions): Promise<void> => {
  if (NIX_MANAGED) {
    throw new Error("Nix-managed Executor cannot install a mutable background service.");
  }
  if (!executorAvailable()) {
""",
    ),
    _SourcePatch(
        _CLI_SOURCE,
        """const supervisedServiceOrigin = (port: number): string => `http://127.0.0.1:${port}`;
""",
        """const NIX_MANAGED = true;

const supervisedServiceOrigin = (port: number): string => `http://127.0.0.1:${port}`;
""",
    ),
    _SourcePatch(
        _CLI_SOURCE,
        (
            """const installService = (port: number, commandName: string, boot = false) =>
  Effect.gen(function* () {
    const command = `${cliPrefix} ${commandName}`;
    if (isDevMode) {
      return yield* Effect.fail(
        new Error(
          [
"""
            + _CLI_COMPILED_BINARY_MESSAGE
            + _CLI_DEV_CHECKOUT_MESSAGE
            + """          ].join("\\n"),
        ),
      );
    }

    const backend = getServiceBackend();
"""
        ),
        (
            """const installService = (port: number, commandName: string, boot = false) =>
  Effect.gen(function* () {
    if (NIX_MANAGED) {
      return yield* Effect.fail(
        new Error("Nix-managed Executor cannot install a mutable background service."),
      );
    }
    const command = `${cliPrefix} ${commandName}`;
    if (isDevMode) {
      return yield* Effect.fail(
        new Error(
          [
"""
            + _CLI_COMPILED_BINARY_MESSAGE
            + _CLI_DEV_CHECKOUT_MESSAGE
            + """          ].join("\\n"),
        ),
      );
    }

    const backend = getServiceBackend();
"""
        ),
    ),
    _SourcePatch(
        _CLI_SOURCE,
        """const serviceUninstallCommand = Command.make("uninstall", {}, () =>
  Effect.gen(function* () {
    const backend = getServiceBackend();
""",
        """const serviceUninstallCommand = Command.make("uninstall", {}, () =>
  Effect.gen(function* () {
    if (NIX_MANAGED) {
      return yield* Effect.fail(
        new Error("Nix-managed Executor cannot uninstall a mutable background service."),
      );
    }
    const backend = getServiceBackend();
""",
    ),
    _SourcePatch(
        _CLI_SOURCE,
        """const serviceRestartCommand = Command.make("restart", {}, () =>
  Effect.gen(function* () {
    const backend = getServiceBackend();
""",
        """const serviceRestartCommand = Command.make("restart", {}, () =>
  Effect.gen(function* () {
    if (NIX_MANAGED) {
      return yield* Effect.fail(
        new Error("Nix-managed Executor cannot restart a mutable background service."),
      );
    }
    const backend = getServiceBackend();
""",
    ),
    _SourcePatch(
        _CRASH_SCREEN_SOURCE,
        """        <button id="update" class="secondary">Check for updates</button>
""",
        """        <span class="secondary">Updates are managed by Nix.</span>
""",
    ),
    _SourcePatch(
        _CRASH_SCREEN_SOURCE,
        """      document.getElementById("update").addEventListener("click", async () => {
        status.textContent = "Checking for updates\\\\u2026";
        try {
          // Outcomes surface as native dialogs (install prompt / no updates).
          await window.executor.checkForUpdates();
          status.textContent = "";
        } catch {
          status.textContent = "Update check failed \\\\u2014 check your network.";
        }
      });
""",
        "",
    ),
    _SourcePatch(
        _UPDATE_CHECK_SOURCE,
        """export const EXECUTOR_PACKAGE_NAME = "executor";

/** Lightweight dist-tags-only registry endpoint (not the full packument). */
""",
        """export const EXECUTOR_PACKAGE_NAME = "executor";
const NIX_MANAGED = true;

/** Lightweight dist-tags-only registry endpoint (not the full packument). */
""",
    ),
    _SourcePatch(
        _UPDATE_CHECK_SOURCE,
        (
            "export const resolveDistTags = async (options?: ResolveDistTagsOptions): "
            "Promise<DistTags> => {\n"
            "  const env = options?.env ?? ambientEnv();\n"
        ),
        (
            "export const resolveDistTags = async (options?: ResolveDistTagsOptions): "
            "Promise<DistTags> => {\n"
            "  if (NIX_MANAGED) return {};\n"
            "  const env = options?.env ?? ambientEnv();\n"
        ),
    ),
)


def patch_tree(source_root: Path) -> None:
    """Disable all updater and app-owned mutable service paths atomically."""
    sources: dict[Path, str] = {}
    for relative in dict.fromkeys(patch.path for patch in _PATCHES):
        path = source_root / relative
        source = path.read_text(encoding="utf-8")
        if _PATCH_SENTINEL in source:
            msg = "Executor source-policy patch was already applied"
            raise RuntimeError(msg)
        sources[relative] = source

    patched_sources = plan_exact_text_patches(
        sources,
        (ExactTextPatch(patch.path, patch.old, patch.new) for patch in _PATCHES),
        mismatch_message=lambda _patch, count: (
            f"expected one Executor source-policy anchor, found {count}"
        ),
    )

    for relative, patched in patched_sources.items():
        (source_root / relative).write_text(patched, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Apply the Nix source-policy patch to one Executor source tree."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    args = parser.parse_args(argv)
    patch_tree(args.source_root)
    return 0


if __name__ == "__main__":  # pragma: no cover -- packaged CLI guard
    raise SystemExit(main())
