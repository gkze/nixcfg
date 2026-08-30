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
from lib.update.paths import REPO_ROOT
from lib.update.updaters import (
    GitHubReleaseUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import metadata_as_mapping

if TYPE_CHECKING:
    import aiohttp

    from lib.update.config import UpdateConfig
    from lib.update.events import EventStream

_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_LOCKED_PACKAGE_TEMPLATE = r'"{name}":\s*\["{name}@([^"\s]+)"'


@dataclass(frozen=True, slots=True)
class _HashRequest:
    hash_type: HashType
    url: str
    expr: str
    error: str
    platform: str | None = None


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


def _dependency(mapping: dict[str, object], name: str, *, context: str) -> str:
    dependencies = _require_object(mapping.get("dependencies"), context=context)
    return _require_string(dependencies, name, context=context)


def _locked_package_version(lock_text: str, package: str) -> str:
    escaped = re.escape(package)
    matches = re.findall(
        _LOCKED_PACKAGE_TEMPLATE.format(name=escaped),
        lock_text,
    )
    if len(matches) != 1:
        msg = f"OpenChamber bun.lock must contain exactly one {package} resolution"
        raise RuntimeError(msg)
    return matches[0]


@register_updater
class OpenChamberUpdater(GitHubReleaseUpdater):
    """Pin the audited OpenChamber release and every source-built companion."""

    name = "openchamber"
    GITHUB_OWNER = "openchamber"
    GITHUB_REPO = "openchamber"
    DARWIN_PLATFORM: ClassVar[str] = "aarch64-darwin"
    supported_platforms = (DARWIN_PLATFORM,)

    VERSION = "1.21.0"
    COMMIT = "ad7fd356339ccc5c9af5af1a6786662572d53ed0"
    BUN_VERSION = "1.3.14"
    NODE_ENGINE = ">=22.0.0"
    ELECTRON_VERSION = "43.3.0"
    APP_ID = "dev.openchamber.desktop"
    PRODUCT_NAME = "OpenChamber"
    OPENCODE_VERSION = "1.18.23"
    OPENCODE_COMMIT = "ef2880f379129aa048be9e9353e30aa168d42c17"
    SHERPA_VERSION = "1.13.3"
    SHERPA_COMMIT = "330609dab49be6ee8b30702918ca7abbbad1286a"
    SHERPA_WRAPPER_VERSION = "1.12.28"
    NODE_ADDON_API_VERSION = "8.3.0"
    source_pins: ClassVar[dict[str, str]] = {
        "bunVersion": BUN_VERSION,
        "opencodeCommit": OPENCODE_COMMIT,
        "opencodeVersion": OPENCODE_VERSION,
        "sherpaCommit": SHERPA_COMMIT,
        "sherpaVersion": SHERPA_VERSION,
        "sherpaWrapperVersion": SHERPA_WRAPPER_VERSION,
    }

    @classmethod
    def _bun_url(cls) -> str:
        return (
            "https://github.com/oven-sh/bun/releases/download/"
            f"bun-v{cls.BUN_VERSION}/bun-darwin-aarch64.zip"
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

    @classmethod
    def _sherpa_wrapper_url(cls) -> str:
        version = cls.SHERPA_WRAPPER_VERSION
        return (
            "https://registry.npmjs.org/sherpa-onnx-node/-/"
            f"sherpa-onnx-node-{version}.tgz"
        )

    @classmethod
    def _node_addon_api_url(cls) -> str:
        version = cls.NODE_ADDON_API_VERSION
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
    ) -> None:
        root = _require_object(root_payload, context="root manifest")
        _require_exact(
            _require_string(root, "version", context="root manifest"),
            cls.VERSION,
            context="root version",
        )
        _require_exact(
            _require_string(root, "packageManager", context="root manifest"),
            f"bun@{cls.BUN_VERSION}",
            context="package manager",
        )
        engines = _require_object(root.get("engines"), context="root engines")
        _require_exact(
            _require_string(engines, "node", context="root engines"),
            cls.NODE_ENGINE,
            context="Node engine",
        )

        electron = _require_object(electron_payload, context="Electron manifest")
        _require_exact(
            _require_string(electron, "version", context="Electron manifest"),
            cls.VERSION,
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
        _require_exact(
            _require_string(
                dev_dependencies,
                "electron",
                context="Electron devDependencies",
            ),
            f"^{cls.ELECTRON_VERSION}",
            context="Electron dependency",
        )

        web = _require_object(web_payload, context="web manifest")
        _require_exact(
            _require_string(web, "version", context="web manifest"),
            cls.VERSION,
            context="web package version",
        )
        _require_exact(
            _dependency(web, "@opencode-ai/sdk", context="web dependencies"),
            cls.OPENCODE_VERSION,
            context="OpenCode SDK dependency",
        )
        _require_exact(
            _dependency(web, "sherpa-onnx-node", context="web dependencies"),
            cls.SHERPA_WRAPPER_VERSION,
            context="sherpa wrapper dependency",
        )

        locked_versions = {
            "@opencode-ai/sdk": cls.OPENCODE_VERSION,
            "electron": cls.ELECTRON_VERSION,
            "sherpa-onnx-node": cls.SHERPA_WRAPPER_VERSION,
            "sherpa-onnx-darwin-arm64": cls.SHERPA_VERSION,
        }
        for package, expected in locked_versions.items():
            _require_exact(
                _locked_package_version(lock_text, package),
                expected,
                context=f"locked {package}",
            )

    @classmethod
    def _validate_companion_manifests(
        cls,
        *,
        opencode_payload: object,
        opencode_hashes_payload: object,
        sherpa_payload: object,
    ) -> str:
        opencode = _require_object(opencode_payload, context="OpenCode manifest")
        _require_exact(
            _require_string(opencode, "packageManager", context="OpenCode manifest"),
            f"bun@{cls.BUN_VERSION}",
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
        _require_exact(
            _dependency(sherpa, "node-addon-api", context="sherpa dependencies"),
            f"^{cls.NODE_ADDON_API_VERSION}",
            context="sherpa node-addon-api dependency",
        )
        return opencode_node_modules_hash

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve and validate the one release supported by this source build."""
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
        self._validate_openchamber_manifests(
            root_payload=root_payload,
            electron_payload=electron_payload,
            web_payload=web_payload,
            lock_text=lock_text,
        )

        opencode_tag = f"v{self.OPENCODE_VERSION}"
        opencode_commit = await self._resolve_tag_commit(
            session,
            owner="anomalyco",
            repo="opencode",
            tag=opencode_tag,
            config=self.config,
        )
        _require_exact(
            opencode_commit,
            self.OPENCODE_COMMIT,
            context="OpenCode commit",
        )
        sherpa_tag = f"v{self.SHERPA_VERSION}"
        sherpa_commit = await self._resolve_tag_commit(
            session,
            owner="k2-fsa",
            repo="sherpa-onnx",
            tag=sherpa_tag,
            config=self.config,
        )
        _require_exact(sherpa_commit, self.SHERPA_COMMIT, context="sherpa commit")

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
        opencode_node_modules_hash = self._validate_companion_manifests(
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
                "bunUrl": self._bun_url(),
                "commit": commit,
                "electronVersion": self.ELECTRON_VERSION,
                "nodeAddonApiUrl": self._node_addon_api_url(),
                "openchamberUrl": openchamber_url,
                "opencodeNodeModulesHash": opencode_node_modules_hash,
                "opencodeUrl": opencode_url,
                "sherpaOnnxUrl": sherpa_url,
                "sherpaOnnxNodeUrl": self._sherpa_wrapper_url(),
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

    @classmethod
    def _exact_bun_expr(cls, *, url: str, hash_value: str) -> FunctionCall:
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
                        value=StringPrimitive(value=cls.BUN_VERSION),
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
        src_hash: str,
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
                            url=bun_url,
                            hash_value=bun_hash,
                        ),
                    ),
                    Binding(
                        name="bunVersion",
                        value=StringPrimitive(value=cls.BUN_VERSION),
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
                    Binding(name="version", value=StringPrimitive(value=cls.VERSION)),
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
            "commit",
            "electronVersion",
            "nodeAddonApiUrl",
            "openchamberUrl",
            "opencodeNodeModulesHash",
            "opencodeUrl",
            "sherpaOnnxUrl",
            "sherpaOnnxNodeUrl",
            "tag",
        )
        result = {
            key: _require_string(metadata, key, context="release metadata")
            for key in keys
        }
        _require_exact(info.version, cls.VERSION, context="release version")
        _require_exact(result["bunUrl"], cls._bun_url(), context="Bun asset URL")
        _require_exact(result["commit"], cls.COMMIT, context="release commit")
        _require_exact(
            result["electronVersion"],
            cls.ELECTRON_VERSION,
            context="Electron version",
        )
        HashEntry.create("nodeModulesHash", result["opencodeNodeModulesHash"])
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
            (self.GITHUB_OWNER, self.GITHUB_REPO, self.COMMIT, "openchamberUrl"),
            ("anomalyco", "opencode", self.OPENCODE_COMMIT, "opencodeUrl"),
            ("k2-fsa", "sherpa-onnx", self.SHERPA_COMMIT, "sherpaOnnxUrl"),
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
                    src_hash=source_hashes[openchamber_url],
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
            "pins": self.source_pins,
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
