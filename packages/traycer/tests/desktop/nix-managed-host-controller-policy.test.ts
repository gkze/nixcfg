import { afterEach, describe, expect, it, vi } from "vitest";

const cliCalls = vi.hoisted(() => ({ run: vi.fn(), stream: vi.fn() }));

vi.mock("electron", () => ({
  app: { getPath: () => "/tmp", isPackaged: true, getAppPath: () => "/tmp" },
}));
vi.mock("electron-log", () => ({
  default: {
    transports: { file: { level: "info" }, console: { level: "info" } },
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
    debug: vi.fn(),
  },
}));
vi.mock("../../cli/traycer-cli", () => ({
  runBundledTraycerCliJson: cliCalls.run,
  streamBundledTraycerCliJson: cliCalls.stream,
  TraycerCliError: class extends Error {},
}));
vi.mock("../../cli/cli-discovery", () => ({
  resolveBundledCliPath: vi.fn(() => Promise.resolve(null)),
}));
vi.mock("../../app/host-login-item", () => ({
  hasUnappliedPendingLoginItemRevision: vi.fn(() => Promise.resolve(false)),
  hostManagesHostLoginItem: vi.fn(() => Promise.resolve(false)),
  readHostLoginItemStatus: vi.fn(() => "not-registered"),
  registerHostLoginItem: vi.fn(() => Promise.resolve("not-registered")),
  unregisterHostLoginItem: vi.fn(() => Promise.resolve()),
}));

import { HostController } from "../host-controller";

afterEach(() => vi.clearAllMocks());

describe("Nix-managed Desktop Host controller policy", () => {
  it("makes background stageLatest reconciliation an inert resolved promise", async () => {
    const lifecycleCalls = vi.fn();
    const controller = new HostController({
      environment: "production",
      hostLifecycle: new Proxy({}, { get: () => lifecycleCalls }) as never,
      reachabilityProbe: () => Promise.resolve(false),
      desktopLockWaitMs: 1,
      desktopLockPollIntervalMs: 1,
    });

    await expect(controller.stageLatest()).resolves.toBeUndefined();
    expect(cliCalls.run).not.toHaveBeenCalled();
    expect(cliCalls.stream).not.toHaveBeenCalled();
    expect(lifecycleCalls).not.toHaveBeenCalled();
  });
});
