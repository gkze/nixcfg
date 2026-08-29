import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const output = vi.hoisted(() => ({ stdout: [] as string[] }));
const logger = vi.hoisted(() => ({
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
}));
const provisionHost = vi.hoisted(() => vi.fn());
const serviceStatus = vi.hoisted(() => vi.fn());

vi.mock("../runner/std-write", () => ({
  flushStdio: () => Promise.resolve(),
  writeStderr: () => undefined,
  writeStdout: (text: string) => output.stdout.push(text),
}));
vi.mock("../logger", () => ({
  createCliLogger: () => logger,
  errorFromUnknown: (value: unknown) => (value instanceof Error ? value : new Error(String(value))),
}));
vi.mock("@sentry/node", () => ({
  captureException: vi.fn(),
  flush: () => Promise.resolve(true),
}));
vi.mock("../host/provision", () => ({ provisionHost }));
vi.mock("../service", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../service")>();
  return {
    ...actual,
    createServiceController: () => ({ status: serviceStatus }),
    serviceLabelFor: () => "ai.traycer.host",
  };
});

import { restartWithPendingCliUpgradeFinalize } from "../commands/host-restart";
import { maybeAutoBootstrap } from "../host/auto-bootstrap";
import { ensureHost } from "../host/ensure";
import { buildProgram } from "../index";
import { readHostInstallRecord } from "../manifest/host-install";
import { reconcilePostFinalizeMarker } from "../upgrade/finalize-helper";
import { hostInstallDir, hostInstallRecordPath } from "../store/paths";

const managedMessage =
  "This Traycer build is managed by Nix; change Desktop, CLI, and Host bytes through nixcfg.";
const releaseVersion = "1.2.0";

const commands: ReadonlyArray<readonly [string, ...string[]]> = [
  ["host", "install", "--from", "/definitely/missing-host.tar.gz"],
  ["host", "apply"],
  ["host", "purge-stage", "--expected-stage-fingerprint", "synthetic-stage"],
  [
    "host",
    "stamp-runtime",
    "--expected-install-generation",
    "synthetic-install",
    "--observed-pid",
    "1",
    "--observed-started-at",
    "1970-01-01T00:00:00.000Z",
    "--observed-runtime-version",
    releaseVersion,
  ],
  ["host", "update"],
  ["host", "download", "latest"],
  ["host", "uninstall"],
  ["host", "service", "install"],
  ["host", "service", "uninstall"],
  ["cli", "upgrade", "--dry-run", "--target", releaseVersion],
  [
    "cli",
    "mark-source",
    "--source",
    "desktop",
    "--binary-path",
    "/nix/store/synthetic-traycer",
    "--installed-version",
    releaseVersion,
  ],
  ["cli", "finalize-upgrade"],
  [
    "cli",
    "re-anchor",
    "--binary-path",
    "/nix/store/synthetic-traycer",
    "--installed-version",
    releaseVersion,
  ],
];

const runtime = {
  json: true,
  quiet: false,
  noProgress: true,
  noBootstrap: false,
  nonInteractive: false,
  environment: "production",
  logger,
} as const;

describe("Nix-managed Traycer CLI policy", () => {
  beforeEach(() => {
    output.stdout.length = 0;
    vi.clearAllMocks();
    serviceStatus.mockResolvedValue({ state: "not-installed" });
    provisionHost.mockResolvedValue({
      action: "none",
      installed: true,
      registered: false,
      running: false,
      postSwapError: null,
    });
  });

  afterEach(() => {
    process.exitCode = undefined;
    vi.restoreAllMocks();
  });

  it.each(commands)("refuses the public %s mutation route", async (...argv) => {
    const exit = vi.spyOn(process, "exit").mockImplementation((code): never => {
      throw new Error(`__nix_policy_exit_${code ?? 0}`);
    });
    const program = buildProgram();
    program.exitOverride();

    await program.parseAsync([...argv, "--json"], { from: "user" });

    expect(process.exitCode).toBe(1);
    expect(exit).not.toHaveBeenCalled();
    expect(output.stdout).toHaveLength(1);
    const event = JSON.parse(output.stdout[0] ?? "null") as Record<string, unknown>;
    expect(event).toMatchObject({
      type: "result",
      status: "error",
      error: {
        code: "E_INVALID_ARGUMENT",
        message: managedMessage,
        details: { packageManager: "nix" },
      },
    });
  });

  it("routes production Host reads to the immutable authenticated runtime", async () => {
    const installDir = hostInstallDir("production");
    expect(installDir).toMatch(/^\/nix\/store\/[0-9a-z]{32}-traycer-host-/);
    expect(hostInstallRecordPath("production")).toBe(join(installDir, "install.json"));
    const record = await readHostInstallRecord("production");
    expect(record).not.toBeNull();
    expect(record).toMatchObject({
      version: "1.2.0",
      runtimeVersion: "1.2.0",
      executablePath: join(installDir, "host-runtime", "traycer-host"),
    });
    expect(hostInstallDir("dev")).not.toBe(installDir);
  });

  it("permits only the read-only exact Host ensure shape", async () => {
    const base = {
      runtime,
      versionRequest: null,
      fromPath: null,
      enableLinger: false,
      allowSelfInvocation: false,
      noServiceRegister: true,
      force: false,
      onProgress: null,
      beforeMutate: null,
    } as const;

    for (const mutation of [
      { versionRequest: "latest" },
      { fromPath: "/tmp/host.tar.gz" },
      { noServiceRegister: false },
    ] as const) {
      await expect(ensureHost({ ...base, ...mutation })).rejects.toMatchObject({
        code: "E_INVALID_ARGUMENT",
        message: managedMessage,
      });
    }
    expect(provisionHost).not.toHaveBeenCalled();

    await ensureHost(base);
    expect(provisionHost).toHaveBeenCalledTimes(1);
    expect(provisionHost).toHaveBeenCalledWith(expect.objectContaining({ registerService: false }));
  });

  it("turns implicit bootstrap, restart finalization, and marker repair into no-ops", async () => {
    const bootstrap = await maybeAutoBootstrap({
      runtime,
      trigger: "host-status",
      onProgress: null,
    });
    expect(bootstrap).toMatchObject({ status: "skipped", reason: "nix-managed" });
    expect(provisionHost).not.toHaveBeenCalled();

    const stopForRestart = vi.fn(() => Promise.resolve({ wasLoaded: true }));
    const relaunchAfterRestart = vi.fn(() => Promise.resolve());
    const restart = await restartWithPendingCliUpgradeFinalize({
      environment: "production",
      controller: { stopForRestart, relaunchAfterRestart },
      label: "ai.traycer.host",
    } as never);
    expect(restart).toEqual({
      finalize: { status: "no-pending" },
      helper: null,
      markerReconcile: null,
      helperOwnsServiceStart: false,
    });
    expect(stopForRestart).toHaveBeenCalledTimes(1);
    expect(relaunchAfterRestart).toHaveBeenCalledTimes(1);

    await expect(reconcilePostFinalizeMarker({ environment: "production" })).resolves.toEqual({
      status: "no-marker",
    });
  });
});
