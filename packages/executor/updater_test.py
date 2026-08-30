"""Focused contracts for the source-built Executor macOS package."""

import asyncio
import hashlib
import json
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Protocol

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.ellipses import Ellipses
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    binding_map,
    expect_binding,
    nix_apply,
    nix_attrset_call,
    parse_nix_expr,
)
from lib.tests._shell_ast import (
    command_texts,
    indented_string_body,
    iter_nodes,
    node_text,
    parse_shell,
)
from lib.tests._updater_helpers import collect_events, load_repo_module, run_async
from lib.update.artifacts import GeneratedArtifact
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import (
    CommandResult,
    UpdateEvent,
    UpdateEventKind,
    expect_artifact_updates,
    expect_source_hashes,
)
from lib.update.nix import _build_fetch_from_github_call
from lib.update.nix_expr import identifier_attr_path
from lib.update.paths import REPO_ROOT
from lib.update.updaters import UpdateContext, VersionInfo

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable

    from lib.update.process import RunCommandOptions


class _Patch(Protocol):
    path: Path
    old: str
    new: str


_VERSION = "1.6.0"
_COMMIT = "0ae7959fe03e8fb98d0b8a961438ad88e49fbd4b"
_SRC_HASH = "sha256-NYydsI91ijZldG2GjsaZMyuvUhQrSbxuQDJKS9PdJ4g="
_BUN_VERSION = "1.3.11"
_BUN_SOURCE_URL = (
    "https://github.com/oven-sh/bun/releases/download/"
    f"bun-v{_BUN_VERSION}/bun-darwin-aarch64.zip"
)
_BUN_SOURCE_HASH = "sha256-b1o0Z+2crsR5W/eM1HZQfZ+HDH1XuGyUX8szgSZ3L/w="
_EXECUTOR_ENTITLEMENT_KEYS = (
    "com.apple.security.cs.allow-dyld-environment-variables",
    "com.apple.security.cs.allow-jit",
    "com.apple.security.cs.allow-unsigned-executable-memory",
    "com.apple.security.cs.disable-library-validation",
)
_EXECUTOR_REQUIRED_RESOURCE_PATHS = (
    "executor",
    "emscripten-module.wasm",
    "keyring.node",
    "libsql.node",
    "mcp-app.html",
    "onepassword-core_bg.wasm",
    "workerd",
    "worker-bundler/dist/esbuild.wasm",
    "worker-bundler/dist/index.bundled.js",
    "worker-bundler/dist/index.js",
)
_EXECUTOR_NATIVE_RESOURCE_PATHS = (
    "executor",
    "keyring.node",
    "libsql.node",
    "workerd",
)
_EXECUTOR_NATIVE_MINIMUM_MACOS_VERSIONS = (
    ("executor", "13.0"),
    ("keyring.node", "11.0"),
    ("libsql.node", "14.0"),
    ("workerd", "13.5"),
)
_EXECUTOR_WASM_RESOURCE_PATHS = (
    "emscripten-module.wasm",
    "onepassword-core_bg.wasm",
    "worker-bundler/dist/esbuild.wasm",
)
_EXECUTOR_MANAGED_POLICY_PROBES = (
    (
        "Nix-managed Executor cannot install a mutable background service.",
        ("install", "--port", "49213"),
    ),
    (
        "Nix-managed Executor cannot install a mutable background service.",
        ("service", "install", "--port", "49213"),
    ),
    (
        "Nix-managed Executor cannot uninstall a mutable background service.",
        ("service", "uninstall"),
    ),
    (
        "Nix-managed Executor cannot restart a mutable background service.",
        ("service", "restart"),
    ),
)
_PACKAGE_DIR = REPO_ROOT / "packages/executor"
_BUN_LOCK_SHA256 = "0cc194b2f757a10a09c8aaad333bbbbcc3f670881225889defd9ee40eea7f940"
_BUN_NIX_SHA256 = "afd386ec099c028c0eb6aaa10def26b3b1cd857bec0850bf7088b10a5f78d8b5"
_UPDATER_PATCH_PINS = {
    "@1password/sdk-core@0.4.1-beta.1": (
        "source:patches/@1password%2Fsdk-core@0.4.1-beta.1.patch"
    ),
    "@electric-sql/pglite-socket@0.1.4": (
        "source:patches/@electric-sql%2Fpglite-socket@0.1.4.patch"
    ),
    "agents@0.17.3": "source:patches/agents@0.17.3.patch",
    "bunLockPatch": "local:bun-lock-libsql-0.3.19-remove-self-dependency.patch",
    "effectLspPatchVersion": "0.85.1",
    "libsql@0.3.19": "local:libsql-0.3.19-remove-self-dependency.patch",
    "libsql@0.5.29": "source:patches/libsql@0.5.29.patch",
    "postgres@3.4.9": "source:patches/postgres@3.4.9.patch",
}
_PATCH_METADATA_PIN_NAMES = ("bunLockPatch", "effectLspPatchVersion")

# Byte-for-byte service-management excerpts from Executor commit
# 0ae7959fe03e8fb98d0b8a961438ad88e49fbd4b. Keep this fixture independent of
# patch_nix_managed.py so its anchor contract is not self-fulfilling.
_PINNED_CLI_SERVICE_SOURCE = r"""const supervisedServiceOrigin = (port: number): string => `http://127.0.0.1:${port}`;

const installService = (port: number, commandName: string, boot = false) =>
  Effect.gen(function* () {
    const command = `${cliPrefix} ${commandName}`;
    if (isDevMode) {
      return yield* Effect.fail(
        new Error(
          [
            `\`${command}\` requires the compiled \`executor\` binary so the OS can run it directly.`,
            `In a dev checkout, run \`${cliPrefix} daemon run --foreground\` instead.`,
          ].join("\n"),
        ),
      );
    }

    const backend = getServiceBackend();
    yield* backend.install({ executablePath: process.execPath, port, version: CLI_VERSION, boot });
  });

const serviceUninstallCommand = Command.make("uninstall", {}, () =>
  Effect.gen(function* () {
    const backend = getServiceBackend();
    yield* backend.uninstall();
  }),
);

const serviceRestartCommand = Command.make("restart", {}, () =>
  Effect.gen(function* () {
    const backend = getServiceBackend();
    yield* backend.restart();
  }),
);
"""


def _load_updater_module() -> ModuleType:
    return load_repo_module(
        "packages/executor/updater.py",
        "executor_updater_dedicated_test",
    )


def _load_patch_module() -> ModuleType:
    return load_repo_module(
        "packages/executor/patch_nix_managed.py",
        "executor_nix_policy_patch_test",
    )


def _write_patch_fixture(root: Path, patches: Iterable[_Patch]) -> dict[Path, str]:
    by_path: dict[Path, list[str]] = {}
    for patch in patches:
        by_path.setdefault(patch.path, []).append(patch.old)
    originals: dict[Path, str] = {}
    for relative, anchors in by_path.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n/* fixture boundary */\n".join(anchors)
        path.write_text(content, encoding="utf-8")
        originals[relative] = content
    return originals


def _patch_replacement(module: ModuleType, anchor: str) -> str:
    matches = [patch.new for patch in module._PATCHES if anchor in patch.old]
    assert len(matches) == 1
    return matches[0]


def _normalized_shell_command(command: str) -> str:
    return " ".join(command.replace("\\\n", " ").split())


def _typescript_ast_facts(sources: dict[str, str]) -> dict[str, object]:
    bun = shutil.which("bun")
    assert bun is not None
    script = r"""
import ts from "typescript";

const sources = JSON.parse(await Bun.stdin.text());
const output = {};
for (const [name, text] of Object.entries(sources)) {
  const source = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const callHandlers = {};
  const effectGenerators = {};
  const variables = {};
  const functionStatements = {};
  const objects = [];
  const visit = (node) => {
    if (
      ts.isCallExpression(node) &&
      ts.isPropertyAccessExpression(node.expression) &&
      node.expression.name.text === "handle" &&
      node.arguments.length >= 2 &&
      ts.isStringLiteral(node.arguments[0])
    ) {
      const handler = node.arguments[1];
      if (
        (ts.isArrowFunction(handler) || ts.isFunctionExpression(handler)) &&
        ts.isBlock(handler.body)
      ) {
        callHandlers[node.arguments[0].text] = handler.body.statements.map((statement) =>
          statement.getText(source),
        );
      }
    }
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)) {
      if (node.initializer) variables[node.name.text] = node.initializer.getText(source);
      if (
        node.initializer &&
        (ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer)) &&
        ts.isBlock(node.initializer.body)
      ) {
        functionStatements[node.name.text] = node.initializer.body.statements.map((statement) =>
          statement.getText(source),
        );
      }
      const generators = [];
      const findEffectGenerators = (child) => {
        if (
          ts.isCallExpression(child) &&
          ts.isPropertyAccessExpression(child.expression) &&
          child.expression.expression.getText(source) === "Effect" &&
          child.expression.name.text === "gen"
        ) {
          const callback = child.arguments[0];
          if (callback && ts.isFunctionExpression(callback) && ts.isBlock(callback.body)) {
            generators.push(callback.body.statements.map((statement) => statement.getText(source)));
          }
        }
        ts.forEachChild(child, findEffectGenerators);
      };
      if (node.initializer) findEffectGenerators(node.initializer);
      if (generators.length) effectGenerators[node.name.text] = generators;
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
    ts.forEachChild(node, visit);
  };
  visit(source);
  output[name] = {
    callHandlers,
    diagnostics: source.parseDiagnostics.map((diagnostic) =>
      ts.flattenDiagnosticMessageText(diagnostic.messageText, "\n"),
    ),
    effectGenerators,
    functionStatements,
    objects,
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
    return payload


def test_executor_resolves_release_to_exact_source_and_toolchain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Release discovery must validate immutable source and build-tool metadata."""
    module = _load_updater_module()
    updater = module.ExecutorUpdater()
    api_paths: list[str] = []
    fetched_urls: list[str] = []

    async def github_payload(
        _session: object,
        path: str,
        *,
        config: object,
    ) -> dict[str, str]:
        assert config == updater.config
        api_paths.append(path)
        if path.endswith("/releases/latest"):
            return {"tag_name": f"v{_VERSION}"}
        return {"sha": _COMMIT}

    async def manifest_payload(
        _session: object,
        url: str,
        *,
        config: object,
    ) -> object:
        assert config == updater.config
        fetched_urls.append(url)
        if url.endswith("/package.json") and "/apps/desktop/" not in url:
            return {"packageManager": "bun@1.3.11"}
        return {
            "version": _VERSION,
            "devDependencies": {"electron": "41.2.1"},
        }

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        github_payload,
    )
    monkeypatch.setattr(module, "fetch_json", manifest_payload)

    assert run_async(updater.fetch_latest(object())) == VersionInfo(
        version=_VERSION,
        metadata={
            "bunVersion": "1.3.11",
            "commit": _COMMIT,
            "electronVersion": "41.2.1",
            "tag": f"v{_VERSION}",
        },
    )
    assert api_paths == [
        "repos/UsefulSoftwareCo/executor/releases/latest",
        f"repos/UsefulSoftwareCo/executor/commits/v{_VERSION}",
    ]
    assert fetched_urls == [
        f"https://raw.githubusercontent.com/UsefulSoftwareCo/executor/{_COMMIT}/package.json",
        f"https://raw.githubusercontent.com/UsefulSoftwareCo/executor/{_COMMIT}/apps/desktop/package.json",
    ]


@pytest.mark.parametrize(
    ("commit_payload", "root_manifest", "desktop_manifest", "error_type", "match"),
    [
        ([], {}, {}, TypeError, "has no immutable source commit"),
        ({"sha": "main"}, {}, {}, RuntimeError, "has no immutable source commit"),
        (
            {"sha": _COMMIT},
            [],
            {},
            TypeError,
            "root manifest is not a JSON object",
        ),
        (
            {"sha": _COMMIT},
            {},
            {},
            TypeError,
            "packageManager is missing",
        ),
        (
            {"sha": _COMMIT},
            {"packageManager": "npm@11"},
            {},
            RuntimeError,
            "requires Bun 1.3.11",
        ),
        (
            {"sha": _COMMIT},
            {"packageManager": "bun@1.3.11"},
            [],
            TypeError,
            "desktop manifest is not a JSON object",
        ),
        (
            {"sha": _COMMIT},
            {"packageManager": "bun@1.3.11"},
            {"devDependencies": {"electron": "41.2.1"}},
            TypeError,
            "desktop manifest version is missing",
        ),
        (
            {"sha": _COMMIT},
            {"packageManager": "bun@1.3.11"},
            {"version": _VERSION, "devDependencies": {"electron": ""}},
            TypeError,
            "Electron version is missing",
        ),
        (
            {"sha": _COMMIT},
            {"packageManager": "bun@1.3.11"},
            {"version": "1.5.39", "devDependencies": {"electron": "41.2.1"}},
            RuntimeError,
            "does not match release version",
        ),
        (
            {"sha": _COMMIT},
            {"packageManager": "bun@1.3.11"},
            {"version": _VERSION},
            TypeError,
            "Electron version is missing",
        ),
    ],
)
def test_executor_rejects_incoherent_release_metadata(
    monkeypatch: pytest.MonkeyPatch,
    commit_payload: object,
    root_manifest: object,
    desktop_manifest: object,
    error_type: type[Exception],
    match: str,
) -> None:
    """Mutable refs and mismatched toolchains must fail before hashing."""
    module = _load_updater_module()

    responses = iter(({"tag_name": f"v{_VERSION}"}, commit_payload))
    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=next(responses)),
    )
    manifests = iter((root_manifest, desktop_manifest))
    monkeypatch.setattr(
        module,
        "fetch_json",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=next(manifests)),
    )

    with pytest.raises(error_type, match=match):
        run_async(module.ExecutorUpdater().fetch_latest(object()))


def test_executor_materializes_bun_closure_then_hashes_exact_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The updater must regenerate the Bun graph from the exact release lock."""
    module = _load_updater_module()
    updater = module.ExecutorUpdater()
    fetched_urls: list[str] = []
    seen_commands: list[list[str]] = []
    hash_calls: list[str] = []
    normalized_inputs: list[str] = []

    async def fetch_url(
        _session: object,
        url: str,
        **_kwargs: object,
    ) -> bytes:
        fetched_urls.append(url)
        return b"exact bun lock\n"

    async def run_command(
        args: list[str],
        *,
        options: RunCommandOptions,
    ) -> AsyncIterator[UpdateEvent]:
        assert options.source == "executor"
        seen_commands.append(args)
        output_path = Path(args[-1])
        output_path.write_text("{ generated = true; }\n", encoding="utf-8")
        yield UpdateEvent.status(options.source, "bun2nix running")
        yield UpdateEvent.value(
            options.source,
            CommandResult(args=args, returncode=0, stdout="", stderr=""),
        )

    computed_hashes = iter((_SRC_HASH, _BUN_SOURCE_HASH))

    async def compute_hash(
        source: str,
        expr: str,
        *,
        config: object,
    ) -> AsyncIterator[UpdateEvent]:
        assert source == "executor"
        assert config == updater.config
        hash_calls.append(expr)
        yield UpdateEvent.status(source, "fixed-output hash running")
        yield UpdateEvent.value(source, next(computed_hashes))

    def normalize_bun_nix_path(path: Path) -> None:
        normalized_inputs.append(path.read_text(encoding="utf-8"))
        path.write_text("{ normalized = true; }\n", encoding="utf-8")

    monkeypatch.setattr(module.update_net, "fetch_url", fetch_url)
    monkeypatch.setattr(module, "run_command", run_command)
    monkeypatch.setattr(module, "normalize_bun_nix_path", normalize_bun_nix_path)
    monkeypatch.setattr(module.update_nix, "compute_fixed_output_hash", compute_hash)
    info = VersionInfo(
        _VERSION,
        {
            "bunVersion": "1.3.11",
            "commit": _COMMIT,
            "electronVersion": "41.2.1",
            "tag": f"v{_VERSION}",
        },
    )

    events = run_async(collect_events(updater.fetch_hashes(info, object())))

    assert fetched_urls == [
        f"https://raw.githubusercontent.com/UsefulSoftwareCo/executor/{_COMMIT}/bun.lock"
    ]
    assert len(seen_commands) == 1
    command = seen_commands[0]
    assert command[:4] == [
        "nix",
        "run",
        "path:.#pkgs.aarch64-darwin.executor.passthru.bun2nix",
        "--",
    ]
    assert command[4:6] == ["--lock-file", command[5]]
    assert command[6:8] == ["--copy-prefix", "./"]
    assert command[8:10] == ["--output-file", command[9]]

    artifact_event = next(
        event for event in events if event.kind is UpdateEventKind.ARTIFACT
    )
    assert expect_artifact_updates(artifact_event.payload) == [
        GeneratedArtifact.text(_PACKAGE_DIR / "bun.lock", "exact bun lock\n"),
        GeneratedArtifact.text(
            _PACKAGE_DIR / "bun.nix",
            "{ normalized = true; }\n",
        ),
    ]
    assert normalized_inputs == ["{ generated = true; }\n"]
    assert len(hash_calls) == 2
    assert_nix_ast_equal(
        hash_calls[0],
        _build_fetch_from_github_call(
            "UsefulSoftwareCo",
            "executor",
            rev=_COMMIT,
            fetch_submodules=False,
        ),
    )
    assert_nix_ast_equal(
        hash_calls[1],
        f"""
        pkgs.fetchurl {{
          url = "{_BUN_SOURCE_URL}";
          hash = pkgs.lib.fakeHash;
        }}
        """,
    )
    value_event = events[-1]
    assert value_event.kind is UpdateEventKind.VALUE
    assert expect_source_hashes(value_event.payload) == [
        HashEntry.create("srcHash", _SRC_HASH),
        HashEntry.create("sha256", _BUN_SOURCE_HASH),
    ]
    assert [
        event.message for event in events if event.kind is UpdateEventKind.STATUS
    ] == [
        "bun2nix running",
        "fixed-output hash running",
        "fixed-output hash running",
    ]


def test_executor_dry_run_skips_generated_artifact_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry-run hashing must remain read-only and avoid the Bun generator."""
    module = _load_updater_module()
    updater = module.ExecutorUpdater()

    async def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("artifact materialization ran during dry-run")

    computed_hashes = iter((_SRC_HASH, _BUN_SOURCE_HASH))

    async def compute_hash(
        source: str,
        _expr: str,
        *,
        config: object,
    ) -> AsyncIterator[UpdateEvent]:
        assert config == updater.config
        yield UpdateEvent.value(source, next(computed_hashes))

    monkeypatch.setattr(module.update_net, "fetch_url", forbidden)
    monkeypatch.setattr(module, "run_command", forbidden)
    monkeypatch.setattr(module.update_nix, "compute_fixed_output_hash", compute_hash)
    info = VersionInfo(
        _VERSION,
        {
            "bunVersion": "1.3.11",
            "commit": _COMMIT,
            "electronVersion": "41.2.1",
        },
    )

    events = run_async(
        collect_events(
            updater.fetch_hashes(
                info,
                object(),
                context=UpdateContext(current=None, dry_run=True),
            )
        )
    )

    assert all(event.kind is not UpdateEventKind.ARTIFACT for event in events)
    assert expect_source_hashes(events[-1].payload) == [
        HashEntry.create("srcHash", _SRC_HASH),
        HashEntry.create("sha256", _BUN_SOURCE_HASH),
    ]


def test_executor_materialization_requires_a_registered_package_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Artifact promotion must fail before network access without a package."""
    module = _load_updater_module()
    updater = module.ExecutorUpdater()

    async def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("network access ran without a package directory")

    monkeypatch.setattr(module, "updater_dir_for", lambda _name: None)
    monkeypatch.setattr(module.update_net, "fetch_url", forbidden)
    info = VersionInfo(
        _VERSION,
        {
            "bunVersion": "1.3.11",
            "commit": _COMMIT,
            "electronVersion": "41.2.1",
        },
    )

    with pytest.raises(RuntimeError, match="Package directory not found"):
        run_async(collect_events(updater.fetch_hashes(info, object())))


@pytest.mark.parametrize(
    ("returncode", "writes_output", "match"),
    [
        (1, False, "Refresh Executor Bun closure failed"),
        (0, False, "did not produce bun.nix"),
    ],
)
def test_executor_rejects_failed_or_incomplete_bun_generation(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    writes_output: bool,
    match: str,
) -> None:
    """A failed generator may never publish a partial closure artifact."""
    module = _load_updater_module()
    updater = module.ExecutorUpdater()

    async def fetch_url(*_args: object, **_kwargs: object) -> bytes:
        return b"exact bun lock\n"

    async def run_command(
        args: list[str],
        *,
        options: RunCommandOptions,
    ) -> AsyncIterator[UpdateEvent]:
        if writes_output:
            Path(args[-1]).write_text("{ generated = true; }\n", encoding="utf-8")
        yield UpdateEvent.value(
            options.source,
            CommandResult(
                args=args,
                returncode=returncode,
                stdout="",
                stderr="generator failed" if returncode else "",
            ),
        )

    monkeypatch.setattr(module.update_net, "fetch_url", fetch_url)
    monkeypatch.setattr(module, "run_command", run_command)
    info = VersionInfo(
        _VERSION,
        {
            "bunVersion": "1.3.11",
            "commit": _COMMIT,
            "electronVersion": "41.2.1",
        },
    )

    with pytest.raises(RuntimeError, match=match):
        run_async(collect_events(updater.fetch_hashes(info, object())))


def test_executor_hashing_requires_a_value_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A silent hash worker must not create a source entry."""
    module = _load_updater_module()
    updater = module.ExecutorUpdater()

    async def compute_hash(
        source: str,
        _expr: str,
        *,
        config: object,
    ) -> AsyncIterator[UpdateEvent]:
        assert source == updater.name
        assert config == updater.config
        if False:
            yield UpdateEvent.status(source, "unreachable")

    monkeypatch.setattr(module.update_nix, "compute_fixed_output_hash", compute_hash)
    info = VersionInfo(
        _VERSION,
        {
            "bunVersion": "1.3.11",
            "commit": _COMMIT,
            "electronVersion": "41.2.1",
        },
    )

    with pytest.raises(RuntimeError, match="Missing srcHash output"):
        run_async(
            collect_events(
                updater.fetch_hashes(
                    info,
                    object(),
                    context=UpdateContext(current=None, dry_run=True),
                )
            )
        )


def test_executor_persists_exact_source_and_toolchain_metadata() -> None:
    """sources.json must retain every input needed to reproduce the build."""
    module = _load_updater_module()
    info = VersionInfo(
        _VERSION,
        {
            "bunVersion": "1.3.11",
            "commit": _COMMIT,
            "electronVersion": "41.2.1",
        },
    )

    assert module.ExecutorUpdater().build_result(
        info,
        [
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("sha256", _BUN_SOURCE_HASH),
        ],
    ) == SourceEntry.model_validate({
        "version": _VERSION,
        "commit": _COMMIT,
        "electronVersion": "41.2.1",
        "pins": _UPDATER_PATCH_PINS,
        "hashes": HashCollection.from_value([
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("sha256", _BUN_SOURCE_HASH, url=_BUN_SOURCE_URL),
        ]),
    })


def test_executor_validates_the_materialized_source_package() -> None:
    """Final updater validation must build the not-yet-promoted path flake."""
    updater = _load_updater_module().ExecutorUpdater()

    assert updater.supported_platforms == ("aarch64-darwin",)
    assert updater.generated_artifact_files == ("bun.lock", "bun.nix")
    assert updater.derivation_validations == (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            mode="build",
        ),
    )


@pytest.mark.parametrize(
    ("metadata", "match"),
    [
        ({}, "immutable source commit"),
        ({"commit": _COMMIT}, "Electron version"),
        (
            {"commit": _COMMIT, "electronVersion": "41.2.1"},
            "Bun version",
        ),
        (
            {
                "commit": _COMMIT,
                "electronVersion": "41.2.1",
                "bunVersion": "1.3.10",
            },
            "requires Bun 1.3.11",
        ),
    ],
)
def test_executor_result_requires_complete_toolchain_metadata(
    metadata: dict[str, str],
    match: str,
) -> None:
    """Hand-written updater results may not omit reproducibility metadata."""
    with pytest.raises(RuntimeError, match=match):
        _load_updater_module().ExecutorUpdater().build_result(
            VersionInfo(_VERSION, metadata),
            [HashEntry.create("srcHash", _SRC_HASH)],
        )


@pytest.mark.parametrize(
    ("hashes", "error_type", "match"),
    [
        (
            {"aarch64-darwin": _BUN_SOURCE_HASH},
            TypeError,
            "structured source hash entries",
        ),
        (
            [HashEntry.create("srcHash", _SRC_HASH)],
            RuntimeError,
            "one Bun source hash, found 0",
        ),
        (
            [
                HashEntry.create("srcHash", _SRC_HASH),
                HashEntry.create("sha256", _BUN_SOURCE_HASH),
                HashEntry.create(
                    "sha256", _SRC_HASH, url="https://example.invalid/bun.zip"
                ),
            ],
            RuntimeError,
            "one Bun source hash, found 2",
        ),
    ],
)
def test_executor_result_requires_one_structured_bun_source(
    hashes: SourceHashes,
    error_type: type[Exception],
    match: str,
) -> None:
    """Promotion must reject ambiguous or non-structured Bun source metadata."""
    info = VersionInfo(
        _VERSION,
        {
            "bunVersion": _BUN_VERSION,
            "commit": _COMMIT,
            "electronVersion": "41.2.1",
        },
    )

    with pytest.raises(error_type, match=match):
        _load_updater_module().ExecutorUpdater().build_result(info, hashes)


def test_executor_sources_pin_the_acquired_public_release() -> None:
    """The package source entry must match the verified immutable release."""
    source = SourceEntry.model_validate_json(
        (_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8")
    )

    assert source == SourceEntry.model_validate({
        "version": _VERSION,
        "commit": _COMMIT,
        "electronVersion": "41.2.1",
        "pins": _UPDATER_PATCH_PINS,
        "hashes": HashCollection.from_value([
            HashEntry.create("sha256", _BUN_SOURCE_HASH, url=_BUN_SOURCE_URL),
            HashEntry.create("srcHash", _SRC_HASH),
        ]),
    })


def test_executor_bun_closure_is_exact_and_semantically_parseable() -> None:
    """Checked-in Bun artifacts must be the exact lock-derived closure."""
    bun_lock = (_PACKAGE_DIR / "bun.lock").read_bytes()
    bun_nix = (_PACKAGE_DIR / "bun.nix").read_bytes()

    assert hashlib.sha256(bun_lock).hexdigest() == _BUN_LOCK_SHA256
    assert hashlib.sha256(bun_nix).hexdigest() == _BUN_NIX_SHA256

    closure = expect_instance(
        parse_nix_expr(bun_nix.decode("utf-8")),
        FunctionDefinition,
    )
    assert [
        argument.name
        for argument in closure.argument_set
        if isinstance(argument, Identifier)
    ] == [
        "copyPathToStore",
        "fetchurl",
    ]
    assert isinstance(closure.argument_set[-1], Ellipses)
    packages = expect_instance(closure.output, AttributeSet)
    assert len(packages.values) == 2767
    package_bindings = binding_map(packages.values)
    assert_nix_ast_equal(
        package_bindings['"@executor-js/desktop"'].value,
        "copyPathToStore ./apps/desktop",
    )
    assert_nix_ast_equal(
        package_bindings['"@executor-js/local"'].value,
        "copyPathToStore ./apps/local",
    )
    for package_name in _UPDATER_PATCH_PINS:
        if package_name in _PATCH_METADATA_PIN_NAMES:
            continue
        package = expect_instance(
            package_bindings[f'"{package_name}"'].value,
            FunctionCall,
        )
        assert package.name == Identifier(name="fetchurl")


def _executor_package_contract() -> tuple[Assertion, AttributeSet]:
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    assert Identifier(name="runCommand") in package.argument_set
    platform_assertion = expect_instance(package.output, Assertion)
    runtime_assertion = expect_instance(platform_assertion.body, Assertion)
    package_binding = expect_binding(platform_assertion.scope, "package")
    derivation = expect_instance(package_binding.value, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)

    assert_nix_ast_equal(
        platform_assertion.expression,
        'stdenv.hostPlatform.system == "aarch64-darwin"',
    )
    assert_nix_ast_equal(runtime_assertion.expression, "electronRuntimeVersionCheck")
    assert_nix_ast_equal(runtime_assertion.body, Identifier(name="package"))
    assert_nix_ast_equal(derivation.name, "stdenv.mkDerivation")
    return platform_assertion, arguments


def test_executor_package_pins_exact_source_and_toolchains() -> None:
    """The package must bind the immutable source and every exact toolchain input."""
    platform_assertion, _ = _executor_package_contract()

    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "src").value,
        nix_attrset_call(
            Identifier(name="fetchFromGitHub"),
            owner="UsefulSoftwareCo",
            repo="executor",
            rev=identifier_attr_path("selfSource", "commit"),
            hash=nix_apply(
                identifier_attr_path("outputs", "lib", "sourceHash"),
                Identifier(name="pname"),
                StringPrimitive(value="srcHash"),
            ),
        ),
    )
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "electronBuild").value,
        "nixcfgElectron.sourceBuildFor electronVersion",
    )
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "electronVersion").value,
        "selfSource.electronVersion",
    )
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "executorEntitlements").value,
        "./entitlements.plist",
    )
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "executorEntitlementKeys").value,
        "[\n" + "\n".join(f'  "{key}"' for key in _EXECUTOR_ENTITLEMENT_KEYS) + "\n]",
    )
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "executorRequiredResourcePaths").value,
        "[\n"
        + "\n".join(f'  "{path}"' for path in _EXECUTOR_REQUIRED_RESOURCE_PATHS)
        + "\n]",
    )
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "executorNativeResourcePaths").value,
        "[\n"
        + "\n".join(f'  "{path}"' for path in _EXECUTOR_NATIVE_RESOURCE_PATHS)
        + "\n]",
    )
    assert_nix_ast_equal(
        expect_binding(
            platform_assertion.scope,
            "executorNativeMinimumMacosVersions",
        ).value,
        "[\n"
        + "\n".join(
            f'  {{\n    path = "{path}";\n    version = "{version}";\n  }}'
            for path, version in _EXECUTOR_NATIVE_MINIMUM_MACOS_VERSIONS
        )
        + "\n]",
    )
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "minimumMacosVersion").value,
        StringPrimitive(value="14.0"),
    )
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "executorWasmResourcePaths").value,
        "[\n"
        + "\n".join(f'  "{path}"' for path in _EXECUTOR_WASM_RESOURCE_PATHS)
        + "\n]",
    )
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "executorWasmResourceArguments").value,
        """lib.concatMapStringsSep " " (
          path: ''"$executorResources/${path}"''
        ) executorWasmResourcePaths""",
    )
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "executorManagedPolicyProbes").value,
        "[\n"
        + "\n".join(
            "  {\n"
            f'    message = "{message}";\n'
            "    arguments = [\n"
            + "\n".join(f'      "{argument}"' for argument in arguments)
            + "\n    ];\n"
            "  }"
            for message, arguments in _EXECUTOR_MANAGED_POLICY_PROBES
        )
        + "\n]",
    )
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "bunSourceMetadata").value,
        'outputs.lib.sourceHashEntry pname "sha256"',
    )
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "bunVersionMatch").value,
        'builtins.match ".*/bun-v([^/]+)/.*" bunSourceMetadata.url',
    )
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "bunVersion").value,
        """
        if bunVersionMatch == null then
          throw "Executor updater produced an invalid Bun source URL: ${bunSourceMetadata.url}"
        else
          builtins.head bunVersionMatch
        """,
    )
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "bunSource").value,
        """fetchurl {
          inherit (bunSourceMetadata) hash url;
        }""",
    )
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "bunExact").value,
        """bun.overrideAttrs (previousAttrs: {
          version = bunVersion;
          src = bunSource;
          passthru = (previousAttrs.passthru or { }) // {
            sources = {
              aarch64-darwin = bunSource;
            };
          };
        })""",
    )
    fake_node_derivation = expect_instance(
        expect_binding(platform_assertion.scope, "bunWithFakeNode").value,
        FunctionCall,
    )
    fake_node_arguments = expect_instance(
        fake_node_derivation.argument,
        AttributeSet,
    )
    assert_nix_ast_equal(fake_node_derivation.name, "stdenvNoCC.mkDerivation")
    fake_node_install = expect_binding(
        fake_node_arguments.values,
        "installPhase",
    ).value.rebuild()
    expected_fake_node_install = """''
          runHook preInstall

          cp -R "${bunExact}/." "$out"
          chmod u+w "$out/bin"
          for nodeBinary in node npm bunx; do
            if [ ! -e "$out/bin/$nodeBinary" ]; then
              ln -s "$out/bin/bun" "$out/bin/$nodeBinary"
            fi
          done
          makeWrapper "$out/bin/bunx" "$out/bin/npx"

          runHook postInstall
        ''"""
    assert_nix_ast_equal(
        f"{{ bunExact }}: {fake_node_install}",
        f"{{ bunExact }}: {expected_fake_node_install}",
    )


def test_executor_package_owned_entitlements_are_an_exact_allowlist() -> None:
    """Signing must never inherit new upstream privileges without explicit review."""
    entitlements = plistlib.loads((_PACKAGE_DIR / "entitlements.plist").read_bytes())

    assert entitlements == dict.fromkeys(_EXECUTOR_ENTITLEMENT_KEYS, True)


def test_executor_package_builds_the_owned_desktop_and_sidecar() -> None:
    """The derivation must patch, compile, and package the managed app and CLI."""
    _, arguments = _executor_package_contract()

    assert_nix_ast_equal(
        expect_binding(arguments.values, "nativeBuildInputs").value,
        "[ bunExact bun2nix.hook cctools gnupatch libarchive makeWrapper nodejs_24 python3 ]",
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "bunDeps").value,
        "bunDeps",
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "bunInstallFlags").value,
        '[ "--linker=isolated" "--backend=symlink" "--frozen-lockfile" ]',
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "dontRunLifecycleScripts").value,
        Primitive(value=True),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "strictDeps").value,
        Primitive(value=True),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "__darwinAllowLocalNetworking").value,
        Primitive(value=True),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "env").value,
        """electronBuild.commonEnv // {
          CI = "1";
          CSC_IDENTITY_AUTO_DISCOVERY = "false";
          EXECUTOR_DISABLE_UPDATE_CHECK = "1";
          NODE_OPTIONS = "--max-old-space-size=6144";
        }""",
    )
    post_cache_setup = expect_instance(
        expect_binding(arguments.values, "postBunSetInstallCacheDirPhase").value,
        IndentedString,
    )
    assert [
        _normalized_shell_command(command)
        for command in command_texts(
            parse_shell(indented_string_body(post_cache_setup.rebuild())),
            "__NIX_INTERP__",
        )
    ] == [
        "__NIX_INTERP__ __NIX_INTERP__ "
        '"$BUN_INSTALL_CACHE_DIR" "$bunDeps/share/bun-cache" '
        "/nix/store __NIX_INTERP__"
    ]

    post_patch = expect_instance(
        expect_binding(arguments.values, "postPatch").value,
        IndentedString,
    )
    assert command_texts(
        parse_shell(indented_string_body(post_patch.rebuild())),
        "__NIX_INTERP__",
    ) == ['PYTHONPATH=__NIX_INTERP__ __NIX_INTERP__ __NIX_INTERP__ "$PWD"']
    assert command_texts(
        parse_shell(indented_string_body(post_patch.rebuild())),
        "patch",
    ) == ["patch -p1"]
    assert_nix_ast_equal(
        f"{{ lib, python3 }}: {{ postPatch = {post_patch.rebuild()}; }}",
        """{ lib, python3 }: {
          postPatch = ''
            patch -p1 < ${bunLockPatch}
            PYTHONPATH=${
              lib.fileset.toSource {
                root = ../..;
                fileset = lib.fileset.unions [
                  ../../lib/__init__.py
                  ../../lib/exact_text_patch.py
                ];
              }
            } ${lib.getExe python3} ${./patch_nix_managed.py} "$PWD"
          '';
        }""",
    )

    build_phase = expect_instance(
        expect_binding(arguments.values, "buildPhase").value,
        IndentedString,
    )
    build_commands = parse_shell(indented_string_body(build_phase.rebuild()))
    assert command_texts(build_commands, "bun") == [
        "bun --version",
        "bun run prepare",
        "bun run --filter @executor-js/local build",
        "bun ./scripts/build-sidecar.ts",
        "bun run test:smoke",
    ]
    assert [
        _normalized_shell_command(command)
        for command in command_texts(build_commands, "bunx")
    ] == [
        "bunx --bun electron-vite build",
        "bunx --bun electron-builder --mac --arm64 --dir --publish never "
        "--config electron-builder.config.ts -c.mac.identity=null "
        "-c.mac.hardenedRuntime=false -c.mac.notarize=false "
        "-c.mac.minimumSystemVersion=__NIX_INTERP__ "
        "-c.npmRebuild=false __NIX_INTERP__",
    ]
    ordered_build_commands = [
        _normalized_shell_command(command) for command in command_texts(build_commands)
    ]
    assert 'test "$(bun --version)" = "__NIX_INTERP__"' in ordered_build_commands
    assert (
        ordered_build_commands.index("bun ./scripts/build-sidecar.ts")
        < (ordered_build_commands.index("bun run test:smoke"))
        < ordered_build_commands.index("bunx --bun electron-vite build")
    )
    assert [
        _normalized_shell_command(command)
        for command in command_texts(build_commands, "grep")
    ] == [
        "grep -Fq '\"use effect-lsp-patch-version __NIX_INTERP__\";' "
        "node_modules/typescript/lib/typescript.js",
        "grep -Fq '\"use effect-lsp-patch-version __NIX_INTERP__\";' "
        "node_modules/typescript/lib/_tsc.js",
    ]


def test_executor_package_validates_runtime_and_managed_policy() -> None:
    """Install checks must prove identity, resources, signing, and fail-closed policy."""
    _, arguments = _executor_package_contract()

    assert_nix_ast_equal(
        expect_binding(arguments.values, "doInstallCheck").value,
        Primitive(value=True),
    )
    install_phase = expect_instance(
        expect_binding(arguments.values, "installPhase").value,
        IndentedString,
    )
    normalized_install_phase_commands = [
        _normalized_shell_command(command)
        for command in command_texts(
            parse_shell(indented_string_body(install_phase.rebuild()))
        )
    ]
    assert (
        "/usr/bin/plutil -replace LSMinimumSystemVersion -string "
        '"__NIX_INTERP__" '
        '"$out/Applications/__NIX_INTERP__/Contents/Info.plist"'
        in normalized_install_phase_commands
    )
    post_fixup = expect_instance(
        expect_binding(arguments.values, "postFixup").value,
        IndentedString,
    )
    assert [
        _normalized_shell_command(command)
        for command in command_texts(
            parse_shell(indented_string_body(post_fixup.rebuild())),
            "/usr/bin/codesign",
        )
    ] == [
        "/usr/bin/codesign --force --sign - --options runtime "
        '--entitlements "$entitlements" "$cli"',
        "/usr/bin/codesign --force --deep --sign - --options runtime "
        '--entitlements "$entitlements" "$app"',
    ]

    install_check = expect_instance(
        expect_binding(arguments.values, "installCheckPhase").value,
        IndentedString,
    )
    install_check_commands = parse_shell(indented_string_body(install_check.rebuild()))
    codesign_checks = command_texts(install_check_commands, "/usr/bin/codesign")
    assert '/usr/bin/codesign --verify --strict "$target"' in codesign_checks
    assert '/usr/bin/codesign --display --verbose=4 "$target"' in codesign_checks
    assert (
        '/usr/bin/codesign --display --entitlements - --xml "$target"'
        in codesign_checks
    )
    assert (
        '/usr/libexec/PlistBuddy -c "Print :$entitlement" "$entitlementsPlist"'
        in command_texts(install_check_commands, "/usr/libexec/PlistBuddy")
    )
    brittle_validation_pipelines = [
        node_text(node, install_check_commands.sanitized)
        for node in iter_nodes(install_check_commands.tree.root_node, "pipeline")
        if "/usr/bin/codesign" in node_text(node, install_check_commands.sanitized)
        or "/usr/bin/otool" in node_text(node, install_check_commands.sanitized)
    ]
    assert brittle_validation_pipelines == []
    minimum_version_parsers = [
        _normalized_shell_command(command)
        for command in command_texts(install_check_commands, "awk")
        if "LC_BUILD_VERSION" in command
    ]
    assert len(minimum_version_parsers) == 1
    minimum_version_parser = minimum_version_parsers[0]
    assert (
        'inBuildVersion && $1 == "minos" '
        "{ print $2; inBuildVersion = 0 }" in minimum_version_parser
    )
    assert "print $2; exit" not in minimum_version_parser
    assert '<<< "$loadCommands"' not in minimum_version_parser
    assert minimum_version_parser.endswith('"$loadCommandsFile"')
    otool_redirects = [
        _normalized_shell_command(node_text(node, install_check_commands.sanitized))
        for node in iter_nodes(
            install_check_commands.tree.root_node,
            "redirected_statement",
        )
        if "/usr/bin/otool" in node_text(node, install_check_commands.sanitized)
    ]
    assert otool_redirects == [
        '/usr/bin/otool -l "$executorResources/$relativePath" > "$loadCommandsFile"'
    ]
    normalized_install_commands = [
        _normalized_shell_command(command)
        for command in command_texts(install_check_commands)
    ]
    assert '/usr/bin/lipo "$target" -verify_arch arm64' in normalized_install_commands
    assert (
        '/usr/bin/codesign --verify --strict "$target"' in normalized_install_commands
    )
    assert (
        'test "$(ELECTRON_RUN_AS_NODE=1 "$executable" -p '
        '\'process.versions.electron\')" = "__NIX_INTERP__"'
        in normalized_install_commands
    )
    assert (
        'test "$("$out/bin/__NIX_INTERP__" --version)" = '
        '"executor v__NIX_INTERP__"' in normalized_install_commands
    )
    assert '"$executorResources/workerd" --version' in normalized_install_commands
    assert (
        "test \"$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "
        '"$plist")" = "__NIX_INTERP__"' in normalized_install_commands
    )
    assert (
        'test "$(realpath "$out/bin/__NIX_INTERP__")" = "$cli"'
        in normalized_install_commands
    )
    assert 'test "$actualMinimum" = "$expectedMinimum"' in normalized_install_commands
    assert (
        "grep -Fq 'name=\"executor-mcp-apps-shell\"' "
        '"$executorResources/mcp-app.html"' in normalized_install_commands
    )
    assert (
        "__NIX_INTERP__ -e ' for (const path of process.argv.slice(1)) { "
        "new WebAssembly.Module(await Bun.file(path).arrayBuffer()); } ' "
        "__NIX_INTERP__" in normalized_install_commands
    )
    assert (
        'env -i HOME="$probeHome" USER=executor-probe LOGNAME=executor-probe '
        'PATH="$fakeBin:/usr/bin:/bin" TMPDIR="$probeRoot/tmp" '
        'XDG_DATA_HOME="$probeHome/data" XDG_CONFIG_HOME="$probeHome/config" '
        'XDG_CACHE_HOME="$probeHome/cache" XDG_STATE_HOME="$probeHome/state" '
        'EXECUTOR_DATA_DIR="$probeHome/data/executor" '
        'EXECUTOR_SCOPE_DIR="$probeHome/scope" EXECUTOR_DISABLE_UPDATE_CHECK=1 '
        "EXECUTOR_DISABLE_ANALYTICS=1 EXECUTOR_DISABLE_INTEGRATIONS_FETCH=1 "
        "DO_NOT_TRACK=1 NO_COLOR=1 CI=1 "
        'EXECUTOR_PROBE_LAUNCHCTL_LOG="$launchctlLog" "$cli" "$@"'
        in normalized_install_commands
    )
    assert 'cmp "$probeRoot/before" "$probeRoot/after"' in normalized_install_commands
    assert 'test ! -s "$launchctlLog"' in normalized_install_commands
    assert 'grep -Fq "$expected" "$probeRoot/output"' in normalized_install_commands
    for managed_message in (
        "Nix-managed Executor cannot rotate a supervised daemon token from the app.",
        "Nix-managed Executor cannot reset data from the app.",
    ):
        assert (
            f"grep -a -Fq '{managed_message}' \"$resources/app.asar\""
            in normalized_install_commands
        )
    assert normalized_install_commands.count("__NIX_INTERP__") == 1


def test_executor_package_exposes_managed_app_and_app_free_cli() -> None:
    """The shared route must be able to install one app and one resource-aware CLI."""
    platform_assertion, arguments = _executor_package_contract()

    passthru = expect_instance(
        expect_binding(arguments.values, "passthru").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        "{ runCommand, pname, version, package, appBundleName }: "
        + expect_binding(passthru.values, "cliPackage").value.rebuild(),
        """
        { runCommand, pname, version, package, appBundleName }:
        runCommand "${pname}-cli-${version}" { } ''
          mkdir -p "$out/bin"
          ln -s \\
            "${package}/Applications/${appBundleName}/Contents/Resources/executor/executor" \\
            "$out/bin/${pname}"
        ''
        """,
    )
    mac_app = expect_instance(
        expect_binding(passthru.values, "macApp").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(mac_app.values, "bundleId").value,
        "appId",
    )
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "appId").value,
        StringPrimitive(value="sh.executor.desktop"),
    )
    assert_nix_ast_equal(
        expect_binding(mac_app.values, "bundleRelPath").value,
        StringPrimitive(value="Applications/Executor.app"),
    )
    metadata = expect_instance(
        expect_binding(arguments.values, "meta").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(metadata.values, "sourceProvenance").value,
        "[ lib.sourceTypes.fromSource lib.sourceTypes.binaryNativeCode ]",
    )


def test_executor_bun_cache_shards_apply_the_exact_lock_patches() -> None:
    """Bun cache materialization must preserve every lockfile patch offline."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    platform_assertion = expect_instance(package.output, Assertion)
    expect_instance(platform_assertion.body, Assertion)

    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "bunPackageShards").value,
        """lib.groupBy (
          entry: builtins.substring 0 2 (builtins.hashString "sha256" entry.name)
        ) bunPackageEntries""",
    )


def test_executor_patch_locks_come_from_updater_metadata() -> None:
    """The updater owns exact patched package specs and their patch sources."""
    module = _load_updater_module()
    assert module.ExecutorUpdater.source_pins == _UPDATER_PATCH_PINS

    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    platform_assertion = expect_instance(package.output, Assertion)
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "patchedBunDependencies").value,
        """
        lib.mapAttrs resolvePinnedPatch (
          builtins.removeAttrs selfSource.pins patchMetadataPinNames
        )
        """,
    )
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "patchMetadataPinNames").value,
        '[ "bunLockPatch" "effectLspPatchVersion" ]',
    )
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "bunLockPatch").value,
        'resolvePinnedPatch "bunLockPatch" selfSource.pins.bunLockPatch',
    )
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "effectLspPatchVersion").value,
        "selfSource.pins.effectLspPatchVersion",
    )
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "resolvePinnedPatch").value,
        """_: patch:
        if lib.hasPrefix "source:" patch then
          src + "/${lib.removePrefix "source:" patch}"
        else if lib.hasPrefix "local:" patch then
          ./. + "/${lib.removePrefix "local:" patch}"
        else
          throw "Executor updater emitted an unsupported patch source"
        """,
    )
    assert_nix_ast_equal(
        expect_binding(platform_assertion.scope, "bunDeps").value,
        """pkgs.symlinkJoin {
          name = "executor-bun-cache";
          paths = builtins.attrValues (builtins.mapAttrs buildBunShard bunPackageShards);
          passthru.nixcfg = {
            packageCount = builtins.length bunPackageEntries;
            shardCount = builtins.length shardSizes;
            maxShardSize = builtins.foldl' lib.max 0 shardSizes;
            minShardSize = builtins.foldl' lib.min (builtins.head shardSizes) (builtins.tail shardSizes);
          };
        }""",
    )


def test_executor_bun_workspace_paths_materialize_from_source_at_build_time() -> None:
    """Generated workspace entries must not realize the fetched source during eval."""
    platform_assertion, _ = _executor_package_contract()

    materializer = expect_binding(
        platform_assertion.scope,
        "copyBunWorkspacePathToStore",
    ).value
    assert_nix_ast_equal(
        "{ lib, pkgs, runCommand, src }: " + materializer.rebuild(),
        """{ lib, pkgs, runCommand, src }: path:
        let
          generatedRoot = "${toString ./.}/";
          pathString = toString path;
          relativePath = lib.removePrefix generatedRoot pathString;
          derivationName = lib.replaceStrings [ "/" ] [ "-" ] relativePath;
        in
        assert lib.assertMsg (lib.hasPrefix generatedRoot pathString) (
          "Executor bun.nix referenced a path outside its generated workspace: ${pathString}"
        );
        runCommand "executor-bun-workspace-${derivationName}" { inherit src; } ''
          mkdir -p "$out"
          cp -R "$src/${relativePath}/." "$out"
        ''""",
    )


def test_executor_libsql_0319_metadata_patches_remove_only_the_self_dependency(
    tmp_path: Path,
) -> None:
    """The offline Bun graph must correct libsql's published self-cycle exactly."""
    patch_executable = shutil.which("patch")
    bun_executable = shutil.which("bun")
    assert patch_executable is not None
    assert bun_executable is not None

    package_root = tmp_path / "package"
    package_root.mkdir()
    package_json = package_root / "package.json"
    optional_dependencies = {
        "@libsql/darwin-arm64": "0.3.19",
        "@libsql/darwin-x64": "0.3.19",
        "@libsql/linux-arm64-gnu": "0.3.19",
        "@libsql/linux-arm64-musl": "0.3.19",
        "@libsql/linux-x64-gnu": "0.3.19",
        "@libsql/linux-x64-musl": "0.3.19",
        "@libsql/win32-x64-msvc": "0.3.19",
    }
    expected_manifest = {
        "name": "libsql",
        "version": "0.3.19",
        "dependencies": {
            "@neon-rs/load": "^0.0.4",
            "detect-libc": "2.0.2",
        },
        "optionalDependencies": optional_dependencies,
    }
    package_json.write_text(
        json.dumps(
            expected_manifest
            | {
                "dependencies": expected_manifest["dependencies"]
                | {"libsql": "^0.3.15"}
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(  # noqa: S603
        [
            patch_executable,
            "-p1",
            "-i",
            str(_PACKAGE_DIR / "libsql-0.3.19-remove-self-dependency.patch"),
        ],
        cwd=package_root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(package_json.read_text(encoding="utf-8")) == expected_manifest

    lock_root = tmp_path / "lock"
    lock_root.mkdir()
    lock_file = lock_root / "bun.lock"
    shutil.copy2(_PACKAGE_DIR / "bun.lock", lock_file)
    subprocess.run(  # noqa: S603
        [
            patch_executable,
            "-p1",
            "-i",
            str(_PACKAGE_DIR / "bun-lock-libsql-0.3.19-remove-self-dependency.patch"),
        ],
        cwd=lock_root,
        check=True,
        capture_output=True,
        text=True,
    )
    result = subprocess.run(  # noqa: S603
        [
            bun_executable,
            "-e",
            (
                "const lock=Bun.JSONC.parse(await Bun.file(process.argv[1]).text());"
                "console.log(JSON.stringify(lock.packages["
                "'@libsql/kysely-libsql/@libsql/client/libsql'][2].dependencies));"
            ),
            str(lock_file),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == {
        "@neon-rs/load": "^0.0.4",
        "detect-libc": "2.0.2",
    }


def _write_bun_cache_package(
    root: Path,
    *,
    name: str,
    version: str,
    files: dict[str, tuple[str, int]],
) -> None:
    root.mkdir(parents=True)
    (root / "package.json").write_text(
        json.dumps({"name": name, "version": version}) + "\n",
        encoding="utf-8",
    )
    for relative_path, (contents, mode) in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        path.chmod(mode)


def test_executor_materializes_only_prepare_mutation_packages(
    tmp_path: Path,
) -> None:
    """The temp Bun cache must copy up only packages upstream prepare mutates."""
    bash = shutil.which("bash")
    python = sys.executable
    assert bash is not None

    store_root = tmp_path / "store"
    source_cache = tmp_path / "source-cache"
    mutable_cache = tmp_path / "mutable-cache"
    store_root.mkdir()
    source_cache.mkdir()
    mutable_cache.mkdir()

    packages = {
        "typescript@5.9.3@@@1": (
            "typescript",
            "5.9.3",
            {
                "lib/typescript.js": ("typescript-original\n", 0o444),
                "lib/_tsc.js": ("tsc-original\n", 0o444),
            },
        ),
        "@typescript/native-preview-darwin-arm64@7.0.0-opaque@@@1": (
            "@typescript/native-preview-darwin-arm64",
            "7.0.0-dev.20260415.1",
            {"lib/tsgo": ("native-preview-original\n", 0o555)},
        ),
        "left-pad@1.3.0@@@1": (
            "left-pad",
            "1.3.0",
            {"index.js": ("module.exports = () => {};\n", 0o444)},
        ),
    }
    store_packages: dict[str, Path] = {}
    for cache_key, (name, version, files) in packages.items():
        store_package = store_root / cache_key.replace("/", "--")
        _write_bun_cache_package(
            store_package,
            name=name,
            version=version,
            files=files,
        )
        store_packages[cache_key] = store_package
        for cache in (source_cache, mutable_cache):
            entry = cache / cache_key
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.symlink_to(store_package, target_is_directory=True)

    subprocess.run(  # noqa: S603
        [
            bash,
            str(_PACKAGE_DIR / "materialize-mutable-bun-cache.sh"),
            str(mutable_cache),
            str(source_cache),
            str(store_root),
            python,
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    for cache_key in packages:
        mutable_entry = mutable_cache / cache_key
        source_entry = source_cache / cache_key
        assert source_entry.is_symlink()
        if cache_key == "left-pad@1.3.0@@@1":
            assert mutable_entry.is_symlink()
        else:
            assert mutable_entry.is_dir()
            assert not mutable_entry.is_symlink()
            assert mutable_entry.resolve().is_relative_to(mutable_cache.resolve())

    typescript = mutable_cache / "typescript@5.9.3@@@1"
    (typescript / "lib/typescript.js").write_text(
        '"use effect-lsp-patch-version 0.85.1";\n',
        encoding="utf-8",
    )
    (typescript / "lib/_tsc.js").write_text(
        '"use effect-lsp-patch-version 0.85.1";\n',
        encoding="utf-8",
    )

    native_preview = (
        mutable_cache / "@typescript/native-preview-darwin-arm64@7.0.0-opaque@@@1"
    )
    tsgo = native_preview / "lib/tsgo"
    original_tsgo = native_preview / "lib/tsgo.original"
    tsgo.rename(original_tsgo)
    tsgo.write_text("effect-tsgo\n", encoding="utf-8")
    tsgo.chmod(0o755)

    assert tsgo.read_text(encoding="utf-8") == "effect-tsgo\n"
    assert tsgo.stat().st_mode & 0o111
    assert original_tsgo.read_text(encoding="utf-8") == "native-preview-original\n"
    assert (store_packages["typescript@5.9.3@@@1"] / "lib/typescript.js").read_text(
        encoding="utf-8"
    ) == "typescript-original\n"
    assert (
        store_packages["@typescript/native-preview-darwin-arm64@7.0.0-opaque@@@1"]
        / "lib/tsgo"
    ).read_text(encoding="utf-8") == "native-preview-original\n"


def test_executor_nix_policy_patch_disables_every_mutable_surface(
    tmp_path: Path,
) -> None:
    """The source patch must cover updater, CLI, UI, IPC, and service writes."""
    module = _load_patch_module()
    originals = _write_patch_fixture(tmp_path, module._PATCHES)

    assert module.main([str(tmp_path)]) == 0

    patched_by_path: dict[Path, str] = {}
    for relative, original in originals.items():
        expected = original
        for patch in module._PATCHES:
            if patch.path == relative:
                expected = expected.replace(patch.old, patch.new)
        actual = (tmp_path / relative).read_text(encoding="utf-8")
        assert actual == expected
        patched_by_path[relative] = actual

    all_patched = "\n".join(patched_by_path.values())
    assert module._CLI_SOURCE in patched_by_path
    assert "const NIX_MANAGED = true;" in all_patched
    assert "Updates are managed by Nix." in all_patched
    assert "autoUpdater.checkForUpdates()" not in all_patched
    assert "autoUpdater.quitAndInstall(false, true)" not in all_patched
    assert 'label: "Check for Updates…"' not in all_patched
    assert 'id="update"' not in all_patched
    assert "if (NIX_MANAGED) return {};" in all_patched
    assert (
        "Nix-managed Executor cannot install a mutable background service"
        in all_patched
    )
    for operation in ("install", "uninstall", "restart"):
        assert (
            f"Nix-managed Executor cannot {operation} a mutable background service."
            in all_patched
        )
    assert (
        "Nix-managed Executor cannot rotate a supervised daemon token from the app."
        in all_patched
    )
    assert "Nix-managed Executor cannot reset data from the app." in all_patched

    with pytest.raises(RuntimeError, match="already applied"):
        module.patch_tree(tmp_path)


@pytest.mark.parametrize("copies", [0, 2])
def test_executor_nix_policy_patch_rejects_source_drift_atomically(
    tmp_path: Path,
    copies: int,
) -> None:
    """Any missing or duplicated anchor must leave every source file untouched."""
    module = _load_patch_module()
    target = module._PATCHES[-1]
    patches = [*module._PATCHES[:-1], *([target] * copies)]
    originals = _write_patch_fixture(tmp_path, patches)

    with pytest.raises(
        RuntimeError,
        match=f"expected one Executor source-policy anchor, found {copies}",
    ):
        module.patch_tree(tmp_path)

    assert {
        relative: (tmp_path / relative).read_text(encoding="utf-8")
        for relative in originals
    } == originals


def test_executor_policy_replacements_have_fail_closed_typescript_ast() -> None:
    """AST structure, not text fragments, must prove every critical early guard."""
    module = _load_patch_module()
    sources = {
        "constants.ts": _patch_replacement(
            module, 'import updater from "electron-updater"'
        ),
        "supervision.ts": _patch_replacement(module, "const ensureSupervisedConnection")
        + "  return null;\n};\n",
        "fake-update.ts": _patch_replacement(module, "const applyFakeUpdateFromEnv")
        + "};\n",
        "prompt-install.ts": _patch_replacement(module, "const promptInstallUpdate")
        + "};\n",
        "setup-updater.ts": _patch_replacement(module, "const setupAutoUpdater")
        + "};\n",
        "run-update.ts": _patch_replacement(module, "const runUpdateCheck")
        + "  }\n};\n",
        "service-install.ts": _patch_replacement(
            module,
            "export const installSupervisedService",
        )
        + "  }\n};\n",
        "resolve-tags.ts": _patch_replacement(module, "export const resolveDistTags")
        + "  return {};\n};\n",
        "rotate-token.ts": _patch_replacement(
            module,
            'ipcMain.handle("executor:server:rotate-token"',
        ),
        "reset-state.ts": _patch_replacement(
            module,
            'ipcMain.handle("executor:state:reset"',
        ),
        "fatal-reset-buttons.ts": "const options = {\n"
        + _patch_replacement(module, 'buttons: ["Quit", "Reset data and retry…"]')
        + "};\n",
        "fatal-reset-retry.ts": "async function fatalRecovery() {\n"
        + _patch_replacement(module, "const retryAfterReset = response === 1")
        + "}\n",
        "menu.ts": "const menu = [\n"
        + _patch_replacement(module, 'label: "Check for Updates…"')
        + "];\n",
    }

    facts = _typescript_ast_facts(sources)
    assert all(value["diagnostics"] == [] for value in facts.values())  # type: ignore[index,union-attr]

    constants = facts["constants.ts"]
    assert constants["variables"] == {  # type: ignore[index]
        "NIX_MANAGED": "true",
        "NIX_MANAGED_MESSAGE": '"Updates are managed by Nix."',
    }
    functions = {
        name: facts[name]["functionStatements"]  # type: ignore[index]
        for name in (
            "supervision.ts",
            "fake-update.ts",
            "prompt-install.ts",
            "setup-updater.ts",
            "run-update.ts",
            "service-install.ts",
            "resolve-tags.ts",
        )
    }
    assert functions["supervision.ts"]["ensureSupervisedConnection"][0] == (
        "if (NIX_MANAGED) return null;"
    )
    assert functions["fake-update.ts"]["applyFakeUpdateFromEnv"][0] == (
        "if (NIX_MANAGED) return;"
    )
    assert functions["prompt-install.ts"]["promptInstallUpdate"][0] == (
        "if (NIX_MANAGED) return;"
    )
    assert functions["setup-updater.ts"]["setupAutoUpdater"][:3] == [
        "autoUpdater.autoDownload = false;",
        "autoUpdater.autoInstallOnAppQuit = false;",
        "if (NIX_MANAGED) return;",
    ]
    assert functions["run-update.ts"]["runUpdateCheck"][0].startswith(
        "if (NIX_MANAGED)"
    )
    assert (
        "throw new Error"
        in functions["service-install.ts"]["installSupervisedService"][0]
    )
    assert functions["resolve-tags.ts"]["resolveDistTags"][0] == (
        "if (NIX_MANAGED) return {};"
    )
    managed_state_handlers = {
        "rotate-token.ts": (
            "executor:server:rotate-token",
            "Nix-managed Executor cannot rotate a supervised daemon token from the app.",
            ("rotateServerToken();", "restartSupervisedService();"),
        ),
        "reset-state.ts": (
            "executor:state:reset",
            "Nix-managed Executor cannot reset data from the app.",
            ("confirmResetState()", "resetExecutorState()"),
        ),
    }
    for source_name, (
        channel,
        refusal,
        later_mutations,
    ) in managed_state_handlers.items():
        statements = facts[source_name]["callHandlers"][channel]  # type: ignore[index]
        first_statement = " ".join(statements[0].split())
        expected_guard = (
            "if (NIX_MANAGED && connection?.supervisedDaemon) {"
            if source_name == "rotate-token.ts"
            else "if (NIX_MANAGED) {"
        )
        assert first_statement.startswith(expected_guard)
        assert f'throw new Error("{refusal}");' in first_statement
        for mutation in later_mutations:
            mutation_indexes = [
                index
                for index, statement in enumerate(statements)
                if mutation in statement
            ]
            assert mutation_indexes
            assert mutation_indexes[0] > 0
    assert {
        "buttons": 'NIX_MANAGED ? ["Quit"] : ["Quit", "Reset data and retry…"]'
    } in facts["fatal-reset-buttons.ts"]["objects"]  # type: ignore[index,operator]
    assert facts["fatal-reset-retry.ts"]["variables"]["retryAfterReset"] == (  # type: ignore[index]
        "!NIX_MANAGED && response === 1 && (await confirmResetState())"
    )
    assert {
        "enabled": "false",
        "label": '"Updates managed by Nix"',
    } in facts["menu.ts"]["objects"]  # type: ignore[index,operator]


def test_executor_cli_service_mutations_fail_before_backend_selection(
    tmp_path: Path,
) -> None:
    """The exact pinned CLI excerpts must fail before selecting a backend."""
    module = _load_patch_module()
    non_cli_patches = [
        patch for patch in module._PATCHES if patch.path != module._CLI_SOURCE
    ]
    _write_patch_fixture(tmp_path, non_cli_patches)
    cli_source = tmp_path / module._CLI_SOURCE
    cli_source.parent.mkdir(parents=True, exist_ok=True)
    cli_source.write_text(_PINNED_CLI_SERVICE_SOURCE, encoding="utf-8")

    module.patch_tree(tmp_path)

    facts = _typescript_ast_facts({
        "pinned-cli.ts": cli_source.read_text(encoding="utf-8")
    })["pinned-cli.ts"]
    assert facts["diagnostics"] == []  # type: ignore[index]
    assert facts["variables"]["NIX_MANAGED"] == "true"  # type: ignore[index]

    commands = {
        "install": "installService",
        "uninstall": "serviceUninstallCommand",
        "restart": "serviceRestartCommand",
    }
    for operation, command_name in commands.items():
        generators = facts["effectGenerators"][command_name]  # type: ignore[index]
        assert len(generators) == 1
        statements = generators[0]
        first_statement = " ".join(statements[0].split())
        assert first_statement.startswith("if (NIX_MANAGED) {")
        assert "return yield* Effect.fail(" in first_statement
        assert (
            f'"Nix-managed Executor cannot {operation} a mutable background service."'
            in first_statement
        )
        backend_indexes = [
            index
            for index, statement in enumerate(statements)
            if "getServiceBackend()" in statement
        ]
        assert backend_indexes
        assert backend_indexes[0] > 0
