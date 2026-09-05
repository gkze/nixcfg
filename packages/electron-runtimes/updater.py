"""Updater for the shared, exact Electron runtime inventory."""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, override

from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from lib.system_policy import electron_artifact_tags
from lib.update import nix as update_nix
from lib.update import process as update_process
from lib.update.artifacts import GeneratedArtifact
from lib.update.events import (
    EventStream,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_hash_mapping,
    expect_str,
    require_value,
)
from lib.update.nix_expr import compact_nix_expr, select_attrs
from lib.update.paths import updater_dir_for
from lib.update.planner import aggregate_source_members
from lib.update.updaters import (
    UpdateContext,
    Updater,
    VersionInfo,
    ensure_updaters_loaded,
    register_updater,
)
from lib.update.updaters.core import _coerce_context
from lib.update.updaters.metadata import MappingMetadata, metadata_as_mapping

if TYPE_CHECKING:
    from collections.abc import Mapping

    import aiohttp

_INVENTORY_VERSION = "inventory-v1"
_POLICY_SCHEMA_VERSION = 1
_VERSION_PATTERN = re.compile(
    r"^(?P<major>[0-9]+)\.(?P<minor>[0-9]+)\.(?P<patch>[0-9]+)$"
)


@dataclass(frozen=True, slots=True)
class ElectronInventoryMetadata(MappingMetadata):
    """Exact runtime versions derived from all declared source consumers."""

    versions: tuple[str, ...]

    @override
    def to_dict(self) -> dict[str, object]:
        """Expose a JSON-compatible version list through updater metadata."""
        return {"versions": list(self.versions)}


def _version_key(version: str) -> tuple[int, int, int]:
    match = _VERSION_PATTERN.fullmatch(version)
    if match is None:
        msg = f"Electron runtime policy requires exact semver, got {version!r}"
        raise RuntimeError(msg)
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )


def _validate_versions(raw_versions: object) -> tuple[str, ...]:
    if not isinstance(raw_versions, list | tuple):
        msg = "Electron runtime inventory version list must be an array"
        raise TypeError(msg)
    if not raw_versions:
        msg = "Electron runtime inventory requires at least one Electron version"
        raise RuntimeError(msg)
    if not all(isinstance(version, str) for version in raw_versions):
        msg = "Electron runtime inventory version list must contain only strings"
        raise TypeError(msg)
    versions = tuple(raw_versions)
    ordered = sorted(versions, key=_version_key)
    if list(versions) != ordered or len(set(versions)) != len(versions):
        msg = "Electron runtime versions must be unique and strictly increasing"
        raise RuntimeError(msg)
    return versions


@register_updater
class ElectronRuntimesUpdater(Updater):
    """Materialize immutable Electron release artifacts for exact policy versions."""

    name = "electron-runtimes"
    materialize_when_current = True
    generated_artifact_files = ("versions.json",)
    PLATFORMS: ClassVar[dict[str, str]] = electron_artifact_tags()

    @classmethod
    def _consumer_versions(
        cls,
        effective_sources: Mapping[str, SourceEntry],
    ) -> tuple[str, ...]:
        """Derive the runtime inventory from every declared consumer source."""
        consumer_names = aggregate_source_members(ensure_updaters_loaded(), cls.name)
        if not consumer_names:
            msg = "Electron runtime inventory has no registered consumers"
            raise RuntimeError(msg)
        missing_sources = [
            name for name in consumer_names if name not in effective_sources
        ]
        if missing_sources:
            missing = ", ".join(missing_sources)
            msg = f"Electron runtime consumers are missing source metadata: {missing}"
            raise RuntimeError(msg)

        versions: set[str] = set()
        for name in consumer_names:
            entry = effective_sources[name]
            if (entry.pins or {}).get("electronVersion") is not None:
                msg = (
                    f"Electron runtime consumer {name!r} still uses legacy "
                    "pins.electronVersion metadata"
                )
                raise RuntimeError(msg)
            version = entry.electron_version
            if version is None:
                msg = (
                    f"Electron runtime consumer {name!r} does not contribute "
                    "an exact electronVersion"
                )
                raise RuntimeError(msg)
            _version_key(version)
            versions.add(version)
        return tuple(sorted(versions, key=_version_key))

    @staticmethod
    def _require_versions(info: VersionInfo) -> tuple[str, ...]:
        metadata = metadata_as_mapping(
            info.metadata,
            context="Electron runtime inventory metadata",
        )
        try:
            raw_versions = metadata["versions"]
        except KeyError as exc:
            msg = "Electron runtime inventory metadata is missing its version list"
            raise TypeError(msg) from exc
        return _validate_versions(raw_versions)

    @staticmethod
    def _runtime_key(version: str, artifact: str) -> str:
        return f"{version}:{artifact}"

    @classmethod
    def _required_urls(cls, versions: tuple[str, ...]) -> dict[str, str]:
        return {
            cls._runtime_key(version, artifact): cls._artifact_url(version, artifact)
            for version in versions
            for artifact in ("headers", *cls.PLATFORMS)
        }

    @staticmethod
    def _current_hashes(
        context: UpdateContext | SourceEntry | None,
    ) -> dict[str, HashEntry]:
        current = _coerce_context(context).current
        if current is None or current.hashes.entries is None:
            return {}
        hashes: dict[str, HashEntry] = {}
        for entry in current.hashes.entries:
            key = entry.platform
            if entry.hash_type != "sha256" or key is None:
                msg = "Electron runtime inventory contains a malformed hash record"
                raise RuntimeError(msg)
            if key in hashes:
                msg = f"Electron runtime inventory contains duplicate record {key!r}"
                raise RuntimeError(msg)
            hashes[key] = entry
        return hashes

    @staticmethod
    def _current_urls(
        context: UpdateContext | SourceEntry | None,
    ) -> dict[str, str]:
        current = _coerce_context(context).current
        return {} if current is None else current.urls or {}

    @classmethod
    def _binary_url(cls, version: str, platform: str) -> str:
        tag = cls.PLATFORMS[platform]
        return (
            "https://github.com/electron/electron/releases/download/"
            f"v{version}/electron-v{version}-{tag}.zip"
        )

    @staticmethod
    def _headers_url(version: str) -> str:
        return (
            "https://artifacts.electronjs.org/headers/dist/"
            f"v{version}/node-v{version}-headers.tar.gz"
        )

    @classmethod
    def _artifact_url(cls, version: str, artifact: str) -> str:
        if artifact == "headers":
            return cls._headers_url(version)
        return cls._binary_url(version, artifact)

    @classmethod
    def _headers_expr(cls, version: str, url: str) -> str:
        expression = FunctionCall(
            name=select_attrs(Identifier(name="pkgs"), "fetchzip"),
            argument=AttributeSet(
                values=[
                    Binding(
                        name="name",
                        value=StringPrimitive(value=f"electron-{version}-headers"),
                    ),
                    Binding(
                        name="url",
                        value=StringPrimitive(value=url),
                    ),
                    Binding(
                        name="hash",
                        value=select_attrs(
                            Identifier(name="pkgs"),
                            "lib",
                            "fakeHash",
                        ),
                    ),
                ]
            ),
        )
        return compact_nix_expr(expression.rebuild())

    async def fetch_latest(
        self,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> VersionInfo:
        """Derive exact versions from the run's effective consumer sources."""
        _ = session
        versions = self._consumer_versions(_coerce_context(context).effective_sources)
        return VersionInfo(
            version=_INVENTORY_VERSION,
            metadata=ElectronInventoryMetadata(versions=versions),
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Skip multi-gigabyte rehashing when the exact inventory is complete."""
        current = _coerce_context(context).current
        if current is None or current.version != _INVENTORY_VERSION:
            return False
        required_urls = self._required_urls(self._require_versions(info))
        current_hashes = self._current_hashes(context)
        return (
            self._current_urls(context) == required_urls
            and set(current_hashes) == set(required_urls)
            and all(
                not entry.hash.startswith(HashCollection.FAKE_HASH_PREFIX)
                for entry in current_hashes.values()
            )
        )

    @override
    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist the exact URL mapping that owns every artifact hash."""
        return self._build_result_with_urls(
            info,
            hashes,
            self._required_urls(self._require_versions(info)),
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Hash missing runtime binaries and unpacked header trees."""
        _ = session
        versions = self._require_versions(info)
        required_urls = self._required_urls(versions)
        required_keys = tuple(required_urls)
        current_hashes = self._current_hashes(context)
        current_urls = self._current_urls(context)
        reusable = {
            key: entry
            for key, entry in current_hashes.items()
            if key in required_urls
            and current_urls.get(key) == required_urls[key]
            and not entry.hash.startswith(HashCollection.FAKE_HASH_PREFIX)
        }

        binary_urls = {
            self._runtime_key(version, platform): required_urls[
                self._runtime_key(version, platform)
            ]
            for version in versions
            for platform in self.PLATFORMS
            if self._runtime_key(version, platform) not in reusable
        }
        hashes_by_url: dict[str, str] = {}
        if binary_urls:
            binary_drain = ValueDrain[dict[str, str]]()
            async for event in drain_value_events(
                update_process.compute_url_hashes(
                    self.name,
                    binary_urls.values(),
                    config=self.config,
                ),
                binary_drain,
                parse=expect_hash_mapping,
            ):
                yield event
            hashes_by_url = require_value(
                binary_drain,
                "Missing Electron binary hash output",
            )

        resolved = dict(reusable)
        for version in versions:
            header_key = self._runtime_key(version, "headers")
            if header_key not in resolved:
                header_drain = ValueDrain[str]()
                async for event in drain_value_events(
                    update_nix.compute_fixed_output_hash(
                        self.name,
                        self._headers_expr(version, required_urls[header_key]),
                        config=self.config,
                    ),
                    header_drain,
                    parse=expect_str,
                ):
                    yield event
                resolved[header_key] = HashEntry.create(
                    "sha256",
                    require_value(
                        header_drain,
                        f"Missing Electron {version} headers hash output",
                    ),
                    platform=header_key,
                )

            for platform in self.PLATFORMS:
                key = self._runtime_key(version, platform)
                if key in resolved:
                    continue
                url = binary_urls[key]
                try:
                    hash_value = hashes_by_url[url]
                except KeyError as exc:
                    msg = f"Missing Electron binary hash output for {key}"
                    raise RuntimeError(msg) from exc
                resolved[key] = HashEntry.create(
                    "sha256",
                    hash_value,
                    platform=key,
                )

        hashes: SourceHashes = [resolved[key] for key in required_keys]
        package_dir = updater_dir_for(self.name)
        if package_dir is None:
            msg = f"Package directory not found for {self.name}"
            raise RuntimeError(msg)
        yield UpdateEvent.artifact(
            self.name,
            GeneratedArtifact.json(
                package_dir / self.generated_artifact_files[0],
                {
                    "schemaVersion": _POLICY_SCHEMA_VERSION,
                    "versions": list(versions),
                },
            ),
        )
        yield UpdateEvent.value(self.name, hashes)
