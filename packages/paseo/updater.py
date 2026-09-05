"""Exact-source updater for the intentionally gated Paseo desktop package."""

import asyncio
import re
import urllib.parse
from dataclasses import dataclass
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
from lib.update.derivation_validation import DerivationValidation
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
from lib.update.npm_semver import require_npm_version_matches_spec
from lib.update.paths import updater_dir_for
from lib.update.updaters import (
    GitHubReleaseUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import metadata_as_mapping
from packages.paseo.patch_nix_managed import validate_claude_provider_source

if TYPE_CHECKING:
    import aiohttp
    from nix_manipulator.expressions.inherit import Inherit

    from lib.update.config import UpdateConfig
    from lib.update.events import EventStream

_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_EXACT_VERSION_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_SHA512_INTEGRITY_PATTERN = re.compile(r"^sha512-[A-Za-z0-9+/]{86}==$")

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
        "commit": "5c210da409e7f1e51ddf445134a4376fdbd70d7d",
    },
    "flatbuffers": {
        "owner": "google",
        "repo": "flatbuffers",
        "tag": "v23.5.26",
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
    "re2": {
        "owner": "google",
        "repo": "re2",
        "tag": "2024-07-02",
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


@dataclass(frozen=True, slots=True)
class _ManifestContract:
    app_builder_lib_version: str
    claude_agent_sdk_version: str
    electron_version: str
    esbuild_version: str
    node_addon_api_version: str
    onnxruntime_version: str
    sherpa_version: str


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


def _require_exact_version(value: str, *, context: str) -> str:
    if _EXACT_VERSION_PATTERN.fullmatch(value) is None:
        msg = f"Paseo {context} must be an exact semantic version, got {value!r}"
        raise RuntimeError(msg)
    return value


def _require_commit(value: str, *, context: str) -> str:
    if _COMMIT_PATTERN.fullmatch(value) is None:
        msg = f"Paseo {context} must be an immutable commit, got {value!r}"
        raise RuntimeError(msg)
    return value


def _require_sha512_integrity(value: str, *, context: str) -> str:
    if _SHA512_INTEGRITY_PATTERN.fullmatch(value) is None:
        msg = f"Paseo {context} must be a sha512 SRI integrity, got {value!r}"
        raise RuntimeError(msg)
    return value


def _dependency(mapping: dict[str, object], name: str, *, context: str) -> str:
    dependencies = _require_object(mapping.get("dependencies"), context=context)
    return _require_string(dependencies, name, context=context)


def _candidate_sherpa_version(server_payload: object) -> str:
    """Derive the sherpa release selected by an immutable Paseo manifest."""
    server = _require_object(server_payload, context="server manifest")
    return _require_exact_version(
        _dependency(server, "sherpa-onnx-node", context="server dependencies"),
        context="sherpa wrapper dependency",
    )


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


def _first_locked_entry(
    lock: dict[str, object],
    package_paths: tuple[str, ...],
    *,
    context: str,
) -> tuple[str, dict[str, object]]:
    packages = _require_object(lock.get("packages"), context="lock packages")
    for package_path in package_paths:
        entry = packages.get(package_path)
        if entry is not None:
            return package_path, _require_object(entry, context=context)
    msg = f"Paseo {context} is missing from the lock manifest"
    raise RuntimeError(msg)


@register_updater
class PaseoUpdater(GitHubReleaseUpdater):
    """Resolve compatible Paseo releases against an audited native foundation."""

    name = "paseo"
    aggregate_into = ("electron-runtimes",)
    GITHUB_OWNER = "getpaseo"
    GITHUB_REPO = "paseo"
    DARWIN_PLATFORM: ClassVar[str] = "aarch64-darwin"
    supported_platforms = (DARWIN_PLATFORM,)
    generated_artifact_files = ("native-lock.json",)

    compatibility_pin_rationale = (
        "Paseo's package-local app-builder-lib patch and backport, fetchNpmDeps "
        "schema, and static Sherpa/ONNX source inventories are implementation "
        "constraints independent of each release manifest."
    )
    compatibility_pins: ClassVar[dict[str, str]] = {
        "appBuilderLibBackportCommit": "2ff9190aadc791503a6e62cdcbfa975448bc49bf",
        "appBuilderLibVersion": "26.8.1",
        "nodeAddonApiVersion": "8.3.0",
        "npmFetcherVersion": "2",
        "onnxruntimeVersion": "1.23.2",
        "sherpaVersion": "1.12.28",
    }
    derivation_validations = (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=(DARWIN_PLATFORM,),
            mode="build",
        ),
    )

    @classmethod
    def _app_builder_lib_backport_commit(cls) -> str:
        return _require_commit(
            cls.get_compatibility_pin("appBuilderLibBackportCommit"),
            context="app-builder-lib backport commit",
        )

    @classmethod
    def _npm_fetcher_version(cls) -> int:
        value = cls.get_compatibility_pin("npmFetcherVersion")
        if not value.isdecimal() or int(value) <= 0:
            msg = "Paseo npmFetcherVersion compatibility pin must be a positive integer"
            raise RuntimeError(msg)
        return int(value)

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

    @classmethod
    async def _resolve_onnx_native_dependencies(
        cls,
        session: aiohttp.ClientSession,
        *,
        config: UpdateConfig,
    ) -> dict[str, dict[str, str]]:
        async def resolve(
            dependency: dict[str, str],
        ) -> dict[str, str]:
            commit = dependency.get("commit")
            if commit is None:
                commit = await cls._resolve_tag_commit(
                    session,
                    owner=dependency["owner"],
                    repo=dependency["repo"],
                    tag=dependency["tag"],
                    config=config,
                )
            else:
                _require_commit(commit, context=f"{dependency['repo']} commit")
            return {**dependency, "commit": commit}

        resolved = await asyncio.gather(
            *(resolve(dependency) for dependency in _ONNX_NATIVE_DEPENDENCIES.values())
        )
        return dict(zip(_ONNX_NATIVE_DEPENDENCIES, resolved, strict=True))

    @staticmethod
    def _archive_url(owner: str, repo: str, commit: str) -> str:
        return f"https://github.com/{owner}/{repo}/archive/{commit}.tar.gz"

    @staticmethod
    def _sherpa_wrapper_url(version: str) -> str:
        return (
            "https://registry.npmjs.org/sherpa-onnx-node/-/"
            f"sherpa-onnx-node-{version}.tgz"
        )

    @staticmethod
    def _node_addon_api_url(version: str) -> str:
        return (
            f"https://registry.npmjs.org/node-addon-api/-/node-addon-api-{version}.tgz"
        )

    @staticmethod
    def _npm_url(package: str, version: str) -> str:
        basename = package.rsplit("/", maxsplit=1)[-1]
        return f"https://registry.npmjs.org/{package}/-/{basename}-{version}.tgz"

    @staticmethod
    def _validate_claude_provider_source(payload: bytes) -> None:
        try:
            validate_claude_provider_source(payload.decode("utf-8"))
        except RuntimeError as exc:
            msg = "Paseo Claude provider runtime seam drifted"
            raise RuntimeError(msg) from exc

    @classmethod
    def _validate_claude_sdk_lock(
        cls,
        *,
        server: dict[str, object],
        lock: dict[str, object],
    ) -> str:
        version = _require_exact_version(
            _dependency(
                server,
                "@anthropic-ai/claude-agent-sdk",
                context="server dependencies",
            ),
            context="Claude Agent SDK dependency",
        )
        sdk_path, sdk_lock = _first_locked_entry(
            lock,
            (
                "packages/server/node_modules/@anthropic-ai/claude-agent-sdk",
                "node_modules/@anthropic-ai/claude-agent-sdk",
            ),
            context="locked Claude Agent SDK",
        )
        _require_exact(
            _require_string(sdk_lock, "version", context="locked Claude Agent SDK"),
            version,
            context="locked Claude Agent SDK version",
        )
        _require_exact(
            _require_string(sdk_lock, "resolved", context="locked Claude Agent SDK"),
            cls._npm_url("@anthropic-ai/claude-agent-sdk", version),
            context="locked Claude Agent SDK URL",
        )
        _require_sha512_integrity(
            _require_string(sdk_lock, "integrity", context="locked Claude Agent SDK"),
            context="locked Claude Agent SDK integrity",
        )

        platform_package = "@anthropic-ai/claude-agent-sdk-darwin-arm64"
        sdk_optional_dependencies = _require_object(
            sdk_lock.get("optionalDependencies"),
            context="locked Claude Agent SDK optionalDependencies",
        )
        _require_exact(
            _require_string(
                sdk_optional_dependencies,
                platform_package,
                context="locked Claude Agent SDK optionalDependencies",
            ),
            version,
            context="locked Claude Agent SDK darwin-arm64 dependency",
        )
        platform_paths = [f"{sdk_path}/node_modules/{platform_package}"]
        if sdk_path.startswith("packages/server/"):
            platform_paths.append(f"packages/server/node_modules/{platform_package}")
        platform_paths.append(f"node_modules/{platform_package}")
        _platform_path, platform_lock = _first_locked_entry(
            lock,
            tuple(platform_paths),
            context="locked Claude Agent SDK darwin-arm64",
        )
        _require_exact(
            _require_string(
                platform_lock,
                "version",
                context="locked Claude Agent SDK darwin-arm64",
            ),
            version,
            context="locked Claude Agent SDK darwin-arm64 version",
        )
        _require_exact(
            _require_string(
                platform_lock,
                "resolved",
                context="locked Claude Agent SDK darwin-arm64",
            ),
            cls._npm_url(platform_package, version),
            context="locked Claude Agent SDK darwin-arm64 URL",
        )
        _require_sha512_integrity(
            _require_string(
                platform_lock,
                "integrity",
                context="locked Claude Agent SDK darwin-arm64",
            ),
            context="locked Claude Agent SDK darwin-arm64 integrity",
        )
        if platform_lock.get("optional") is not True:
            msg = "Paseo locked Claude Agent SDK darwin-arm64 must be optional"
            raise RuntimeError(msg)
        for key, expected in (("os", ["darwin"]), ("cpu", ["arm64"])):
            if platform_lock.get(key) != expected:
                msg = (
                    "Paseo locked Claude Agent SDK darwin-arm64 "
                    f"{key} must be {expected!r}"
                )
                raise RuntimeError(msg)
        return version

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
        release_version: str,
    ) -> _ManifestContract:
        release_version = _require_exact_version(
            release_version,
            context="release version",
        )
        root = _require_object(root_payload, context="root manifest")
        desktop = _require_object(desktop_payload, context="desktop manifest")
        server = _require_object(server_payload, context="server manifest")
        lock = _require_object(lock_payload, context="lock manifest")
        sherpa = _require_object(sherpa_payload, context="sherpa addon manifest")

        manifest_versions = (
            ("lock", _require_string(lock, "version", context="lock manifest")),
            (
                "lock root package",
                _locked_path_version(lock, "", context="locked root package"),
            ),
            ("root", _require_string(root, "version", context="root manifest")),
            (
                "desktop",
                _require_string(desktop, "version", context="desktop manifest"),
            ),
            ("server", _require_string(server, "version", context="server manifest")),
        )
        for label, manifest_version in manifest_versions:
            _require_exact(
                _require_exact_version(
                    manifest_version,
                    context=f"{label} version",
                ),
                release_version,
                context=f"{label} version",
            )

        dev_dependencies = _require_object(
            desktop.get("devDependencies"),
            context="desktop devDependencies",
        )
        electron_version = _require_exact_version(
            _require_string(
                dev_dependencies,
                "electron",
                context="desktop devDependencies",
            ),
            context="Electron dependency",
        )
        _require_exact(
            _locked_version(lock, "electron"),
            electron_version,
            context="locked electron",
        )

        electron_builder_version = _require_exact_version(
            _require_string(
                dev_dependencies,
                "electron-builder",
                context="desktop devDependencies",
            ),
            context="electron-builder dependency",
        )
        _require_exact(
            _locked_version(lock, "electron-builder"),
            electron_builder_version,
            context="locked electron-builder",
        )
        lock_packages = _require_object(lock.get("packages"), context="lock packages")
        electron_builder_lock = _require_object(
            lock_packages.get("node_modules/electron-builder"),
            context="locked electron-builder",
        )
        app_builder_lib_version = _require_exact_version(
            _dependency(
                electron_builder_lock,
                "app-builder-lib",
                context="locked electron-builder dependencies",
            ),
            context="app-builder-lib dependency",
        )
        _require_exact(
            _locked_version(lock, "app-builder-lib"),
            app_builder_lib_version,
            context="locked app-builder-lib",
        )
        _require_exact(
            app_builder_lib_version,
            cls.get_compatibility_pin("appBuilderLibVersion"),
            context="supported app-builder-lib version",
        )
        sherpa_version = _candidate_sherpa_version(server)
        _require_exact(
            sherpa_version,
            cls.get_compatibility_pin("sherpaVersion"),
            context="supported Sherpa version",
        )
        for package in ("sherpa-onnx-node", "sherpa-onnx-darwin-arm64"):
            _require_exact(
                _locked_version(lock, package),
                sherpa_version,
                context=f"locked {package}",
            )

        esbuild_version = _require_exact_version(
            _locked_path_version(
                lock,
                "packages/server/node_modules/esbuild",
                context="locked server esbuild",
            ),
            context="locked server esbuild",
        )
        require_npm_version_matches_spec(
            esbuild_version,
            _dependency(server, "esbuild", context="server dependencies"),
            context="Paseo esbuild",
        )
        _require_exact(
            _locked_path_version(
                lock,
                "packages/server/node_modules/@esbuild/darwin-arm64",
                context="locked server esbuild darwin-arm64",
            ),
            esbuild_version,
            context="locked server esbuild darwin-arm64",
        )

        claude_agent_sdk_version = cls._validate_claude_sdk_lock(
            server=server,
            lock=lock,
        )

        node_addon_api_spec = _dependency(
            sherpa,
            "node-addon-api",
            context="sherpa addon dependencies",
        )
        node_addon_api_version = _require_exact_version(
            cls.get_compatibility_pin("nodeAddonApiVersion"),
            context="supported node-addon-api version",
        )
        require_npm_version_matches_spec(
            node_addon_api_version,
            node_addon_api_spec,
            context="Paseo sherpa node-addon-api",
        )

        ort_matches = re.findall(
            r"/v([^/]+)/onnxruntime-osx-arm64-([^/]+)\.tgz",
            sherpa_ort_cmake,
        )
        if len(ort_matches) != 1 or ort_matches[0][0] != ort_matches[0][1]:
            msg = "Paseo sherpa source must select one exact ONNX Runtime archive"
            raise RuntimeError(msg)
        onnxruntime_version = _require_exact_version(
            ort_matches[0][0],
            context="sherpa ONNX Runtime version",
        )
        _require_exact(
            onnxruntime_version,
            cls.get_compatibility_pin("onnxruntimeVersion"),
            context="supported ONNX Runtime version",
        )
        return _ManifestContract(
            app_builder_lib_version=app_builder_lib_version,
            claude_agent_sdk_version=claude_agent_sdk_version,
            electron_version=electron_version,
            esbuild_version=esbuild_version,
            node_addon_api_version=node_addon_api_version,
            onnxruntime_version=onnxruntime_version,
            sherpa_version=sherpa_version,
        )

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve the latest release against the supported native foundation."""
        release = await self._fetch_latest_release_payload(session)
        tag = self._release_tag_from_payload(release)
        version = _require_exact_version(
            self._normalize_release_version(tag),
            context="release version",
        )

        commit = await self._resolve_tag_commit(
            session,
            owner=self.GITHUB_OWNER,
            repo=self.GITHUB_REPO,
            tag=tag,
            config=self.config,
        )

        def paseo_raw(path: str) -> str:
            return github_raw_url(
                self.GITHUB_OWNER,
                self.GITHUB_REPO,
                commit,
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
        sherpa_version = _candidate_sherpa_version(server_payload)
        _require_exact(
            sherpa_version,
            self.get_compatibility_pin("sherpaVersion"),
            context="supported Sherpa version",
        )
        sherpa_commit = await self._resolve_tag_commit(
            session,
            owner="k2-fsa",
            repo="sherpa-onnx",
            tag=f"v{sherpa_version}",
            config=self.config,
        )

        def sherpa_raw(path: str) -> str:
            return github_raw_url(
                "k2-fsa",
                "sherpa-onnx",
                sherpa_commit,
                path,
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
        contract = self._validate_manifests(
            root_payload=root_payload,
            desktop_payload=desktop_payload,
            server_payload=server_payload,
            lock_payload=lock_payload,
            sherpa_payload=sherpa_payload,
            sherpa_ort_cmake=sherpa_ort_cmake,
            release_version=version,
        )
        onnxruntime_commit = await self._resolve_tag_commit(
            session,
            owner="microsoft",
            repo="onnxruntime",
            tag=f"v{contract.onnxruntime_version}",
            config=self.config,
        )
        onnx_dependencies = await self._resolve_onnx_native_dependencies(
            session,
            config=self.config,
        )

        return VersionInfo(
            version=version,
            metadata={
                "commit": commit,
                "appBuilderLibVersion": contract.app_builder_lib_version,
                "claudeAgentSdkVersion": contract.claude_agent_sdk_version,
                "electronVersion": contract.electron_version,
                "esbuildVersion": contract.esbuild_version,
                "nodeAddonApiUrl": self._node_addon_api_url(
                    contract.node_addon_api_version
                ),
                "nodeAddonApiVersion": contract.node_addon_api_version,
                "onnxruntimeCommit": onnxruntime_commit,
                "onnxruntimeUrl": self._archive_url(
                    "microsoft",
                    "onnxruntime",
                    onnxruntime_commit,
                ),
                "onnxruntimeVersion": contract.onnxruntime_version,
                "onnxDependencies": onnx_dependencies,
                "paseoUrl": self._archive_url(
                    self.GITHUB_OWNER, self.GITHUB_REPO, commit
                ),
                "sherpaCommit": sherpa_commit,
                "sherpaOnnxNodeUrl": self._sherpa_wrapper_url(contract.sherpa_version),
                "sherpaOnnxUrl": self._archive_url(
                    "k2-fsa",
                    "sherpa-onnx",
                    sherpa_commit,
                ),
                "sherpaVersion": contract.sherpa_version,
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
    def _native_lock_payload(
        cls,
        metadata: dict[str, str],
        hashes: dict[str, str],
        onnx_dependencies: dict[str, dict[str, str]],
    ) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "paseo": {
                "version": metadata["version"],
                "commit": metadata["commit"],
                "electronVersion": metadata["electronVersion"],
                "nodeAddonApiVersion": metadata["nodeAddonApiVersion"],
                "npmFetcherVersion": cls._npm_fetcher_version(),
                "esbuildVersion": metadata["esbuildVersion"],
                "claudeAgentSdkVersion": metadata["claudeAgentSdkVersion"],
                "appBuilderLibVersion": metadata["appBuilderLibVersion"],
                "appBuilderLibBackportCommit": (cls._app_builder_lib_backport_commit()),
            },
            "sherpaOnnx": {
                "version": metadata["sherpaVersion"],
                "commit": metadata["sherpaCommit"],
                "onnxruntime": {
                    "version": metadata["onnxruntimeVersion"],
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
                "version": metadata["onnxruntimeVersion"],
                "commit": metadata["onnxruntimeCommit"],
                "dependencies": {
                    name: {**dependency, "hash": hashes[f"onnx:{name}"]}
                    for name, dependency in onnx_dependencies.items()
                },
                "patches": [
                    {**patch, "hash": hashes[f"patch:{index}"]}
                    for index, patch in enumerate(_ONNX_NATIVE_PATCHES)
                ],
                "sourceClosureComplete": True,
            },
        }

    @classmethod
    def _native_hash_requests(
        cls,
        onnx_dependencies: dict[str, dict[str, str]],
    ) -> tuple[tuple[str, str], ...]:
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
                    rev=dependency["commit"],
                    fetch_submodules=False,
                ),
            )
            for name, dependency in onnx_dependencies.items()
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
    def _npm_deps_expr(
        cls,
        *,
        commit: str,
        src_hash: str,
        version: str,
    ) -> str:
        expression = FunctionCall(
            name=identifier_attr_path("pkgs", "fetchNpmDeps"),
            argument=AttributeSet(
                values=[
                    Binding(
                        name="name",
                        value=StringPrimitive(value=f"{cls.name}-{version}-npm-deps"),
                    ),
                    Binding(
                        name="src",
                        value=_build_fetch_from_github_call(
                            cls.GITHUB_OWNER,
                            cls.GITHUB_REPO,
                            rev=commit,
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
                        value=Primitive(value=cls._npm_fetcher_version()),
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
                "appBuilderLibVersion",
                "claudeAgentSdkVersion",
                "commit",
                "electronVersion",
                "esbuildVersion",
                "nodeAddonApiUrl",
                "nodeAddonApiVersion",
                "onnxruntimeCommit",
                "onnxruntimeUrl",
                "onnxruntimeVersion",
                "paseoUrl",
                "sherpaCommit",
                "sherpaOnnxNodeUrl",
                "sherpaOnnxUrl",
                "sherpaVersion",
                "tag",
            )
        }
        version = _require_exact_version(info.version, context="release version")
        result["version"] = version
        for key in (
            "appBuilderLibVersion",
            "claudeAgentSdkVersion",
            "electronVersion",
            "esbuildVersion",
            "nodeAddonApiVersion",
            "onnxruntimeVersion",
            "sherpaVersion",
        ):
            _require_exact_version(result[key], context=key)
        for metadata_key, pin_key in (
            ("appBuilderLibVersion", "appBuilderLibVersion"),
            ("nodeAddonApiVersion", "nodeAddonApiVersion"),
            ("onnxruntimeVersion", "onnxruntimeVersion"),
            ("sherpaVersion", "sherpaVersion"),
        ):
            _require_exact(
                result[metadata_key],
                cls.get_compatibility_pin(pin_key),
                context=f"supported {metadata_key}",
            )
        for key in ("commit", "onnxruntimeCommit", "sherpaCommit"):
            _require_commit(result[key], context=key)
        _require_exact(
            result["tag"],
            f"v{version}",
            context="release tag",
        )
        expected_urls = {
            "nodeAddonApiUrl": cls._node_addon_api_url(result["nodeAddonApiVersion"]),
            "onnxruntimeUrl": cls._archive_url(
                "microsoft",
                "onnxruntime",
                result["onnxruntimeCommit"],
            ),
            "paseoUrl": cls._archive_url(
                cls.GITHUB_OWNER,
                cls.GITHUB_REPO,
                result["commit"],
            ),
            "sherpaOnnxNodeUrl": cls._sherpa_wrapper_url(result["sherpaVersion"]),
            "sherpaOnnxUrl": cls._archive_url(
                "k2-fsa",
                "sherpa-onnx",
                result["sherpaCommit"],
            ),
        }
        for key, expected in expected_urls.items():
            _require_exact(result[key], expected, context=key)
        return result

    @classmethod
    def _required_onnx_dependencies(
        cls,
        info: VersionInfo,
    ) -> dict[str, dict[str, str]]:
        metadata = metadata_as_mapping(info.metadata, context="Paseo release metadata")
        raw_dependencies = _require_object(
            metadata.get("onnxDependencies"),
            context="release ONNX dependencies",
        )
        if set(raw_dependencies) != set(_ONNX_NATIVE_DEPENDENCIES):
            msg = "Paseo release ONNX dependencies do not match the supported inventory"
            raise RuntimeError(msg)

        result: dict[str, dict[str, str]] = {}
        for name, expected in _ONNX_NATIVE_DEPENDENCIES.items():
            dependency = _require_object(
                raw_dependencies[name],
                context=f"release ONNX dependency {name}",
            )
            if set(dependency) != set(expected) | {"commit"}:
                msg = f"Paseo release ONNX dependency {name} has unexpected fields"
                raise RuntimeError(msg)
            normalized = {
                key: _require_string(
                    dependency,
                    key,
                    context=f"release ONNX dependency {name}",
                )
                for key in expected
            }
            for key, value in expected.items():
                _require_exact(
                    normalized[key],
                    value,
                    context=f"ONNX dependency {name} {key}",
                )
            normalized["commit"] = _require_commit(
                _require_string(
                    dependency,
                    "commit",
                    context=f"release ONNX dependency {name}",
                ),
                context=f"ONNX dependency {name} commit",
            )
            result[name] = normalized
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
        onnx_dependencies = self._required_onnx_dependencies(info)
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
                    metadata["commit"],
                    "paseoUrl",
                    False,
                ),
                (
                    "k2-fsa",
                    "sherpa-onnx",
                    metadata["sherpaCommit"],
                    "sherpaOnnxUrl",
                    False,
                ),
                (
                    "microsoft",
                    "onnxruntime",
                    metadata["onnxruntimeCommit"],
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
                self._npm_deps_expr(
                    commit=metadata["commit"],
                    src_hash=source_hashes[paseo_url],
                    version=info.version,
                ),
                config=self.config,
            ),
            npm_drain,
            parse=expect_str,
        ):
            yield event
        npm_hash = require_value(npm_drain, "Missing Paseo npmDepsHash output")
        entries.append(HashEntry.create("npmDepsHash", npm_hash, url=paseo_url))

        native_hashes: dict[str, str] = {}
        for identity, expression in self._native_hash_requests(onnx_dependencies):
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
                self._native_lock_payload(
                    metadata,
                    native_hashes,
                    onnx_dependencies,
                ),
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
            "pins": self.source_pins_for(info),
            "urls": {
                "nodeAddonApi": metadata["nodeAddonApiUrl"],
                "onnxruntime": metadata["onnxruntimeUrl"],
                "paseo": metadata["paseoUrl"],
                "sherpaOnnx": metadata["sherpaOnnxUrl"],
                "sherpaOnnxNode": metadata["sherpaOnnxNodeUrl"],
            },
        })
