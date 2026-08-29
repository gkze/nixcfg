import { EventEmitter } from "node:events";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReconcileCliDeps } from "../../cli/cli-reconcile";

const releaseVersion = "1.2.0";

class FakeAutoUpdater extends EventEmitter {
  logger: unknown = null;
  autoDownload = false;
  autoInstallOnAppQuit = false;
  allowPrerelease = true;
  requestHeaders: Record<string, string> | null = null;
  readonly setFeedURL = vi.fn();
  readonly checkForUpdates = vi.fn(() => Promise.resolve(null));
  readonly downloadUpdate = vi.fn(() => Promise.resolve([]));
  readonly quitAndInstall = vi.fn();
}

async function loadManagedUpdater() {
  vi.resetModules();
  const autoUpdater = new FakeAutoUpdater();
  const persist = vi.fn(() => Promise.resolve(true));
  const preferences = { allowPrerelease: false };
  vi.doMock("electron", () => ({ app: { getVersion: () => releaseVersion } }));
  vi.doMock("electron-updater", () => ({ autoUpdater }));
  vi.doMock("../../notifications", () => ({
    showSimpleNotification: vi.fn(),
  }));
  vi.doMock("../logger", () => ({
    log: {
      debug: vi.fn(),
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
    },
  }));
  vi.doMock("../update-preferences", () => ({
    hydrateUpdatePreferences: vi.fn(() => Promise.resolve(preferences)),
    prereleaseUpdatesEnabled: vi.fn(() => preferences.allowPrerelease),
    setPrereleaseUpdatesEnabled: persist,
  }));
  vi.doMock("../linux-update-guidance", () => ({
    readLinuxPackageType: vi.fn(() => null),
    resolveLinuxSilentInstallSupported: vi.fn(() => Promise.resolve(false)),
    buildLinuxUpdateGuidance: vi.fn(() => null),
    isLinuxEscalationError: vi.fn(() => false),
  }));
  return {
    autoUpdater,
    persist,
    updater: await import("../updater"),
  };
}

afterEach(() => {
  vi.resetModules();
  vi.restoreAllMocks();
  vi.doUnmock("electron");
  vi.doUnmock("electron-updater");
  vi.doUnmock("../logger");
});

describe("Nix-managed Desktop updater policy", () => {
  it("preserves the managed message and refuses every update mutation boundary", async () => {
    const { autoUpdater, persist, updater } = await loadManagedUpdater();
    await updater.installAutoUpdater(true, {
      isAnyWindowFocused: () => true,
      focusPrimaryWindow: vi.fn(),
      installBlockedReason: () => null,
    });

    expect(updater.getAppUpdateSnapshot()).toMatchObject({
      status: "unavailable",
      allowPrerelease: false,
      errorMessage: "Updates are managed by Nix.",
    });
    const manual = await updater.checkForUpdatesNow(false, "manual");
    expect(manual).toMatchObject({
      status: "unavailable",
      errorMessage: "Updates are managed by Nix.",
      lastCheckIntent: "manual",
    });
    const automatic = await updater.checkForUpdatesNow(false, "automatic");
    expect(automatic).toMatchObject({
      status: "unavailable",
      errorMessage: "Updates are managed by Nix.",
      lastCheckIntent: "automatic",
    });

    const channel = await updater.setAllowPrereleaseUpdates(true);
    expect(channel).toMatchObject({
      outcome: "unchanged",
      snapshot: {
        status: "unavailable",
        allowPrerelease: false,
        errorMessage: "Updates are managed by Nix.",
      },
    });
    updater.startUpdateDownload();
    updater.installDownloadedUpdate();

    expect(persist).not.toHaveBeenCalled();
    expect(autoUpdater.setFeedURL).not.toHaveBeenCalled();
    expect(autoUpdater.checkForUpdates).not.toHaveBeenCalled();
    expect(autoUpdater.downloadUpdate).not.toHaveBeenCalled();
    expect(autoUpdater.quitAndInstall).not.toHaveBeenCalled();
  });

  it("skips every launch-time CLI discovery and mutation boundary", async () => {
    const operations = {
      readCliManifest: vi.fn(),
      resolveBundledCliPath: vi.fn(),
      readBundledCliVersion: vi.fn(),
      discoverCli: vi.fn(),
      probeCliVersion: vi.fn(),
      installBundledCli: vi.fn(),
      stableCliBinaryPath: vi.fn(),
      stageBundledCliForUpgrade: vi.fn(),
      stagedFileExists: vi.fn(),
      cliBinariesDiffer: vi.fn(),
      writeCliManifestPendingUpgrade: vi.fn(),
      writeDesktopReconcileState: vi.fn(),
    };
    const info = vi.fn();
    vi.doMock("../logger", () => ({
      log: { debug: vi.fn(), info, warn: vi.fn(), error: vi.fn() },
    }));
    const { runLaunchTimeCliReconciliation } = await import("../../cli/cli-reconcile");
    const deps: ReconcileCliDeps = {
      readCliManifest: operations.readCliManifest as never,
      resolveBundledCliPath: operations.resolveBundledCliPath as never,
      readBundledCliVersion: operations.readBundledCliVersion as never,
      discoverCli: operations.discoverCli as never,
      probeCliVersion: operations.probeCliVersion as never,
      installBundledCli: operations.installBundledCli as never,
      stableCliBinaryPath: operations.stableCliBinaryPath as never,
      stageBundledCliForUpgrade: operations.stageBundledCliForUpgrade as never,
      stagedFileExists: operations.stagedFileExists as never,
      cliBinariesDiffer: operations.cliBinariesDiffer as never,
      writeCliManifestPendingUpgrade: operations.writeCliManifestPendingUpgrade as never,
      writeDesktopReconcileState: operations.writeDesktopReconcileState as never,
      now: () => new Date("2026-08-24T00:00:00Z"),
      logger: { info, warn: vi.fn() },
    };

    const outcome = await runLaunchTimeCliReconciliation({
      isDevDesktop: false,
      deps,
    });

    expect(outcome).toEqual({ kind: "skipped-nix-managed" });
    for (const operation of Object.values(operations)) {
      expect(operation).not.toHaveBeenCalled();
    }
    expect(info).toHaveBeenCalledWith(expect.stringContaining("managed by Nix"));
  });
});
