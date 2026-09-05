"""Source, package-shape, and Nix-ownership tests for Hermes Desktop."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.flake_lock import FlakeLock
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    parse_nix_expr,
)
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.tests._updater_helpers import load_repo_module
from lib.update.derivation_validation import DerivationValidation
from lib.update.flake import get_flake_input_version
from lib.update.paths import REPO_ROOT
from lib.update.sources import load_source_entry
from lib.update.updaters import VersionInfo

_PACKAGE_DIR = REPO_ROOT / "packages/hermes-desktop"
_HERMES_EXECUTABLE = "/nix/store/fixture-hermes-agent/bin/hermes"
_HERMES_VERSION = "0.20.6"

_PINNED_SOURCE_FIXTURES: dict[str, str] = {
    "apps/desktop/electron/main.ts": """const IS_PACKAGED = app.isPackaged || Boolean(process.env.HERMES_DESKTOP_IS_PACKAGED)
const DEFAULT_UPDATE_BRANCH = 'main'

function resolveUpdateRoot() {
  const candidates = [
    process.env.HERMES_DESKTOP_HERMES_ROOT && path.resolve(process.env.HERMES_DESKTOP_HERMES_ROOT),
    !IS_PACKAGED && isHermesSourceRoot(SOURCE_REPO_ROOT) ? SOURCE_REPO_ROOT : null,
    isHermesSourceRoot(ACTIVE_HERMES_ROOT) ? ACTIVE_HERMES_ROOT : null
  ].filter(Boolean)

  return candidates.find(c => directoryExists(path.join(c, '.git'))) || candidates[0] || ACTIVE_HERMES_ROOT
}

function resolveHermesBackend(backendArgs) {
  return {
    label: 'mutable fallback',
    command: findOnPath('hermes'),
    args: backendArgs,
    bootstrap: true,
    env: {},
    kind: 'command',
    shell: false
  }
}

async function checkUpdates() {
  return null
}

async function applyUpdates(opts: { stopSafeBlockers?: boolean } = {}) {
  return opts
}

  const checkForUpdatesItem = {
    label: 'Check for Updates…',
    click: () => sendOpenUpdatesRequested()
  }

async function requestManagedSshUpdate(rawId) {
  const connectionId = String(rawId || '').trim()
  const existing = managedConnectionUpdates.get(connectionId)
  return existing
}

ipcMain.handle('hermes:connections:update-all', async (_event, payload) => {
  const registry = readDesktopConnectionsRegistry()
  return { ok: true, payload, registry }
})

ipcMain.handle('hermes:updates:branch:set', async (_event, name) => {
  const branch = typeof name === 'string' && name.trim() ? name.trim() : DEFAULT_UPDATE_BRANCH
  writeDesktopUpdateConfig({ branch })

  return { branch }
})

function resolveHermesVersion() {
  try {
    const root = resolveUpdateRoot()
    const initPath = path.join(root, 'hermes_cli', '__init__.py')

    if (fileExists(initPath)) {
      const raw = fs.readFileSync(initPath, 'utf8')
      const match = raw.match(/__version__\\s*=\\s*["']([^"']+)["']/)

      if (match) {
        return match[1]
      }
    }
  } catch {
    // Fall through to the Electron app version below.
  }

  return app.getVersion()
}
""",
    "apps/desktop/package.json": """{
  "build": {
    "afterSign": "scripts/notarize.mjs",
    "asar": true
  }
}
""",
    "apps/desktop/src/store/updates.ts": """export interface UpdateApplyState {
  applying: boolean
}

export const openUpdateOverlayFor = (target: UpdateTarget) => {
  $updateOverlayTarget.set(target)
}

export function reportBackendContract(contract: number | undefined): void {
  void contract
}

export function maybeNotifyUpdateAvailable(status: DesktopUpdateStatus | null) {
  void status
}

export async function checkBackendUpdates(): Promise<DesktopUpdateStatus | null> {
  return null
}

export async function checkUpdates(): Promise<DesktopUpdateStatus | null> {
  const bridge = window.hermesDesktop?.updates

  if (!bridge || $updateChecking.get()) {
    return $updateStatus.get()
  }

  $updateChecking.set(true)

  try {
    const status = await bridge.check()
    $updateStatus.set(status)
    maybeNotifyUpdateAvailable(status)
    void refreshDesktopVersion()

    return status
  } catch (error) {
    const previous = $updateStatus.get()

    const fallback: DesktopUpdateStatus = {
      supported: previous?.supported ?? true,
      branch: previous?.branch,
      error: 'check-failed',
      message: error instanceof Error ? error.message : String(error),
      fetchedAt: Date.now()
    }

    $updateStatus.set(fallback)

    return fallback
  } finally {
    $updateChecking.set(false)
  }
}

export async function applyUpdates(opts: DesktopUpdateApplyOptions = {}): Promise<DesktopUpdateApplyResult> {
  return { ok: true, opts }
}

export function applyBackendUpdate(): Promise<DesktopUpdateApplyResult> {
  return runBackendUpdate()
}

export function applyEverythingUpdate(): Promise<void> {
  if (updateEverythingInFlight) {
    return updateEverythingInFlight
  }

  return runEverythingUpdate()
}

export function startUpdatePoller(): void {
  pollerStarted = true
}
""",
    "apps/desktop/src/app/contrib/hooks/use-desktop-integrations.ts": """export function useDesktopIntegrations() {
  useEffect(() => {
    startUpdatePoller()
    startMcpHealthChecker()
    const unsubscribe = window.hermesDesktop?.onOpenUpdatesRequested?.(() => openUpdatesWindow())

    return () => {
      unsubscribe?.()
      stopUpdatePoller()
      stopMcpHealthChecker()
    }
  }, [])
}
""",
    "apps/desktop/src/app/command-palette/index.tsx": """export function commands() {
  const commands = [
          {
            detail: updateVersionLabel,
            icon: Download,
            id: 'cc-update-hermes',
            keywords: ['update', 'upgrade', 'hermes', 'version', 'system', 'restart'],
            label: cc.updateHermes,
            run: () => requestActiveUpdate()
          },
          {
            id: 'cc-reload-window'
          },
  ]
  return commands
}
""",
    "apps/desktop/src/app/settings/about-settings.tsx": """export function AboutSettings() {
  return (
    <>
        <SectionHeading icon={RefreshCw} title={a.updates} />

        <div
          className="update-settings"
        />

        <ListRow
          description={a.automaticUpdatesDesc}
          hint={a.branchCommit(status?.branch ?? 'unknown', status?.currentSha?.slice(0, 7) ?? 'unknown')}
          title={a.automaticUpdates}
        />

        <UninstallSection />
    </>
  )
}
""",
    "apps/desktop/src/app/shell/hooks/use-statusbar-items.tsx": """const client = {
      onSelect: () => openUpdateOverlayFor('client'),
      title: status.tooltip,
      toggleLabel: copy.toggleVersion,
      variant: 'action'
}

const backend = {
      onSelect: () => openUpdateOverlayFor('backend'),
      title: status.tooltip,
      toggleLabel: copy.toggleBackendVersion,
      variant: 'action'
}
""",
    "apps/desktop/src/api/system.ts": """export function updateHermes(): Promise<ActionResponse> {
  return hermesApi<ActionResponse>({
    ...profileScoped(),
    path: '/api/hermes/update',
    method: 'POST'
  })
}
""",
    "apps/desktop/src/store/managed-updates.ts": """export function managedUpdatesSupported(): boolean {
  return Boolean(managedUpdater())
}
""",
    "apps/desktop/src/app/settings/connections-registry.tsx": """export function ConnectionsRegistrySection() {
  const updateAll = useCallback(async () => {
    if (!bridge?.updateAll) {
      return
    }

    await bridge.updateAll()
  }, [bridge])

  return (
    <>
          {bridge?.updateAll && (registry?.connections.length ?? 0) > 1 && (
            <Button onClick={() => void updateAll()}>{s.updateAll}</Button>
          )}
    </>
  )
}
""",
    "apps/desktop/src/app/command-center/index.tsx": """export function CommandCenter() {
  const runSystemAction = useCallback(
    async (kind: 'restart' | 'update') => {
      setSystemError('')
      await (kind === 'restart' ? restartGateway() : updateHermes())
    },
    []
  )

  return (
    <>
                        <Button onClick={() => void runSystemAction('update')} size="xs" variant="textStrong">
                          {cc.updateHermes}
                        </Button>
    </>
  )
}
""",
}


def _load_updater_module() -> ModuleType:
    return load_repo_module(
        "packages/hermes-desktop/updater.py",
        "hermes_desktop_updater_test",
    )


def _load_patch_module() -> ModuleType:
    return load_repo_module(
        "packages/hermes-desktop/patch_nix_managed.py",
        "hermes_desktop_nix_policy_patch_test",
    )


def _write_pinned_source_fixture(root: Path) -> None:
    """Materialize independently reviewed excerpts from Hermes Desktop 0.17.0."""
    for relative_path, source in _PINNED_SOURCE_FIXTURES.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def _typescript_ast_facts(sources: dict[str, str]) -> dict[str, dict[str, object]]:
    """Parse transformed TypeScript and return semantic policy evidence."""
    bun = shutil.which("bun")
    assert bun is not None
    script = r"""
import ts from "typescript";

const sources = JSON.parse(await Bun.stdin.text());
const output = {};
for (const [name, text] of Object.entries(sources)) {
  const kind = name.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const source = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true, kind);
  const callbacks = {};
  const callHandlers = {};
  const conditionalConditions = [];
  const functionStatements = {};
  const logicalAndLeftOperands = [];
  const objects = [];
  const strings = [];
  const topLevelVariableCounts = {};
  const variables = {};
  const jsxText = [];
  const jsxTags = [];

  const statements = (body) => body.statements.map((statement) => statement.getText(source));
  const visit = (node) => {
    if (ts.isFunctionDeclaration(node) && node.name && node.body) {
      functionStatements[node.name.text] = statements(node.body);
    }
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) {
      variables[node.name.text] = node.initializer.getText(source);
      if ((ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer)) && ts.isBlock(node.initializer.body)) {
        functionStatements[node.name.text] = statements(node.initializer.body);
      }
    }
    if (ts.isCallExpression(node)) {
      const callee = node.expression.getText(source);
      for (const argument of node.arguments) {
        if ((ts.isArrowFunction(argument) || ts.isFunctionExpression(argument)) && ts.isBlock(argument.body)) {
          (callbacks[callee] ??= []).push(statements(argument.body));
        }
      }
      if (
        ts.isPropertyAccessExpression(node.expression) &&
        node.expression.name.text === "handle" &&
        node.arguments.length >= 2 &&
        ts.isStringLiteral(node.arguments[0])
      ) {
        const handler = node.arguments[1];
        if ((ts.isArrowFunction(handler) || ts.isFunctionExpression(handler)) && ts.isBlock(handler.body)) {
          callHandlers[node.arguments[0].text] = statements(handler.body);
        }
      }
    }
    if (ts.isConditionalExpression(node)) {
      conditionalConditions.push(node.condition.getText(source));
    }
    if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken) {
      logicalAndLeftOperands.push(node.left.getText(source));
    }
    if (ts.isObjectLiteralExpression(node)) {
      const fields = {};
      for (const property of node.properties) {
        if (ts.isPropertyAssignment(property)) {
          fields[property.name.getText(source).replaceAll('"', "")] = property.initializer.getText(source);
        }
      }
      if (Object.keys(fields).length) objects.push(fields);
    }
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
      strings.push(node.text);
    }
    if (ts.isJsxText(node) && node.text.trim()) {
      jsxText.push(node.text.replaceAll(/\s+/g, " ").trim());
    }
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      jsxTags.push(node.tagName.getText(source));
    }
    ts.forEachChild(node, visit);
  };
  for (const statement of source.statements) {
    if (!ts.isVariableStatement(statement)) continue;
    for (const declaration of statement.declarationList.declarations) {
      if (ts.isIdentifier(declaration.name)) {
        topLevelVariableCounts[declaration.name.text] =
          (topLevelVariableCounts[declaration.name.text] ?? 0) + 1;
      }
    }
  }
  visit(source);
  output[name] = {
    callbacks,
    callHandlers,
    conditionalConditions,
    diagnostics: source.parseDiagnostics.map((diagnostic) =>
      ts.flattenDiagnosticMessageText(diagnostic.messageText, "\n"),
    ),
    functionStatements,
    jsxText,
    jsxTags,
    logicalAndLeftOperands,
    objects,
    strings,
    topLevelVariableCounts,
    variables,
  };
}
console.log(JSON.stringify(output));
"""
    result = subprocess.run(  # noqa: S603
        [bun, "-e", script],
        input=json.dumps(sources),
        text=True,
        capture_output=True,
        check=True,
        cwd=REPO_ROOT,
    )
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return cast("dict[str, dict[str, object]]", payload)


def _main_process_runtime_facts(source: str) -> dict[str, object]:
    """Execute only the parsed Nix-owned main-process declarations with stubs."""
    bun = shutil.which("bun")
    assert bun is not None
    script = r"""
import path from "node:path";
import ts from "typescript";

const text = await Bun.stdin.text();
const source = ts.createSourceFile("main.ts", text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
const managedConstants = [];
const functions = new Map();
let branchHandler = null;

for (const statement of source.statements) {
  if (ts.isVariableStatement(statement)) {
    const hasManagedConstant = statement.declarationList.declarations.some(
      declaration =>
        ts.isIdentifier(declaration.name) &&
        ["NIX_MANAGED_HERMES", "NIX_MANAGED_HERMES_VERSION"].includes(declaration.name.text),
    );
    if (hasManagedConstant) managedConstants.push(statement.getText(source));
  }
  if (ts.isFunctionDeclaration(statement) && statement.name) {
    functions.set(statement.name.text, statement.getText(source));
  }
  if (ts.isExpressionStatement(statement) && ts.isCallExpression(statement.expression)) {
    const call = statement.expression;
    if (
      ts.isPropertyAccessExpression(call.expression) &&
      call.expression.getText(source) === "ipcMain.handle" &&
      call.arguments.length >= 2 &&
      ts.isStringLiteral(call.arguments[0]) &&
      call.arguments[0].text === "hermes:updates:branch:set"
    ) {
      branchHandler = statement.getText(source);
    }
  }
}

if (managedConstants.length !== 2) throw new Error("missing Nix-managed declarations");
for (const name of ["resolveHermesBackend", "resolveUpdateRoot", "resolveHermesVersion"]) {
  if (!functions.has(name)) throw new Error(`missing ${name} declaration`);
}
if (!branchHandler) throw new Error("missing branch-set handler");

const evaluate = new Function("path", `
  return (async () => {
    const app = { isPackaged: true, getVersion: () => '0.17.0' };
    const IS_PACKAGED = app.isPackaged;
    const DEFAULT_UPDATE_BRANCH = 'main';
    const SOURCE_REPO_ROOT = '/mutable/source';
    const ACTIVE_HERMES_ROOT = '/mutable/active';
    const isHermesSourceRoot = () => true;
    const directoryExists = () => true;
    const findOnPath = () => '/usr/local/bin/hermes';
    let pinnedExists = false;
    const fileExists = candidate => pinnedExists && candidate === NIX_MANAGED_HERMES;
    const writes = [];
    const writeDesktopUpdateConfig = config => writes.push(config);
    const handlers = {};
    const ipcMain = { handle: (name, handler) => { handlers[name] = handler; } };

    ${managedConstants.join("\n")}
    ${functions.get("resolveHermesBackend")}
    ${functions.get("resolveUpdateRoot")}
    ${functions.get("resolveHermesVersion")}
    ${branchHandler}

    let missingError = null;
    try {
      resolveHermesBackend(['serve']);
    } catch (error) {
      missingError = error instanceof Error ? error.message : String(error);
    }

    pinnedExists = true;
    const backend = resolveHermesBackend(['serve']);
    const updateRoot = resolveUpdateRoot();
    const hermesVersion = resolveHermesVersion();
    const branchResult = await handlers['hermes:updates:branch:set'](null, 'release');

    return { backend, branchResult, hermesVersion, missingError, updateRoot, writes };
  })();
`);

console.log(JSON.stringify(await evaluate(path)));
"""
    result = subprocess.run(  # noqa: S603
        [bun, "-e", script],
        input=source,
        text=True,
        capture_output=True,
        check=True,
        cwd=REPO_ROOT,
    )
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return cast("dict[str, object]", payload)


def test_hermes_desktop_tracks_the_authoritative_flake_input() -> None:
    """Desktop and CLI should advance atomically from one locked MIT source."""
    module = _load_updater_module()
    updater = module.HermesDesktopUpdater()
    lock = FlakeLock.from_file(REPO_ROOT / "flake.lock")
    root_inputs = lock.root_node.inputs
    assert root_inputs is not None
    input_name = updater.input_name
    assert input_name == "hermes-agent"
    node_name = root_inputs.get(input_name)
    assert isinstance(node_name, str)
    node = lock.nodes[node_name]
    assert node.locked is not None
    assert node.locked.rev is not None
    info = VersionInfo(
        get_flake_input_version(node),
        module.ElectronManifestMetadata(
            node=node,
            commit=node.locked.rev,
            electron_version="42.3.3",
            manifest_path="apps/desktop/package.json",
            manifest_version="1.2.3",
        ),
    )
    result = updater.build_result(info, [])
    agent_source = load_source_entry(REPO_ROOT / "packages/hermes-agent/sources.json")

    assert updater.name == "hermes-desktop"
    assert updater.supported_platforms == ("aarch64-darwin",)
    assert result.version == agent_source.version
    assert result.input == agent_source.input
    assert result.commit == agent_source.commit
    assert result.hashes.equivalent_to(agent_source.hashes)
    assert result.electron_version == "42.3.3"


def _patched_pinned_source(
    tmp_path: Path,
) -> tuple[
    dict[str, str],
    dict[str, str],
    dict[str, dict[str, object]],
    dict[str, dict[str, object]],
]:
    module = _load_patch_module()
    _write_pinned_source_fixture(tmp_path)
    before = {
        relative_path: (tmp_path / relative_path).read_text(encoding="utf-8")
        for relative_path in _PINNED_SOURCE_FIXTURES
    }

    assert module.main([str(tmp_path), _HERMES_EXECUTABLE, _HERMES_VERSION]) == 0

    after = {
        relative_path: (tmp_path / relative_path).read_text(encoding="utf-8")
        for relative_path in _PINNED_SOURCE_FIXTURES
    }
    before_facts = _typescript_ast_facts({
        relative_path: source
        for relative_path, source in before.items()
        if relative_path.endswith((".ts", ".tsx"))
    })
    facts = _typescript_ast_facts({
        relative_path: source
        for relative_path, source in after.items()
        if relative_path.endswith((".ts", ".tsx"))
    })
    assert all(fact["diagnostics"] == [] for fact in before_facts.values())
    assert all(fact["diagnostics"] == [] for fact in facts.values())
    return before, after, before_facts, facts


def test_nix_policy_patch_pins_the_client_runtime_and_self_update(
    tmp_path: Path,
) -> None:
    """The packaged client must use Nix's backend and refuse only self-update."""
    _before, _after, _before_facts, facts = _patched_pinned_source(tmp_path)

    main_path = "apps/desktop/electron/main.ts"
    main = facts[main_path]
    main_functions = cast(
        "dict[str, list[str]]",
        main["functionStatements"],
    )
    main_variables = cast("dict[str, str]", main["variables"])
    main_top_level_variables = cast(
        "dict[str, int]",
        main["topLevelVariableCounts"],
    )
    main_objects = cast("list[dict[str, str]]", main["objects"])

    assert main_variables["NIX_MANAGED_HERMES"] == repr(_HERMES_EXECUTABLE)
    assert main_variables["NIX_MANAGED_HERMES_VERSION"] == json.dumps(_HERMES_VERSION)
    assert main_top_level_variables["NIX_MANAGED_HERMES"] == 1
    assert main_top_level_variables["NIX_MANAGED_HERMES_VERSION"] == 1
    assert (
        main_functions["checkUpdates"][0]
        == """if (IS_PACKAGED) {
    return {
      supported: false,
      reason: 'nix-managed',
      message: 'Updates are managed by Nix.',
      fetchedAt: Date.now()
    }
  }"""
    )
    assert (
        main_functions["applyUpdates"][0]
        == """if (IS_PACKAGED) {
    return { ok: false, error: 'nix-managed', message: 'Updates are managed by Nix.' }
  }"""
    )
    menu_item = next(
        item for item in main_objects if item.get("label") == "'Check for Updates…'"
    )
    assert menu_item["visible"] == "!IS_PACKAGED"

    updates_path = "apps/desktop/src/store/updates.ts"
    updates = facts[updates_path]
    update_functions = cast(
        "dict[str, list[str]]",
        updates["functionStatements"],
    )
    update_variables = cast("dict[str, str]", updates["variables"])
    update_objects = cast("list[dict[str, str]]", updates["objects"])
    update_conditions = cast("list[str]", updates["conditionalConditions"])

    assert update_variables["NIX_MANAGED_CLIENT"] == "import.meta.env.PROD"
    assert "NIX_MANAGED_CLIENT" in update_conditions
    managed_status = next(
        item for item in update_objects if item.get("reason") == "'nix-managed'"
    )
    assert managed_status == {
        "supported": "false",
        "updateAvailable": "false",
        "reason": "'nix-managed'",
        "message": "status.message ?? 'Updates are managed by Nix.'",
    }
    assert (
        update_functions["applyUpdates"][0]
        == """if (NIX_MANAGED_CLIENT) {
    return { ok: false, error: 'nix-managed', message: 'Updates are managed by Nix.' }
  }"""
    )

    package_json = json.loads(
        (tmp_path / "apps/desktop/package.json").read_text(encoding="utf-8")
    )
    assert "afterSign" not in package_json


def _patched_main_runtime(tmp_path: Path) -> dict[str, object]:
    module = _load_patch_module()
    _write_pinned_source_fixture(tmp_path)
    module.patch_tree(tmp_path, _HERMES_EXECUTABLE, _HERMES_VERSION)
    return _main_process_runtime_facts(
        (tmp_path / "apps/desktop/electron/main.ts").read_text(encoding="utf-8")
    )


def test_nix_policy_main_backend_resolution_fails_closed(tmp_path: Path) -> None:
    """A missing closure executable must never reach mutable runtime fallback."""
    runtime = _patched_main_runtime(tmp_path)

    assert runtime["missingError"] == (
        "Nix-managed Hermes executable is missing from the runtime closure: "
        f"{_HERMES_EXECUTABLE}"
    )
    assert runtime["backend"] == {
        "label": f"Nix-managed Hermes CLI at {_HERMES_EXECUTABLE}",
        "command": _HERMES_EXECUTABLE,
        "args": ["serve"],
        "bootstrap": False,
        "env": {},
        "kind": "command",
        "shell": False,
    }


def test_nix_policy_main_update_metadata_uses_the_pinned_root(
    tmp_path: Path,
) -> None:
    """Version and skew metadata must resolve only inside the pinned package."""
    runtime = _patched_main_runtime(tmp_path)

    assert runtime["updateRoot"] == str(Path(_HERMES_EXECUTABLE).parent.parent)


def test_nix_policy_main_reports_the_authoritative_agent_version(
    tmp_path: Path,
) -> None:
    """A packaged app must not fall back to its independently versioned shell."""
    runtime = _patched_main_runtime(tmp_path)

    assert runtime["hermesVersion"] == _HERMES_VERSION


def test_nix_policy_main_branch_config_is_immutable(tmp_path: Path) -> None:
    """The Nix client must report the fixed branch without writing user state."""
    runtime = _patched_main_runtime(tmp_path)

    assert runtime["branchResult"] == {"branch": "main"}
    assert runtime["writes"] == []


def test_nix_policy_patch_preserves_backend_and_fleet_update_admission(
    tmp_path: Path,
) -> None:
    """Remote backend and fleet updates must keep their capability-based paths."""
    before, after, before_facts, facts = _patched_pinned_source(tmp_path)
    main_path = "apps/desktop/electron/main.ts"
    main_functions = cast(
        "dict[str, list[str]]",
        facts[main_path]["functionStatements"],
    )
    before_main_functions = cast(
        "dict[str, list[str]]",
        before_facts[main_path]["functionStatements"],
    )
    main_handlers = cast("dict[str, list[str]]", facts[main_path]["callHandlers"])
    before_main_handlers = cast(
        "dict[str, list[str]]",
        before_facts[main_path]["callHandlers"],
    )
    assert (
        main_functions["requestManagedSshUpdate"]
        == before_main_functions["requestManagedSshUpdate"]
    )
    assert (
        main_handlers["hermes:connections:update-all"]
        == before_main_handlers["hermes:connections:update-all"]
    )

    updates_path = "apps/desktop/src/store/updates.ts"
    update_functions = cast(
        "dict[str, list[str]]",
        facts[updates_path]["functionStatements"],
    )
    before_update_functions = cast(
        "dict[str, list[str]]",
        before_facts[updates_path]["functionStatements"],
    )
    for function_name in (
        "openUpdateOverlayFor",
        "reportBackendContract",
        "maybeNotifyUpdateAvailable",
        "checkBackendUpdates",
        "applyBackendUpdate",
        "applyEverythingUpdate",
        "startUpdatePoller",
    ):
        assert update_functions[function_name] == before_update_functions[function_name]

    for relative_path in (
        "apps/desktop/src/app/contrib/hooks/use-desktop-integrations.ts",
        "apps/desktop/src/app/command-palette/index.tsx",
        "apps/desktop/src/api/system.ts",
        "apps/desktop/src/store/managed-updates.ts",
        "apps/desktop/src/app/settings/connections-registry.tsx",
        "apps/desktop/src/app/command-center/index.tsx",
    ):
        assert after[relative_path] == before[relative_path]


def test_nix_policy_patch_hides_only_client_owned_affordances(
    tmp_path: Path,
) -> None:
    """Client update and uninstall UI must be inert without hiding backend UI."""
    _before, _after, before_facts, facts = _patched_pinned_source(tmp_path)
    about = facts["apps/desktop/src/app/settings/about-settings.tsx"]
    assert "!import.meta.env.PROD" in cast(
        "list[str]",
        about["logicalAndLeftOperands"],
    )
    about_tags = cast("list[str]", about["jsxTags"])
    assert about_tags.count("ListRow") == 1
    assert about_tags.count("UninstallSection") == 1

    statusbar_path = "apps/desktop/src/app/shell/hooks/use-statusbar-items.tsx"
    statusbar_objects = cast("list[dict[str, str]]", facts[statusbar_path]["objects"])
    before_statusbar_objects = cast(
        "list[dict[str, str]]",
        before_facts[statusbar_path]["objects"],
    )
    client_status = next(
        item
        for item in statusbar_objects
        if item.get("onSelect", "").endswith("undefined")
    )
    assert client_status["variant"] == "import.meta.env.DEV ? 'action' : 'text'"
    backend_status = next(
        item
        for item in statusbar_objects
        if item.get("toggleLabel") == "copy.toggleBackendVersion"
    )
    before_backend_status = next(
        item
        for item in before_statusbar_objects
        if item.get("toggleLabel") == "copy.toggleBackendVersion"
    )
    assert backend_status == before_backend_status


def test_nix_policy_patch_rejects_late_drift_without_writing_any_file(
    tmp_path: Path,
) -> None:
    """A late source mismatch must leave the complete source tree untouched."""
    module = _load_patch_module()
    _write_pinned_source_fixture(tmp_path)
    late_path = tmp_path / "apps/desktop/src/app/shell/hooks/use-statusbar-items.tsx"
    late_path.write_text(
        late_path.read_text(encoding="utf-8").replace(
            "      variant: 'action'",
            "      variant: 'text'",
            1,
        ),
        encoding="utf-8",
    )
    before = {
        relative_path: (tmp_path / relative_path).read_bytes()
        for relative_path in _PINNED_SOURCE_FIXTURES
    }

    with pytest.raises(RuntimeError, match="expected one Hermes source match"):
        module.patch_tree(tmp_path, _HERMES_EXECUTABLE, _HERMES_VERSION)

    after = {
        relative_path: (tmp_path / relative_path).read_bytes()
        for relative_path in _PINNED_SOURCE_FIXTURES
    }
    assert after == before


@pytest.mark.parametrize("executable", ["hermes", "/tmp/hermes"])
def test_nix_policy_patch_requires_a_nix_store_backend_path(
    tmp_path: Path,
    executable: str,
) -> None:
    """Relative and non-store paths could escape the immutable closure."""
    module = _load_patch_module()

    with pytest.raises(ValueError, match="normalized path under /nix/store"):
        module.patch_tree(tmp_path, executable, _HERMES_VERSION)


@pytest.mark.parametrize("version", ["", " 0.20.6", "0.20.6 "])
def test_nix_policy_patch_requires_a_normalized_agent_version(
    tmp_path: Path,
    version: str,
) -> None:
    """The embedded authoritative version must be present and unambiguous."""
    module = _load_patch_module()

    with pytest.raises(ValueError, match="non-empty normalized string"):
        module.patch_tree(tmp_path, _HERMES_EXECUTABLE, version)


def test_nix_policy_patch_normalizes_the_store_backend_path(tmp_path: Path) -> None:
    """Equivalent store spellings must embed one canonical executable path."""
    module = _load_patch_module()
    _write_pinned_source_fixture(tmp_path)
    unnormalized = "/nix/store/fixture-hermes-agent/bin/../bin/hermes"

    module.patch_tree(tmp_path, unnormalized, _HERMES_VERSION)

    facts = _typescript_ast_facts({
        "apps/desktop/electron/main.ts": (
            tmp_path / "apps/desktop/electron/main.ts"
        ).read_text(encoding="utf-8")
    })
    variables = cast(
        "dict[str, str]",
        facts["apps/desktop/electron/main.ts"]["variables"],
    )
    assert variables["NIX_MANAGED_HERMES"] == repr(os.path.normpath(unnormalized))


def test_hermes_desktop_package_is_a_source_built_managed_mac_app() -> None:
    """The package should pair exact Electron with the managed agent and app."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    first_assertion = expect_instance(package.output, Assertion)
    scope = first_assertion.scope

    assert_nix_ast_equal(
        expect_binding(scope, "src").value,
        "inputs.hermes-agent",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "electronVersion").value,
        "desktopPackageJson.devDependencies.electron",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "electronBuild").value,
        "nixcfgElectron.sourceBuildFor electronVersion",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "hermesExecutable").value,
        "lib.getExe hermes-agent",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "hermesVersion").value,
        "hermes-agent.version",
    )

    second_assertion = expect_instance(first_assertion.body, Assertion)
    third_assertion = expect_instance(second_assertion.body, Assertion)
    derivation = expect_instance(third_assertion.body, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)
    passthru = expect_instance(
        expect_binding(arguments.values, "passthru").value,
        AttributeSet,
    )
    mac_app = expect_instance(
        expect_binding(passthru.values, "macApp").value,
        AttributeSet,
    )
    metadata = expect_instance(
        expect_binding(arguments.values, "meta").value,
        AttributeSet,
    )
    build_phase = expect_instance(
        expect_binding(arguments.values, "buildPhase").value,
        IndentedString,
    )
    post_patch = expect_instance(
        expect_binding(arguments.values, "postPatch").value,
        IndentedString,
    )
    install_check = expect_instance(
        expect_binding(arguments.values, "installCheckPhase").value,
        IndentedString,
    )
    assert command_texts(
        parse_shell(indented_string_body(post_patch.rebuild())),
        "__NIX_INTERP__",
    ) == [
        'PYTHONPATH=__NIX_INTERP__ __NIX_INTERP__ __NIX_INTERP__ "$PWD" '
        "__NIX_INTERP__ __NIX_INTERP__"
    ]
    assert_nix_ast_equal(
        f"{{ lib, python3 }}: {{ postPatch = {post_patch.rebuild()}; }}",
        """{ lib, python3 }: {
          postPatch = ''
            PYTHONPATH=${
              lib.fileset.toSource {
                root = ../..;
                fileset = lib.fileset.unions [
                  ../../lib/__init__.py
                  ../../lib/exact_text_patch.py
                ];
              }
            } ${lib.getExe python3} ${./patch_nix_managed.py} "$PWD" ${
              lib.escapeShellArg hermesExecutable
            } ${lib.escapeShellArg hermesVersion}
          '';
        }""",
    )
    build_shell = parse_shell(indented_string_body(build_phase.rebuild()))
    install_check_shell = parse_shell(indented_string_body(install_check.rebuild()))

    assert_nix_ast_equal(
        derivation.name,
        "hermesNpmLib.buildNpmPackage",
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "dirs").value,
        '[ "apps/desktop" "apps/shared" ]',
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "env").value,
        """(builtins.removeAttrs electronBuild.commonEnv [
          "ELECTRON_SKIP_BINARY_DOWNLOAD"
        ]) // {
          CI = "1";
          CSC_IDENTITY_AUTO_DISCOVERY = "false";
          ELECTRON_IS_DEV = "0";
          HERMES_DESKTOP_HERMES = hermesExecutable;
          NODE_OPTIONS = "--max-old-space-size=6144";
          npm_config_build_from_source = "true";
        }""",
    )
    assert command_texts(build_shell, "node") == [
        "node scripts/bundle-electron-main.mjs",
        "node scripts/stage-native-deps.mjs darwin arm64",
        "node scripts/run-electron-builder.mjs \\\n"
        "      --mac \\\n"
        "      --arm64 \\\n"
        "      --dir \\\n"
        "      --publish never \\\n"
        "      -c.mac.identity=null \\\n"
        "      -c.npmRebuild=true \\\n"
        "      __NIX_INTERP__",
    ]
    assert 'grep -Fxc -- "$managedVersionDeclaration" "$mainBundle"' in command_texts(
        install_check_shell,
        "grep",
    )
    assert_nix_ast_equal(
        expect_binding(mac_app.values, "bundleId").value,
        Identifier(name="appId"),
    )
    assert_nix_ast_equal(
        expect_binding(mac_app.values, "bundleName").value,
        Identifier(name="appBundleName"),
    )
    assert_nix_ast_equal(
        expect_binding(mac_app.values, "bundleRelPath").value,
        '"Applications/${appBundleName}"',
    )
    assert_nix_ast_equal(
        expect_binding(metadata.values, "platforms").value,
        '[ "aarch64-darwin" ]',
    )
    assert_nix_ast_equal(
        expect_binding(metadata.values, "mainProgram").value,
        Identifier(name="pname"),
    )


def test_hermes_desktop_update_builds_the_real_darwin_package() -> None:
    """Source promotion must prove the patch and complete app build together."""
    updater = _load_updater_module().HermesDesktopUpdater

    assert updater.derivation_validations == (
        DerivationValidation(
            installable=".#packages.aarch64-darwin.hermes-desktop",
            systems=("aarch64-darwin",),
            mode="build",
        ),
    )


def test_hermes_desktop_replaces_copied_bundles_with_a_store_symlink() -> None:
    """Activation must not preserve Gatekeeper quarantine on an old copy."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    first_assertion = expect_instance(package.output, Assertion)
    second_assertion = expect_instance(first_assertion.body, Assertion)
    third_assertion = expect_instance(second_assertion.body, Assertion)
    derivation = expect_instance(third_assertion.body, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)
    passthru = expect_instance(
        expect_binding(arguments.values, "passthru").value,
        AttributeSet,
    )
    mac_app = expect_instance(
        expect_binding(passthru.values, "macApp").value,
        AttributeSet,
    )

    assert_nix_ast_equal(
        expect_binding(mac_app.values, "installMode").value,
        '"symlink"',
    )
