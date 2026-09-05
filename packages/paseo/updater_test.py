"""Focused contracts for the deliberately gated Paseo package foundation."""

import json
import os
import shutil
import subprocess
import sys
import tarfile
from collections import Counter
from hashlib import sha256
from pathlib import Path
from textwrap import dedent
from types import ModuleType
from typing import Protocol, cast

import pytest
from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.inherit import Inherit
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.path import NixPath
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    binding_map,
    expect_binding,
    parse_nix_expr,
)
from lib.tests._package_registry import registry_override_metadata
from lib.tests._shell_ast import (
    command_texts,
    indented_string_body,
    iter_nodes,
    node_text,
    parse_shell,
)
from lib.tests._updater_helpers import (
    collect_events,
    install_fixed_hash_stream,
    load_repo_module,
    run_async,
)
from lib.update.artifacts import GeneratedArtifact
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import (
    UpdateEventKind,
    expect_artifact_updates,
    expect_source_hashes,
)
from lib.update.net import github_raw_url
from lib.update.nix import _build_fetch_from_github_call
from lib.update.nix_expr import identifier_attr_path
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo

_PACKAGE_DIR = REPO_ROOT / "packages/paseo"
_VERSION = "0.6.1"
_TAG = f"v{_VERSION}"
_COMMIT = "20d7efc46a316f5a274b9943a5c43b0322269825"
_LATEST_VERSION = "0.7.2"
_LATEST_TAG = f"v{_LATEST_VERSION}"
_LATEST_COMMIT = "9400a49af670fdb5db4af58e73f8df98588dbea9"
_ELECTRON_VERSION = "41.2.0"
_SHERPA_VERSION = "1.12.28"
_SHERPA_COMMIT = "86d3d00e28c22c102fb7d01c7b62fdc4e7a69f1b"
_ONNXRUNTIME_VERSION = "1.23.2"
_ONNXRUNTIME_COMMIT = "a83fc4d58cb48eb68890dd689f94f28288cf2278"
_NODE_ADDON_API_VERSION = "8.3.0"
_NPM_FETCHER_VERSION = 2
_ESBUILD_VERSION = "0.25.12"
_CLAUDE_AGENT_SDK_VERSION = "0.3.220"
_APP_BUILDER_LIB_VERSION = "26.8.1"
_APP_BUILDER_LIB_BACKPORT_COMMIT = "2ff9190aadc791503a6e62cdcbfa975448bc49bf"
_ONNX_DEPENDENCIES = {
    "abseilCpp": {
        "owner": "abseil",
        "repo": "abseil-cpp",
        "tag": "20240722.2",
        "version": "20240722.2",
        "commit": "216a6bed75c9ec254ae0e5af537e5b9635b45191",
    },
    "dlpack": {
        "owner": "dmlc",
        "repo": "dlpack",
        "commit": "5c210da409e7f1e51ddf445134a4376fdbd70d7d",
    },
    "flatbuffers": {
        "owner": "google",
        "repo": "flatbuffers",
        "tag": "v23.5.26",
        "version": "23.5.26",
        "commit": "0100f6a5779831fa7a651e4b67ef389a8752bd9b",
    },
    "mp11": {
        "owner": "boostorg",
        "repo": "mp11",
        "tag": "boost-1.82.0",
        "version": "boost-1.82.0",
        "commit": "0a0b5fb001ce0233ae3a6f99d849c0649e5a7361",
    },
    "onnx": {
        "owner": "onnx",
        "repo": "onnx",
        "tag": "v1.18.0",
        "version": "v1.18.0",
        "commit": "e709452ef2bbc1d113faf678c24e6d3467696e83",
    },
    "re2": {
        "owner": "google",
        "repo": "re2",
        "tag": "2024-07-02",
        "version": "2024-07-02",
        "commit": "6dcd83d60f7944926bfd308cc13979fc53dd69ca",
    },
    "safeint": {
        "owner": "dcleblanc",
        "repo": "safeint",
        "tag": "3.0.28",
        "version": "3.0.28",
        "commit": "4cafc9196c4da9c817992b20f5253ef967685bf8",
    },
}
_CLAUDE_AGENT_SDK_INTEGRITY = (
    "sha512-glc7SdwPkOkLw8oxwLo9PKTdLJGqW/PIR4urWXFoRtX9YllwozsEVc5Tc1+EvLSkfrsx"
    "PJqQWqOgpjUOQXf1oA=="
)
_CLAUDE_AGENT_SDK_DARWIN_ARM64_INTEGRITY = (
    "sha512-7VxlbEosK7DODiOnsjoVd0DSJzbnaPrM2jelMHI0y8zx1UnLS3WC6EFUXbvy74F2s"
    "XqEznh2tzn7EKWInaRN6Q=="
)
_LATEST_CLAUDE_AGENT_SDK_INTEGRITY = (
    "sha512-FtR0HoHHNqeqJWjZN8qLUAzZVFUI9ztXYNPPwv98Ecmv9qq2QTauI8IzkY26CC0mleWA"
    "qb9RQEW2C0OtiUliug=="
)
_LATEST_CLAUDE_AGENT_SDK_DARWIN_ARM64_INTEGRITY = (
    "sha512-pYDv2RT+UVOvEapMEWKf7Gm5a87Si1Zf+XR23IMGYbG5mESatNJ8Gz2PmkrwfTezZknb"
    "0ljWZe8zgcTFITM3dA=="
)
_CLAUDE_RESOLVER_FIXTURE = b"""\
async function resolveClaudeBinary(runtimeSettings?: ProviderRuntimeSettings): Promise<string> {
  const launch = await resolveProviderLaunch({
    commandConfig: runtimeSettings?.command,
    defaultBinary: "claude",
  });
  const availability = await checkProviderLaunchAvailable(launch);
  if (availability.available) {
    return availability.resolvedPath ?? launch.command;
  }
  throw new Error(
    "Claude binary not found. Install Claude Code (https://github.com/anthropics/claude-code) and ensure it is available in your shell PATH.",
  );
}
"""
_CLAUDE_BUILD_OPTIONS_TAIL_FIXTURE = b"""\
    if (this.claudeSessionId && !this.pendingFreshSessionId) {
      base.resume = this.claudeSessionId;
    }
    if (this.runtimeSettings?.disallowedTools?.length) {
      base.disallowedTools = [
        ...(base.disallowedTools ?? []),
        ...this.runtimeSettings.disallowedTools,
      ];
    }
    return base;
  }

  private buildSettingsOptions(
"""
_CLAUDE_PROVIDER_FIXTURE = (
    _CLAUDE_RESOLVER_FIXTURE
    + b"""\

export async function resolveClaudeCodeVersion(
  runtimeSettings?: ProviderRuntimeSettings,
  signal?: AbortSignal,
): Promise<string> {
  const launch = await resolveProviderLaunch({
    commandConfig: runtimeSettings?.command,
    defaultBinary: "claude",
  });
  const availability = await checkProviderLaunchAvailable(launch);
  return launch.command;
}

export class ClaudeAgentClient implements AgentClient {
  async isAvailable(): Promise<boolean> {
    const launch = await resolveProviderLaunch({
      commandConfig: this.runtimeSettings?.command,
      defaultBinary: "claude",
    });
    const availability = await checkProviderLaunchAvailable(launch);
    return availability.available;
  }

  async getDiagnostic(): Promise<{ diagnostic: string }> {
    try {
      const launch = await resolveProviderLaunch({
        commandConfig: this.runtimeSettings?.command,
        defaultBinary: "claude",
      });
      const availability = await checkProviderLaunchAvailable(launch);
      return { diagnostic: launch.command };
    } catch (error) {
      return { diagnostic: String(error) };
    }
  }

  constructor(options: ClaudeAgentClientOptions) {
    this.queryFactory = options.queryFactory;
    this.resolveBinary = options.resolveBinary ?? (() => resolveClaudeBinary(this.runtimeSettings));
    this.resolveVersion =
      options.resolveVersion ??
      ((signal) => resolveClaudeCodeVersion(this.runtimeSettings, signal));
  }

  createSession(): ClaudeAgentSession {
    return new ClaudeAgentSession({
      resolveBinary: this.resolveBinary,
    });
  }

  resumeSession(): ClaudeAgentSession {
    return new ClaudeAgentSession({
      resolveBinary: this.resolveBinary,
    });
  }
}

class ClaudeAgentSession implements AgentSession {
  constructor(options: ClaudeAgentSessionOptions) {
    this.queryFactory = options.queryFactory;
    this.resolveBinary = options.resolveBinary;
    this.contextUsage = new ClaudeContextUsageState(
      200_000,
    );
  }

  private async buildOptions(): Promise<ClaudeOptions> {
    const claudeBinary = await this.resolveBinary();
    const base: ClaudeOptions = {
      pathToClaudeCodeExecutable: claudeBinary,
    };
"""
    + _CLAUDE_BUILD_OPTIONS_TAIL_FIXTURE
    + b"""\
    providerOptions: ClaudeOptions,
  ): ClaudeOptions {
    return providerOptions;
  }
}
"""
)
_CLAUDE_PROVIDER_PATH = "packages/server/src/server/agent/providers/claude/agent.ts"
_TEST_CLAUDE_CODE_EXECUTABLE = (
    "/nix/store/00000000000000000000000000000000-claude-code/bin/claude"
)
_CLAUDE_RESOLVE_ASSIGNMENT = (
    b"this.resolveBinary = options.resolveBinary ?? "
    b"(() => resolveClaudeBinary(this.runtimeSettings));"
)
_CLAUDE_BINARY_RESOLUTION = b"const claudeBinary = await this.resolveBinary();"
_CLAUDE_OPTIONS_HANDOFF = b"pathToClaudeCodeExecutable: claudeBinary,"
_CLAUDE_DEFAULT_BINARY = b'defaultBinary: "claude",'
_CLAUDE_DEFAULT_BINARY_COUNT = 4
_REQUIRED_PATCH_COUNTS = {
    "packages/app/src/desktop/updates/use-desktop-app-updater.ts": 1,
    "packages/desktop/electron-builder.yml": 2,
    "packages/desktop/src/daemon/daemon-manager.ts": 3,
    "packages/desktop/src/features/auto-updater.ts": 1,
    "packages/desktop/src/integrations/cli-install/install.ts": 1,
    "packages/desktop/src/main.ts": 1,
    "packages/server/src/server/session/daemon/daemon-self-updater.ts": 2,
    "packages/server/src/server/session/daemon/npm-global-cli.ts": 2,
}
_REQUIRED_ANCHOR_COUNTS = {
    "packages/app/src/desktop/updates/use-desktop-app-updater.ts": 2,
    "packages/desktop/src/features/auto-updater.ts": 1,
    "packages/desktop/src/integrations/cli-install/install.ts": 1,
    "packages/server/src/server/session/daemon/npm-global-cli.ts": 1,
}


class _ManifestContractLike(Protocol):
    app_builder_lib_version: str
    esbuild_version: str
    node_addon_api_version: str
    onnxruntime_version: str
    sherpa_version: str


# These fixtures and expected replacement fragments are intentionally authored
# from the pinned v0.6.1 tree, never generated from the production patch tables.
_PINNED_REPLACEMENT_FRAGMENTS = {
    "packages/app/src/desktop/updates/use-desktop-app-updater.ts": (
        "const isDesktopApp = false;",
    ),
    "packages/desktop/electron-builder.yml": (
        "node_modules/sherpa-onnx-darwin-arm64/**/*",
        "notarize: false",
    ),
    "packages/desktop/src/daemon/daemon-manager.ts": (
        'errorMessage: "Updates are managed by Nix."',
        "installed: false",
        'if (app.isPackaged) throw new Error("Updates are managed by Nix.")',
    ),
    "packages/desktop/src/features/auto-updater.ts": ("isPackaged: () => false",),
    "packages/desktop/src/integrations/cli-install/install.ts": (
        'if (app.isPackaged) throw new Error("Updates are managed by Nix.")',
    ),
    "packages/desktop/src/main.ts": ("if (app.isPackaged) return false;",),
    "packages/server/src/server/session/daemon/daemon-self-updater.ts": (
        "function nixOwnsUpdates(): boolean",
        "if (nixOwnsUpdates())",
    ),
    "packages/server/src/server/session/daemon/npm-global-cli.ts": (
        "return Promise.resolve({",
        'stderr: "Updates are managed by Nix."',
    ),
    _CLAUDE_PROVIDER_PATH: (
        f"defaultBinary: {json.dumps(_TEST_CLAUDE_CODE_EXECUTABLE)},",
        "pathToClaudeCodeExecutable: claudeBinary,",
        "base.pathToClaudeCodeExecutable = claudeBinary;\n    return base;",
    ),
}
_PINNED_SOURCE_FIXTURES = {
    "packages/app/src/desktop/updates/use-desktop-app-updater.ts": (
        "  const isDesktopApp = shouldShowDesktopUpdateSection();\n"
        '    void checkForUpdates({ intent: "automatic", silent: true });\n'
        '    void checkForUpdates({ intent: "automatic", silent: true });\n'
    ),
    "packages/desktop/electron-builder.yml": (
        "asarUnpack:\n"
        "  - dist/daemon/node-entrypoint-runner.js\n"
        "  - node_modules/@getpaseo/server/dist/server/terminal/"
        "shell-integration/**/*\n"
        "  notarize: true\n"
    ),
    "packages/desktop/src/daemon/daemon-manager.ts": (
        "    check_app_update: async (args) => {\n"
        "      const currentVersion = resolveDesktopAppVersion();\n"
        "      return checkForAppUpdate({\n"
        "    install_app_update: async (args) => {\n"
        "      const currentVersion = resolveDesktopAppVersion();\n"
        "      return downloadAndInstallUpdate(\n"
        "    install_cli: () => installCli(),\n"
    ),
    "packages/desktop/src/features/auto-updater.ts": (
        "  isPackaged: () => app.isPackaged,\n"
        "    autoUpdater.quitAndInstall(isSilent, isForceRunAfter);\n"
    ),
    "packages/desktop/src/integrations/cli-install/install.ts": (
        "export async function installCli(): Promise<InstallStatus> {\n"
        "  const targetPath = getCliTargetPath();\n"
        "  const { shellUpdated } = await ensurePathInShellRc();\n"
    ),
    "packages/desktop/src/main.ts": (
        "  installAppUpdateOnQuit: async (signal) => {\n"
        "    const settings = await getDesktopSettingsStore().get();\n"
    ),
    "packages/server/src/server/session/daemon/daemon-self-updater.ts": (
        "const DESKTOP_MANAGED_UPDATE_ERROR =\n"
        '  "This daemon is managed by Paseo Desktop. Update Paseo Desktop on the host.";\n'
        "  async update(input: DaemonSelfUpdateInput): "
        "Promise<DaemonSelfUpdateResult> {\n"
        "    if (input.desktopManaged) {\n"
    ),
    "packages/server/src/server/session/daemon/npm-global-cli.ts": (
        'export const PASEO_CLI_PACKAGE = "@getpaseo/cli";\n'
        "const NPM_INSTALL_TIMEOUT_MS = 300_000;\n"
        "  installLatest(): Promise<CommandResult> {\n"
        '    return this.runCommand("npm", ["install", "-g", '
        "`${PASEO_CLI_PACKAGE}@latest`], {\n"
        "      timeout: NPM_INSTALL_TIMEOUT_MS,\n"
        "      maxBuffer: NPM_MAX_BUFFER_BYTES,\n"
        "    });\n"
        "  }\n"
    ),
    _CLAUDE_PROVIDER_PATH: _CLAUDE_PROVIDER_FIXTURE.decode(),
}
_ORIGINAL_MUTATION_FRAGMENTS = {
    "packages/app/src/desktop/updates/use-desktop-app-updater.ts": (
        "  const isDesktopApp = shouldShowDesktopUpdateSection();\n",
    ),
    "packages/desktop/electron-builder.yml": ("  notarize: true\n",),
    "packages/desktop/src/daemon/daemon-manager.ts": (
        "    check_app_update: async (args) => {\n"
        "      const currentVersion = resolveDesktopAppVersion();\n"
        "      return checkForAppUpdate({\n",
        "    install_app_update: async (args) => {\n"
        "      const currentVersion = resolveDesktopAppVersion();\n"
        "      return downloadAndInstallUpdate(\n",
        "    install_cli: () => installCli(),\n",
    ),
    "packages/desktop/src/features/auto-updater.ts": (
        "  isPackaged: () => app.isPackaged,\n",
    ),
    "packages/desktop/src/integrations/cli-install/install.ts": (
        "export async function installCli(): Promise<InstallStatus> {\n"
        "  const targetPath = getCliTargetPath();\n",
    ),
    "packages/desktop/src/main.ts": (
        "  installAppUpdateOnQuit: async (signal) => {\n"
        "    const settings = await getDesktopSettingsStore().get();\n",
    ),
    "packages/server/src/server/session/daemon/daemon-self-updater.ts": (
        "  async update(input: DaemonSelfUpdateInput): "
        "Promise<DaemonSelfUpdateResult> {\n"
        "    if (input.desktopManaged) {\n",
    ),
    "packages/server/src/server/session/daemon/npm-global-cli.ts": (
        "const NPM_INSTALL_TIMEOUT_MS = 300_000;\n",
        "  installLatest(): Promise<CommandResult> {\n"
        '    return this.runCommand("npm", ["install", "-g", '
        "`${PASEO_CLI_PACKAGE}@latest`], {\n"
        "      timeout: NPM_INSTALL_TIMEOUT_MS,\n"
        "      maxBuffer: NPM_MAX_BUFFER_BYTES,\n"
        "    });\n"
        "  }\n",
    ),
}
_MISSING_ANCHOR_PATH = "packages/desktop/src/features/auto-updater.ts"
_MISSING_ANCHOR_TEXT = "    autoUpdater.quitAndInstall(isSilent, isForceRunAfter);\n"
_HASHES = (
    "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
    "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
    "sha256-DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD=",
    "sha256-EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE=",
    "sha256-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF=",
)


def _load_updater_module() -> ModuleType:
    return load_repo_module(
        "packages/paseo/updater.py",
        "paseo_updater_dedicated_test",
    )


def _load_patcher_module() -> ModuleType:
    return load_repo_module(
        "packages/paseo/patch_nix_managed.py",
        "paseo_patcher_dedicated_test",
    )


def _sherpa_addon_install_phase() -> str:
    package = expect_instance(
        parse_nix_expr(
            (_PACKAGE_DIR / "sherpa-node-addon.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(package.output, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)
    install_phase = expect_instance(
        expect_binding(arguments.values, "installPhase").value,
        IndentedString,
    )
    return indented_string_body(install_phase.rebuild())


def _urls(*, paseo_commit: str = _COMMIT) -> dict[str, str]:
    return {
        "nodeAddonApiUrl": (
            "https://registry.npmjs.org/node-addon-api/-/node-addon-api-8.3.0.tgz"
        ),
        "onnxruntimeUrl": (
            "https://github.com/microsoft/onnxruntime/archive/"
            f"{_ONNXRUNTIME_COMMIT}.tar.gz"
        ),
        "paseoUrl": (
            f"https://github.com/getpaseo/paseo/archive/{paseo_commit}.tar.gz"
        ),
        "sherpaOnnxNodeUrl": (
            "https://registry.npmjs.org/sherpa-onnx-node/-/sherpa-onnx-node-1.12.28.tgz"
        ),
        "sherpaOnnxUrl": (
            f"https://github.com/k2-fsa/sherpa-onnx/archive/{_SHERPA_COMMIT}.tar.gz"
        ),
    }


def _version_info(
    *,
    release_version: str = _VERSION,
    release_commit: str = _COMMIT,
    claude_sdk_version: str = _CLAUDE_AGENT_SDK_VERSION,
    **metadata_overrides: str,
) -> VersionInfo:
    metadata = {
        "appBuilderLibVersion": _APP_BUILDER_LIB_VERSION,
        "claudeAgentSdkVersion": claude_sdk_version,
        "commit": release_commit,
        "electronVersion": _ELECTRON_VERSION,
        "esbuildVersion": _ESBUILD_VERSION,
        "nodeAddonApiVersion": _NODE_ADDON_API_VERSION,
        "onnxDependencies": {
            name: dict(dependency) for name, dependency in _ONNX_DEPENDENCIES.items()
        },
        "onnxruntimeCommit": _ONNXRUNTIME_COMMIT,
        "onnxruntimeVersion": _ONNXRUNTIME_VERSION,
        "sherpaCommit": _SHERPA_COMMIT,
        "sherpaVersion": _SHERPA_VERSION,
        "tag": f"v{release_version}",
        **_urls(paseo_commit=release_commit),
    }
    metadata.update(metadata_overrides)
    return VersionInfo(
        version=release_version,
        metadata=metadata,
    )


def _complete_hash_entries(urls: dict[str, str]) -> list[HashEntry]:
    return [
        HashEntry.create("srcHash", value, url=url)
        for value, url in zip(
            _HASHES[:3],
            (urls["paseoUrl"], urls["sherpaOnnxUrl"], urls["onnxruntimeUrl"]),
            strict=True,
        )
    ] + [
        HashEntry.create("sha256", _HASHES[3], url=urls["nodeAddonApiUrl"]),
        HashEntry.create("sha256", _HASHES[4], url=urls["sherpaOnnxNodeUrl"]),
        HashEntry.create("npmDepsHash", _HASHES[5], url=urls["paseoUrl"]),
    ]


def _lock_manifest(
    *,
    version: str = _VERSION,
    claude_sdk_version: str = _CLAUDE_AGENT_SDK_VERSION,
    claude_sdk_integrity: str = _CLAUDE_AGENT_SDK_INTEGRITY,
    claude_sdk_platform_integrity: str = (_CLAUDE_AGENT_SDK_DARWIN_ARM64_INTEGRITY),
    hoisted_claude_sdk: bool = False,
) -> dict[str, object]:
    sdk_path = (
        "node_modules/@anthropic-ai/claude-agent-sdk"
        if hoisted_claude_sdk
        else "packages/server/node_modules/@anthropic-ai/claude-agent-sdk"
    )
    platform_package = "@anthropic-ai/claude-agent-sdk-darwin-arm64"
    platform_path = (
        f"node_modules/{platform_package}"
        if hoisted_claude_sdk
        else f"{sdk_path}/node_modules/{platform_package}"
    )
    return {
        "version": version,
        "packages": {
            "": {"version": version},
            "node_modules/electron": {"version": "41.2.0"},
            "node_modules/electron-builder": {
                "version": _APP_BUILDER_LIB_VERSION,
                "dependencies": {
                    "app-builder-lib": _APP_BUILDER_LIB_VERSION,
                },
            },
            "node_modules/app-builder-lib": {
                "version": _APP_BUILDER_LIB_VERSION,
            },
            sdk_path: {
                "version": claude_sdk_version,
                "resolved": (
                    "https://registry.npmjs.org/@anthropic-ai/claude-agent-sdk/-/"
                    f"claude-agent-sdk-{claude_sdk_version}.tgz"
                ),
                "integrity": claude_sdk_integrity,
                "optionalDependencies": {
                    platform_package: claude_sdk_version,
                },
            },
            platform_path: {
                "version": claude_sdk_version,
                "resolved": (
                    "https://registry.npmjs.org/@anthropic-ai/"
                    "claude-agent-sdk-darwin-arm64/-/"
                    f"claude-agent-sdk-darwin-arm64-{claude_sdk_version}.tgz"
                ),
                "integrity": claude_sdk_platform_integrity,
                "optional": True,
                "os": ["darwin"],
                "cpu": ["arm64"],
            },
            # The desktop lock has its own wrapper dependency. The source-built
            # Sherpa addon is audited independently at node-addon-api 8.3.0.
            "node_modules/node-addon-api": {"version": "7.1.1"},
            "node_modules/sherpa-onnx-darwin-arm64": {"version": "1.12.28"},
            "node_modules/sherpa-onnx-node": {"version": "1.12.28"},
            "packages/server/node_modules/@esbuild/darwin-arm64": {
                "version": _ESBUILD_VERSION
            },
            "packages/server/node_modules/esbuild": {"version": _ESBUILD_VERSION},
        },
    }


def _write_patcher_fixture(root: Path) -> None:
    for relative_path, source in _PINNED_SOURCE_FIXTURES.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def _validate_manifests(
    module: ModuleType,
    *,
    lock_payload: object | None = None,
    release_version: str = _VERSION,
    root_payload: object | None = None,
    server_payload: object | None = None,
    sherpa_payload: object | None = None,
    sherpa_ort_cmake: str | None = None,
) -> _ManifestContractLike:
    return module.PaseoUpdater._validate_manifests(
        root_payload=(
            {"version": release_version} if root_payload is None else root_payload
        ),
        desktop_payload={
            "version": release_version,
            "devDependencies": {
                "electron": _ELECTRON_VERSION,
                "electron-builder": _APP_BUILDER_LIB_VERSION,
            },
        },
        server_payload=(
            {
                "version": release_version,
                "dependencies": {
                    "@anthropic-ai/claude-agent-sdk": _CLAUDE_AGENT_SDK_VERSION,
                    "esbuild": f"^{_ESBUILD_VERSION}",
                    "sherpa-onnx-node": "1.12.28",
                },
            }
            if server_payload is None
            else server_payload
        ),
        lock_payload=(
            _lock_manifest(version=release_version)
            if lock_payload is None
            else lock_payload
        ),
        sherpa_payload=(
            {"dependencies": {"node-addon-api": "^8.3.0"}}
            if sherpa_payload is None
            else sherpa_payload
        ),
        sherpa_ort_cmake=(
            "https://github.com/microsoft/onnxruntime/releases/download/"
            "v1.23.2/onnxruntime-osx-arm64-1.23.2.tgz"
            if sherpa_ort_cmake is None
            else sherpa_ort_cmake
        ),
        release_version=release_version,
    )


def test_paseo_accepts_a_compatible_future_release_version() -> None:
    """A routine later release must not require changing an updater version pin."""
    module = _load_updater_module()

    _validate_manifests(module, release_version="9.8.7")


@pytest.mark.parametrize(
    ("component", "expected_error"),
    [
        ("app-builder-lib", "supported app-builder-lib version must be"),
        ("sherpa", "supported Sherpa version must be"),
        ("onnxruntime", "supported ONNX Runtime version must be"),
    ],
)
def test_paseo_rejects_unreviewed_native_inventory_versions(
    component: str,
    expected_error: str,
) -> None:
    """Version-specific patches and source inventories must fail closed on drift."""
    module = _load_updater_module()
    lock = _lock_manifest()
    packages = cast("dict[str, object]", lock["packages"])
    server_payload: object | None = None
    sherpa_ort_cmake: str | None = None
    if component == "app-builder-lib":
        electron_builder = cast(
            "dict[str, object]",
            packages["node_modules/electron-builder"],
        )
        dependencies = cast("dict[str, object]", electron_builder["dependencies"])
        dependencies["app-builder-lib"] = "26.9.0"
        packages["node_modules/app-builder-lib"] = {"version": "26.9.0"}
    elif component == "sherpa":
        packages["node_modules/sherpa-onnx-node"] = {"version": "1.13.0"}
        packages["node_modules/sherpa-onnx-darwin-arm64"] = {"version": "1.13.0"}
        server_payload = {
            "version": _VERSION,
            "dependencies": {
                "@anthropic-ai/claude-agent-sdk": _CLAUDE_AGENT_SDK_VERSION,
                "esbuild": f"^{_ESBUILD_VERSION}",
                "sherpa-onnx-node": "1.13.0",
            },
        }
    else:
        sherpa_ort_cmake = (
            "https://github.com/microsoft/onnxruntime/releases/download/"
            "v1.24.0/onnxruntime-osx-arm64-1.24.0.tgz"
        )

    with pytest.raises(RuntimeError, match=expected_error):
        _validate_manifests(
            module,
            lock_payload=lock,
            server_payload=server_payload,
            sherpa_ort_cmake=sherpa_ort_cmake,
        )


def test_paseo_resolves_exact_release_and_native_source_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery must accept the current release from relational source evidence."""
    module = _load_updater_module()
    updater = module.PaseoUpdater()
    claude_sdk_version = "0.3.246"
    api_paths: list[str] = []
    fetched_urls: list[str] = []

    async def release_payload(
        _session: object,
        path: str,
        *,
        config: object,
    ) -> dict[str, str]:
        assert config == updater.config
        api_paths.append(path)
        return {"tag_name": _LATEST_TAG}

    commits = {
        f"repos/getpaseo/paseo/commits/{_LATEST_TAG}": _LATEST_COMMIT,
        "repos/k2-fsa/sherpa-onnx/commits/v1.12.28": _SHERPA_COMMIT,
        "repos/microsoft/onnxruntime/commits/v1.23.2": _ONNXRUNTIME_COMMIT,
        **{
            f"repos/{dependency['owner']}/{dependency['repo']}/commits/"
            f"{dependency['tag']}": dependency["commit"]
            for dependency in _ONNX_DEPENDENCIES.values()
            if "tag" in dependency
        },
    }

    async def commit_payload(
        _session: object,
        path: str,
        *,
        config: object,
    ) -> dict[str, str]:
        assert config == updater.config
        api_paths.append(path)
        return {"sha": commits[path]}

    async def json_payload(
        _session: object,
        url: str,
        *,
        config: object,
    ) -> object:
        assert config == updater.config
        fetched_urls.append(url)
        if url.endswith(f"/{_LATEST_COMMIT}/package.json"):
            return {"version": _LATEST_VERSION}
        if url.endswith("/packages/desktop/package.json"):
            return {
                "version": _LATEST_VERSION,
                "devDependencies": {
                    "electron": _ELECTRON_VERSION,
                    "electron-builder": _APP_BUILDER_LIB_VERSION,
                },
            }
        if url.endswith("/packages/server/package.json"):
            return {
                "version": _LATEST_VERSION,
                "dependencies": {
                    "@anthropic-ai/claude-agent-sdk": claude_sdk_version,
                    "esbuild": f"^{_ESBUILD_VERSION}",
                    "sherpa-onnx-node": "1.12.28",
                },
            }
        if url.endswith("/package-lock.json"):
            return _lock_manifest(
                version=_LATEST_VERSION,
                claude_sdk_version=claude_sdk_version,
                claude_sdk_integrity=_LATEST_CLAUDE_AGENT_SDK_INTEGRITY,
                claude_sdk_platform_integrity=(
                    _LATEST_CLAUDE_AGENT_SDK_DARWIN_ARM64_INTEGRITY
                ),
                hoisted_claude_sdk=True,
            )
        if url.endswith("/scripts/node-addon-api/package.json"):
            return {"dependencies": {"node-addon-api": "^8.3.0"}}
        msg = f"unexpected exact-source URL: {url}"
        raise AssertionError(msg)

    async def bytes_payload(
        _session: object,
        url: str,
        *,
        config: object,
    ) -> bytes:
        assert config == updater.config
        fetched_urls.append(url)
        if url.endswith("/packages/server/src/server/agent/providers/claude/agent.ts"):
            return _CLAUDE_PROVIDER_FIXTURE
        return (
            b"https://github.com/microsoft/onnxruntime/releases/download/"
            b"v1.23.2/onnxruntime-osx-arm64-1.23.2.tgz"
        )

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        release_payload,
    )
    monkeypatch.setattr(module, "fetch_github_api", commit_payload)
    monkeypatch.setattr(module, "fetch_json", json_payload)
    monkeypatch.setattr(module, "fetch_url", bytes_payload)

    assert run_async(updater.fetch_latest(object())) == _version_info(
        release_version=_LATEST_VERSION,
        release_commit=_LATEST_COMMIT,
        claude_sdk_version=claude_sdk_version,
    )
    assert api_paths == [
        "repos/getpaseo/paseo/releases/latest",
        f"repos/getpaseo/paseo/commits/{_LATEST_TAG}",
        "repos/k2-fsa/sherpa-onnx/commits/v1.12.28",
        "repos/microsoft/onnxruntime/commits/v1.23.2",
        "repos/abseil/abseil-cpp/commits/20240722.2",
        "repos/google/flatbuffers/commits/v23.5.26",
        "repos/boostorg/mp11/commits/boost-1.82.0",
        "repos/onnx/onnx/commits/v1.18.0",
        "repos/google/re2/commits/2024-07-02",
        "repos/dcleblanc/safeint/commits/3.0.28",
    ]
    assert len(fetched_urls) == 7


@pytest.mark.parametrize(
    ("package_path", "expected_error"),
    [
        (
            "packages/server/node_modules/esbuild",
            "locked server esbuild darwin-arm64",
        ),
        (
            "packages/server/node_modules/@esbuild/darwin-arm64",
            "locked server esbuild darwin-arm64",
        ),
    ],
)
def test_paseo_rejects_unreviewed_packaged_esbuild_runtime(
    package_path: str,
    expected_error: str,
) -> None:
    """The server compiler and its admitted arm64 executable must stay in lockstep."""
    module = _load_updater_module()
    lock = _lock_manifest()
    packages = cast("dict[str, object]", lock["packages"])
    packages[package_path] = {"version": "0.25.13"}

    with pytest.raises(RuntimeError, match=expected_error):
        _validate_manifests(module, lock_payload=lock)


@pytest.mark.parametrize(
    "spec",
    [_ESBUILD_VERSION, f"^{_ESBUILD_VERSION}", f"~{_ESBUILD_VERSION}"],
)
def test_paseo_accepts_supported_esbuild_dependency_specs(spec: str) -> None:
    """Exact, caret, and tilde specs may select the exact locked compiler."""
    module = _load_updater_module()

    contract = _validate_manifests(
        module,
        server_payload={
            "version": _VERSION,
            "dependencies": {
                "@anthropic-ai/claude-agent-sdk": _CLAUDE_AGENT_SDK_VERSION,
                "esbuild": spec,
                "sherpa-onnx-node": _SHERPA_VERSION,
            },
        },
    )

    assert contract.esbuild_version == _ESBUILD_VERSION


def test_paseo_rejects_out_of_range_esbuild_dependency() -> None:
    """The source manifest must include the exact compiler selected by the lock."""
    module = _load_updater_module()

    with pytest.raises(RuntimeError, match="does not satisfy npm range"):
        _validate_manifests(
            module,
            server_payload={
                "version": _VERSION,
                "dependencies": {
                    "@anthropic-ai/claude-agent-sdk": _CLAUDE_AGENT_SDK_VERSION,
                    "esbuild": "^0.25.13",
                    "sherpa-onnx-node": "1.12.28",
                },
            },
        )


def test_paseo_rejects_claude_external_runtime_seam_drift() -> None:
    """Changing the executable handoff must fail structural validation."""
    module = _load_updater_module()
    source = _CLAUDE_PROVIDER_FIXTURE.replace(
        b"pathToClaudeCodeExecutable: claudeBinary,",
        b"pathToClaudeCodeExecutable: bundledSdkBinary,",
    )
    with pytest.raises(RuntimeError, match="Claude provider runtime seam"):
        module.PaseoUpdater._validate_claude_provider_source(source)


def test_paseo_rejects_incomplete_claude_launch_defaults() -> None:
    """Every provider launch surface must default to the package executable."""
    module = _load_updater_module()
    prefix, separator, suffix = _CLAUDE_PROVIDER_FIXTURE.rpartition(
        _CLAUDE_DEFAULT_BINARY
    )
    assert separator == _CLAUDE_DEFAULT_BINARY
    source = prefix + b'defaultBinary: "codexx",' + suffix
    with pytest.raises(RuntimeError, match="Claude provider runtime seam"):
        module.PaseoUpdater._validate_claude_provider_source(source)


def test_paseo_accepts_unrelated_claude_provider_source_changes() -> None:
    """Immutable source provenance must not pin unrelated provider bytes."""
    module = _load_updater_module()
    module.PaseoUpdater._validate_claude_provider_source(
        b"// unrelated compatible upstream change\n" + _CLAUDE_PROVIDER_FIXTURE
    )


def test_paseo_patcher_accepts_unrelated_claude_provider_source_changes(
    tmp_path: Path,
) -> None:
    """The patcher keys compatibility to mutation seams rather than a file digest."""
    module = _load_patcher_module()
    root = tmp_path / "source"
    _write_patcher_fixture(root)
    provider = root / _CLAUDE_PROVIDER_PATH
    provider.write_bytes(
        b"// unrelated compatible upstream change\n" + provider.read_bytes()
    )

    module.patch_tree(
        root,
        claude_code_executable=_TEST_CLAUDE_CODE_EXECUTABLE,
        check=True,
    )


def test_paseo_manifest_drift_fails_closed() -> None:
    """A native dependency version change must stop metadata promotion."""
    module = _load_updater_module()
    lock = _lock_manifest()
    packages = cast("dict[str, object]", lock["packages"])
    packages["node_modules/sherpa-onnx-node"] = {"version": "1.12.29"}

    with pytest.raises(RuntimeError, match="locked sherpa-onnx-node"):
        module.PaseoUpdater._validate_manifests(
            root_payload={"version": _VERSION},
            desktop_payload={
                "version": _VERSION,
                "devDependencies": {
                    "electron": _ELECTRON_VERSION,
                    "electron-builder": _APP_BUILDER_LIB_VERSION,
                },
            },
            server_payload={
                "version": _VERSION,
                "dependencies": {
                    "@anthropic-ai/claude-agent-sdk": _CLAUDE_AGENT_SDK_VERSION,
                    "esbuild": f"^{_ESBUILD_VERSION}",
                    "sherpa-onnx-node": "1.12.28",
                },
            },
            lock_payload=lock,
            sherpa_payload={"dependencies": {"node-addon-api": "^8.3.0"}},
            sherpa_ort_cmake=(
                "https://github.com/microsoft/onnxruntime/releases/download/"
                "v1.23.2/onnxruntime-osx-arm64-1.23.2.tgz"
            ),
            release_version=_VERSION,
        )


def test_paseo_rejects_malformed_manifest_payloads() -> None:
    """Malformed JSON shapes and missing strings fail before hash promotion."""
    module = _load_updater_module()

    with pytest.raises(TypeError, match="root manifest is not a JSON object"):
        _validate_manifests(module, root_payload=[])
    with pytest.raises(TypeError, match="test payload is missing version"):
        module._require_string({}, "version", context="test payload")
    with pytest.raises(TypeError, match="test payload is missing version"):
        module._require_string({"version": ""}, "version", context="test payload")
    with pytest.raises(RuntimeError, match="exact semantic version"):
        module._require_exact_version("latest", context="test version")
    with pytest.raises(RuntimeError, match="immutable commit"):
        module._require_commit("main", context="test commit")


@pytest.mark.parametrize("value", ["invalid", "0"])
def test_paseo_rejects_invalid_npm_fetcher_compatibility_pin(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """The reviewed fetcher schema pin must remain a positive integer."""
    module = _load_updater_module()
    compatibility_pins = {
        **module.PaseoUpdater.compatibility_pins,
        "npmFetcherVersion": value,
    }
    monkeypatch.setattr(module.PaseoUpdater, "compatibility_pins", compatibility_pins)

    with pytest.raises(RuntimeError, match="must be a positive integer"):
        module.PaseoUpdater._npm_fetcher_version()


def test_paseo_rejects_a_missing_claude_sdk_lock_entry() -> None:
    """A manifest dependency without a resolvable lock entry must fail closed."""
    module = _load_updater_module()
    lock = _lock_manifest()
    packages = cast("dict[str, object]", lock["packages"])
    del packages["packages/server/node_modules/@anthropic-ai/claude-agent-sdk"]

    with pytest.raises(RuntimeError, match="Claude Agent SDK is missing"):
        _validate_manifests(module, lock_payload=lock)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("optional", False, "must be optional"),
        ("os", ["linux"], "os must be"),
        ("cpu", ["x64"], "cpu must be"),
    ],
)
def test_paseo_rejects_incompatible_claude_platform_metadata(
    field: str,
    value: object,
    match: str,
) -> None:
    """The resolved opaque SDK runtime must be the optional Darwin arm64 package."""
    module = _load_updater_module()
    lock = _lock_manifest()
    packages = cast("dict[str, object]", lock["packages"])
    platform = cast(
        "dict[str, object]",
        packages[
            "packages/server/node_modules/@anthropic-ai/claude-agent-sdk/"
            "node_modules/@anthropic-ai/claude-agent-sdk-darwin-arm64"
        ],
    )
    platform[field] = value

    with pytest.raises(RuntimeError, match=match):
        _validate_manifests(module, lock_payload=lock)


@pytest.mark.parametrize(
    "spec",
    [
        _NODE_ADDON_API_VERSION,
        f"^{_NODE_ADDON_API_VERSION}",
        f"~{_NODE_ADDON_API_VERSION}",
    ],
)
def test_paseo_accepts_supported_node_addon_api_specs(spec: str) -> None:
    """Range spelling may vary while the fetched native header stays exact."""
    module = _load_updater_module()

    contract = _validate_manifests(
        module,
        sherpa_payload={"dependencies": {"node-addon-api": spec}},
    )

    assert contract.node_addon_api_version == _NODE_ADDON_API_VERSION


def test_paseo_rejects_an_invalid_node_addon_api_spec() -> None:
    """Unsupported npm range syntax must fail before source hashing."""
    module = _load_updater_module()
    with pytest.raises(RuntimeError, match="valid npm semantic-version range"):
        _validate_manifests(
            module,
            sherpa_payload={"dependencies": {"node-addon-api": "latest"}},
        )


def test_paseo_rejects_out_of_range_node_addon_api_dependency() -> None:
    """Sherpa's dependency range must contain the exact fetched header version."""
    module = _load_updater_module()

    with pytest.raises(RuntimeError, match="does not satisfy npm range"):
        _validate_manifests(
            module,
            sherpa_payload={"dependencies": {"node-addon-api": "^9.0.0"}},
        )


def test_paseo_rejects_inconsistent_onnxruntime_archive_identity() -> None:
    """Sherpa's ONNX Runtime tag and archive filename must identify one version."""
    module = _load_updater_module()
    with pytest.raises(RuntimeError, match="one exact ONNX Runtime archive"):
        _validate_manifests(
            module,
            sherpa_ort_cmake=(
                "https://github.com/microsoft/onnxruntime/releases/download/"
                "v1.23.2/onnxruntime-osx-arm64-1.23.1.tgz"
            ),
        )


def test_paseo_rejects_nonimmutable_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GitHub tag response must resolve to a full immutable commit."""
    module = _load_updater_module()

    async def invalid_commit_payload(
        _session: object,
        _path: str,
        *,
        config: object,
    ) -> dict[str, str]:
        assert config is sentinel
        return {"sha": "not-an-immutable-commit"}

    sentinel = object()
    monkeypatch.setattr(module, "fetch_github_api", invalid_commit_payload)
    with pytest.raises(RuntimeError, match="is not an immutable commit"):
        run_async(
            module.PaseoUpdater._resolve_tag_commit(
                object(),
                owner="getpaseo",
                repo="paseo",
                tag=_TAG,
                config=sentinel,
            )
        )


def test_paseo_rejects_an_incomplete_onnx_dependency_inventory() -> None:
    """Hashing must not silently omit a native source from the reviewed graph."""
    module = _load_updater_module()
    info = _version_info()
    metadata = cast("dict[str, object]", info.metadata)
    dependencies = cast(
        "dict[str, dict[str, str]]",
        metadata["onnxDependencies"],
    )
    del dependencies["safeint"]

    with pytest.raises(RuntimeError, match="do not match the supported inventory"):
        module.PaseoUpdater._required_onnx_dependencies(info)


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        ("owner", "unreviewed", "ONNX dependency abseilCpp owner must be"),
        ("commit", "main", "must be an immutable commit"),
        ("rev", "20240722.2", "has unexpected fields"),
    ],
)
def test_paseo_rejects_drifted_onnx_dependency_metadata(
    field: str,
    value: str,
    expected_error: str,
) -> None:
    """Only reviewed provenance plus one immutable commit reaches native hashing."""
    module = _load_updater_module()
    info = _version_info()
    metadata = cast("dict[str, object]", info.metadata)
    dependencies = cast(
        "dict[str, dict[str, str]]",
        metadata["onnxDependencies"],
    )
    dependencies["abseilCpp"][field] = value

    with pytest.raises(RuntimeError, match=expected_error):
        module.PaseoUpdater._required_onnx_dependencies(info)


def test_paseo_rejects_an_untrusted_claude_sdk_platform_runtime() -> None:
    """The opaque SDK runtime may not drift to an unreviewed npm artifact."""
    module = _load_updater_module()
    lock = _lock_manifest()
    packages = cast("dict[str, object]", lock["packages"])
    platform = cast(
        "dict[str, object]",
        packages[
            "packages/server/node_modules/@anthropic-ai/claude-agent-sdk/"
            "node_modules/@anthropic-ai/claude-agent-sdk-darwin-arm64"
        ],
    )
    platform["resolved"] = "https://example.invalid/unreviewed-runtime.tgz"

    with pytest.raises(
        RuntimeError,
        match="locked Claude Agent SDK darwin-arm64 URL must be",
    ):
        _validate_manifests(module, lock_payload=lock)


@pytest.mark.parametrize(
    ("field", "replacement", "expected_error"),
    [
        (
            "resolved",
            "https://example.invalid/unreviewed-sdk.tgz",
            "locked Claude Agent SDK URL must be",
        ),
        (
            "integrity",
            "sha512-unreviewed",
            "locked Claude Agent SDK integrity must be",
        ),
    ],
)
def test_paseo_rejects_an_untrusted_claude_sdk_source(
    field: str,
    replacement: str,
    expected_error: str,
) -> None:
    """The JavaScript SDK orchestrating external Claude remains byte-pinned."""
    module = _load_updater_module()
    lock = _lock_manifest()
    packages = cast("dict[str, object]", lock["packages"])
    sdk = cast(
        "dict[str, object]",
        packages["packages/server/node_modules/@anthropic-ai/claude-agent-sdk"],
    )
    sdk[field] = replacement

    with pytest.raises(RuntimeError, match=expected_error):
        _validate_manifests(module, lock_payload=lock)


def test_paseo_hashes_sources_then_npm_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hashing must include three source trees, two tarballs, and npm closure."""
    module = _load_updater_module()
    updater = module.PaseoUpdater()
    native_lock = json.loads(
        (_PACKAGE_DIR / "native-lock.json").read_text(encoding="utf-8")
    )
    native_hashes_by_identity = {
        **{
            f"sherpa:{name}": item["hash"]
            for name, item in native_lock["sherpaOnnx"]["dependencies"].items()
        },
        **{
            f"onnx:{name}": item["hash"]
            for name, item in native_lock["onnxruntime"]["dependencies"].items()
        },
        **{
            f"patch:{index}": item["hash"]
            for index, item in enumerate(native_lock["onnxruntime"]["patches"])
        },
    }
    native_requests = updater._native_hash_requests({
        name: dict(dependency) for name, dependency in _ONNX_DEPENDENCIES.items()
    })
    native_hashes = [
        native_hashes_by_identity[identity] for identity, _expression in native_requests
    ]
    calls = install_fixed_hash_stream(
        monkeypatch,
        tuple(
            (f"hash-step-{index}", value)
            for index, value in enumerate((*_HASHES, *native_hashes))
        ),
    )

    events = run_async(collect_events(updater.fetch_hashes(_version_info(), object())))
    value_events = [event for event in events if event.kind is UpdateEventKind.VALUE]
    entries = cast("list[HashEntry]", expect_source_hashes(value_events[-1].payload))

    assert len(calls) == 28
    assert_nix_ast_equal(
        str(calls[0]["expr"]),
        _build_fetch_from_github_call(
            "getpaseo",
            "paseo",
            rev=_COMMIT,
            fetch_submodules=False,
        ),
    )
    assert_nix_ast_equal(
        str(calls[2]["expr"]),
        _build_fetch_from_github_call(
            "microsoft",
            "onnxruntime",
            rev=_ONNXRUNTIME_COMMIT,
            fetch_submodules=True,
        ),
    )
    assert_nix_ast_equal(
        str(calls[5]["expr"]),
        updater._npm_deps_expr(
            commit=_COMMIT,
            src_hash=_HASHES[0],
            version=_VERSION,
        ),
    )
    abseil_request_index = next(
        index
        for index, (identity, _expression) in enumerate(native_requests)
        if identity == "onnx:abseilCpp"
    )
    assert_nix_ast_equal(
        str(calls[6 + abseil_request_index]["expr"]),
        _build_fetch_from_github_call(
            "abseil",
            "abseil-cpp",
            rev=_ONNX_DEPENDENCIES["abseilCpp"]["commit"],
            fetch_submodules=False,
        ),
    )
    npm_expression = expect_instance(
        parse_nix_expr(
            updater._npm_deps_expr(
                commit=_COMMIT,
                src_hash=_HASHES[0],
                version=_VERSION,
            )
        ),
        FunctionCall,
    )
    npm_arguments = expect_instance(npm_expression.argument, AttributeSet)
    assert (
        expect_instance(
            expect_binding(npm_arguments.values, "fetcherVersion").value,
            Primitive,
        ).value
        == _NPM_FETCHER_VERSION
    )
    assert (
        HashEntry.create(
            "npmDepsHash",
            _HASHES[-1],
            url=_urls()["paseoUrl"],
        )
        in entries
    )
    artifact_event = next(
        event for event in events if event.kind is UpdateEventKind.ARTIFACT
    )
    native_lock["paseo"] = {
        "version": _VERSION,
        "commit": _COMMIT,
        "electronVersion": _ELECTRON_VERSION,
        "nodeAddonApiVersion": _NODE_ADDON_API_VERSION,
        "npmFetcherVersion": _NPM_FETCHER_VERSION,
        "esbuildVersion": _ESBUILD_VERSION,
        "claudeAgentSdkVersion": _CLAUDE_AGENT_SDK_VERSION,
        "appBuilderLibVersion": _APP_BUILDER_LIB_VERSION,
        "appBuilderLibBackportCommit": _APP_BUILDER_LIB_BACKPORT_COMMIT,
    }
    assert expect_artifact_updates(artifact_event.payload) == [
        GeneratedArtifact.json(_PACKAGE_DIR / "native-lock.json", native_lock)
    ]


def test_paseo_hashing_requires_a_discovered_artifact_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generated native lock cannot be promoted without package discovery."""
    module = _load_updater_module()

    class MissingArtifactDirectoryUpdater(module.PaseoUpdater):
        name = "missing-paseo-artifact-owner"

    updater = MissingArtifactDirectoryUpdater()
    install_fixed_hash_stream(
        monkeypatch,
        ((None, _HASHES[0]),) * 28,
    )

    with pytest.raises(
        RuntimeError,
        match="Package directory not found for missing-paseo-artifact-owner",
    ):
        run_async(collect_events(updater.fetch_hashes(_version_info(), object())))


def test_paseo_updater_owns_the_complete_native_lock() -> None:
    """The updater artifact is the sole authority for native dependency FODs."""
    updater = _load_updater_module().PaseoUpdater()

    assert updater.get_generated_artifact_files() == ("native-lock.json",)

    native_lock = json.loads(
        (_PACKAGE_DIR / "native-lock.json").read_text(encoding="utf-8")
    )
    source = SourceEntry.model_validate_json(
        (_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8")
    )
    assert source.version is not None
    assert source.commit is not None
    assert source.electron_version is not None
    assert source.urls is not None
    assert native_lock["schemaVersion"] == 1
    paseo_lock = native_lock["paseo"]
    assert set(paseo_lock) == {
        "appBuilderLibBackportCommit",
        "appBuilderLibVersion",
        "claudeAgentSdkVersion",
        "commit",
        "electronVersion",
        "esbuildVersion",
        "nodeAddonApiVersion",
        "npmFetcherVersion",
        "version",
    }
    assert paseo_lock["version"] == source.version
    assert paseo_lock["commit"] == source.commit
    assert paseo_lock["electronVersion"] == source.electron_version
    assert paseo_lock["appBuilderLibBackportCommit"] == updater.get_compatibility_pin(
        "appBuilderLibBackportCommit"
    )
    assert paseo_lock["npmFetcherVersion"] == int(
        updater.get_compatibility_pin("npmFetcherVersion")
    )
    updater._required_metadata(
        VersionInfo(
            version=source.version,
            metadata={
                "appBuilderLibVersion": paseo_lock["appBuilderLibVersion"],
                "claudeAgentSdkVersion": paseo_lock["claudeAgentSdkVersion"],
                "commit": source.commit,
                "electronVersion": source.electron_version,
                "esbuildVersion": paseo_lock["esbuildVersion"],
                "nodeAddonApiUrl": source.urls["nodeAddonApi"],
                "nodeAddonApiVersion": paseo_lock["nodeAddonApiVersion"],
                "onnxruntimeCommit": native_lock["onnxruntime"]["commit"],
                "onnxruntimeUrl": source.urls["onnxruntime"],
                "onnxruntimeVersion": native_lock["onnxruntime"]["version"],
                "paseoUrl": source.urls["paseo"],
                "sherpaCommit": native_lock["sherpaOnnx"]["commit"],
                "sherpaOnnxNodeUrl": source.urls["sherpaOnnxNode"],
                "sherpaOnnxUrl": source.urls["sherpaOnnx"],
                "sherpaVersion": native_lock["sherpaOnnx"]["version"],
                "tag": f"v{source.version}",
            },
        )
    )
    assert native_lock["sherpaOnnx"]["version"] == _SHERPA_VERSION
    assert native_lock["sherpaOnnx"]["commit"] == _SHERPA_COMMIT
    assert native_lock["onnxruntime"]["version"] == _ONNXRUNTIME_VERSION
    assert native_lock["onnxruntime"]["commit"] == _ONNXRUNTIME_COMMIT
    assert set(native_lock["sherpaOnnx"]["dependencies"]) == {
        "eigen",
        "espeakNg",
        "hclustCpp",
        "json",
        "kaldiDecoder",
        "kaldiNativeFbank",
        "kaldifst",
        "kissfft",
        "openfst",
        "piperPhonemize",
        "simpleSentencepiece",
    }
    assert set(native_lock["onnxruntime"]["dependencies"]) == {
        "abseilCpp",
        "dlpack",
        "flatbuffers",
        "mp11",
        "onnx",
        "re2",
        "safeint",
    }
    assert all(
        set(dependency) == set(_ONNX_DEPENDENCIES[name]) | {"hash"}
        and dependency["commit"] == _ONNX_DEPENDENCIES[name]["commit"]
        for name, dependency in native_lock["onnxruntime"]["dependencies"].items()
    )
    assert len(native_lock["onnxruntime"]["patches"]) == 4

    hashes = {
        dependency["hash"]
        for closure in (native_lock["sherpaOnnx"], native_lock["onnxruntime"])
        for dependency in closure["dependencies"].values()
    }
    hashes.update(patch["hash"] for patch in native_lock["onnxruntime"]["patches"])
    assert hashes == {
        "sha256-uTxmfRtpJlzbTZ8w7CH4+su+izB880wLmUKDTG1P2+I=",
        "sha256-cMv0BQ56AUquGRQLBeVySdpHIPVhKEWfvjqTvq+XGuY=",
        "sha256-jxTgJMcJ1zr7QK5pyyLeS3Pbpny85A8uUYgT2oE5q1Y=",
        "sha256-S5LrDAbRBoP3RHzpQGy5fNS0U74Y1yeTIPey8CXBAYc=",
        "sha256-hcpGJTVZJUHrW6bSGEMAnPNHOPUbKLcfhIgqNpS1KL8=",
        "sha256-kXbMZvx84e34XPNVsG4yDFfbYpffdCd/V1GDRoiTz2E=",
        "sha256-xLcBojpAC9qAMlhrAsfg1egTp2WDLfYMI+bfnmKwEPQ=",
        "sha256-SXED5mQWjr45WAt1etvmFvbPhaFlcq9YHKe8QtCrE/0=",
        "sha256-XJjoLMUJxWGFAt3khguOoE2EOFDtV+bWtZC2RLJohT0=",
        "sha256-iWQaRkiaSJh1RkPOV72pybVLTKRkhf3AK/DchLhmZF0=",
        "sha256-F0ioIgYKNbqp9mCfhO/I61TcDnS57OPYI2e3EZ/cda8=",
        "sha256-PuS7MLwi824c4z4Cubh029DEUVYSNPD3MwCDsgzsp3Y=",
        "sha256-YqgzCyNywixebpHGx16tUuczmFS5pjCz5WjR89mv9eI=",
        "sha256-e+dNPNbCHYDXUS/W+hMqf/37fhVgEGzId6rhP3cToTE=",
        "sha256-cLPvjkf2Au+B19PJNrUkTW/VPxybi1MpPxnIl4oo4/o=",
        "sha256-UhtF+CWuyv5/Pq/5agLL4Y95YNP63W2BraprhRqJOag=",
        "sha256-IeANwJlJl45yf8iu/AZNDoiyIvTCZIeK1b74sdCfAIc=",
        "sha256-pjwjrqq6dfiVsXIhbBtbolhiysiFlFTnx5XcX77f+C0=",
        "sha256-FFAJuJse4nmNT3ixvEdlqzbr3edY46SqEFv7z/oo6m0=",
        "sha256-VlTHs0om20kTNvSVQaasSsa5JROliQy4k9BECTsBtbU=",
        "sha256-FSuPybX8f2VoxvLhcYx4rdChaiK8bSUDR32sN3Efwfc=",
        "sha256-vX+kaFiNdmqWI91JELcLpoaVIHBb5EPbI7rCAMYAx04=",
    }
    assert "sourceHash" not in native_lock["onnxruntime"]


def test_paseo_validates_the_materialized_darwin_package() -> None:
    """Promotion must build the candidate so every patch and native gate runs."""
    module = _load_updater_module()

    assert module.PaseoUpdater.get_derivation_validations() == (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=("aarch64-darwin",),
            mode="build",
        ),
    )


def test_paseo_derivations_consume_only_the_updater_owned_native_lock() -> None:
    """The package and both native helpers read the generated closure seam."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    native_lock_formal = next(
        argument
        for argument in package.argument_set
        if isinstance(argument, Identifier) and argument.name == "nativeLock"
    )
    assert_nix_ast_equal(
        native_lock_formal.default_value,
        "builtins.fromJSON (builtins.readFile ./native-lock.json)",
    )
    package_scope = expect_instance(package.output, IfExpression).scope
    assert_nix_ast_equal(
        expect_binding(package_scope, "paseoLock").value,
        "nativeLock.paseo or { }",
    )
    assert_nix_ast_equal(
        expect_binding(package_scope, "npmFetcherVersion").value,
        "paseoLock.npmFetcherVersion or null",
    )
    completeness_terms = []
    pending = [expect_binding(package_scope, "nativeLockComplete").value]
    while pending:
        term = pending.pop(0)
        if isinstance(term, BinaryExpression) and term.operator.name == "&&":
            pending[0:0] = [term.left, term.right]
        else:
            completeness_terms.append(term)
    assert_nix_ast_equal(
        completeness_terms[3],
        "builtins.isInt npmFetcherVersion",
    )
    assert_nix_ast_equal(
        completeness_terms[4],
        "npmFetcherVersion > 0",
    )
    assert_nix_ast_equal(
        expect_binding(package_scope, "completeGitHubDependency").value,
        """
        dependency:
        nonEmptyString (dependency.owner or null)
        && nonEmptyString (dependency.repo or null)
        && commitString (dependency.commit or null)
        && builtins.isString (dependency.hash or null)
        && lib.hasPrefix "sha256-" dependency.hash
        """,
    )
    assert_nix_ast_equal(
        completeness_terms[17],
        "builtins.length (builtins.attrValues onnxruntimeDependencies) == 7",
    )
    assert_nix_ast_equal(
        completeness_terms[18],
        "builtins.all completeGitHubDependency (builtins.attrValues onnxruntimeDependencies)",
    )
    assert_nix_ast_equal(
        expect_binding(package_scope, "sherpaClosure").value,
        "nativeLock.sherpaOnnx or { }",
    )
    assert_nix_ast_equal(
        expect_binding(package_scope, "onnxruntimeClosure").value,
        "nativeLock.onnxruntime or { }",
    )
    sherpa_node_addon = expect_instance(
        expect_binding(package_scope, "sherpaNodeAddon").value,
        FunctionCall,
    )
    sherpa_node_addon_arguments = expect_instance(
        sherpa_node_addon.argument,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(sherpa_node_addon_arguments.values, "closureContract").value,
        "sherpaClosure",
    )

    for filename, field in (
        ("sherpa-source.nix", "sherpaOnnx"),
        ("sherpa-node-addon.nix", "sherpaOnnx"),
        ("onnxruntime-source.nix", "onnxruntime"),
    ):
        helper = expect_instance(
            parse_nix_expr((_PACKAGE_DIR / filename).read_text(encoding="utf-8")),
            FunctionDefinition,
        )
        closure_contract_formal = next(
            argument
            for argument in helper.argument_set
            if isinstance(argument, Identifier) and argument.name == "closureContract"
        )
        assert_nix_ast_equal(
            closure_contract_formal.default_value,
            f"(builtins.fromJSON (builtins.readFile ./native-lock.json)).{field}",
        )


def test_paseo_build_result_requires_complete_exact_closure() -> None:
    """Partial hashes must not replace the intentionally blocked metadata."""
    module = _load_updater_module()
    updater = module.PaseoUpdater()
    urls = _urls()
    entries = [
        HashEntry.create("srcHash", value, url=url)
        for value, url in zip(
            _HASHES[:3],
            (urls["paseoUrl"], urls["sherpaOnnxUrl"], urls["onnxruntimeUrl"]),
            strict=True,
        )
    ]
    entries.extend((
        HashEntry.create("sha256", _HASHES[3], url=urls["nodeAddonApiUrl"]),
        HashEntry.create("sha256", _HASHES[4], url=urls["sherpaOnnxNodeUrl"]),
        HashEntry.create("npmDepsHash", _HASHES[5], url=urls["paseoUrl"]),
    ))

    with pytest.raises(RuntimeError, match="exact closure keys"):
        updater.build_result(_version_info(), entries[:-1])
    with pytest.raises(TypeError, match="structured hash entries"):
        updater.build_result(_version_info(), {"aarch64-darwin": _HASHES[-1]})

    result = updater.build_result(_version_info(), entries)
    assert result == SourceEntry.model_validate({
        "version": _VERSION,
        "commit": _COMMIT,
        "electronVersion": "41.2.0",
        "hashes": HashCollection.from_value(entries),
        "pins": updater.compatibility_pins,
        "urls": {
            "nodeAddonApi": urls["nodeAddonApiUrl"],
            "onnxruntime": urls["onnxruntimeUrl"],
            "paseo": urls["paseoUrl"],
            "sherpaOnnx": urls["sherpaOnnxUrl"],
            "sherpaOnnxNode": urls["sherpaOnnxNodeUrl"],
        },
    })


def test_paseo_build_result_rejects_unpinned_release_tag() -> None:
    """The serialization boundary must reject a release tag outside the audited pin."""
    module = _load_updater_module()
    updater = module.PaseoUpdater()

    with pytest.raises(RuntimeError, match="release tag must be 'v0.6.1'"):
        updater.build_result(
            _version_info(tag="release-0.6.1"),
            _complete_hash_entries(_urls()),
        )


@pytest.mark.parametrize(
    ("metadata_key", "value", "expected_error"),
    [
        (
            "appBuilderLibVersion",
            "26.9.0",
            "supported appBuilderLibVersion must be",
        ),
        (
            "nodeAddonApiVersion",
            "8.4.0",
            "supported nodeAddonApiVersion must be",
        ),
        ("sherpaVersion", "1.13.0", "supported sherpaVersion must be"),
        (
            "onnxruntimeVersion",
            "1.24.0",
            "supported onnxruntimeVersion must be",
        ),
    ],
)
def test_paseo_build_result_rejects_unreviewed_native_inventory_versions(
    metadata_key: str,
    value: str,
    expected_error: str,
) -> None:
    """Persisted package metadata cannot bypass version-specific inventory pins."""
    module = _load_updater_module()
    updater = module.PaseoUpdater()

    with pytest.raises(RuntimeError, match=expected_error):
        updater.build_result(
            _version_info(**{metadata_key: value}),
            _complete_hash_entries(_urls()),
        )


@pytest.mark.parametrize(
    "metadata_key",
    [
        "nodeAddonApiUrl",
        "onnxruntimeUrl",
        "paseoUrl",
        "sherpaOnnxNodeUrl",
        "sherpaOnnxUrl",
    ],
)
def test_paseo_build_result_rejects_unpinned_source_url(metadata_key: str) -> None:
    """Each serialized source URL must remain tied to its audited package identity."""
    module = _load_updater_module()
    updater = module.PaseoUpdater()
    urls = _urls()
    forged_url = f"https://example.invalid/{metadata_key}"
    urls[metadata_key] = forged_url

    with pytest.raises(RuntimeError, match=f"{metadata_key} must be"):
        updater.build_result(
            _version_info(**{metadata_key: forged_url}),
            _complete_hash_entries(urls),
        )


def test_paseo_patcher_is_transactional_and_covers_all_mutation_surfaces(
    tmp_path: Path,
) -> None:
    """The ownership patch must validate, apply once, and reject source drift."""
    module = _load_patcher_module()
    root = tmp_path / "source"
    _write_patcher_fixture(root)
    before = {
        path: path.read_text(encoding="utf-8")
        for path in root.rglob("*")
        if path.is_file()
    }

    module.patch_tree(
        root,
        claude_code_executable=_TEST_CLAUDE_CODE_EXECUTABLE,
        check=True,
    )
    assert {path: path.read_text(encoding="utf-8") for path in before} == before

    module.patch_tree(
        root,
        claude_code_executable=_TEST_CLAUDE_CODE_EXECUTABLE,
    )
    for relative_path, effects in _PINNED_REPLACEMENT_FRAGMENTS.items():
        source = (root / relative_path).read_text(encoding="utf-8")
        assert all(effect in source for effect in effects)
    for relative_path, originals in _ORIGINAL_MUTATION_FRAGMENTS.items():
        source = (root / relative_path).read_text(encoding="utf-8")
        assert all(original not in source for original in originals)
    with pytest.raises(RuntimeError, match="managed-source patch anchor"):
        module.patch_tree(
            root,
            claude_code_executable=_TEST_CLAUDE_CODE_EXECUTABLE,
            check=True,
        )

    assert Counter(item.relative_path for item in module._PATCHES) == Counter(
        _REQUIRED_PATCH_COUNTS
    )
    assert len(module._PATCHES) == sum(_REQUIRED_PATCH_COUNTS.values())
    assert all(item.expected_count == 1 for item in module._PATCHES)
    assert {
        item.relative_path: item.expected_count for item in module._ANCHORS
    } == _REQUIRED_ANCHOR_COUNTS
    assert len(module._ANCHORS) == len(_REQUIRED_ANCHOR_COUNTS)


def test_paseo_patcher_disables_updates_without_renderer_import_meta(
    tmp_path: Path,
) -> None:
    """The packaged Expo renderer must not depend on an absent import-meta registry."""
    module = _load_patcher_module()
    root = tmp_path / "source"
    _write_patcher_fixture(root)

    module.patch_tree(
        root,
        claude_code_executable=_TEST_CLAUDE_CODE_EXECUTABLE,
    )

    updater_source = (
        root / "packages/app/src/desktop/updates/use-desktop-app-updater.ts"
    ).read_text(encoding="utf-8")
    assert "const isDesktopApp = false;" in updater_source
    assert "import.meta" not in updater_source


def test_paseo_patcher_rejects_missing_independent_anchor(tmp_path: Path) -> None:
    """A required unmodified update surface must remain present exactly once."""
    module = _load_patcher_module()
    root = tmp_path / "source"
    _write_patcher_fixture(root)
    path = root / _MISSING_ANCHOR_PATH
    path.write_text(
        path.read_text(encoding="utf-8").replace(_MISSING_ANCHOR_TEXT, "", 1),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="expected 1 managed-source anchor"):
        module.patch_tree(
            root,
            claude_code_executable=_TEST_CLAUDE_CODE_EXECUTABLE,
            check=True,
        )


def test_paseo_patcher_rejects_a_scoped_launch_change_hidden_by_a_decoy(
    tmp_path: Path,
) -> None:
    """A matching comment cannot replace the reviewed availability launch site."""
    module = _load_patcher_module()
    root = tmp_path / "source"
    _write_patcher_fixture(root)
    provider = root / _CLAUDE_PROVIDER_PATH
    source = provider.read_bytes()
    availability_launch = b"""\
  async isAvailable(): Promise<boolean> {
    const launch = await resolveProviderLaunch({
      commandConfig: this.runtimeSettings?.command,
      defaultBinary: "claude",
    });
"""
    assert source.count(availability_launch) == 1
    provider.write_bytes(
        b'// defaultBinary: "claude",\n'
        + source.replace(
            availability_launch,
            availability_launch.replace(b'"claude"', b'"codexx"'),
        )
    )

    with pytest.raises(RuntimeError, match="Claude executable wiring"):
        module.patch_tree(
            root,
            claude_code_executable=_TEST_CLAUDE_CODE_EXECUTABLE,
        )


def test_paseo_patcher_rejects_an_additional_provider_launch(tmp_path: Path) -> None:
    """Every Claude process launch must be one of the four reviewed call sites."""
    module = _load_patcher_module()
    root = tmp_path / "source"
    _write_patcher_fixture(root)
    provider = root / _CLAUDE_PROVIDER_PATH
    provider.write_bytes(
        b"""\
async function unreviewedLaunch(): Promise<void> {
  await resolveProviderLaunch({ defaultBinary: "codexx" });
}

"""
        + provider.read_bytes()
    )

    with pytest.raises(RuntimeError, match="Claude executable wiring"):
        module.patch_tree(
            root,
            claude_code_executable=_TEST_CLAUDE_CODE_EXECUTABLE,
        )


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            b"export class ClaudeAgentClient",
            b"false && class ClaudeAgentClient",
        ),
        (
            _CLAUDE_RESOLVE_ASSIGNMENT,
            b"if (false)\n      " + _CLAUDE_RESOLVE_ASSIGNMENT,
        ),
        (
            _CLAUDE_BINARY_RESOLUTION,
            b"return bundledOptions;\n    " + _CLAUDE_BINARY_RESOLUTION,
        ),
        (
            _CLAUDE_BINARY_RESOLUTION,
            _CLAUDE_BINARY_RESOLUTION + b"    return bundledOptions;\n",
        ),
        (
            _CLAUDE_OPTIONS_HANDOFF,
            _CLAUDE_OPTIONS_HANDOFF
            + b"\n      ...{ pathToClaudeCodeExecutable: bundledSdkBinary },",
        ),
        (
            b"export class ClaudeAgentClient",
            b"@disabled\nexport class ClaudeAgentClient",
        ),
        (
            b"  private async buildOptions",
            b"  @disabled\n  private async buildOptions",
        ),
    ],
    ids=(
        "short-circuited-client-owner",
        "unbraced-dead-constructor",
        "unreachable-build-chain",
        "post-resolution-early-return",
        "later-spread-override",
        "client-class-decorator",
        "method-decorator",
    ),
)
def test_paseo_patcher_rejects_claude_control_flow_drift(
    tmp_path: Path,
    old: bytes,
    new: bytes,
) -> None:
    """Targeted wiring checks reject dead paths and later executable overrides."""
    module = _load_patcher_module()
    root = tmp_path / "source"
    _write_patcher_fixture(root)
    provider = root / _CLAUDE_PROVIDER_PATH
    source = provider.read_bytes()
    assert source.count(old) == 1
    provider.write_bytes(source.replace(old, new))
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}

    with pytest.raises(RuntimeError, match="reviewed Claude"):
        module.patch_tree(
            root,
            claude_code_executable=_TEST_CLAUDE_CODE_EXECUTABLE,
        )

    assert {path: path.read_bytes() for path in before} == before


def test_paseo_patcher_rejects_non_utf8_provider_source(tmp_path: Path) -> None:
    """Provider source must remain valid UTF-8 before structural patching."""
    module = _load_patcher_module()
    root = tmp_path / "source"
    _write_patcher_fixture(root)
    provider = root / _CLAUDE_PROVIDER_PATH
    provider.write_bytes(b"\xff" + provider.read_bytes())

    with pytest.raises(UnicodeDecodeError):
        module.patch_tree(
            root,
            claude_code_executable=_TEST_CLAUDE_CODE_EXECUTABLE,
        )


@pytest.mark.parametrize("resolver_count", [0, 2])
def test_paseo_patcher_rejects_ambiguous_claude_resolver_function(
    tmp_path: Path,
    resolver_count: int,
) -> None:
    """The package-owned executable must target exactly one reviewed resolver."""
    module = _load_patcher_module()
    root = tmp_path / "source"
    _write_patcher_fixture(root)
    provider = root / _CLAUDE_PROVIDER_PATH
    source = provider.read_bytes()
    if resolver_count == 0:
        source = source.replace(_CLAUDE_RESOLVER_FIXTURE, b"")
    else:
        source = _CLAUDE_RESOLVER_FIXTURE + source
    provider.write_bytes(source)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}

    with pytest.raises(RuntimeError, match="reviewed Claude resolver function"):
        module.patch_tree(
            root,
            claude_code_executable=_TEST_CLAUDE_CODE_EXECUTABLE,
        )

    assert {path: path.read_bytes() for path in before} == before


def test_paseo_patcher_rewrites_every_reviewed_claude_launch_default(
    tmp_path: Path,
) -> None:
    """Availability, diagnostics, versions, and sessions share the store path."""
    module = _load_patcher_module()
    root = tmp_path / "source"
    _write_patcher_fixture(root)

    module.patch_tree(
        root,
        claude_code_executable=_TEST_CLAUDE_CODE_EXECUTABLE,
    )

    provider = (root / _CLAUDE_PROVIDER_PATH).read_bytes()
    package_default = (
        b"defaultBinary: " + json.dumps(_TEST_CLAUDE_CODE_EXECUTABLE).encode() + b","
    )
    assert provider.count(_CLAUDE_DEFAULT_BINARY) == 0
    assert provider.count(package_default) == _CLAUDE_DEFAULT_BINARY_COUNT


def test_paseo_patcher_rejects_incomplete_claude_launch_defaults_after_repin(
    tmp_path: Path,
) -> None:
    """A deliberate source re-pin cannot leave one ambient-PATH launch seam."""
    module = _load_patcher_module()
    root = tmp_path / "source"
    _write_patcher_fixture(root)
    provider = root / _CLAUDE_PROVIDER_PATH
    source = provider.read_bytes()
    assert source.count(_CLAUDE_DEFAULT_BINARY) == _CLAUDE_DEFAULT_BINARY_COUNT
    prefix, separator, suffix = source.rpartition(_CLAUDE_DEFAULT_BINARY)
    assert separator == _CLAUDE_DEFAULT_BINARY
    source = prefix + b'defaultBinary: "codexx",' + suffix
    provider.write_bytes(source)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}

    with pytest.raises(RuntimeError, match="reviewed Claude launch defaults"):
        module.patch_tree(
            root,
            claude_code_executable=_TEST_CLAUDE_CODE_EXECUTABLE,
        )

    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("tail_count", [0, 2])
def test_paseo_patcher_rejects_ambiguous_claude_build_options_tail(
    tmp_path: Path,
    tail_count: int,
) -> None:
    """A deliberate re-pin must retain one exact final SDK options handoff."""
    module = _load_patcher_module()
    root = tmp_path / "source"
    _write_patcher_fixture(root)
    provider = root / _CLAUDE_PROVIDER_PATH
    source = provider.read_bytes()
    if tail_count == 0:
        source = source.replace(_CLAUDE_BUILD_OPTIONS_TAIL_FIXTURE, b"")
    else:
        source = _CLAUDE_BUILD_OPTIONS_TAIL_FIXTURE + source
    provider.write_bytes(source)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}

    with pytest.raises(RuntimeError, match="reviewed Claude buildOptions tail"):
        module.patch_tree(
            root,
            claude_code_executable=_TEST_CLAUDE_CODE_EXECUTABLE,
        )

    assert {path: path.read_bytes() for path in before} == before


def test_paseo_patcher_makes_the_claude_executable_the_final_base_mutation(
    tmp_path: Path,
) -> None:
    """Later options logic cannot override the package-owned executable."""
    module = _load_patcher_module()
    root = tmp_path / "source"
    _write_patcher_fixture(root)

    module.patch_tree(
        root,
        claude_code_executable=_TEST_CLAUDE_CODE_EXECUTABLE,
    )

    provider = (root / _CLAUDE_PROVIDER_PATH).read_text(encoding="utf-8")
    final_handoff = (
        "    base.pathToClaudeCodeExecutable = claudeBinary;\n    return base;"
    )
    assert provider.count(final_handoff) == 1
    assert provider.count("pathToClaudeCodeExecutable: claudeBinary,") == 1


def test_paseo_patcher_json_escapes_the_absolute_claude_executable(
    tmp_path: Path,
) -> None:
    """The injected TypeScript string must be valid for any absolute Nix path."""
    module = _load_patcher_module()
    root = tmp_path / "source"
    _write_patcher_fixture(root)
    executable = '/nix/store/fake-"quoted"\\claude/bin/claude'

    module.patch_tree(root, claude_code_executable=executable)

    provider = (root / _CLAUDE_PROVIDER_PATH).read_text(encoding="utf-8")
    assert provider.count(f"defaultBinary: {json.dumps(executable)},") == (
        _CLAUDE_DEFAULT_BINARY_COUNT
    )
    assert "pathToClaudeCodeExecutable: claudeBinary," in provider


def test_paseo_patcher_rejects_a_relative_claude_executable(tmp_path: Path) -> None:
    """Finder launches must never depend on the ambient command search path."""
    module = _load_patcher_module()
    root = tmp_path / "source"
    _write_patcher_fixture(root)

    with pytest.raises(ValueError, match="must be an absolute path"):
        module.patch_tree(root, claude_code_executable="claude")


def test_paseo_patcher_cli_checks_then_applies(tmp_path: Path) -> None:
    """The package-facing CLI supports both dry-run and transactional apply."""
    module = _load_patcher_module()
    root = tmp_path / "source"
    _write_patcher_fixture(root)

    cli_args = [
        str(root),
        "--claude-code-executable",
        _TEST_CLAUDE_CODE_EXECUTABLE,
    ]
    assert module.main([*cli_args, "--check"]) == 0
    assert module.main(cli_args) == 0
    for relative_path, effects in _PINNED_REPLACEMENT_FRAGMENTS.items():
        source = (root / relative_path).read_text(encoding="utf-8")
        assert all(effect in source for effect in effects)


def test_paseo_source_metadata_is_complete_and_the_manifest_is_default() -> None:
    """The generated source graph must remain complete across routine releases."""
    source = SourceEntry.model_validate_json(
        (_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8")
    )
    entries = source.hashes.entries
    assert entries is not None
    assert source.version is not None
    assert source.commit is not None
    assert source.electron_version is not None
    assert source.urls is not None
    assert set(source.urls) == {
        "nodeAddonApi",
        "onnxruntime",
        "paseo",
        "sherpaOnnx",
        "sherpaOnnxNode",
    }
    expected = {
        ("srcHash", source.urls["paseo"]),
        ("srcHash", source.urls["sherpaOnnx"]),
        ("srcHash", source.urls["onnxruntime"]),
        ("sha256", source.urls["nodeAddonApi"]),
        ("sha256", source.urls["sherpaOnnxNode"]),
        ("npmDepsHash", source.urls["paseo"]),
    }
    assert len(entries) == len(expected)
    assert {(entry.hash_type, entry.url) for entry in entries} == expected
    assert all(
        not entry.hash.startswith(HashCollection.FAKE_HASH_PREFIX) for entry in entries
    )
    wrapper = parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8"))
    assert_nix_ast_equal(
        wrapper,
        """
        {
          callPackage,
          expectedNativeManifest ? ./native-manifest.txt,
          selfSource ? builtins.fromJSON (builtins.readFile ./sources.json),
          ...
        }:
        callPackage ./package.nix {
          inherit expectedNativeManifest selfSource;
        }
        """,
    )

    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    condition = expect_instance(final.condition, BinaryExpression)
    assert condition.operator.name == "=="
    assert expect_instance(condition.left, Identifier).name == "unresolvedBuildGates"
    assert expect_instance(condition.right, NixList).value == []
    assert expect_instance(final.consequence, Identifier).name == "realPackage"
    assert expect_instance(final.alternative, Identifier).name == "blockedPackage"


def test_paseo_registry_exports_only_on_arm64_darwin() -> None:
    """Discovery and platform metadata must expose only the audited target."""
    registry = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/registry.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    registry_output = expect_instance(registry.output, AttributeSet)

    assert registry_override_metadata(registry_output)["paseo"] == {
        "constraint": ["aarch64-darwin"]
    }


def test_paseo_reviewed_native_manifest_matches_the_realized_inventory() -> None:
    """The realized package must retain the exact reviewed native inventory."""
    manifest = (_PACKAGE_DIR / "native-manifest.txt").read_bytes()

    assert manifest.endswith(b"\n")
    assert not manifest.endswith(b"\n\n")
    assert sha256(manifest).hexdigest() == (
        "841ed05049fdf6919d1c8124fcb00dfb22403be4b9c16bf59bee440ecc51e0ad"
    )
    rows = manifest.decode("utf-8").splitlines()
    assert len(rows) == 201
    assert rows == sorted(rows, key=str.encode)
    assert len(rows) == len(set(rows))
    assert all(
        count.isdecimal() and int(count) > 0 and path and "\t" not in path
        for count, path in (row.split("\t", maxsplit=1) for row in rows)
    )
    assert "0\t__PASEO_NATIVE_MANIFEST_PENDING__" not in rows
    assert (
        "1\tContents/Resources/app.asar.unpacked/node_modules/@esbuild/"
        "darwin-arm64/bin/esbuild"
    ) in rows
    assert "1\tapp.asar/node_modules/@esbuild/darwin-arm64/bin/esbuild" in rows
    assert (
        "1\tContents/Resources/app.asar.unpacked/node_modules/node-pty/"
        "build/Release/pty.node"
    ) in rows
    assert (
        "1\tContents/Resources/app.asar.unpacked/node_modules/node-pty/"
        "build/Release/spawn-helper"
    ) in rows


def test_paseo_native_validator_uses_safe_otool_aliases(
    tmp_path: Path,
) -> None:
    """All otool modes must avoid archive-member parsing of helper names."""
    app = tmp_path / "Paseo.app"
    asar_root = tmp_path / "asar"
    asar_root.mkdir()
    helper_relative = (
        "Contents/Frameworks/Paseo Helper (GPU).app/Contents/MacOS/Paseo Helper (GPU)"
    )
    native_descriptions = {
        "Contents/MacOS/Paseo": "Mach-O 64-bit arm64 executable",
        (
            "Contents/Resources/app.asar.unpacked/node_modules/node-pty/"
            "build/Release/pty.node"
        ): "Mach-O 64-bit arm64 dynamically linked shared library",
        (
            "Contents/Resources/app.asar.unpacked/node_modules/"
            "sherpa-onnx-darwin-arm64/sherpa-onnx.node"
        ): "Mach-O 64-bit arm64 dynamically linked shared library",
        helper_relative: "Mach-O 64-bit arm64 executable",
    }
    metadata: dict[str, dict[str, object]] = {}
    for relative_path, description in native_descriptions.items():
        candidate = app / relative_path
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(b"fixture Mach-O\n")
        candidate.chmod(0o755)
        metadata[str(candidate.resolve())] = {
            "dependencies": ["/usr/lib/libSystem.B.dylib"],
            "description": description,
            "id": None if "executable" in description else f"@rpath/{candidate.name}",
            "rpaths": [],
        }

    metadata_path = tmp_path / "macho.json"
    metadata_path.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
    otool_calls = tmp_path / "otool-calls.jsonl"
    tool_program = f"#!{sys.executable}\n" + dedent(
        """\
        import json
        import os
        from pathlib import Path
        import sys

        metadata = json.loads(Path(os.environ["PASEO_MACHO_FIXTURE"]).read_text())
        tool = Path(sys.argv[0]).name
        candidate = sys.argv[-1]
        resolved_candidate = str(Path(candidate).resolve(strict=True))
        record = metadata.get(resolved_candidate)
        if tool == "file":
            print(record["description"] if record else "ASCII text")
        elif tool == "lipo":
            print("arm64")
        elif tool == "otool":
            if "(" in candidate or ")" in candidate:
                raise SystemExit("parenthesized otool argv rejected")
            call = {
                "argument": candidate,
                "operation": sys.argv[1],
                "resolved": resolved_candidate,
            }
            with Path(os.environ["PASEO_OTOOL_CALLS"]).open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write(json.dumps(call, sort_keys=True) + "\\n")
            if record is None:
                raise SystemExit(f"unknown otool target: {candidate}")
            if (
                os.environ.get("PASEO_OTOOL_FAIL_TARGET") == resolved_candidate
                and sys.argv[1] == "-L"
            ):
                raise SystemExit("forced otool fixture failure")
            if sys.argv[1] == "-D":
                print(f"{candidate}:")
                if record["id"]:
                    print(record["id"])
            elif sys.argv[1] == "-L":
                print(f"{candidate}:")
                for dependency in record["dependencies"]:
                    print(
                        f"    {dependency} (compatibility version 0.0.0, "
                        "current version 0.0.0)"
                    )
            elif sys.argv[1] == "-l":
                for index, rpath in enumerate(record["rpaths"]):
                    print(f"Load command {index}")
                    print("          cmd LC_RPATH")
                    print("      cmdsize 48")
                    print(f"         path {rpath} (offset 12)")
            else:
                raise SystemExit(f"unsupported otool invocation: {sys.argv!r}")
        else:
            raise SystemExit(f"unsupported fixture invocation: {sys.argv!r}")
        """
    )
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    tools: dict[str, Path] = {}
    for name in ("file", "lipo", "otool"):
        tool_path = tool_dir / name
        tool_path.write_text(tool_program, encoding="utf-8")
        tool_path.chmod(0o755)
        tools[name] = tool_path

    expected_manifest = tmp_path / "expected-manifest"
    expected_manifest.write_text(
        "".join(f"1\t{path}\n" for path in sorted(native_descriptions)),
        encoding="utf-8",
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    helper = str((app / helper_relative).resolve())
    validator_argv = [
        "/bin/bash",
        str(_PACKAGE_DIR / "validate-native-bundle.sh"),
        str(app),
        str(asar_root),
        str(expected_manifest),
        "Paseo",
    ]
    validator_env = os.environ | {
        "PASEO_FILE_TOOL": str(tools["file"]),
        "PASEO_LIPO_TOOL": str(tools["lipo"]),
        "PASEO_MACHO_FIXTURE": str(metadata_path),
        "PASEO_OTOOL_CALLS": str(otool_calls),
        "PASEO_OTOOL_TOOL": str(tools["otool"]),
        "PASEO_PYTHON": sys.executable,
        "TMPDIR": str(scratch),
    }
    completed = subprocess.run(  # noqa: S603
        validator_argv,
        env=validator_env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    calls = [
        json.loads(line)
        for line in otool_calls.read_text(encoding="utf-8").splitlines()
    ]
    assert calls
    assert all(
        Path(call["argument"]).is_relative_to(scratch)
        and "(" not in call["argument"]
        and ")" not in call["argument"]
        for call in calls
    )
    assert {call["operation"] for call in calls if call["resolved"] == helper} == {
        "-D",
        "-L",
        "-l",
    }

    failed = subprocess.run(  # noqa: S603
        validator_argv,
        env=validator_env | {"PASEO_OTOOL_FAIL_TARGET": helper},
        check=False,
        capture_output=True,
        text=True,
    )
    assert failed.returncode != 0
    assert (
        "Mach-O inspection failed for "
        f"{helper_relative}: forced otool fixture failure" in failed.stderr
    )


def test_paseo_blocked_foundation_does_not_advertise_a_mac_app() -> None:
    """Only a fully ungated real package may expose the Applications route."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    common_passthru = expect_instance(
        expect_binding(final.scope, "commonPassthru").value,
        AttributeSet,
    )
    assert "macApp" not in binding_map(common_passthru.values)

    blocked_package = expect_instance(
        expect_binding(final.scope, "blockedPackage").value,
        FunctionCall,
    )
    blocked_arguments = expect_instance(blocked_package.argument, AttributeSet)
    blocked_passthru = expect_instance(
        expect_binding(blocked_arguments.values, "passthru").value,
        BinaryExpression,
    )
    assert blocked_passthru.operator.name == "//"
    assert_nix_ast_equal(blocked_passthru.left, "commonPassthru")
    audit_leaves = expect_instance(blocked_passthru.right, AttributeSet)
    assert_nix_ast_equal(
        audit_leaves,
        """
        {
          inherit
            onnxruntimeExact
            sherpaExact
            sherpaNodeAddon
            ;
        }
        """,
    )
    assert {"electronBuild", "macApp", "npmDeps"}.isdisjoint(
        binding_map(audit_leaves.values)
    )

    real_package = expect_instance(
        expect_binding(final.scope, "realPackage").value,
        FunctionCall,
    )
    real_arguments = expect_instance(real_package.argument, AttributeSet)
    real_passthru = expect_instance(
        expect_binding(real_arguments.values, "passthru").value,
        BinaryExpression,
    )
    assert real_passthru.operator.name == "//"
    assert_nix_ast_equal(real_passthru.left, "commonPassthru")
    real_only = expect_instance(real_passthru.right, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(real_only.values, "macApp").value,
        """
        {
          bundleId = appId;
          bundleName = appBundleName;
          bundleRelPath = "Applications/${appBundleName}";
          installMode = "copy";
        }
        """,
    )


def test_paseo_requires_the_exact_sherpa_wrapper_url() -> None:
    """The executable wrapper input must retain its audited npm origin."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)

    assert_nix_ast_equal(
        expect_binding(final.scope, "expectedSherpaWrapperUrl").value,
        '"https://registry.npmjs.org/sherpa-onnx-node/-/'
        'sherpa-onnx-node-${sherpaVersion}.tgz"',
    )

    gates = []
    pending = [expect_binding(final.scope, "unresolvedBuildGates").value]
    while pending:
        gate = pending.pop(0)
        if isinstance(gate, BinaryExpression) and gate.operator.name == "++":
            pending[0:0] = [gate.left, gate.right]
        else:
            gates.append(gate)
    assert_nix_ast_equal(
        gates[8],
        "lib.optional "
        "(sherpaWrapperUrl != expectedSherpaWrapperUrl) "
        '"sherpa-onnx-node wrapper URL must be exactly ${expectedSherpaWrapperUrl}"',
    )


def test_paseo_fetches_the_onnxruntime_submodule_closure() -> None:
    """The realized ONNX Runtime source must match the updater's closure hash."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)

    assert_nix_ast_equal(
        expect_binding(final.scope, "onnxruntimeSrc").value,
        FunctionCall(
            name=Identifier(name="fetchFromGitHub"),
            argument=AttributeSet(
                values=[
                    Binding(name="owner", value=StringPrimitive(value="microsoft")),
                    Binding(name="repo", value=StringPrimitive(value="onnxruntime")),
                    Binding(
                        name="rev",
                        value=identifier_attr_path("onnxruntimeClosure", "commit"),
                    ),
                    Binding(name="fetchSubmodules", value=Primitive(value=True)),
                    Inherit(
                        from_expression=Identifier(name="onnxruntimeSourceHash"),
                        names=[Identifier(name="hash")],
                    ),
                ]
            ),
        ),
    )


def test_paseo_onnxruntime_helper_declares_the_reviewed_source_closure() -> None:
    """The helper fetches immutable sources and requires protobuf_32 capability."""
    helper = expect_instance(
        parse_nix_expr(
            (_PACKAGE_DIR / "onnxruntime-source.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(helper.output, FunctionCall)
    assert_nix_ast_equal(
        expect_binding(derivation.scope, "effectiveClosureContract").value,
        "closureContract // { inherit sourceHash; }",
    )
    assert_nix_ast_equal(
        expect_binding(derivation.scope, "fetchGitHubDependency").value,
        FunctionDefinition(
            argument_set=Identifier(name="dependency"),
            output=FunctionCall(
                name=Identifier(name="fetchFromGitHub"),
                argument=AttributeSet(
                    values=[
                        Inherit(
                            from_expression=Identifier(name="dependency"),
                            names=[
                                Identifier(name="owner"),
                                Identifier(name="repo"),
                                Identifier(name="hash"),
                            ],
                            name_gaps=[" ", " "],
                        ),
                        Binding(
                            name="rev",
                            value=identifier_attr_path("dependency", "commit"),
                        ),
                    ]
                ),
            ),
        ),
    )
    assert_nix_ast_equal(
        expect_binding(derivation.scope, "abseilCppSrc").value,
        "fetchGitHubDependency closureContract.dependencies.abseilCpp",
    )
    assert_nix_ast_equal(
        expect_binding(derivation.scope, "onnxPatches").value,
        'builtins.filter (patch: patch.target == "onnx") closureContract.patches',
    )

    parenthesized = expect_instance(derivation.argument, Parenthesis)
    final_attrs = expect_instance(parenthesized.value, FunctionDefinition)
    attributes = expect_instance(final_attrs.output, AttributeSet)
    passthru = expect_instance(
        expect_binding(attributes.values, "passthru").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "paseoExactSource").value,
        "effectiveClosureContract",
    )
    formal_names = {
        formal.name for formal in helper.argument_set if isinstance(formal, Identifier)
    }
    assert "protobuf_32" in formal_names
    assert "protobuf" not in formal_names
    assert_nix_ast_equal(
        expect_binding(derivation.scope, "protobuf32").value,
        """
        assert lib.assertMsg
          (lib.versions.major (lib.getVersion protobuf_32) == "32")
          "Paseo ONNX Runtime requires the protobuf_32 major version lane";
        protobuf_32
        """,
    )
    assert_nix_ast_equal(
        expect_binding(attributes.values, "nativeBuildInputs").value,
        "[ cmake pkg-config protobuf32 python3 ]",
    )
    assert_nix_ast_equal(
        expect_binding(attributes.values, "buildInputs").value,
        """
        [
          eigen
          glibcLocales
          howard-hinnant-date
          libpng
          microsoft-gsl
          nlohmann_json
          protobuf32
          zlib
        ]
        ++ lib.optional (lib.meta.availableOn stdenv.hostPlatform cpuinfo) cpuinfo
        ++ [ (darwinMinVersionHook "13.3") ]
        """,
    )
    cmake_flags = expect_instance(
        expect_binding(attributes.values, "cmakeFlags").value,
        NixList,
    )
    assert_nix_ast_equal(
        cmake_flags.value[13],
        '(lib.cmakeFeature "ONNX_CUSTOM_PROTOC_EXECUTABLE" (lib.getExe protobuf32))',
    )
    assert_nix_ast_equal(
        expect_binding(attributes.values, "passthru").value,
        """
        {
          paseoExactSource = effectiveClosureContract;
          protobuf = protobuf32;
        }
        """,
    )

    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    helper_call = expect_instance(
        expect_binding(final.scope, "onnxruntimeExact").value,
        FunctionCall,
    )
    helper_arguments = expect_instance(helper_call.argument, AttributeSet)
    assert "sourceClosureComplete" not in binding_map(helper_arguments.values)
    assert_nix_ast_equal(
        expect_binding(helper_arguments.values, "closureContract").value,
        "onnxruntimeClosure",
    )
    assert_nix_ast_equal(
        expect_binding(helper_arguments.values, "sourceHash").value,
        "onnxruntimeSourceHash.hash",
    )


def test_paseo_onnxruntime_pkgconfig_relocation_is_a_reviewed_patch(
    tmp_path: Path,
) -> None:
    """The static pkg-config correction must be reviewable and apply exactly."""
    helper = expect_instance(
        parse_nix_expr(
            (_PACKAGE_DIR / "onnxruntime-source.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(helper.output, FunctionCall)
    parenthesized = expect_instance(derivation.argument, Parenthesis)
    final_attrs = expect_instance(parenthesized.value, FunctionDefinition)
    attributes = expect_instance(final_attrs.output, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(attributes.values, "patches").value,
        """(map fetchReviewedPatch onnxruntimePatches) ++ [
          ./protobuf34-nodiscard.patch
          ./onnxruntime-pkgconfig-prefix.patch
        ]""",
    )
    post_patch = expect_instance(
        expect_binding(attributes.values, "postPatch").value,
        IndentedString,
    )
    post_patch_shell = parse_shell(indented_string_body(post_patch.rebuild()))
    assert command_texts(post_patch_shell, "substituteInPlace") == [
        '''substituteInPlace onnxruntime/core/platform/env.h \\
      --replace-fail \\
        "GetRuntimePath() const { return PathString(); }" \\
        "GetRuntimePath() const { return PathString(\\"$out/lib/\\"); }"''',
        '''substituteInPlace cmake/onnxruntime.cmake \\
      --replace-fail "INSTALL_NAME_DIR @rpath" "INSTALL_NAME_DIR $out/lib"''',
    ]

    patch_executable = shutil.which("patch")
    assert patch_executable is not None
    source = tmp_path / "cmake/libonnxruntime.pc.cmake.in"
    source.parent.mkdir()
    source.write_text(
        """\
prefix=@CMAKE_INSTALL_PREFIX@
bindir=${prefix}/@CMAKE_INSTALL_BINDIR@
mandir=${prefix}/@CMAKE_INSTALL_MANDIR@
docdir=${prefix}/@CMAKE_INSTALL_DOCDIR@
libdir=${prefix}/@CMAKE_INSTALL_LIBDIR@
includedir=${prefix}/@CMAKE_INSTALL_INCLUDEDIR@/@CMAKE_PROJECT_NAME@

Name: @CMAKE_PROJECT_NAME@
Description: ONNX runtime
URL: https://github.com/microsoft/@CMAKE_PROJECT_NAME@
Version: @ORT_VERSION@
Libs: -L${libdir} -l@CMAKE_PROJECT_NAME@
Cflags: -I${includedir}
""",
        encoding="utf-8",
    )
    subprocess.run(  # noqa: S603
        [
            patch_executable,
            "-p1",
            "-i",
            str(_PACKAGE_DIR / "onnxruntime-pkgconfig-prefix.patch"),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert (
        source.read_text(encoding="utf-8")
        == """\
prefix=@CMAKE_INSTALL_PREFIX@
bindir=@CMAKE_INSTALL_BINDIR@
mandir=@CMAKE_INSTALL_MANDIR@
docdir=@CMAKE_INSTALL_DOCDIR@
libdir=@CMAKE_INSTALL_LIBDIR@
includedir=@CMAKE_INSTALL_INCLUDEDIR@/@CMAKE_PROJECT_NAME@

Name: @CMAKE_PROJECT_NAME@
Description: ONNX runtime
URL: https://github.com/microsoft/@CMAKE_PROJECT_NAME@
Version: @ORT_VERSION@
Libs: -L${libdir} -l@CMAKE_PROJECT_NAME@
Cflags: -I${includedir}
"""
    )


def test_paseo_sherpa_helper_declares_the_reviewed_fetchcontent_closure(
    tmp_path: Path,
) -> None:
    """The npm-addon-equivalent Sherpa build must be network-complete."""
    helper = expect_instance(
        parse_nix_expr(
            (_PACKAGE_DIR / "sherpa-source.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(helper.output, FunctionCall)
    assert_nix_ast_equal(
        expect_binding(derivation.scope, "fetchContentCache").value,
        """
        lib.mapAttrsToList (_: dependency: {
          name = dependency.file;
          src = fetchurl {
            inherit (dependency) url hash;
          };
        }) closureContract.dependencies
        """,
    )

    attributes = expect_instance(derivation.argument, AttributeSet)
    preconfigure = expect_instance(
        expect_binding(attributes.values, "preConfigure").value,
        IndentedString,
    )
    preconfigure_interpolation = preconfigure.value.strip()
    assert preconfigure_interpolation.startswith("${")
    assert preconfigure_interpolation.endswith("}")
    assert_nix_ast_equal(
        parse_nix_expr(preconfigure_interpolation[2:-1]),
        r"""
        lib.concatMapStringsSep "\n"
          (dependency: "cp ${dependency.src} ./${dependency.name}")
          fetchContentCache
        """,
    )
    patches = expect_instance(
        expect_binding(attributes.values, "patches").value,
        NixList,
    )
    assert [expect_instance(patch, NixPath).path for patch in patches.value] == [
        "./sherpa-use-external-onnxruntime.patch"
    ]
    assert "postPatch" not in binding_map(attributes.values)

    patch_executable = shutil.which("patch")
    assert patch_executable is not None
    source = tmp_path / "cmake/onnxruntime.cmake"
    source.parent.mkdir()
    source.write_text(
        "\n" * 214
        + """\
    message(STATUS "onnxruntime lib files: ${onnxruntime_lib_files}")

    install(FILES ${onnxruntime_lib_files} DESTINATION lib)

    if(WIN32)
      install(FILES ${onnxruntime_lib_files} DESTINATION bin)
    endif()
""",
        encoding="utf-8",
    )
    subprocess.run(  # noqa: S603
        [
            patch_executable,
            "-p1",
            "-i",
            str(_PACKAGE_DIR / "sherpa-use-external-onnxruntime.patch"),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert (
        source.read_text(encoding="utf-8")
        == "\n" * 214
        + """\
    message(STATUS "onnxruntime lib files: ${onnxruntime_lib_files}")

    # Paseo bundles the exact ONNX Runtime output separately.

    if(WIN32)
      install(FILES ${onnxruntime_lib_files} DESTINATION bin)
    endif()
"""
    )
    assert_nix_ast_equal(
        expect_binding(attributes.values, "cmakeFlags").value,
        """
        [
          (lib.cmakeBool "FETCHCONTENT_QUIET" false)
          (lib.cmakeBool "BUILD_SHARED_LIBS" true)
          (lib.cmakeFeature "CMAKE_CXX_FLAGS" "-DSHERPA_ONNX_DISABLE_COREML")
          (lib.cmakeBool "SHERPA_ONNX_USE_PRE_INSTALLED_ONNXRUNTIME_IF_AVAILABLE" true)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_BINARY" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_C_API" true)
          (lib.cmakeBool "SHERPA_ONNX_BUILD_C_API_EXAMPLES" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_EXAMPLES" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_GPU" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_JNI" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_PORTAUDIO" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_PYTHON" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_SPEAKER_DIARIZATION" true)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_TESTS" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_TTS" true)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_WEBSOCKET" false)
        ]
        """,
    )
    passthru = expect_instance(
        expect_binding(attributes.values, "passthru").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "paseoExactSource").value,
        "closureContract",
    )


def test_paseo_gates_on_the_updater_owned_onnxruntime_closure_hash() -> None:
    """The promoted source hash is injected into the reviewed native contract."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    assert_nix_ast_equal(
        expect_binding(final.scope, "nativeSourceClosureComplete").value,
        """
        nativeLockComplete
        && (onnxruntimeExact.passthru.paseoExactSource.sourceClosureComplete or false)
        && (sherpaExact.passthru.paseoExactSource.sourceClosureComplete or false)
        """,
    )

    gates = []
    pending = [expect_binding(final.scope, "unresolvedBuildGates").value]
    while pending:
        gate = pending.pop(0)
        if isinstance(gate, BinaryExpression) and gate.operator.name == "++":
            pending[0:0] = [gate.left, gate.right]
        else:
            gates.append(gate)
    assert_nix_ast_equal(
        gates[7],
        "lib.optional (onnxruntimeSourceHash == null) "
        '"ONNX Runtime ${onnxruntimeVersion} srcHash is missing"',
    )


def test_paseo_prunes_the_sdk_runtime_and_injects_package_owned_claude() -> None:
    """Finder launches must use the exact package-owned Claude executable."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    formal_names = {
        formal.name for formal in package.argument_set if isinstance(formal, Identifier)
    }
    assert "claude-code" in formal_names
    assert formal_names.isdisjoint({
        "anthropicRipgrepSource",
        "anthropicTreeSitterBashSource",
    })

    final = expect_instance(package.output, IfExpression)
    plan = expect_instance(
        expect_binding(
            expect_instance(
                expect_binding(final.scope, "commonPassthru").value,
                AttributeSet,
            ).values,
            "exactSourcePlan",
        ).value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(plan.values, "claudeRuntime").value,
        """
        {
          sdkVersion = claudeAgentSdkVersion;
          executable = claudeCodeExecutable;
          platformPackagePruned = true;
          treeSitter = "external-claude-runtime";
        }
        """,
    )

    gates = []
    pending = [expect_binding(final.scope, "unresolvedBuildGates").value]
    while pending:
        gate = pending.pop(0)
        if isinstance(gate, BinaryExpression) and gate.operator.name == "++":
            pending[0:0] = [gate.left, gate.right]
        else:
            gates.append(gate)
    assert len(gates) == 15
    assert_nix_ast_equal(
        gates[13],
        "lib.optional (expectedNativeManifest == null) "
        '"Paseo exact native relative-path/count manifest is unresolved"',
    )
    assert_nix_ast_equal(
        gates[14],
        "lib.optional (!nativeSourceClosureComplete) "
        '"Paseo native transitive source closures are incomplete or unvalidated"',
    )

    real_package = expect_instance(
        expect_binding(final.scope, "realPackage").value,
        FunctionCall,
    )
    arguments = expect_instance(real_package.argument, AttributeSet)
    configure = expect_instance(
        expect_binding(arguments.values, "configurePhase").value,
        IndentedString,
    )
    shell = parse_shell(indented_string_body(configure.rebuild()))
    platform_loop = [
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "for_statement")
        if "sdkPlatformPath" in node_text(node, shell.sanitized)
    ]
    assert platform_loop == [
        """for sdkPlatformPath in \\
        "node_modules/$sdkPlatformPackage" \\
        "packages/server/node_modules/$sdkPlatformPackage" \\
        "packages/server/node_modules/@anthropic-ai/claude-agent-sdk/node_modules/$sdkPlatformPackage"
      do
        rm -rf "$sdkPlatformPath"
      done"""
    ]
    assert command_texts(shell, "rm").count('rm -rf "$sdkPlatformPath"') == 1
    semantic_commands = command_texts(shell)
    assert all(
        "claude-agent-sdk/vendor" not in command for command in semantic_commands
    )
    assert all("anthropicRipgrepSource" not in command for command in semantic_commands)
    assert all(
        "anthropicTreeSitterBashSource" not in command for command in semantic_commands
    )


def test_paseo_discards_npm_sherpa_binaries_before_installing_source_outputs() -> None:
    """The audited addon overlay cannot merge with an opaque optional package."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    real_package = expect_instance(
        expect_binding(final.scope, "realPackage").value,
        FunctionCall,
    )
    arguments = expect_instance(real_package.argument, AttributeSet)
    configure = expect_instance(
        expect_binding(arguments.values, "configurePhase").value,
        IndentedString,
    )
    shell = parse_shell(indented_string_body(configure.rebuild()))
    commands = command_texts(shell)
    discard = """rm -rf \\
        node_modules/sherpa-onnx-node \\
        node_modules/sherpa-onnx-darwin-arm64"""
    install = "cp -R __NIX_INTERP__/node_modules/. node_modules/"

    assert commands.count(discard) == 1
    assert commands.index(discard) < commands.index(install)


def test_paseo_replays_the_locked_npm_peer_resolution_offline() -> None:
    """The npm hook must use the peer policy that produced the fixed-output cache."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    real_package = expect_instance(
        expect_binding(final.scope, "realPackage").value,
        FunctionCall,
    )
    arguments = expect_instance(real_package.argument, AttributeSet)

    npm_deps = expect_instance(
        expect_binding(final.scope, "npmDeps").value,
        FunctionCall,
    )
    npm_arguments = expect_instance(npm_deps.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(npm_arguments.values, "fetcherVersion").value,
        "npmFetcherVersion",
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "npmFlags").value,
        '[ "--legacy-peer-deps" ]',
    )
    environment = expect_instance(
        expect_binding(arguments.values, "env").value,
        BinaryExpression,
    )
    environment_owned = expect_instance(environment.right, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(
            environment_owned.values,
            "NIX_NPM_FETCHER_VERSION",
        ).value,
        "toString npmFetcherVersion",
    )


_APP_BUILDER_LIB_COLLECTOR_26_8_1_FIXTURE = """\
"use strict";
class NodeModulesCollector {
    constructor() {
        this.allDependencies = new Map();
        this.cache = {
            exists: Object.create(null),
            realPath: Object.create(null),
        };
    }
    parseNameVersion(identifier) {
        const lastAt = identifier.lastIndexOf("@");
        if (lastAt <= 0) {
            return { name: identifier, version: "unknown" };
        }
        const name = identifier.slice(0, lastAt);
        const version = identifier.slice(lastAt + 1);
        return { name, version };
    }
    transformToHoisterTree(obj, key, nodes = new Map()) {
        let node = nodes.get(key);
        const { name, version } = this.parseNameVersion(key);
        if (!node) {
            node = {
                name,
                identName: name,
                reference: version,
                dependencies: new Set(),
                peerNames: new Set(),
            };
            nodes.set(key, node);
            const deps = (obj[key] || {}).dependencies || [];
            for (const dep of deps) {
                const child = this.transformToHoisterTree(obj, dep, nodes);
                node.dependencies.add(child);
            }
        }
        return node;
    }
    async _getNodeModules(dependencies, result) {
        var _a;
        if (dependencies.size === 0) {
            return;
        }
        for (const d of dependencies.values()) {
            const reference = [...d.references][0];
            const key = `${d.name}@${reference}`;
            const p = (_a = this.allDependencies.get(key)) === null || _a === void 0 ? void 0 : _a.path;
            if (p === undefined) {
                throw new Error(`missing fixture path for ${key}`);
            }
            if (!(await this.cache.exists[p])) {
                throw new Error(`missing fixture package for ${key}`);
            }
            const node = {
                name: d.name,
                version: reference,
                dir: await this.cache.realPath[p],
            };
            result.push(node);
            if (d.dependencies.size > 0) {
                node.dependencies = [];
                await this._getNodeModules(d.dependencies, node.dependencies);
            }
        }
        result.sort((a, b) => a.name.localeCompare(b.name));
    }
}
module.exports = { NodeModulesCollector };
"""

_APP_BUILDER_LIB_CYCLE_HARNESS = """\
"use strict";
const { NodeModulesCollector } = require(process.argv[2]);

function moduleNode(name) {
  return { name, references: new Set(["1.0.0"]), dependencies: new Set() };
}

function flatten(nodes, names = []) {
  for (const node of nodes) {
    names.push(node.name);
    flatten(node.dependencies || [], names);
  }
  return names;
}

async function main() {
  const collector = new NodeModulesCollector();
  const self = collector.transformToHoisterTree(
    { "self@1.0.0": { dependencies: ["self@1.0.0"] } },
    "self@1.0.0",
  );
  if (self.dependencies.size !== 0) {
    throw new Error("self-edge was retained");
  }

  const a = moduleNode("a");
  const b = moduleNode("b");
  a.dependencies.add(b);
  b.dependencies.add(a);
  const leaf = moduleNode("leaf");
  const left = moduleNode("left");
  const right = moduleNode("right");
  left.dependencies.add(leaf);
  right.dependencies.add(leaf);

  for (const node of [a, b, leaf, left, right]) {
    const key = `${node.name}@1.0.0`;
    const path = `/${node.name}`;
    collector.allDependencies.set(key, { path });
    collector.cache.exists[path] = true;
    collector.cache.realPath[path] = `/real${path}`;
  }

  const cyclic = [];
  await collector._getNodeModules(new Set([a]), cyclic);
  if (JSON.stringify(flatten(cyclic)) !== JSON.stringify(["a", "b"])) {
    throw new Error(`mutual cycle result drifted: ${JSON.stringify(cyclic)}`);
  }

  const diamond = [];
  await collector._getNodeModules(new Set([left, right]), diamond);
  const diamondNames = flatten(diamond);
  if (diamondNames.filter(name => name === "leaf").length !== 2) {
    throw new Error(`path-local repeats were lost: ${JSON.stringify(diamond)}`);
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
"""


def _materialize_app_builder_lib_26_8_1(
    root: Path,
    *,
    collector_source: str = _APP_BUILDER_LIB_COLLECTOR_26_8_1_FIXTURE,
) -> Path:
    package = root / "node_modules/app-builder-lib"
    collector = package / "out/node-module-collector/nodeModulesCollector.js"
    collector.parent.mkdir(parents=True)
    collector.write_text(collector_source, encoding="utf-8")
    (package / "package.json").write_text(
        json.dumps({"name": "app-builder-lib", "version": "26.8.1"}),
        encoding="utf-8",
    )
    return collector


def _apply_paseo_app_builder_lib_cycle_guard(
    root: Path,
) -> subprocess.CompletedProcess[str]:
    patch = _PACKAGE_DIR / "app-builder-lib-26.8.1-cycle-guard.patch"
    collector = (
        root
        / "node_modules/app-builder-lib/out/node-module-collector/nodeModulesCollector.js"
    )
    patch_executable = shutil.which("patch")
    node_executable = shutil.which("node")
    assert patch.is_file()
    assert patch_executable is not None
    assert node_executable is not None
    patch_command = [
        patch_executable,
        "--batch",
        "--forward",
        "--fuzz=0",
        "--strip=1",
        f"--directory={root}",
        f"--input={patch}",
    ]
    dry_run = subprocess.run(  # noqa: S603
        [
            *patch_command,
            "--dry-run",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if dry_run.returncode != 0:
        return dry_run

    applied = subprocess.run(  # noqa: S603
        patch_command,
        check=False,
        capture_output=True,
        text=True,
    )
    if applied.returncode != 0:
        return applied

    return subprocess.run(  # noqa: S603
        [node_executable, "--check", str(collector)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_paseo_app_builder_lib_cycle_guard_is_path_local(tmp_path: Path) -> None:
    """Self and mutual cycles terminate without globally deduplicating diamonds."""
    collector = _materialize_app_builder_lib_26_8_1(tmp_path)

    result = _apply_paseo_app_builder_lib_cycle_guard(tmp_path)

    assert result.returncode == 0, result.stderr
    node_executable = shutil.which("node")
    assert node_executable is not None
    harness = tmp_path / "cycle-harness.js"
    harness.write_text(_APP_BUILDER_LIB_CYCLE_HARNESS, encoding="utf-8")
    subprocess.run(  # noqa: S603
        [node_executable, str(harness), str(collector)],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )


def test_paseo_app_builder_lib_cycle_guard_rejects_drift_transactionally(
    tmp_path: Path,
) -> None:
    """A late hunk mismatch must fail the dry-run before any source is changed."""
    drifted = _APP_BUILDER_LIB_COLLECTOR_26_8_1_FIXTURE.replace(
        "await this._getNodeModules(d.dependencies, node.dependencies);",
        "await this._getNodeModules(d.dependencies, node.dependencies /* drift */);",
    )
    collector = _materialize_app_builder_lib_26_8_1(
        tmp_path,
        collector_source=drifted,
    )

    result = _apply_paseo_app_builder_lib_cycle_guard(tmp_path)

    assert result.returncode != 0
    assert collector.read_text(encoding="utf-8") == drifted


def test_paseo_applies_the_cycle_guard_after_npm_materialization() -> None:
    """The post-configure hook patches installed modules before any build command."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    real_package = expect_instance(
        expect_binding(final.scope, "realPackage").value,
        FunctionCall,
    )
    arguments = expect_instance(real_package.argument, AttributeSet)
    post_configure = expect_instance(
        expect_binding(arguments.values, "postConfigure").value,
        IndentedString,
    )
    post_configure_shell = parse_shell(indented_string_body(post_configure.rebuild()))
    assert command_texts(post_configure_shell, "patch") == [
        'patch --dry-run "__NIX_INTERP__"',
        'patch "__NIX_INTERP__"',
    ]
    assert command_texts(post_configure_shell)[-1] == (
        '__NIX_INTERP__ --check "$appBuilderLibCollector"'
    )
    assignments = {
        node_text(node, post_configure_shell.sanitized)
        for node in iter_nodes(
            post_configure_shell.tree.root_node,
            "variable_assignment",
        )
    }
    assert "appBuilderLibPatch=__NIX_INTERP__" in assignments
    assert (
        """appBuilderLibPatchOptions=(
        --batch
        --forward
        --fuzz=0
        --strip=1
        --directory="$PWD"
        --input="$appBuilderLibPatch"
      )"""
        in assignments
    )
    assert any(
        assignment.startswith("installedAppBuilderLibVersion=")
        and '"$appBuilderLibManifest"' in assignment
        for assignment in assignments
    )
    assert '[ "$installedAppBuilderLibVersion" != __NIX_INTERP__ ]' in command_texts(
        post_configure_shell
    )

    configure = expect_instance(
        expect_binding(arguments.values, "configurePhase").value,
        IndentedString,
    )
    configure_shell = parse_shell(indented_string_body(configure.rebuild()))
    assert command_texts(configure_shell, "runHook")[-1] == "runHook postConfigure"


def test_paseo_keeps_the_reviewed_node_heap_after_fixing_the_cycle() -> None:
    """The structural collector fix removes the temporary builder-only override."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    real_package = expect_instance(
        expect_binding(final.scope, "realPackage").value,
        FunctionCall,
    )
    arguments = expect_instance(real_package.argument, AttributeSet)

    environment = expect_instance(
        expect_binding(arguments.values, "env").value,
        BinaryExpression,
    )
    environment_owned = expect_instance(environment.right, AttributeSet)
    assert (
        expect_instance(
            expect_binding(environment_owned.values, "NODE_OPTIONS").value,
            StringPrimitive,
        ).value
        == "--max-old-space-size=6144"
    )

    build_phase = expect_instance(
        expect_binding(arguments.values, "buildPhase").value,
        IndentedString,
    )
    shell = parse_shell(indented_string_body(build_phase.rebuild()))
    builder_commands = command_texts(
        shell,
        "../../node_modules/.bin/electron-builder",
    )
    assert len(builder_commands) == 1
    assert builder_commands[0].splitlines()[0] == (
        "../../node_modules/.bin/electron-builder \\"
    )
    assert [
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "variable_assignment")
        if node_text(node, shell.sanitized).startswith("NODE_OPTIONS=")
    ] == []


def test_paseo_rebuilds_and_gates_server_node_pty_before_packaging() -> None:
    """The packaged server copy must come from a proven arm64 source build."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    real_package = expect_instance(
        expect_binding(final.scope, "realPackage").value,
        FunctionCall,
    )
    arguments = expect_instance(real_package.argument, AttributeSet)
    build_phase = expect_instance(
        expect_binding(arguments.values, "buildPhase").value,
        IndentedString,
    )
    shell = parse_shell(indented_string_body(build_phase.rebuild()))
    commands = command_texts(shell)

    rebuilds = [
        command
        for command in commands
        if command.startswith("npm exec -- electron-rebuild")
    ]
    assert len(rebuilds) == 1
    rebuild = rebuilds[0]
    assert all(
        flag in rebuild
        for flag in (
            "--module-dir packages/server",
            "--only=node-pty",
            "--build-from-source",
        )
    )

    discard = next(
        command
        for command in commands
        if command.startswith("rm -rf")
        and "packages/server/node_modules/node-pty/prebuilds" in command
    )
    builder = next(
        command
        for command in commands
        if command.startswith("../../node_modules/.bin/electron-builder")
    )
    assert commands.index(rebuild) < commands.index(discard) < commands.index(builder)

    assignments = {
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "variable_assignment")
    }
    assert {
        'nodePtyRelease="packages/server/node_modules/node-pty/build/Release"',
        'nodePtyAddon="$nodePtyRelease/pty.node"',
        'nodePtySpawnHelper="$nodePtyRelease/spawn-helper"',
    } <= assignments

    conditionals = [
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "if_statement")
    ]
    assert any(
        '[ ! -f "$nodePtyAddon" ]' in conditional and "exit 1" in conditional
        for conditional in conditionals
    )
    assert any(
        '[ ! -f "$nodePtySpawnHelper" ]' in conditional
        and '[ ! -x "$nodePtySpawnHelper" ]' in conditional
        and "exit 1" in conditional
        for conditional in conditionals
    )

    native_gate_node = next(
        node
        for node in iter_nodes(shell.tree.root_node, "for_statement")
        if "for nodePtyArtifact in" in node_text(node, shell.sanitized)
    )
    native_gate = node_text(native_gate_node, shell.sanitized)
    assert '"$nodePtyAddon" "$nodePtySpawnHelper"' in native_gate
    assert '/usr/bin/file -b "$nodePtyArtifact"' in native_gate
    assert '/usr/bin/lipo -archs "$nodePtyArtifact"' in native_gate
    assert '[ "$nodePtyArchitectures" != arm64 ]' in native_gate
    assert "not a Mach-O" in native_gate
    assert "exit 1" in native_gate
    builder_node = next(
        node
        for node in iter_nodes(shell.tree.root_node, "command")
        if node_text(node, shell.sanitized).startswith(
            "../../node_modules/.bin/electron-builder"
        )
    )
    assert native_gate_node.end_byte < builder_node.start_byte


def test_paseo_install_check_loads_the_colocated_native_runtimes() -> None:
    """The packaged node-pty and esbuild runtimes must be exact and executable."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    real_package = expect_instance(
        expect_binding(final.scope, "realPackage").value,
        FunctionCall,
    )
    arguments = expect_instance(real_package.argument, AttributeSet)
    install_check = expect_instance(
        expect_binding(arguments.values, "installCheckPhase").value,
        IndentedString,
    )
    shell = parse_shell(indented_string_body(install_check.rebuild()))

    assignments = {
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "variable_assignment")
    }
    assert {
        'packagedEsbuild="$unpacked/@esbuild/darwin-arm64/bin/esbuild"',
        'packagedNodePtyRelease="$unpacked/node-pty/build/Release"',
        'packagedNodePtyAddon="$packagedNodePtyRelease/pty.node"',
        'packagedNodePtySpawnHelper="$packagedNodePtyRelease/spawn-helper"',
    } <= assignments

    required_paths = next(
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "for_statement")
        if "for path in" in node_text(node, shell.sanitized)
    )
    assert '"$packagedNodePtyAddon"' in required_paths
    assert '"$packagedNodePtySpawnHelper"' in required_paths
    assert '"$packagedEsbuild"' in required_paths

    conditionals = [
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "if_statement")
    ]
    assert any(
        '[ ! -f "$packagedNodePtyAddon" ]' in conditional and "exit 1" in conditional
        for conditional in conditionals
    )
    assert any(
        '[ ! -f "$packagedNodePtySpawnHelper" ]' in conditional
        and '[ ! -x "$packagedNodePtySpawnHelper" ]' in conditional
        and "exit 1" in conditional
        for conditional in conditionals
    )
    assert any(
        '[ ! -f "$packagedEsbuild" ]' in conditional
        and '[ ! -x "$packagedEsbuild" ]' in conditional
        and "exit 1" in conditional
        for conditional in conditionals
    )
    assert any(
        '[ "$packagedEsbuildVersion" !=' in conditional and "exit 1" in conditional
        for conditional in conditionals
    )

    esbuild_version_assignment = next(
        assignment
        for assignment in assignments
        if assignment.startswith('packagedEsbuildVersion="$(')
    )
    assert '"$packagedEsbuild" --version' in esbuild_version_assignment

    native_gate_node = next(
        node
        for node in iter_nodes(shell.tree.root_node, "for_statement")
        if "for packagedNativeArtifact in" in node_text(node, shell.sanitized)
    )
    native_gate = node_text(native_gate_node, shell.sanitized)
    assert all(
        candidate in native_gate
        for candidate in (
            '"$packagedEsbuild"',
            '"$packagedNodePtyAddon"',
            '"$packagedNodePtySpawnHelper"',
        )
    )
    assert '/usr/bin/file -b "$packagedNativeArtifact"' in native_gate
    assert '/usr/bin/lipo -archs "$packagedNativeArtifact"' in native_gate
    assert '[ "$packagedNativeArchitectures" != arm64 ]' in native_gate
    assert "not a Mach-O" in native_gate
    assert "exit 1" in native_gate

    runtime_script_node = next(
        node
        for node in iter_nodes(shell.tree.root_node, "heredoc_body")
        if "process.versions.electron" in node_text(node, shell.sanitized)
    )
    runtime_script = node_text(runtime_script_node, shell.sanitized)
    for fragment in (
        'const fs = require("node:fs");',
        'const nodePtyAddon = path.join(process.env.PASEO_NODE_PTY_RELEASE, "pty.node");',
        "const nodePtySpawnHelper = path.join(",
        "process.env.PASEO_NODE_PTY_RELEASE,",
        '"spawn-helper",',
        "fs.accessSync(nodePtySpawnHelper, fs.constants.X_OK);",
        "require(nodePtyAddon);",
    ):
        assert fragment in runtime_script
    assert native_gate_node.end_byte < runtime_script_node.start_byte
    runtime_command = next(
        command
        for command in command_texts(shell)
        if 'PASEO_NODE_PTY_RELEASE="$packagedNodePtyRelease"' in command
        and "ELECTRON_RUN_AS_NODE=1" in command
        and '"$executable"' in command
    )
    normalized_runtime_command = " ".join(runtime_command.replace("\\\n", " ").split())
    assert "env -u DYLD_LIBRARY_PATH" in normalized_runtime_command
    assert "-u DYLD_FRAMEWORK_PATH" in normalized_runtime_command
    assert "-u DYLD_INSERT_LIBRARIES" in normalized_runtime_command
    assert "DYLD_LIBRARY_PATH=" not in normalized_runtime_command


def test_paseo_install_check_proves_the_packaged_claude_executable() -> None:
    """The transpiled ASAR must retain the exact package-owned executable path."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    assert_nix_ast_equal(
        expect_binding(final.scope, "claudeCodeExecutable").value,
        "lib.getExe claude-code",
    )

    real_package = expect_instance(
        expect_binding(final.scope, "realPackage").value,
        FunctionCall,
    )
    arguments = expect_instance(real_package.argument, AttributeSet)
    environment = expect_instance(
        expect_binding(arguments.values, "env").value,
        BinaryExpression,
    )
    environment_owned = expect_instance(environment.right, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(environment_owned.values, "CLAUDE_CODE_EXECUTABLE").value,
        "claudeCodeExecutable",
    )

    post_patch = expect_instance(
        expect_binding(arguments.values, "postPatch").value,
        IndentedString,
    )
    post_patch_shell = parse_shell(indented_string_body(post_patch.rebuild()))
    assert any(
        '--claude-code-executable "$CLAUDE_CODE_EXECUTABLE"' in command
        for command in command_texts(post_patch_shell)
    )

    install_check = expect_instance(
        expect_binding(arguments.values, "installCheckPhase").value,
        IndentedString,
    )
    install_check_shell = parse_shell(indented_string_body(install_check.rebuild()))
    asar_extract = 'node_modules/.bin/asar extract "$resources/app.asar" "$asarRoot"'
    install_commands = command_texts(install_check_shell)
    assert 'grep -R -Fq "$CLAUDE_CODE_EXECUTABLE" "$asarRoot"' not in install_commands
    compiled_finds = [
        command
        for command in command_texts(install_check_shell, "find")
        if '"$asarRoot"' in command and "-type f" in command
    ]
    assert len(compiled_finds) == 1
    compiled_find = compiled_finds[0]
    assert all(pattern in compiled_find for pattern in ("'*.js'", "'*.cjs'", "'*.mjs'"))
    assert "*.map" not in compiled_find

    opaque_runtime_finds = [
        command
        for command in command_texts(install_check_shell, "find")
        if "claude-agent-sdk-darwin-arm64" in command
    ]
    assert len(opaque_runtime_finds) == 1
    assert all(
        token in opaque_runtime_finds[0]
        for token in (
            'find "$asarRoot" "$resources/app.asar.unpacked"',
            "-path '*/@anthropic-ai/claude-agent-sdk-darwin-arm64*'",
            "-print -quit",
        )
    )

    executable_probes = [
        command
        for command in command_texts(install_check_shell, "grep")
        if '"$CLAUDE_CODE_EXECUTABLE"' in command
    ]
    assert len(executable_probes) == 1
    assert all(
        token in executable_probes[0]
        for token in (
            "grep -H -F -o --",
            '"$CLAUDE_CODE_EXECUTABLE"',
            '"$compiledArtifact"',
        )
    )
    while_statements = [
        node_text(node, install_check_shell.sanitized)
        for node in iter_nodes(install_check_shell.tree.root_node, "while_statement")
    ]
    assert len(while_statements) == 1
    assert '>> "$claudeExecutableMatches"' in while_statements[0]
    redirected_statements = [
        node_text(node, install_check_shell.sanitized)
        for node in iter_nodes(
            install_check_shell.tree.root_node,
            "redirected_statement",
        )
    ]
    assert any(
        statement.startswith("while IFS=")
        and statement.endswith('done < "$compiledArtifacts"')
        for statement in redirected_statements
    )
    wc_commands = command_texts(install_check_shell, "wc")
    assert wc_commands == ["wc -l"]
    variable_assignments = [
        node_text(node, install_check_shell.sanitized)
        for node in iter_nodes(
            install_check_shell.tree.root_node,
            "variable_assignment",
        )
    ]
    assert any(
        'claudeExecutableMatchCount="$(wc -l < "$claudeExecutableMatches")"'
        in assignment
        for assignment in variable_assignments
    )
    if_statements = [
        node_text(node, install_check_shell.sanitized)
        for node in iter_nodes(install_check_shell.tree.root_node, "if_statement")
    ]
    assert any(
        '"$claudeExecutableMatchCount" -ne 4' in statement
        for statement in if_statements
    )
    assert any(
        '"$grepStatus" -ne 1' in statement and 'exit "$grepStatus"' in statement
        for statement in if_statements
    )
    assert any(
        "opaque Claude Agent SDK platform runtime entered the Paseo bundle" in statement
        for statement in if_statements
    )
    assert install_commands.index(asar_extract) < install_commands.index(compiled_find)


def test_sherpa_addon_rewrites_the_platform_manifest_as_structured_json(
    tmp_path: Path,
) -> None:
    """The platform manifest must preserve audited metadata while changing two keys."""
    package = expect_instance(
        parse_nix_expr(
            (_PACKAGE_DIR / "sherpa-node-addon.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(package.output, FunctionCall)
    attributes = expect_instance(derivation.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(attributes.values, "version").value,
        "closureContract.version",
    )
    environment = expect_instance(
        expect_binding(attributes.values, "env").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(environment.values, "PASEO_MANIFEST_REWRITER").value,
        "lib.getExe nodejs_24",
    )
    assert_nix_ast_equal(
        expect_binding(environment.values, "PASEO_MANIFEST_REWRITE_SCRIPT").value,
        '"${./rewrite-sherpa-platform-manifest.mjs}"',
    )

    install_shell = parse_shell(_sherpa_addon_install_phase())
    assert command_texts(install_shell, "substituteInPlace") == []
    assert '''"$PASEO_MANIFEST_REWRITER" "$PASEO_MANIFEST_REWRITE_SCRIPT" \\
      "$platform/package.json"''' in command_texts(install_shell)

    node_executable = shutil.which("node")
    assert node_executable is not None
    manifest = tmp_path / "package.json"
    original = {
        "name": "sherpa-onnx-node",
        "version": "1.12.28",
        "main": "sherpa-onnx.js",
        "description": "Node wrapper for sherpa-onnx",
        "optionalDependencies": {"keep": "1.0.0"},
    }
    manifest.write_text(json.dumps(original, indent=4) + "\n", encoding="utf-8")
    subprocess.run(  # noqa: S603
        [
            node_executable,
            str(_PACKAGE_DIR / "rewrite-sherpa-platform-manifest.mjs"),
            str(manifest),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(manifest.read_text(encoding="utf-8")) == original | {
        "name": "sherpa-onnx-darwin-arm64",
        "main": "sherpa-onnx.node",
    }


@pytest.mark.parametrize(
    ("field", "unexpected"),
    [
        ("name", "sherpa-onnx-renamed"),
        ("version", "1.12.29"),
        ("main", "dist/index.js"),
    ],
)
def test_sherpa_addon_rejects_drifted_wrapper_manifest_fields(
    tmp_path: Path,
    field: str,
    unexpected: str,
) -> None:
    """Upstream wrapper drift must fail before the copied manifest is modified."""
    node_executable = shutil.which("node")
    assert node_executable is not None
    manifest = tmp_path / "package.json"
    wrapper = {
        "name": "sherpa-onnx-node",
        "version": "1.12.28",
        "main": "sherpa-onnx.js",
    }
    wrapper[field] = unexpected
    original = json.dumps(wrapper, indent=2) + "\n"
    manifest.write_text(original, encoding="utf-8")

    completed = subprocess.run(  # noqa: S603
        [
            node_executable,
            str(_PACKAGE_DIR / "rewrite-sherpa-platform-manifest.mjs"),
            str(manifest),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert f"unexpected sherpa wrapper {field}" in completed.stderr
    assert manifest.read_text(encoding="utf-8") == original


def test_sherpa_addon_rejects_flattened_library_collisions_before_copy(
    tmp_path: Path,
) -> None:
    """Two exact source closures may not silently overwrite the same dylib name."""
    wrapper_tree = tmp_path / "wrapper" / "package"
    wrapper_tree.mkdir(parents=True)
    (wrapper_tree / "package.json").write_text(
        '{"name":"sherpa-onnx-node","main":"sherpa-onnx.js"}\n',
        encoding="utf-8",
    )
    wrapper_archive = tmp_path / "wrapper.tgz"
    with tarfile.open(wrapper_archive, "w:gz") as archive:
        archive.add(wrapper_tree, arcname="package")

    build_root = tmp_path / "build"
    build_root.mkdir()
    (build_root / "sherpa-onnx.node").write_bytes(b"addon")
    sherpa_root = tmp_path / "sherpa"
    onnxruntime_root = tmp_path / "onnxruntime"
    for root, payload in ((sherpa_root, b"sherpa"), (onnxruntime_root, b"onnx")):
        library_dir = root / "lib"
        library_dir.mkdir(parents=True)
        (library_dir / "libduplicate.dylib").write_bytes(payload)

    install_phase = _sherpa_addon_install_phase()
    replacements = {
        "${wrapperSrc}": str(wrapper_archive),
        "${lib.getLib sherpaExact}": str(sherpa_root),
        "${lib.getLib onnxruntimeExact}": str(onnxruntime_root),
    }
    for interpolation, value in replacements.items():
        install_phase = install_phase.replace(interpolation, value)

    output = tmp_path / "output"
    completed = subprocess.run(  # noqa: S603
        [
            "/bin/bash",
            "-c",
            "set -euo pipefail\nrunHook() { :; }\n" + install_phase,
        ],
        cwd=build_root,
        env=os.environ
        | {
            "out": str(output),
            "TMPDIR": str(tmp_path),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "duplicate sherpa runtime library basename: libduplicate.dylib" in (
        completed.stderr
    )
    assert not output.exists()


def test_sherpa_addon_recursively_materializes_store_dylib_dependencies() -> None:
    """Every discovered store dylib must join the flattened audited work queue."""
    shell = parse_shell(_sherpa_addon_install_phase())
    copy_commands = command_texts(shell, "cp")
    assert 'cp -L "$dependency" "$replacement"' in copy_commands

    if_statements = [
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "if_statement")
    ]
    assert any(
        'cmp -s "$dependency" "$recordedSource"' in statement
        and "dependency basename collision" in statement
        for statement in if_statements
    )

    variable_assignments = [
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "variable_assignment")
    ]
    assert 'runtimeProcessed="$TMPDIR/paseo-sherpa-runtime-processed"' in (
        variable_assignments
    )
    while_statements = [
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "while_statement")
    ]
    assert any(
        'grep -F -x -q "$candidate" "$runtimeProcessed"' in statement
        and 'binary="$candidate"' in statement
        and 'echo "$binary" >>"$runtimeProcessed"' in statement
        for statement in while_statements
    )


def test_sherpa_addon_removes_store_rpaths_from_the_flattened_runtime() -> None:
    """Loader-relative linkage must not retain a store-only search path."""
    shell = parse_shell(_sherpa_addon_install_phase())
    install_name_commands = command_texts(shell, "/usr/bin/install_name_tool")
    assert (
        '/usr/bin/install_name_tool -delete_rpath "$runtimeRpath" "$binary"'
        in install_name_commands
    )
    while_statements = [
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "while_statement")
    ]
    assert any(
        'case "$runtimeRpath" in' in statement and "/nix/store/*)" in statement
        for statement in while_statements
    )
    assert '/usr/bin/otool -l "$binary"' in command_texts(shell, "/usr/bin/otool")


def test_sherpa_addon_rejects_any_remaining_store_rpath() -> None:
    """The helper must prove that its standalone flattened output is relocatable."""
    package = expect_instance(
        parse_nix_expr(
            (_PACKAGE_DIR / "sherpa-node-addon.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(package.output, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)
    install_check = expect_instance(
        expect_binding(arguments.values, "installCheckPhase").value,
        IndentedString,
    )
    shell = parse_shell(indented_string_body(install_check.rebuild()))
    dependency_pipelines = [
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "pipeline")
    ]
    assert any(
        '/usr/bin/otool -L "$binary"' in pipeline
        and "tail -n +2" in pipeline
        and "grep -q '/nix/store/'" in pipeline
        for pipeline in dependency_pipelines
    )
    assert '/usr/bin/otool -l "$binary"' in command_texts(shell, "/usr/bin/otool")
    if_statements = [
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "if_statement")
    ]
    assert any(
        "sherpa runtime retains a Nix-store LC_RPATH" in statement
        and "grep -q '/nix/store/'" in statement
        for statement in if_statements
    )


def test_paseo_nix_files_are_structurally_parseable() -> None:
    """Every package-local Nix expression must parse without evaluation or realization."""
    for path in sorted(_PACKAGE_DIR.glob("*.nix")):
        assert parse_nix_expr(path.read_text(encoding="utf-8")) is not None, path


def test_paseo_revalidates_current_metadata() -> None:
    """Current version metadata must still refresh every fixed-output closure."""
    updater = _load_updater_module().PaseoUpdater()
    assert run_async(updater._is_latest(None, _version_info())) is False


def test_paseo_raw_urls_are_commit_pinned() -> None:
    """The discovery contract reads manifests only from the immutable tree."""
    assert github_raw_url("getpaseo", "paseo", _COMMIT, "package.json").endswith(
        f"/{_COMMIT}/package.json"
    )
