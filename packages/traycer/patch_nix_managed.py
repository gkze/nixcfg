"""Apply fail-closed Nix ownership policy to the pinned Traycer source."""

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_STORE_PATH_PATTERN = re.compile(
    r"^/nix/store/[0-9abcdfghijklmnpqrsvwxyz]{32}-[^/\n\r\"']+$"
)
_EXPECTED_ARG_COUNT = 3
_EXPECTED_CHECK_ARG_COUNT = 4


@dataclass(frozen=True, slots=True)
class SourcePatch:
    """One exact, idempotent source replacement."""

    path: Path
    old: str
    new: str


def _require_store_path(value: str) -> str:
    if _STORE_PATH_PATTERN.fullmatch(value) is None:
        msg = f"Traycer Host runtime must be one literal Nix store path, got {value!r}"
        raise ValueError(msg)
    return value


def _patches(host_runtime: str) -> tuple[SourcePatch, ...]:
    managed_message = (
        "This Traycer build is managed by Nix; change Desktop, CLI, and Host "
        "bytes through nixcfg."
    )
    expected_executable = f"{host_runtime}/host-runtime/traycer-host"
    host_runtime_literal = json.dumps(host_runtime)
    expected_executable_literal = json.dumps(expected_executable)
    managed_message_literal = json.dumps(managed_message)
    return (
        SourcePatch(
            Path("protocol/src/config/installation.ts"),
            """const HOST_INSTALL_DIRNAME = "install";
const HOST_STAGED_DIRNAME = "staged";""",
            f"""const HOST_INSTALL_DIRNAME = "install";
const NIX_MANAGED_HOST_INSTALL_DIR = {host_runtime_literal};
const HOST_STAGED_DIRNAME = "staged";""",
        ),
        SourcePatch(
            Path("protocol/src/config/installation.ts"),
            """export function hostInstallDir(environment: Environment): string {
  return join(hostInstallHomeDir(environment), HOST_INSTALL_DIRNAME);
}""",
            """export function hostInstallDir(environment: Environment): string {
  return environment === "production"
    ? NIX_MANAGED_HOST_INSTALL_DIR
    : join(hostInstallHomeDir(environment), HOST_INSTALL_DIRNAME);
}""",
        ),
        SourcePatch(
            Path("clients/traycer-cli/src/index.ts"),
            """function withRunner(
  cmd: CommanderCommand,
  build: (
    opts: Record<string, unknown>,
    args: ReadonlyArray<string | undefined>,
  ) => CommandFn,
): CommanderCommand {
  return addRunnerFlags(cmd).action(async (...actionArgs: unknown[]) => {
    const command = actionArgs[actionArgs.length - 1] as CommanderCommand;
    const positionals = extractActionPositionals(actionArgs);
    const optsBag = command.optsWithGlobals() as Record<string, unknown>;
    const fn = build(optsBag, positionals);
    await runCommand(fn, extractRunnerFlags(optsBag));
  });
}""",
            f"""const NIX_MANAGED_CLI_UPDATES = true;
const NIX_MANAGED_COMMAND_PATHS = new Set([
  "traycer host install",
  "traycer host apply",
  "traycer host purge-stage",
  "traycer host stamp-runtime",
  "traycer host update",
  "traycer host download",
  "traycer host uninstall",
  "traycer host service install",
  "traycer host service uninstall",
  "traycer cli upgrade",
  "traycer cli mark-source",
  "traycer cli finalize-upgrade",
  "traycer cli re-anchor",
]);

function commandPath(command: CommanderCommand): string {{
  const names: string[] = [];
  let current: CommanderCommand | null = command;
  while (current !== null) {{
    names.unshift(current.name());
    current = current.parent;
  }}
  return names.join(" ");
}}

function nixManagedCommand(): CommandFn {{
  return async () => {{
    throw cliError({{
      code: CLI_ERROR_CODES.INVALID_ARGUMENT,
      message: {managed_message_literal},
      details: {{ packageManager: "nix" }},
      exitCode: 1,
    }});
  }};
}}

function withRunner(
  cmd: CommanderCommand,
  build: (
    opts: Record<string, unknown>,
    args: ReadonlyArray<string | undefined>,
  ) => CommandFn,
): CommanderCommand {{
  return addRunnerFlags(cmd).action(async (...actionArgs: unknown[]) => {{
    const command = actionArgs[actionArgs.length - 1] as CommanderCommand;
    const positionals = extractActionPositionals(actionArgs);
    const optsBag = command.optsWithGlobals() as Record<string, unknown>;
    const fn = NIX_MANAGED_COMMAND_PATHS.has(commandPath(command))
      ? nixManagedCommand()
      : build(optsBag, positionals);
    await runCommand(fn, extractRunnerFlags(optsBag));
  }});
}}""",
        ),
        SourcePatch(
            Path("clients/traycer-cli/src/index.ts"),
            """      const replacedRunningBinary =
        await refreshCliSlotBeforeCommand(supervisedStart);""",
            """      const replacedRunningBinary = NIX_MANAGED_CLI_UPDATES
        ? false
        : await refreshCliSlotBeforeCommand(supervisedStart);""",
        ),
        SourcePatch(
            Path("clients/traycer-cli/src/host/ensure.ts"),
            """import { config } from "../config";
import { currentInstallPlatform, type InstallSourceArg } from "../installer";""",
            """import { config } from "../config";
import { currentInstallPlatform, type InstallSourceArg } from "../installer";
import { readHostInstallRecord } from "../manifest/host-install";""",
        ),
        SourcePatch(
            Path("clients/traycer-cli/src/host/ensure.ts"),
            """  opts.runtime.logger.info("Host ensure started", {
    environment: opts.runtime.environment,
    hasExplicitVersion: opts.versionRequest !== null,
    hasFromPath: opts.fromPath !== null,
    enableLinger: opts.enableLinger,
    allowSelfInvocation: opts.allowSelfInvocation,
    noServiceRegister: opts.noServiceRegister,
    force: opts.force,
  });""",
            f"""  opts.runtime.logger.info("Host ensure started", {{
    environment: opts.runtime.environment,
    hasExplicitVersion: opts.versionRequest !== null,
    hasFromPath: opts.fromPath !== null,
    enableLinger: opts.enableLinger,
    allowSelfInvocation: opts.allowSelfInvocation,
    noServiceRegister: opts.noServiceRegister,
    force: opts.force,
  }});
  const installed = await readHostInstallRecord(opts.runtime.environment);
  const expectedExecutable = {expected_executable_literal};
  if (
    opts.versionRequest !== null ||
    opts.fromPath !== null ||
    !opts.noServiceRegister ||
    installed === null ||
    installed.version !== config.supportedHostVersion ||
    installed.runtimeVersion !== config.supportedHostVersion ||
    installed.executablePath !== expectedExecutable
  ) {{
    throw cliError({{
      code: CLI_ERROR_CODES.INVALID_ARGUMENT,
      message: {managed_message_literal},
      details: {{ packageManager: "nix" }},
      exitCode: 1,
    }});
  }}""",
        ),
        SourcePatch(
            Path("clients/desktop/src/electron-main/app/updater.ts"),
            "const CURRENT_VERSION = app.getVersion();",
            """const CURRENT_VERSION = app.getVersion();
const NIX_MANAGED_DESKTOP_UPDATES = true;
const NIX_MANAGED_UPDATE_MESSAGE = "Updates are managed by Nix.";""",
        ),
        SourcePatch(
            Path("clients/desktop/src/electron-main/app/updater.ts"),
            """  if (installed) {
    return;
  }
  installed = true;
  try {""",
            """  if (installed) {
    return;
  }
  installed = true;
  if (NIX_MANAGED_DESKTOP_UPDATES) {
    updaterDeps = deps;
    resolveInstallBlockedReason = deps.installBlockedReason;
    currentSnapshot = {
      ...currentSnapshot,
      status: "unavailable",
      installBlockedReason: currentInstallBlockedReason(),
      errorMessage: NIX_MANAGED_UPDATE_MESSAGE,
    };
    markUpdaterInitialized("initialized");
    return;
  }
  try {""",
        ),
        SourcePatch(
            Path("clients/desktop/src/electron-main/app/updater.ts"),
            """async function canCheckForUpdates(isDev: boolean): Promise<boolean> {
  // `isDev` is the dev deploy slot""",
            """async function canCheckForUpdates(isDev: boolean): Promise<boolean> {
  if (NIX_MANAGED_DESKTOP_UPDATES) return false;
  // `isDev` is the dev deploy slot""",
        ),
        SourcePatch(
            Path("clients/desktop/src/electron-main/app/updater.ts"),
            """  if (!(await canCheckForUpdates(isDev))) {
    log.debug("[updater] check skipped outside a shipped build");""",
            """  if (NIX_MANAGED_DESKTOP_UPDATES) {
    return emitSnapshot({
      status: "unavailable",
      errorMessage: NIX_MANAGED_UPDATE_MESSAGE,
      lastCheckedAt:
        intent === "manual" ? new Date().toISOString() : currentSnapshot.lastCheckedAt,
      lastCheckIntent: intent,
    });
  }
  if (!(await canCheckForUpdates(isDev))) {
    log.debug("[updater] check skipped outside a shipped build");""",
        ),
        SourcePatch(
            Path("clients/desktop/src/electron-main/app/updater.ts"),
            """  await updaterInitialized;
  // Idempotent set (channel unchanged): change nothing""",
            """  await updaterInitialized;
  if (NIX_MANAGED_DESKTOP_UPDATES) {
    return {
      outcome: "unchanged",
      snapshot: emitSnapshot({
        status: "unavailable",
        errorMessage: NIX_MANAGED_UPDATE_MESSAGE,
        allowPrerelease: prereleaseUpdatesEnabled(),
      }),
    };
  }
  // Idempotent set (channel unchanged): change nothing""",
        ),
        SourcePatch(
            Path("clients/desktop/src/electron-main/cli/cli-reconcile.ts"),
            """export type CliReconcileOutcome =
  | {
      readonly kind: "skipped-dev-desktop";
    }""",
            """const NIX_MANAGED_CLI_RECONCILIATION = true;

export type CliReconcileOutcome =
  | {
      readonly kind: "skipped-dev-desktop" | "skipped-nix-managed";
    }""",
        ),
        SourcePatch(
            Path("clients/desktop/src/electron-main/cli/cli-reconcile.ts"),
            """}): Promise<CliReconcileOutcome> {
  if (args.isDevDesktop) {""",
            """}): Promise<CliReconcileOutcome> {
  if (NIX_MANAGED_CLI_RECONCILIATION) {
    args.deps.logger.info(
      "[cli-reconcile] skipping launch-time reconciliation because CLI bytes are managed by Nix",
    );
    return { kind: "skipped-nix-managed" };
  }
  if (args.isDevDesktop) {""",
        ),
        SourcePatch(
            Path("clients/desktop/src/electron-main/host/host-controller.ts"),
            """  stageLatest(): Promise<void> {
    if (this.stageLatestInFlight !== null) {
      return this.stageLatestInFlight;
    }
    const job = this.runStageLatest().finally(() => {
      if (this.stageLatestInFlight === job) {
        this.stageLatestInFlight = null;
      }
    });
    this.stageLatestInFlight = job;
    return job;
  }""",
            """  stageLatest(): Promise<void> {
    return Promise.resolve();
  }""",
        ),
        SourcePatch(
            Path("clients/traycer-cli/src/host/auto-bootstrap.ts"),
            """import { installSourceLogFields } from "./install-source-log-fields";""",
            """import { installSourceLogFields } from "./install-source-log-fields";

const NIX_MANAGED_HOST_LIFECYCLE = true;""",
        ),
        SourcePatch(
            Path("clients/traycer-cli/src/host/auto-bootstrap.ts"),
            """  | "service-registration-warning";""",
            """  | "service-registration-warning"
  | "nix-managed";""",
        ),
        SourcePatch(
            Path("clients/traycer-cli/src/host/auto-bootstrap.ts"),
            """  const decision = await evaluateAutoBootstrap(opts);
  if (
    decision.status !== "service-registered" &&""",
            """  const decision = await evaluateAutoBootstrap(opts);
  if (
    NIX_MANAGED_HOST_LIFECYCLE &&
    (decision.status === "service-registered" || decision.status === "installed")
  ) {
    opts.runtime.logger.info("Auto-bootstrap skipped; Host is managed by Nix", {
      environment: opts.runtime.environment,
      trigger: opts.trigger,
      hostInstalled: decision.hostInstalled,
      serviceRegistered: decision.serviceRegistered,
    });
    return {
      ...decision,
      status: "skipped",
      reason: "nix-managed",
      postSwapError: null,
      error: null,
    };
  }
  if (
    decision.status !== "service-registered" &&""",
        ),
        SourcePatch(
            Path("clients/traycer-cli/src/commands/host-status.ts"),
            """    case "skipped":
      if (decision.reason === "explicit-no-bootstrap") {
        return c.dim("bootstrap: skipped (--no-bootstrap)");
      }
      return c.dim(""",
            """    case "skipped":
      if (decision.reason === "explicit-no-bootstrap") {
        return c.dim("bootstrap: skipped (--no-bootstrap)");
      }
      if (decision.reason === "nix-managed") {
        return c.dim("bootstrap: skipped (managed by Nix)");
      }
      return c.dim(""",
        ),
        SourcePatch(
            Path("clients/traycer-cli/src/commands/host-restart.ts"),
            """// `traycer host restart` - kicks the OS service so the supervisor""",
            """const NIX_MANAGED_CLI_UPDATES = true;

// `traycer host restart` - kicks the OS service so the supervisor""",
        ),
        SourcePatch(
            Path("clients/traycer-cli/src/commands/host-restart.ts"),
            """export async function restartWithPendingCliUpgradeFinalize(
  args: RestartFinalizeArgs,
): Promise<RestartFinalizeResult> {
  // 1. Apply any marker from a prior helper attempt. This may clear""",
            """export async function restartWithPendingCliUpgradeFinalize(
  args: RestartFinalizeArgs,
): Promise<RestartFinalizeResult> {
  if (NIX_MANAGED_CLI_UPDATES) {
    const stop = await args.controller.stopForRestart(args.label, {
      force: args.force,
    });
    await args.controller.relaunchAfterRestart(args.label, stop);
    return {
      finalize: { status: "no-pending" },
      helper: null,
      markerReconcile: null,
      helperOwnsServiceStart: false,
    };
  }
  // 1. Apply any marker from a prior helper attempt. This may clear""",
        ),
        SourcePatch(
            Path("clients/traycer-cli/src/upgrade/finalize-helper.ts"),
            """export type ReconcileOutcome =""",
            """const NIX_MANAGED_CLI_UPDATES = true;

export type ReconcileOutcome =""",
        ),
        SourcePatch(
            Path("clients/traycer-cli/src/upgrade/finalize-helper.ts"),
            """export async function reconcilePostFinalizeMarker(opts: {
  readonly environment: Environment;
}): Promise<ReconcileOutcome> {
  const markerPath = cliPostFinalizeMarkerPath(opts.environment);""",
            """export async function reconcilePostFinalizeMarker(opts: {
  readonly environment: Environment;
}): Promise<ReconcileOutcome> {
  if (NIX_MANAGED_CLI_UPDATES) {
    return { status: "no-marker" };
  }
  const markerPath = cliPostFinalizeMarkerPath(opts.environment);""",
        ),
        SourcePatch(
            Path("clients/desktop/package.json"),
            '    "asar": true,\n',
            """    "asar": {
      "smartUnpack": false
    },
""",
        ),
        SourcePatch(
            Path("clients/desktop/package.json"),
            '    "afterPack": "scripts/prepack/inject-host-launch-agent.cjs",\n',
            '    "afterPack": null,\n',
        ),
        SourcePatch(
            Path("clients/desktop/package.json"),
            '      "minimumSystemVersion": "12.0",\n',
            '      "minimumSystemVersion": "14.0",\n',
        ),
        SourcePatch(
            Path("clients/desktop/package.json"),
            """    "files": [
      "dist/**/*",
      "package.json",
      "!**/*.map",
      "node_modules/font-list/**/*"
    ],
    "asarUnpack": [
      "node_modules/font-list/**/*"
    ],""",
            """    "files": [
      "dist/**/*",
      "package.json",
      "node_modules/font-list/**/*",
      "!**/*.map",
      "!node_modules/font-list/LICENSE",
      "!node_modules/font-list/index.d.cts",
      "!node_modules/font-list/index.d.mts",
      "!node_modules/font-list/index.mjs",
      "!node_modules/font-list/libs/darwin/fontlist.m",
      "!node_modules/font-list/libs/linux/**/*",
      "!node_modules/font-list/libs/win32/**/*"
    ],
    "asarUnpack": [
      "node_modules/font-list/libs/darwin/fontlist"
    ],""",
        ),
        SourcePatch(
            Path("clients/desktop/package.json"),
            """      {
        "from": "resources/host",
        "to": "host",
        "filter": [
          "README.md",
          ".gitkeep"
        ]
      },
      {
        "from": "resources/cli",
        "to": "cli",
        "filter": [
          "**/*",
          "!README.md"
        ]
      },
      {
        "from": "resources/tray",
        "to": "tray",
        "filter": [
          "**/*.png"
        ]
      }""",
            """      {
        "from": "resources/cli",
        "to": "cli",
        "filter": [
          "**/*",
          "!README.md"
        ]
      },
      {
        "from": "resources/tray",
        "to": "tray",
        "filter": [
          "tray.png",
          "tray@2x.png",
          "trayTemplate.png",
          "trayTemplate@2x.png"
        ]
      }""",
        ),
        SourcePatch(
            Path("clients/desktop/scripts/build-main-bundle.cjs"),
            """  sourcemap: "external",
  legalComments: "none",
  // Sentry's proxy module emits this warning for entry points with no""",
            """  sourcemap: "external",
  legalComments: "none",
  minifyWhitespace: true,
  minifyIdentifiers: true,
  keepNames: true,
  // Sentry's proxy module emits this warning for entry points with no""",
        ),
        SourcePatch(
            Path("scripts/native-packaging/sea-toolchain.cjs"),
            """    absWorkingDir: cwd || REPO_ROOT,
    // Sentry's proxy module emits this warning for entry points with no""",
            """    absWorkingDir: cwd || REPO_ROOT,
    minifyWhitespace: true,
    minifyIdentifiers: true,
    keepNames: true,
    // Sentry's proxy module emits this warning for entry points with no""",
        ),
        SourcePatch(
            Path("scripts/native-packaging/sea-toolchain.cjs"),
            """  const res = spawnSync("codesign", ["--remove-signature", target], {
    stdio: "inherit",
  });""",
            """  const res = spawnSync(
    "/usr/bin/codesign",
    ["--remove-signature", target],
    { stdio: "inherit" },
  );""",
        ),
        SourcePatch(
            Path("scripts/native-packaging/sea-toolchain.cjs"),
            """  const res = spawnSync("codesign", args, {
    stdio: "inherit",
  });""",
            """  const res = spawnSync("/usr/bin/codesign", args, {
    stdio: "inherit",
  });""",
        ),
        SourcePatch(
            Path("scripts/native-packaging/sea-toolchain.cjs"),
            """  const hostNode = resolveSeaHostNode();
  writeSeaConfig({
    mainBundle: bundleFile,
    outputBlob: blobFile,
    assets: assets || null,
    configPath: configFile,
  });
  generateSeaBlob({ hostNode, configPath: configFile, cwd: workDir });""",
            """  const hostNode = resolveSeaHostNode();
  const seaConfigDirectory = path.dirname(configFile);
  writeSeaConfig({
    mainBundle: path.relative(seaConfigDirectory, bundleFile),
    outputBlob: path.relative(seaConfigDirectory, blobFile),
    assets: assets || null,
    configPath: configFile,
  });
  generateSeaBlob({
    hostNode,
    configPath: configFile,
    cwd: seaConfigDirectory,
  });""",
        ),
    )


def _apply_one(source: str, patch: SourcePatch) -> tuple[str, bool]:
    old_count = source.count(patch.old)
    new_count = source.count(patch.new)
    unapplied_count = old_count - (new_count * patch.new.count(patch.old))
    if unapplied_count == 1 and new_count == 0:
        return source.replace(patch.old, patch.new, 1), True
    if unapplied_count == 0 and new_count == 1:
        return source, False
    msg = (
        f"Traycer source anchor drift in {patch.path}: expected one old or "
        "one applied anchor, got "
        f"old={old_count}, unapplied={unapplied_count}, new={new_count}"
    )
    raise RuntimeError(msg)


def apply_patches(
    source_root: Path,
    host_runtime: str,
    *,
    check: bool = False,
) -> int:
    """Apply all policy patches atomically after validating every source anchor."""
    host_runtime = _require_store_path(host_runtime)
    if not source_root.is_dir():
        msg = f"Traycer source root is not a directory: {source_root}"
        raise ValueError(msg)

    updated: dict[Path, str] = {}
    applied = 0
    for patch in _patches(host_runtime):
        path = source_root / patch.path
        if path not in updated:
            updated[path] = path.read_text(encoding="utf-8")
        updated[path], changed = _apply_one(updated[path], patch)
        applied += int(changed)

    if not check:
        for path, source in updated.items():
            if source != path.read_text(encoding="utf-8"):
                path.write_text(source, encoding="utf-8")
    return applied


def main(argv: list[str] | None = None) -> int:
    """CLI entry point used by the Nix build."""
    args = sys.argv if argv is None else argv
    check = len(args) == _EXPECTED_CHECK_ARG_COUNT and args[1] == "--check"
    if check:
        source_arg = args[2]
        host_runtime_arg = args[3]
    elif len(args) == _EXPECTED_ARG_COUNT:
        source_arg = args[1]
        host_runtime_arg = args[2]
    else:
        sys.stderr.write(
            "usage: patch_nix_managed.py [--check] "
            "<traycer-source-root> <host-runtime-store-path>\n"
        )
        return 2
    try:
        applied = apply_patches(
            Path(source_arg),
            host_runtime_arg,
            check=check,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        sys.stderr.write(f"patch_nix_managed.py: {exc}\n")
        return 1
    action = "validated" if check else "applied"
    sys.stdout.write(f"{action} {applied} Traycer Nix policy patches\n")
    return 0


if __name__ == "__main__":  # pragma: no cover -- direct CLI entry point
    raise SystemExit(main())
