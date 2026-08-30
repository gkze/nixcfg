"""Exact-source updater for the intentionally gated Paseo desktop package."""

import re
import urllib.parse
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, ClassVar, cast

from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import (
    HashCollection,
    HashEntry,
    HashType,
    SourceEntry,
    SourceHashes,
)
from lib.update import nix as update_nix
from lib.update.artifacts import GeneratedArtifact
from lib.update.events import (
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_str,
    require_value,
)
from lib.update.net import fetch_github_api, fetch_json, fetch_url, github_raw_url
from lib.update.nix import _build_fetch_from_github_call, _build_fetch_from_github_expr
from lib.update.nix_expr import compact_nix_expr, identifier_attr_path
from lib.update.paths import updater_dir_for
from lib.update.updaters import (
    GitHubReleaseUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import metadata_as_mapping

if TYPE_CHECKING:
    import aiohttp
    from nix_manipulator.expressions.inherit import Inherit

    from lib.update.config import UpdateConfig
    from lib.update.events import EventStream

_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")

_SHERPA_NATIVE_DEPENDENCIES = {
    "eigen": {
        "file": "eigen-3.4.1.tar.gz",
        "url": "https://gitlab.com/libeigen/eigen/-/archive/3.4.1/eigen-3.4.1.tar.gz",
    },
    "espeakNg": {
        "file": "espeak-ng-f6fed6c58b5e0998b8e68c6610125e2d07d595a7.zip",
        "url": (
            "https://github.com/csukuangfj/espeak-ng/archive/"
            "f6fed6c58b5e0998b8e68c6610125e2d07d595a7.zip"
        ),
    },
    "hclustCpp": {
        "file": "hclust-cpp-2026-02-25.tar.gz",
        "url": (
            "https://github.com/csukuangfj/hclust-cpp/archive/refs/tags/"
            "2026-02-25.tar.gz"
        ),
    },
    "json": {
        "file": "json-3.12.0.tar.gz",
        "url": ("https://github.com/nlohmann/json/archive/refs/tags/v3.12.0.tar.gz"),
    },
    "kaldiDecoder": {
        "file": "kaldi-decoder-0.2.11.tar.gz",
        "url": (
            "https://github.com/k2-fsa/kaldi-decoder/archive/refs/tags/v0.2.11.tar.gz"
        ),
    },
    "kaldiNativeFbank": {
        "file": "kaldi-native-fbank-1.22.3.tar.gz",
        "url": (
            "https://github.com/csukuangfj/kaldi-native-fbank/archive/refs/tags/"
            "v1.22.3.tar.gz"
        ),
    },
    "kaldifst": {
        "file": "kaldifst-1.7.17.tar.gz",
        "url": ("https://github.com/k2-fsa/kaldifst/archive/refs/tags/v1.7.17.tar.gz"),
    },
    "kissfft": {
        "file": "kissfft-febd4caeed32e33ad8b2e0bb5ea77542c40f18ec.zip",
        "url": (
            "https://github.com/mborgerding/kissfft/archive/"
            "febd4caeed32e33ad8b2e0bb5ea77542c40f18ec.zip"
        ),
    },
    "openfst": {
        "file": "openfst-sherpa-onnx-2024-06-19.tar.gz",
        "url": (
            "https://github.com/csukuangfj/openfst/archive/refs/tags/"
            "sherpa-onnx-2024-06-19.tar.gz"
        ),
    },
    "piperPhonemize": {
        "file": "piper-phonemize-78a788e0b719013401572d70fef372e77bff8e43.zip",
        "url": (
            "https://github.com/csukuangfj/piper-phonemize/archive/"
            "78a788e0b719013401572d70fef372e77bff8e43.zip"
        ),
    },
    "simpleSentencepiece": {
        "file": "simple-sentencepiece-0.7.tar.gz",
        "url": (
            "https://github.com/pkufool/simple-sentencepiece/archive/refs/tags/"
            "v0.7.tar.gz"
        ),
    },
}

_ONNX_NATIVE_DEPENDENCIES: dict[str, dict[str, str]] = {
    "abseilCpp": {
        "owner": "abseil",
        "repo": "abseil-cpp",
        "tag": "20240722.2",
        "version": "20240722.2",
    },
    "dlpack": {
        "owner": "dmlc",
        "repo": "dlpack",
        "rev": "5c210da409e7f1e51ddf445134a4376fdbd70d7d",
        "commit": "5c210da409e7f1e51ddf445134a4376fdbd70d7d",
    },
    "flatbuffers": {
        "owner": "google",
        "repo": "flatbuffers",
        "rev": "v23.5.26",
        "version": "23.5.26",
    },
    "mp11": {
        "owner": "boostorg",
        "repo": "mp11",
        "tag": "boost-1.82.0",
        "version": "boost-1.82.0",
    },
    "onnx": {
        "owner": "onnx",
        "repo": "onnx",
        "tag": "v1.18.0",
        "version": "v1.18.0",
    },
    "protobuf": {
        "owner": "protocolbuffers",
        "repo": "protobuf",
        "tag": "v32.1",
        "version": "32.1",
        "nixpkgsAttribute": "protobuf_32",
    },
    "re2": {
        "owner": "google",
        "repo": "re2",
        "rev": "2024-07-02",
        "version": "2024-07-02",
    },
    "safeint": {
        "owner": "dcleblanc",
        "repo": "safeint",
        "tag": "3.0.28",
        "version": "3.0.28",
    },
}

_ONNX_NATIVE_PATCHES: tuple[dict[str, object], ...] = (
    {
        "url": (
            "https://github.com/onnx/onnx/commit/"
            "595a069aaac07586f111681245bc808ee63551f8.patch"
        ),
        "includes": ["onnx/defs/schema.h"],
        "target": "onnx",
    },
    {
        "url": (
            "https://github.com/onnx/onnx/commit/"
            "6769c41ad64ebca0358da8c7211d2c6d0e627b2b.patch"
        ),
        "target": "onnx",
    },
    {
        "url": (
            "https://github.com/microsoft/onnxruntime/commit/"
            "d6e712c5b7b6260a61e54d1fe40107cf5366ee77.patch"
        ),
        "target": "onnxruntime",
    },
    {
        "url": (
            "https://github.com/microsoft/onnxruntime/commit/"
            "8ebd0bf1cf02414584d15d7244b07fa97d65ba02.patch"
        ),
        "target": "onnxruntime",
    },
)


@dataclass(frozen=True, slots=True)
class _HashRequest:
    hash_type: HashType
    url: str
    expr: str
    error: str


def _require_object(value: object, *, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = f"Paseo {context} is not a JSON object"
        raise TypeError(msg)
    return cast("dict[str, object]", value)


def _require_string(mapping: dict[str, object], key: str, *, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        msg = f"Paseo {context} is missing {key}"
        raise TypeError(msg)
    return value


def _require_exact(actual: str, expected: str, *, context: str) -> str:
    if actual != expected:
        msg = f"Paseo {context} must be {expected!r}, got {actual!r}"
        raise RuntimeError(msg)
    return actual


def _dependency(mapping: dict[str, object], name: str, *, context: str) -> str:
    dependencies = _require_object(mapping.get("dependencies"), context=context)
    return _require_string(dependencies, name, context=context)


def _locked_version(lock: dict[str, object], package: str) -> str:
    return _locked_path_version(
        lock,
        f"node_modules/{package}",
        context=f"locked {package}",
    )


def _locked_path_version(
    lock: dict[str, object],
    package_path: str,
    *,
    context: str,
) -> str:
    packages = _require_object(lock.get("packages"), context="lock packages")
    entry = _require_object(
        packages.get(package_path),
        context=context,
    )
    return _require_string(entry, "version", context=context)


@register_updater
class PaseoUpdater(GitHubReleaseUpdater):
    """Pin one audited Paseo tree and its native source-build inputs."""

    name = "paseo"
    GITHUB_OWNER = "getpaseo"
    GITHUB_REPO = "paseo"
    DARWIN_PLATFORM: ClassVar[str] = "aarch64-darwin"
    supported_platforms = (DARWIN_PLATFORM,)
    generated_artifact_files = ("native-lock.json",)

    VERSION = "0.6.1"
    COMMIT = "20d7efc46a316f5a274b9943a5c43b0322269825"
    ELECTRON_VERSION = "41.2.0"
    APP_ID = "sh.paseo.desktop"
    SHERPA_VERSION = "1.12.28"
    SHERPA_COMMIT = "86d3d00e28c22c102fb7d01c7b62fdc4e7a69f1b"
    ONNXRUNTIME_VERSION = "1.23.2"
    ONNXRUNTIME_COMMIT = "a83fc4d58cb48eb68890dd689f94f28288cf2278"
    NODE_ADDON_API_VERSION = "8.3.0"
    NPM_FETCHER_VERSION = 2
    ESBUILD_VERSION = "0.25.12"
    CLAUDE_AGENT_SDK_VERSION = "0.3.220"
    APP_BUILDER_LIB_VERSION = "26.8.1"
    APP_BUILDER_LIB_BACKPORT_COMMIT = "2ff9190aadc791503a6e62cdcbfa975448bc49bf"
    CLAUDE_AGENT_SDK_URL = (
        "https://registry.npmjs.org/@anthropic-ai/claude-agent-sdk/-/"
        f"claude-agent-sdk-{CLAUDE_AGENT_SDK_VERSION}.tgz"
    )
    CLAUDE_AGENT_SDK_INTEGRITY = (
        "sha512-glc7SdwPkOkLw8oxwLo9PKTdLJGqW/PIR4urWXFoRtX9YllwozsEVc5Tc1+EvLSkfrsx"
        "PJqQWqOgpjUOQXf1oA=="
    )
    CLAUDE_AGENT_SDK_DARWIN_ARM64_URL = (
        "https://registry.npmjs.org/@anthropic-ai/"
        "claude-agent-sdk-darwin-arm64/-/"
        f"claude-agent-sdk-darwin-arm64-{CLAUDE_AGENT_SDK_VERSION}.tgz"
    )
    CLAUDE_AGENT_SDK_DARWIN_ARM64_INTEGRITY = (
        "sha512-7VxlbEosK7DODiOnsjoVd0DSJzbnaPrM2jelMHI0y8zx1UnLS3WC6EFUXbvy74F2s"
        "XqEznh2tzn7EKWInaRN6Q=="
    )
    CLAUDE_PROVIDER_SOURCE_DIGEST = (
        "0a5062a28d1a2e54017b62a3de46f15a4eadb37f5c6f2e9b15d93b99c85019e6"
    )

    @staticmethod
    async def _resolve_tag_commit(
        session: aiohttp.ClientSession,
        *,
        owner: str,
        repo: str,
        tag: str,
        config: UpdateConfig,
    ) -> str:
        tag_path = urllib.parse.quote(tag, safe="")
        payload = await fetch_github_api(
            session,
            f"repos/{owner}/{repo}/commits/{tag_path}",
            config=config,
        )
        commit = _require_string(
            _require_object(payload, context=f"{owner}/{repo} commit response"),
            "sha",
            context=f"{owner}/{repo} commit response",
        )
        if _COMMIT_PATTERN.fullmatch(commit) is None:
            msg = f"Paseo {owner}/{repo} tag {tag} is not an immutable commit"
            raise RuntimeError(msg)
        return commit

    @staticmethod
    def _archive_url(owner: str, repo: str, commit: str) -> str:
        return f"https://github.com/{owner}/{repo}/archive/{commit}.tar.gz"

    @classmethod
    def _sherpa_wrapper_url(cls) -> str:
        return (
            "https://registry.npmjs.org/sherpa-onnx-node/-/"
            f"sherpa-onnx-node-{cls.SHERPA_VERSION}.tgz"
        )

    @classmethod
    def _node_addon_api_url(cls) -> str:
        return (
            "https://registry.npmjs.org/node-addon-api/-/"
            f"node-addon-api-{cls.NODE_ADDON_API_VERSION}.tgz"
        )

    @classmethod
    def _validate_claude_provider_source(cls, payload: bytes) -> None:
        _require_exact(
            sha256(payload).hexdigest(),
            cls.CLAUDE_PROVIDER_SOURCE_DIGEST,
            context="Claude provider source digest",
        )
        source = payload.decode("utf-8")
        anchors = {
            "async function resolveClaudeBinary(": 1,
            "this.resolveBinary = options.resolveBinary ?? "
            "(() => resolveClaudeBinary(this.runtimeSettings));": 1,
            "const claudeBinary = await this.resolveBinary();": 1,
            "pathToClaudeCodeExecutable: claudeBinary,": 1,
            "Claude binary not found. Install Claude Code": 1,
            'defaultBinary: "claude",': 4,
        }
        if any(source.count(anchor) != count for anchor, count in anchors.items()):
            msg = "Paseo Claude provider runtime seam drifted"
            raise RuntimeError(msg)

    @classmethod
    def _validate_manifests(
        cls,
        *,
        root_payload: object,
        desktop_payload: object,
        server_payload: object,
        lock_payload: object,
        sherpa_payload: object,
        sherpa_ort_cmake: str,
    ) -> None:
        root = _require_object(root_payload, context="root manifest")
        desktop = _require_object(desktop_payload, context="desktop manifest")
        server = _require_object(server_payload, context="server manifest")
        lock = _require_object(lock_payload, context="lock manifest")
        sherpa = _require_object(sherpa_payload, context="sherpa addon manifest")

        for label, manifest in (
            ("root", root),
            ("desktop", desktop),
            ("server", server),
        ):
            _require_exact(
                _require_string(manifest, "version", context=f"{label} manifest"),
                cls.VERSION,
                context=f"{label} version",
            )

        dev_dependencies = _require_object(
            desktop.get("devDependencies"),
            context="desktop devDependencies",
        )
        _require_exact(
            _require_string(
                dev_dependencies, "electron", context="desktop devDependencies"
            ),
            cls.ELECTRON_VERSION,
            context="Electron dependency",
        )
        _require_exact(
            _dependency(server, "sherpa-onnx-node", context="server dependencies"),
            cls.SHERPA_VERSION,
            context="sherpa wrapper dependency",
        )
        _require_exact(
            _dependency(server, "esbuild", context="server dependencies"),
            f"^{cls.ESBUILD_VERSION}",
            context="esbuild dependency",
        )
        _require_exact(
            _dependency(
                server,
                "@anthropic-ai/claude-agent-sdk",
                context="server dependencies",
            ),
            cls.CLAUDE_AGENT_SDK_VERSION,
            context="Claude Agent SDK dependency",
        )
        for package, expected in (
            ("electron", cls.ELECTRON_VERSION),
            ("sherpa-onnx-node", cls.SHERPA_VERSION),
            ("sherpa-onnx-darwin-arm64", cls.SHERPA_VERSION),
        ):
            _require_exact(
                _locked_version(lock, package),
                expected,
                context=f"locked {package}",
            )

        lock_packages = _require_object(lock.get("packages"), context="lock packages")
        for package_path, context in (
            ("packages/server/node_modules/esbuild", "locked server esbuild"),
            (
                "packages/server/node_modules/@esbuild/darwin-arm64",
                "locked server esbuild darwin-arm64",
            ),
        ):
            _require_exact(
                _locked_path_version(lock, package_path, context=context),
                cls.ESBUILD_VERSION,
                context=context,
            )
        sdk_lock = _require_object(
            lock_packages.get(
                "packages/server/node_modules/@anthropic-ai/claude-agent-sdk"
            ),
            context="locked Claude Agent SDK",
        )
        _require_exact(
            _require_string(sdk_lock, "version", context="locked Claude Agent SDK"),
            cls.CLAUDE_AGENT_SDK_VERSION,
            context="locked Claude Agent SDK version",
        )
        _require_exact(
            _require_string(sdk_lock, "resolved", context="locked Claude Agent SDK"),
            cls.CLAUDE_AGENT_SDK_URL,
            context="locked Claude Agent SDK URL",
        )
        _require_exact(
            _require_string(sdk_lock, "integrity", context="locked Claude Agent SDK"),
            cls.CLAUDE_AGENT_SDK_INTEGRITY,
            context="locked Claude Agent SDK integrity",
        )
        sdk_platform_lock = _require_object(
            lock_packages.get(
                "packages/server/node_modules/@anthropic-ai/claude-agent-sdk/"
                "node_modules/@anthropic-ai/claude-agent-sdk-darwin-arm64"
            ),
            context="locked Claude Agent SDK darwin-arm64",
        )
        _require_exact(
            _require_string(
                sdk_platform_lock,
                "version",
                context="locked Claude Agent SDK darwin-arm64",
            ),
            cls.CLAUDE_AGENT_SDK_VERSION,
            context="locked Claude Agent SDK darwin-arm64 version",
        )
        _require_exact(
            _require_string(
                sdk_platform_lock,
                "resolved",
                context="locked Claude Agent SDK darwin-arm64",
            ),
            cls.CLAUDE_AGENT_SDK_DARWIN_ARM64_URL,
            context="locked Claude Agent SDK darwin-arm64 URL",
        )
        _require_exact(
            _require_string(
                sdk_platform_lock,
                "integrity",
                context="locked Claude Agent SDK darwin-arm64",
            ),
            cls.CLAUDE_AGENT_SDK_DARWIN_ARM64_INTEGRITY,
            context="locked Claude Agent SDK darwin-arm64 integrity",
        )

        _require_exact(
            _dependency(sherpa, "node-addon-api", context="sherpa addon dependencies"),
            f"^{cls.NODE_ADDON_API_VERSION}",
            context="sherpa node-addon-api dependency",
        )
        ort_marker = (
            f"/v{cls.ONNXRUNTIME_VERSION}/"
            f"onnxruntime-osx-arm64-{cls.ONNXRUNTIME_VERSION}.tgz"
        )
        if ort_marker not in sherpa_ort_cmake:
            msg = f"Paseo sherpa source does not select ONNX Runtime {cls.ONNXRUNTIME_VERSION}"
            raise RuntimeError(msg)

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve and validate the one release supported by this foundation."""
        release = await self._fetch_latest_release_payload(session)
        tag = self._release_tag_from_payload(release)
        version = self._normalize_release_version(tag)
        _require_exact(version, self.VERSION, context="release version")

        commit = await self._resolve_tag_commit(
            session,
            owner=self.GITHUB_OWNER,
            repo=self.GITHUB_REPO,
            tag=tag,
            config=self.config,
        )
        _require_exact(commit, self.COMMIT, context="release commit")
        sherpa_commit = await self._resolve_tag_commit(
            session,
            owner="k2-fsa",
            repo="sherpa-onnx",
            tag=f"v{self.SHERPA_VERSION}",
            config=self.config,
        )
        _require_exact(sherpa_commit, self.SHERPA_COMMIT, context="sherpa commit")
        onnxruntime_commit = await self._resolve_tag_commit(
            session,
            owner="microsoft",
            repo="onnxruntime",
            tag=f"v{self.ONNXRUNTIME_VERSION}",
            config=self.config,
        )
        _require_exact(
            onnxruntime_commit,
            self.ONNXRUNTIME_COMMIT,
            context="ONNX Runtime commit",
        )

        def paseo_raw(path: str) -> str:
            return github_raw_url(
                self.GITHUB_OWNER,
                self.GITHUB_REPO,
                commit,
                path,
            )

        def sherpa_raw(path: str) -> str:
            return github_raw_url(
                "k2-fsa",
                "sherpa-onnx",
                sherpa_commit,
                path,
            )

        root_payload = await fetch_json(
            session, paseo_raw("package.json"), config=self.config
        )
        desktop_payload = await fetch_json(
            session,
            paseo_raw("packages/desktop/package.json"),
            config=self.config,
        )
        server_payload = await fetch_json(
            session,
            paseo_raw("packages/server/package.json"),
            config=self.config,
        )
        lock_payload = await fetch_json(
            session,
            paseo_raw("package-lock.json"),
            config=self.config,
        )
        sherpa_payload = await fetch_json(
            session,
            sherpa_raw("scripts/node-addon-api/package.json"),
            config=self.config,
        )
        sherpa_ort_cmake = (
            await fetch_url(
                session,
                sherpa_raw("cmake/onnxruntime-osx-arm64.cmake"),
                config=self.config,
            )
        ).decode("utf-8")
        claude_provider_source = await fetch_url(
            session,
            paseo_raw("packages/server/src/server/agent/providers/claude/agent.ts"),
            config=self.config,
        )
        self._validate_claude_provider_source(claude_provider_source)
        self._validate_manifests(
            root_payload=root_payload,
            desktop_payload=desktop_payload,
            server_payload=server_payload,
            lock_payload=lock_payload,
            sherpa_payload=sherpa_payload,
            sherpa_ort_cmake=sherpa_ort_cmake,
        )

        return VersionInfo(
            version=version,
            metadata={
                "commit": commit,
                "electronVersion": self.ELECTRON_VERSION,
                "nodeAddonApiUrl": self._node_addon_api_url(),
                "onnxruntimeUrl": self._archive_url(
                    "microsoft",
                    "onnxruntime",
                    onnxruntime_commit,
                ),
                "paseoUrl": self._archive_url(
                    self.GITHUB_OWNER, self.GITHUB_REPO, commit
                ),
                "sherpaOnnxNodeUrl": self._sherpa_wrapper_url(),
                "sherpaOnnxUrl": self._archive_url(
                    "k2-fsa",
                    "sherpa-onnx",
                    sherpa_commit,
                ),
                "tag": tag,
            },
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Recompute closure hashes before considering this source current."""
        _ = (context, info)
        return False

    @staticmethod
    def _fetchurl_expr(url: str) -> str:
        expression = FunctionCall(
            name=identifier_attr_path("pkgs", "fetchurl"),
            argument=AttributeSet(
                values=[
                    Binding(name="url", value=StringPrimitive(value=url)),
                    Binding(
                        name="hash",
                        value=identifier_attr_path("pkgs", "lib", "fakeHash"),
                    ),
                ]
            ),
        )
        return compact_nix_expr(expression.rebuild())

    @staticmethod
    def _fetchpatch_expr(url: str, *, includes: list[str] | None = None) -> str:
        values: list[Binding | Inherit] = [
            Binding(name="url", value=StringPrimitive(value=url)),
            Binding(
                name="hash",
                value=identifier_attr_path("pkgs", "lib", "fakeHash"),
            ),
        ]
        if includes is not None:
            values.append(
                Binding(
                    name="includes",
                    value=NixList(
                        value=[StringPrimitive(value=value) for value in includes]
                    ),
                )
            )
        expression = FunctionCall(
            name=identifier_attr_path("pkgs", "fetchpatch"),
            argument=AttributeSet(values=values),
        )
        return compact_nix_expr(expression.rebuild())

    @classmethod
    def _native_lock_payload(cls, hashes: dict[str, str]) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "paseo": {
                "version": cls.VERSION,
                "commit": cls.COMMIT,
                "electronVersion": cls.ELECTRON_VERSION,
                "nodeAddonApiVersion": cls.NODE_ADDON_API_VERSION,
                "npmFetcherVersion": cls.NPM_FETCHER_VERSION,
                "esbuildVersion": cls.ESBUILD_VERSION,
                "claudeAgentSdkVersion": cls.CLAUDE_AGENT_SDK_VERSION,
                "appBuilderLibVersion": cls.APP_BUILDER_LIB_VERSION,
                "appBuilderLibBackportCommit": cls.APP_BUILDER_LIB_BACKPORT_COMMIT,
            },
            "sherpaOnnx": {
                "version": cls.SHERPA_VERSION,
                "commit": cls.SHERPA_COMMIT,
                "onnxruntime": {
                    "version": cls.ONNXRUNTIME_VERSION,
                    "source": "paseo-exact-source-build",
                },
                "npmAddonBuild": {
                    "workflow": ".github/workflows/npm-addon-macos.yaml",
                    "portaudio": False,
                    "websocket": False,
                    "tts": True,
                    "speakerDiarization": True,
                },
                "dependencies": {
                    name: {**dependency, "hash": hashes[f"sherpa:{name}"]}
                    for name, dependency in _SHERPA_NATIVE_DEPENDENCIES.items()
                },
                "sourceClosureComplete": True,
            },
            "onnxruntime": {
                "version": cls.ONNXRUNTIME_VERSION,
                "commit": cls.ONNXRUNTIME_COMMIT,
                "nixpkgsRecipe": {
                    "commit": "e1e423f183cde97926ac113d8a4de5a5042a7264",
                    "path": "pkgs/by-name/on/onnxruntime/package.nix",
                },
                "dependencies": {
                    name: {**dependency, "hash": hashes[f"onnx:{name}"]}
                    for name, dependency in _ONNX_NATIVE_DEPENDENCIES.items()
                },
                "patches": [
                    {**patch, "hash": hashes[f"patch:{index}"]}
                    for index, patch in enumerate(_ONNX_NATIVE_PATCHES)
                ],
                "sourceClosureComplete": True,
            },
        }

    @classmethod
    def _native_hash_requests(cls) -> tuple[tuple[str, str], ...]:
        requests = [
            (f"sherpa:{name}", cls._fetchurl_expr(dependency["url"]))
            for name, dependency in _SHERPA_NATIVE_DEPENDENCIES.items()
        ]
        requests.extend(
            (
                f"onnx:{name}",
                _build_fetch_from_github_expr(
                    dependency["owner"],
                    dependency["repo"],
                    rev=dependency.get("rev", dependency.get("tag", "")),
                    fetch_submodules=False,
                ),
            )
            for name, dependency in _ONNX_NATIVE_DEPENDENCIES.items()
        )
        requests.extend(
            (
                f"patch:{index}",
                cls._fetchpatch_expr(
                    cast("str", patch["url"]),
                    includes=cast("list[str] | None", patch.get("includes")),
                ),
            )
            for index, patch in enumerate(_ONNX_NATIVE_PATCHES)
        )
        return tuple(requests)

    @classmethod
    def _npm_deps_expr(cls, *, src_hash: str) -> str:
        expression = FunctionCall(
            name=identifier_attr_path("pkgs", "fetchNpmDeps"),
            argument=AttributeSet(
                values=[
                    Binding(
                        name="name",
                        value=StringPrimitive(
                            value=f"{cls.name}-{cls.VERSION}-npm-deps"
                        ),
                    ),
                    Binding(
                        name="src",
                        value=_build_fetch_from_github_call(
                            cls.GITHUB_OWNER,
                            cls.GITHUB_REPO,
                            rev=cls.COMMIT,
                            hash_value=src_hash,
                            fetch_submodules=False,
                        ),
                    ),
                    Binding(
                        name="hash",
                        value=identifier_attr_path("pkgs", "lib", "fakeHash"),
                    ),
                    Binding(
                        name="fetcherVersion",
                        value=Primitive(value=cls.NPM_FETCHER_VERSION),
                    ),
                ]
            ),
        )
        return compact_nix_expr(expression.rebuild())

    @classmethod
    def _required_metadata(cls, info: VersionInfo) -> dict[str, str]:
        metadata = metadata_as_mapping(info.metadata, context="Paseo release metadata")
        result = {
            key: _require_string(metadata, key, context="release metadata")
            for key in (
                "commit",
                "electronVersion",
                "nodeAddonApiUrl",
                "onnxruntimeUrl",
                "paseoUrl",
                "sherpaOnnxNodeUrl",
                "sherpaOnnxUrl",
                "tag",
            )
        }
        _require_exact(info.version, cls.VERSION, context="release version")
        _require_exact(result["commit"], cls.COMMIT, context="release commit")
        _require_exact(
            result["electronVersion"],
            cls.ELECTRON_VERSION,
            context="Electron version",
        )
        _require_exact(result["tag"], f"v{cls.VERSION}", context="release tag")
        expected_urls = {
            "nodeAddonApiUrl": cls._node_addon_api_url(),
            "onnxruntimeUrl": cls._archive_url(
                "microsoft",
                "onnxruntime",
                cls.ONNXRUNTIME_COMMIT,
            ),
            "paseoUrl": cls._archive_url(
                cls.GITHUB_OWNER,
                cls.GITHUB_REPO,
                cls.COMMIT,
            ),
            "sherpaOnnxNodeUrl": cls._sherpa_wrapper_url(),
            "sherpaOnnxUrl": cls._archive_url(
                "k2-fsa",
                "sherpa-onnx",
                cls.SHERPA_COMMIT,
            ),
        }
        for key, expected in expected_urls.items():
            _require_exact(result[key], expected, context=key)
        return result

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Hash every exact source plus the npm lock closure, without building."""
        _ = (session, context)
        metadata = self._required_metadata(info)
        requests = [
            _HashRequest(
                hash_type="srcHash",
                url=metadata[url_key],
                expr=_build_fetch_from_github_expr(
                    owner,
                    repo,
                    rev=commit,
                    fetch_submodules=fetch_submodules,
                ),
                error=f"Missing {repo} srcHash output",
            )
            for owner, repo, commit, url_key, fetch_submodules in (
                (
                    self.GITHUB_OWNER,
                    self.GITHUB_REPO,
                    self.COMMIT,
                    "paseoUrl",
                    False,
                ),
                (
                    "k2-fsa",
                    "sherpa-onnx",
                    self.SHERPA_COMMIT,
                    "sherpaOnnxUrl",
                    False,
                ),
                (
                    "microsoft",
                    "onnxruntime",
                    self.ONNXRUNTIME_COMMIT,
                    "onnxruntimeUrl",
                    True,
                ),
            )
        ]
        for key in ("nodeAddonApiUrl", "sherpaOnnxNodeUrl"):
            url = metadata[key]
            requests.append(
                _HashRequest(
                    hash_type="sha256",
                    url=url,
                    expr=self._fetchurl_expr(url),
                    error=f"Missing URL hash output for {url}",
                )
            )

        entries: list[HashEntry] = []
        source_hashes: dict[str, str] = {}
        for request in requests:
            drain = ValueDrain[str]()
            async for event in drain_value_events(
                update_nix.compute_fixed_output_hash(
                    self.name,
                    request.expr,
                    config=self.config,
                ),
                drain,
                parse=expect_str,
            ):
                yield event
            value = require_value(drain, request.error)
            entries.append(HashEntry.create(request.hash_type, value, url=request.url))
            source_hashes[request.url] = value

        paseo_url = metadata["paseoUrl"]
        npm_drain = ValueDrain[str]()
        async for event in drain_value_events(
            update_nix.compute_fixed_output_hash(
                self.name,
                self._npm_deps_expr(src_hash=source_hashes[paseo_url]),
                config=self.config,
            ),
            npm_drain,
            parse=expect_str,
        ):
            yield event
        npm_hash = require_value(npm_drain, "Missing Paseo npmDepsHash output")
        entries.append(HashEntry.create("npmDepsHash", npm_hash, url=paseo_url))

        native_hashes: dict[str, str] = {}
        for identity, expression in self._native_hash_requests():
            drain = ValueDrain[str]()
            async for event in drain_value_events(
                update_nix.compute_fixed_output_hash(
                    self.name,
                    expression,
                    config=self.config,
                ),
                drain,
                parse=expect_str,
            ):
                yield event
            native_hashes[identity] = require_value(
                drain,
                f"Missing native lock hash output for {identity}",
            )

        package_dir = updater_dir_for(self.name)
        if package_dir is None:
            msg = f"Package directory not found for {self.name}"
            raise RuntimeError(msg)
        yield UpdateEvent.artifact(
            self.name,
            GeneratedArtifact.json(
                package_dir / self.generated_artifact_files[0],
                self._native_lock_payload(native_hashes),
            ),
        )
        yield UpdateEvent.value(self.name, entries)

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist only the complete six-entry exact-source closure."""
        metadata = self._required_metadata(info)
        collection = HashCollection.from_value(hashes)
        entries = collection.entries
        if entries is None:
            msg = "Paseo updater requires structured hash entries"
            raise TypeError(msg)
        expected = {
            ("srcHash", metadata["paseoUrl"]),
            ("srcHash", metadata["sherpaOnnxUrl"]),
            ("srcHash", metadata["onnxruntimeUrl"]),
            ("sha256", metadata["nodeAddonApiUrl"]),
            ("sha256", metadata["sherpaOnnxNodeUrl"]),
            ("npmDepsHash", metadata["paseoUrl"]),
        }
        actual = {(entry.hash_type, entry.url) for entry in entries}
        if actual != expected or len(entries) != len(expected):
            msg = f"Paseo updater expected exact closure keys {expected}, got {actual}"
            raise RuntimeError(msg)
        return SourceEntry.model_validate({
            "version": info.version,
            "commit": metadata["commit"],
            "electronVersion": metadata["electronVersion"],
            "hashes": entries,
            "urls": {
                "nodeAddonApi": metadata["nodeAddonApiUrl"],
                "onnxruntime": metadata["onnxruntimeUrl"],
                "paseo": metadata["paseoUrl"],
                "sherpaOnnx": metadata["sherpaOnnxUrl"],
                "sherpaOnnxNode": metadata["sherpaOnnxNodeUrl"],
            },
        })
