"""Exact-source updater for the Nix-owned OpenChamber desktop package."""

import re
import urllib.parse
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, cast

from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.path import NixPath
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import (
    HashCollection,
    HashEntry,
    HashType,
    SourceEntry,
    SourceHashes,
)
from lib.update import nix as update_nix
from lib.update.bun_lock import parse_bun_lock_text
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import (
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_str,
    require_value,
)
from lib.update.net import fetch_github_api, fetch_json, fetch_url, github_raw_url
from lib.update.nix import (
    _build_fetch_from_github_call,
    _build_fetch_from_github_expr,
)
from lib.update.nix_expr import compact_nix_expr, identifier_attr_path
from lib.update.npm_semver import require_npm_version_matches_spec
from lib.update.paths import REPO_ROOT
from lib.update.updaters import (
    GitHubReleaseUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import metadata_as_mapping
from lib.update.updaters.node_compatibility import (
    require_supported_node_engine,
    resolve_package_passthru_version,
)

if TYPE_CHECKING:
    import aiohttp

    from lib.update.config import UpdateConfig
    from lib.update.events import EventStream

_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_EXACT_VERSION_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


@dataclass(frozen=True, slots=True)
class _HashRequest:
    hash_type: HashType
    url: str
    expr: str
    error: str
    platform: str | None = None


@dataclass(frozen=True, slots=True)
class _OpenChamberManifestContract:
    bun_version: str
    electron_version: str
    opencode_version: str
    sherpa_version: str
    sherpa_wrapper_version: str


@dataclass(frozen=True, slots=True)
class _CompanionManifestContract:
    node_addon_api_version: str
    opencode_node_modules_hash: str


def _require_object(value: object, *, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = f"OpenChamber {context} is not a JSON object"
        raise TypeError(msg)
    return cast("dict[str, object]", value)


def _require_string(mapping: dict[str, object], key: str, *, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        msg = f"OpenChamber {context} is missing {key}"
        raise TypeError(msg)
    return value


def _require_exact(actual: str, expected: str, *, context: str) -> str:
    if actual != expected:
        msg = f"OpenChamber {context} must be {expected!r}, got {actual!r}"
        raise RuntimeError(msg)
    return actual


def _require_exact_version(value: str, *, context: str) -> str:
    if _EXACT_VERSION_PATTERN.fullmatch(value) is None:
        msg = f"OpenChamber {context} must be an exact semantic version, got {value!r}"
        raise RuntimeError(msg)
    return value


def _require_prefixed_version(value: str, prefix: str, *, context: str) -> str:
    if not value.startswith(prefix):
        msg = f"OpenChamber {context} must start with {prefix!r}, got {value!r}"
        raise RuntimeError(msg)
    return _require_exact_version(value.removeprefix(prefix), context=context)


def _require_commit(value: str, *, context: str) -> str:
    if _COMMIT_PATTERN.fullmatch(value) is None:
        msg = f"OpenChamber {context} must be an immutable commit, got {value!r}"
        raise RuntimeError(msg)
    return value


def _dependency(mapping: dict[str, object], name: str, *, context: str) -> str:
    dependencies = _require_object(mapping.get("dependencies"), context=context)
    return _require_string(dependencies, name, context=context)


def _locked_package_version(packages: dict[str, object], package: str) -> str:
    raw_entry = packages.get(package)
    if raw_entry is None:
        msg = f"OpenChamber bun.lock must contain exactly one {package} resolution"
        raise RuntimeError(msg)
    if not isinstance(raw_entry, list) or not raw_entry:
        msg = f"OpenChamber bun.lock {package} resolution must be a non-empty array"
        raise TypeError(msg)
    resolution = raw_entry[0]
    prefix = f"{package}@"
    if (
        not isinstance(resolution, str)
        or not resolution.startswith(prefix)
        or not resolution.removeprefix(prefix)
    ):
        msg = f"OpenChamber bun.lock {package} resolution must start with {prefix!r}"
        raise RuntimeError(msg)
    return resolution.removeprefix(prefix)


@register_updater
class OpenChamberUpdater(GitHubReleaseUpdater):
    """Resolve a compatible OpenChamber release and its exact source graph."""

    name = "openchamber"
    aggregate_into = ("electron-runtimes",)
    GITHUB_OWNER = "openchamber"
    GITHUB_REPO = "openchamber"
    DARWIN_PLATFORM: ClassVar[str] = "aarch64-darwin"
    NODEJS_VERSION_PASSTHRU: ClassVar[str] = "nodejsVersion"
    supported_platforms = (DARWIN_PLATFORM,)

    APP_ID = "dev.openchamber.desktop"
    PRODUCT_NAME = "OpenChamber"
    derivation_validations = (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=(DARWIN_PLATFORM,),
            mode="build",
        ),
    )

    @staticmethod
    def _bun_url(version: str) -> str:
        return (
            "https://github.com/oven-sh/bun/releases/download/"
            f"bun-v{version}/bun-darwin-aarch64.zip"
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
            msg = f"OpenChamber {owner}/{repo} tag {tag} is not an immutable commit"
            raise RuntimeError(msg)
        return commit

    @classmethod
    def _archive_url(cls, owner: str, repo: str, commit: str) -> str:
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

    @classmethod
    def _validate_openchamber_manifests(
        cls,
        *,
        root_payload: object,
        electron_payload: object,
        web_payload: object,
        lock_text: str,
        release_version: str,
        selected_node_version: str,
    ) -> _OpenChamberManifestContract:
        release_version = _require_exact_version(
            release_version,
            context="release version",
        )
        root = _require_object(root_payload, context="root manifest")
        _require_exact(
            _require_exact_version(
                _require_string(root, "version", context="root manifest"),
                context="root version",
            ),
            release_version,
            context="root version",
        )
        bun_version = _require_prefixed_version(
            _require_string(root, "packageManager", context="root manifest"),
            "bun@",
            context="package manager",
        )
        opencode_version = _require_exact_version(
            _dependency(root, "@opencode-ai/sdk", context="root dependencies"),
            context="root OpenCode SDK dependency",
        )
        engines = _require_object(root.get("engines"), context="root engines")
        require_supported_node_engine(
            _require_string(engines, "node", context="root engines"),
            selected_attr=(f"{cls.name}.passthru.{cls.NODEJS_VERSION_PASSTHRU}"),
            selected_version=selected_node_version,
            source_name="OpenChamber",
        )

        lock = parse_bun_lock_text(lock_text, context="OpenChamber bun.lock")
        lock_packages = _require_object(
            lock.get("packages"),
            context="bun.lock packages",
        )

        electron = _require_object(electron_payload, context="Electron manifest")
        _require_exact(
            _require_exact_version(
                _require_string(electron, "version", context="Electron manifest"),
                context="Electron package version",
            ),
            release_version,
            context="Electron package version",
        )
        build = _require_object(electron.get("build"), context="Electron build")
        _require_exact(
            _require_string(build, "appId", context="Electron build"),
            cls.APP_ID,
            context="bundle identifier",
        )
        _require_exact(
            _require_string(build, "productName", context="Electron build"),
            cls.PRODUCT_NAME,
            context="product name",
        )
        dev_dependencies = _require_object(
            electron.get("devDependencies"),
            context="Electron devDependencies",
        )
        electron_version = _require_exact_version(
            _locked_package_version(lock_packages, "electron"),
            context="locked electron",
        )
        require_npm_version_matches_spec(
            electron_version,
            _require_string(
                dev_dependencies,
                "electron",
                context="Electron devDependencies",
            ),
            context="OpenChamber Electron dependency",
        )

        web = _require_object(web_payload, context="web manifest")
        _require_exact(
            _require_exact_version(
                _require_string(web, "version", context="web manifest"),
                context="web package version",
            ),
            release_version,
            context="web package version",
        )
        _require_exact(
            _require_exact_version(
                _dependency(web, "@opencode-ai/sdk", context="web dependencies"),
                context="web OpenCode SDK dependency",
            ),
            opencode_version,
            context="web OpenCode SDK dependency",
        )
        sherpa_wrapper_version = _require_exact_version(
            _dependency(web, "sherpa-onnx-node", context="web dependencies"),
            context="sherpa wrapper dependency",
        )
        _require_exact(
            _locked_package_version(lock_packages, "@opencode-ai/sdk"),
            opencode_version,
            context="locked @opencode-ai/sdk",
        )
        _require_exact(
            _locked_package_version(lock_packages, "sherpa-onnx-node"),
            sherpa_wrapper_version,
            context="locked sherpa-onnx-node",
        )
        sherpa_version = _require_exact_version(
            _locked_package_version(lock_packages, "sherpa-onnx-darwin-arm64"),
            context="locked sherpa-onnx-darwin-arm64",
        )
        return _OpenChamberManifestContract(
            bun_version=bun_version,
            electron_version=electron_version,
            opencode_version=opencode_version,
            sherpa_version=sherpa_version,
            sherpa_wrapper_version=sherpa_wrapper_version,
        )

    @classmethod
    def _validate_companion_manifests(
        cls,
        *,
        bun_version: str,
        opencode_payload: object,
        opencode_hashes_payload: object,
        sherpa_payload: object,
    ) -> _CompanionManifestContract:
        opencode = _require_object(opencode_payload, context="OpenCode manifest")
        _require_exact(
            _require_string(opencode, "packageManager", context="OpenCode manifest"),
            f"bun@{bun_version}",
            context="OpenCode package manager",
        )
        hashes = _require_object(opencode_hashes_payload, context="OpenCode hashes")
        node_modules = _require_object(
            hashes.get("nodeModules"),
            context="OpenCode nodeModules hashes",
        )
        opencode_node_modules_hash = _require_string(
            node_modules,
            cls.DARWIN_PLATFORM,
            context="OpenCode nodeModules hashes",
        )
        # Validate SRI form through the canonical sources model.
        HashEntry.create("nodeModulesHash", opencode_node_modules_hash)

        sherpa = _require_object(sherpa_payload, context="sherpa Node addon manifest")
        node_addon_api_version = _require_prefixed_version(
            _dependency(sherpa, "node-addon-api", context="sherpa dependencies"),
            "^",
            context="sherpa node-addon-api dependency",
        )
        return _CompanionManifestContract(
            node_addon_api_version=node_addon_api_version,
            opencode_node_modules_hash=opencode_node_modules_hash,
        )

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve the latest release and prove its source-build contract."""
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

        def raw(path: str) -> str:
            return github_raw_url(
                self.GITHUB_OWNER,
                self.GITHUB_REPO,
                commit,
                path,
            )

        root_payload = await fetch_json(
            session, raw("package.json"), config=self.config
        )
        electron_payload = await fetch_json(
            session,
            raw("packages/electron/package.json"),
            config=self.config,
        )
        web_payload = await fetch_json(
            session,
            raw("packages/web/package.json"),
            config=self.config,
        )
        lock_text = (
            await fetch_url(session, raw("bun.lock"), config=self.config)
        ).decode("utf-8")
        selected_node_version = await resolve_package_passthru_version(
            self.name,
            self.NODEJS_VERSION_PASSTHRU,
            command_timeout=self.config.default_subprocess_timeout,
            source_name="OpenChamber",
        )
        manifest_contract = self._validate_openchamber_manifests(
            root_payload=root_payload,
            electron_payload=electron_payload,
            web_payload=web_payload,
            lock_text=lock_text,
            release_version=version,
            selected_node_version=selected_node_version,
        )

        opencode_tag = f"v{manifest_contract.opencode_version}"
        opencode_commit = await self._resolve_tag_commit(
            session,
            owner="anomalyco",
            repo="opencode",
            tag=opencode_tag,
            config=self.config,
        )
        sherpa_tag = f"v{manifest_contract.sherpa_version}"
        sherpa_commit = await self._resolve_tag_commit(
            session,
            owner="k2-fsa",
            repo="sherpa-onnx",
            tag=sherpa_tag,
            config=self.config,
        )

        def opencode_raw(path: str) -> str:
            return github_raw_url(
                "anomalyco",
                "opencode",
                opencode_commit,
                path,
            )

        def sherpa_raw(path: str) -> str:
            return github_raw_url(
                "k2-fsa",
                "sherpa-onnx",
                sherpa_commit,
                path,
            )

        opencode_payload = await fetch_json(
            session,
            opencode_raw("package.json"),
            config=self.config,
        )
        opencode_hashes_payload = await fetch_json(
            session,
            opencode_raw("nix/hashes.json"),
            config=self.config,
        )
        sherpa_payload = await fetch_json(
            session,
            sherpa_raw("scripts/node-addon-api/package.json"),
            config=self.config,
        )
        companion_contract = self._validate_companion_manifests(
            bun_version=manifest_contract.bun_version,
            opencode_payload=opencode_payload,
            opencode_hashes_payload=opencode_hashes_payload,
            sherpa_payload=sherpa_payload,
        )

        openchamber_url = self._archive_url(
            self.GITHUB_OWNER,
            self.GITHUB_REPO,
            commit,
        )
        opencode_url = self._archive_url("anomalyco", "opencode", opencode_commit)
        sherpa_url = self._archive_url("k2-fsa", "sherpa-onnx", sherpa_commit)
        return VersionInfo(
            version=version,
            metadata={
                "bunUrl": self._bun_url(manifest_contract.bun_version),
                "bunVersion": manifest_contract.bun_version,
                "commit": commit,
                "electronVersion": manifest_contract.electron_version,
                "nodeAddonApiUrl": self._node_addon_api_url(
                    companion_contract.node_addon_api_version
                ),
                "nodeAddonApiVersion": companion_contract.node_addon_api_version,
                "openchamberUrl": openchamber_url,
                "opencodeCommit": opencode_commit,
                "opencodeNodeModulesHash": (
                    companion_contract.opencode_node_modules_hash
                ),
                "opencodeUrl": opencode_url,
                "opencodeVersion": manifest_contract.opencode_version,
                "sherpaCommit": sherpa_commit,
                "sherpaOnnxUrl": sherpa_url,
                "sherpaOnnxNodeUrl": self._sherpa_wrapper_url(
                    manifest_contract.sherpa_wrapper_version
                ),
                "sherpaVersion": manifest_contract.sherpa_version,
                "sherpaWrapperVersion": manifest_contract.sherpa_wrapper_version,
                "tag": tag,
            },
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Recompute every fixed-output closure before accepting current metadata."""
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
    def _exact_bun_expr(
        *,
        version: str,
        url: str,
        hash_value: str,
    ) -> FunctionCall:
        bun_source = FunctionCall(
            name=identifier_attr_path("pkgs", "fetchurl"),
            argument=AttributeSet(
                values=[
                    Binding(name="url", value=StringPrimitive(value=url)),
                    Binding(name="hash", value=StringPrimitive(value=hash_value)),
                ]
            ),
        )
        return FunctionCall(
            name=FunctionCall(
                name=identifier_attr_path("pkgs", "callPackage"),
                argument=NixPath(path=str(REPO_ROOT / "packages/openchamber/bun.nix")),
            ),
            argument=AttributeSet(
                values=[
                    Binding(name="bunSource", value=bun_source),
                    Binding(
                        name="version",
                        value=StringPrimitive(value=version),
                    ),
                ],
            ),
        )

    @classmethod
    def _node_modules_expr(
        cls,
        *,
        bun_hash: str,
        bun_url: str,
        bun_version: str,
        commit: str,
        src_hash: str,
        version: str,
    ) -> str:
        package_call = FunctionCall(
            name=FunctionCall(
                name=identifier_attr_path("pkgs", "callPackage"),
                argument=NixPath(
                    path=str(REPO_ROOT / "packages/openchamber/node-modules.nix")
                ),
            ),
            argument=AttributeSet(
                values=[
                    Binding(
                        name="bun",
                        value=cls._exact_bun_expr(
                            version=bun_version,
                            url=bun_url,
                            hash_value=bun_hash,
                        ),
                    ),
                    Binding(
                        name="bunVersion",
                        value=StringPrimitive(value=bun_version),
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
                    Binding(name="version", value=StringPrimitive(value=version)),
                    Binding(
                        name="hash",
                        value=identifier_attr_path("pkgs", "lib", "fakeHash"),
                    ),
                ]
            ),
        )
        return compact_nix_expr(package_call.rebuild())

    @classmethod
    def _required_metadata(cls, info: VersionInfo) -> dict[str, str]:
        metadata = metadata_as_mapping(
            info.metadata,
            context="OpenChamber release metadata",
        )
        keys = (
            "bunUrl",
            "bunVersion",
            "commit",
            "electronVersion",
            "nodeAddonApiUrl",
            "nodeAddonApiVersion",
            "openchamberUrl",
            "opencodeCommit",
            "opencodeNodeModulesHash",
            "opencodeUrl",
            "opencodeVersion",
            "sherpaCommit",
            "sherpaOnnxUrl",
            "sherpaOnnxNodeUrl",
            "sherpaVersion",
            "sherpaWrapperVersion",
            "tag",
        )
        result = {
            key: _require_string(metadata, key, context="release metadata")
            for key in keys
        }
        version = _require_exact_version(info.version, context="release version")
        version_keys = (
            "bunVersion",
            "electronVersion",
            "nodeAddonApiVersion",
            "opencodeVersion",
            "sherpaVersion",
            "sherpaWrapperVersion",
        )
        for key in version_keys:
            _require_exact_version(result[key], context=key)
        for key in ("commit", "opencodeCommit", "sherpaCommit"):
            _require_commit(result[key], context=key)
        _require_exact(
            result["tag"],
            f"v{version}",
            context="release tag",
        )
        expected_urls = {
            "bunUrl": cls._bun_url(result["bunVersion"]),
            "nodeAddonApiUrl": cls._node_addon_api_url(result["nodeAddonApiVersion"]),
            "openchamberUrl": cls._archive_url(
                cls.GITHUB_OWNER,
                cls.GITHUB_REPO,
                result["commit"],
            ),
            "opencodeUrl": cls._archive_url(
                "anomalyco",
                "opencode",
                result["opencodeCommit"],
            ),
            "sherpaOnnxUrl": cls._archive_url(
                "k2-fsa",
                "sherpa-onnx",
                result["sherpaCommit"],
            ),
            "sherpaOnnxNodeUrl": cls._sherpa_wrapper_url(
                result["sherpaWrapperVersion"]
            ),
        }
        for key, expected in expected_urls.items():
            _require_exact(
                result[key],
                expected,
                context=f"{key} provenance",
            )
        HashEntry.create(
            "nodeModulesHash",
            result["opencodeNodeModulesHash"],
        )
        return result

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Hash exact sources and URL inputs, then the OpenChamber Bun closure."""
        _ = (session, context)
        metadata = self._required_metadata(info)
        source_specs = (
            (
                self.GITHUB_OWNER,
                self.GITHUB_REPO,
                metadata["commit"],
                "openchamberUrl",
            ),
            (
                "anomalyco",
                "opencode",
                metadata["opencodeCommit"],
                "opencodeUrl",
            ),
            (
                "k2-fsa",
                "sherpa-onnx",
                metadata["sherpaCommit"],
                "sherpaOnnxUrl",
            ),
        )
        requests = [
            _HashRequest(
                hash_type="srcHash",
                url=metadata[url_key],
                expr=_build_fetch_from_github_expr(
                    owner,
                    repo,
                    rev=commit,
                    fetch_submodules=False,
                ),
                error=f"Missing {repo} srcHash output",
            )
            for owner, repo, commit, url_key in source_specs
        ]
        for url_key in ("bunUrl", "nodeAddonApiUrl", "sherpaOnnxNodeUrl"):
            url = metadata[url_key]
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
            entries.append(
                HashEntry.create(
                    request.hash_type,
                    value,
                    platform=request.platform,
                    url=request.url,
                )
            )
            source_hashes[request.url] = value

        opencode_url = metadata["opencodeUrl"]
        entries.append(
            HashEntry.create(
                "nodeModulesHash",
                metadata["opencodeNodeModulesHash"],
                platform=self.DARWIN_PLATFORM,
                url=opencode_url,
            )
        )
        openchamber_url = metadata["openchamberUrl"]
        node_modules_drain = ValueDrain[str]()
        async for event in drain_value_events(
            update_nix.compute_fixed_output_hash(
                self.name,
                self._node_modules_expr(
                    bun_hash=source_hashes[metadata["bunUrl"]],
                    bun_url=metadata["bunUrl"],
                    bun_version=metadata["bunVersion"],
                    commit=metadata["commit"],
                    src_hash=source_hashes[openchamber_url],
                    version=info.version,
                ),
                config=self.config,
            ),
            node_modules_drain,
            parse=expect_str,
        ):
            yield event
        node_modules_hash = require_value(
            node_modules_drain,
            "Missing OpenChamber nodeModulesHash output",
        )
        entries.append(
            HashEntry.create(
                "nodeModulesHash",
                node_modules_hash,
                platform=self.DARWIN_PLATFORM,
                url=openchamber_url,
            )
        )
        yield UpdateEvent.value(self.name, entries)

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist only the complete eight-entry exact-source closure."""
        metadata = self._required_metadata(info)
        collection = HashCollection.from_value(hashes)
        entries = collection.entries
        if entries is None:
            msg = "OpenChamber updater requires structured hash entries"
            raise TypeError(msg)
        expected_keys = {
            ("srcHash", None, metadata["openchamberUrl"]),
            ("srcHash", None, metadata["opencodeUrl"]),
            ("srcHash", None, metadata["sherpaOnnxUrl"]),
            ("sha256", None, metadata["bunUrl"]),
            ("sha256", None, metadata["nodeAddonApiUrl"]),
            ("sha256", None, metadata["sherpaOnnxNodeUrl"]),
            ("nodeModulesHash", self.DARWIN_PLATFORM, metadata["opencodeUrl"]),
            (
                "nodeModulesHash",
                self.DARWIN_PLATFORM,
                metadata["openchamberUrl"],
            ),
        }
        actual_keys = {
            (entry.hash_type, entry.platform, entry.url) for entry in entries
        }
        if len(entries) != len(expected_keys) or actual_keys != expected_keys:
            msg = "OpenChamber updater requires its complete exact-source hash closure"
            raise RuntimeError(msg)
        opencode_hash = next(
            entry.hash
            for entry in entries
            if entry.hash_type == "nodeModulesHash"
            and entry.url == metadata["opencodeUrl"]
        )
        if opencode_hash != metadata["opencodeNodeModulesHash"]:
            msg = "OpenChamber OpenCode nodeModulesHash differs from exact upstream"
            raise RuntimeError(msg)
        return SourceEntry.model_validate({
            "version": info.version,
            "commit": metadata["commit"],
            "electronVersion": metadata["electronVersion"],
            "pins": {
                "bunVersion": metadata["bunVersion"],
                "opencodeCommit": metadata["opencodeCommit"],
                "opencodeVersion": metadata["opencodeVersion"],
                "sherpaCommit": metadata["sherpaCommit"],
                "sherpaVersion": metadata["sherpaVersion"],
                "sherpaWrapperVersion": metadata["sherpaWrapperVersion"],
            },
            "urls": {
                "bun": metadata["bunUrl"],
                "nodeAddonApi": metadata["nodeAddonApiUrl"],
                "openchamber": metadata["openchamberUrl"],
                "opencode": metadata["opencodeUrl"],
                "sherpaOnnx": metadata["sherpaOnnxUrl"],
                "sherpaOnnxNode": metadata["sherpaOnnxNodeUrl"],
            },
            "hashes": collection,
        })


if __name__ == "__main__":  # pragma: no cover -- updater registry entrypoint
    _MESSAGE = "Run through nixcfg update"
    raise SystemExit(_MESSAGE)
