"""Updater for the source-built Waku macOS app.

Waku publishes app identity through a Sparkle appcast, while the GPL source is
published separately on GitHub.  There is no signed statement binding an app
archive to a Git tree.  This resolver therefore records an evidence-backed
inference, never a cryptographic attestation: it requires the appcast's semantic
version, deterministic build number, artifact URL, and notes URL; resolves the
matching Git tag to a 40-hex commit; requires that commit's parsed Cargo
manifest to declare the same version; and requires the published notes to equal
the versioned source changelog section.  Any divergence fails closed, and the
vendor ZIP remains evidence rather than package input.
"""

import re
import tomllib
import urllib.parse
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from defusedxml import ElementTree

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from lib.update.derivation_validation import DerivationValidation
from lib.update.net import fetch_github_api, fetch_url, github_raw_url
from lib.update.nix import (
    _build_fetch_from_github_expr,
    _build_package_path_attr_expr,
)
from lib.update.updaters import (
    FixedOutputHashStep,
    UpdateContext,
    Updater,
    VersionInfo,
    register_updater,
    stream_fixed_output_hashes,
)

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element

    import aiohttp

    from lib.update.events import EventStream


APPCAST_URL = "https://releases.waku.sh/appcast.xml"
PROVENANCE_INFERENCE = (
    "inferred from matching Sparkle identity, immutable Git tag commit, "
    "Cargo package version, and byte-equivalent release notes"
)
_SPARKLE_NS = "{http://www.andymatuschak.org/xml-namespaces/sparkle}"
_SEMVER_PATTERN = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)$"
)
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class AppcastRelease:
    """Identity fields from the first (latest) Waku appcast item."""

    version: str
    build: str
    artifact_url: str
    notes_url: str


def sparkle_build_number(version: str) -> str:
    """Return the numeric build convention used by Waku's release script."""
    match = _SEMVER_PATTERN.fullmatch(version)
    if match is None:
        msg = f"Waku release is not a three-component semantic version: {version}"
        raise RuntimeError(msg)
    major, minor, patch = (int(match[group]) for group in ("major", "minor", "patch"))
    return str((major * 1_000_000) + (minor * 1_000) + patch)


def _required_item_text(item: Element, field: str) -> str:
    value = item.findtext(f"{_SPARKLE_NS}{field}")
    if value is None or not value.strip():
        msg = f"Waku appcast item is missing sparkle:{field}"
        raise RuntimeError(msg)
    return value.strip()


def resolve_appcast_release(payload: bytes) -> AppcastRelease:
    """Parse and validate the latest Waku appcast identity."""
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        msg = "Invalid Waku appcast XML"
        raise RuntimeError(msg) from exc

    item = root.find("./channel/item")
    if item is None:
        msg = "Waku appcast contains no release items"
        raise RuntimeError(msg)

    version = _required_item_text(item, "shortVersionString")
    build = _required_item_text(item, "version")
    notes_url = _required_item_text(item, "releaseNotesLink")
    enclosure = item.find("enclosure")
    artifact_url = enclosure.get("url") if enclosure is not None else None
    if artifact_url is None or not artifact_url.strip():
        msg = "Waku appcast item is missing enclosure URL"
        raise RuntimeError(msg)
    artifact_url = artifact_url.strip()

    expected_build = sparkle_build_number(version)
    if build != expected_build:
        msg = f"Waku appcast build {build} does not match {version} build {expected_build}"
        raise RuntimeError(msg)

    expected_artifact_url = f"https://releases.waku.sh/Waku-{version}.zip"
    if artifact_url != expected_artifact_url:
        msg = f"Waku appcast has unexpected artifact URL: {artifact_url}"
        raise RuntimeError(msg)

    expected_notes_url = f"https://releases.waku.sh/Waku-{version}.md"
    if notes_url != expected_notes_url:
        msg = f"Waku appcast has unexpected release-notes URL: {notes_url}"
        raise RuntimeError(msg)

    return AppcastRelease(
        version=version,
        build=build,
        artifact_url=artifact_url,
        notes_url=notes_url,
    )


def manifest_version(payload: bytes) -> str:
    """Return the root Cargo package version from source bytes."""
    try:
        manifest = tomllib.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        msg = "Waku source Cargo.toml is not valid UTF-8 TOML"
        raise RuntimeError(msg) from exc
    package = manifest.get("package")
    if not isinstance(package, dict):
        msg = "Waku source Cargo.toml has no package table"
        raise TypeError(msg)
    version = package.get("version")
    if not isinstance(version, str) or not version:
        msg = "Waku source Cargo.toml has no string package.version"
        raise TypeError(msg)
    return version


def changelog_notes(payload: bytes, version: str) -> str:
    """Return one exact Markdown changelog section from source bytes."""
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        msg = "Waku source CHANGELOG.md is not valid UTF-8"
        raise RuntimeError(msg) from exc

    heading = f"## [{version}]"
    try:
        start = lines.index(heading) + 1
    except ValueError as exc:
        msg = f"Waku source changelog has no {version} section"
        raise RuntimeError(msg) from exc

    end = next(
        (index for index in range(start, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    notes = "\n".join(lines[start:end]).strip()
    if not notes:
        msg = f"Waku source changelog has an empty {version} section"
        raise RuntimeError(msg)
    return notes


def _published_notes(payload: bytes) -> str:
    try:
        notes = payload.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        msg = "Waku published release notes are not valid UTF-8"
        raise RuntimeError(msg) from exc
    if not notes:
        msg = "Waku published release notes are empty"
        raise RuntimeError(msg)
    return notes


@register_updater
class WakuUpdater(Updater):
    """Resolve a release through independent appcast and public-source proofs."""

    name = "waku"
    GITHUB_OWNER = "egoist"
    GITHUB_REPO = "waku"
    DARWIN_PLATFORM: ClassVar[str] = "aarch64-darwin"
    supported_platforms = (DARWIN_PLATFORM,)
    derivation_validations = (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            mode="build",
        ),
    )

    @staticmethod
    def _require_commit(info: VersionInfo) -> str:
        commit = info.commit
        if commit is None or _COMMIT_PATTERN.fullmatch(commit) is None:
            msg = "Waku release metadata is missing an immutable source commit"
            raise RuntimeError(msg)
        return commit

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Correlate the appcast release with its exact public source tree."""
        appcast = await fetch_url(
            session,
            APPCAST_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        release = resolve_appcast_release(appcast)
        tag = f"v{release.version}"
        tag_path = urllib.parse.quote(tag, safe="")
        commit_payload = await fetch_github_api(
            session,
            f"repos/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/commits/{tag_path}",
            config=self.config,
        )
        if not isinstance(commit_payload, dict):
            msg = f"Waku release {tag} has no immutable source commit"
            raise TypeError(msg)
        commit = commit_payload.get("sha")
        if not isinstance(commit, str) or _COMMIT_PATTERN.fullmatch(commit) is None:
            msg = f"Waku release {tag} has no immutable source commit"
            raise RuntimeError(msg)

        manifest = await fetch_url(
            session,
            github_raw_url(self.GITHUB_OWNER, self.GITHUB_REPO, commit, "Cargo.toml"),
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        source_version = manifest_version(manifest)
        if source_version != release.version:
            msg = (
                f"Waku source manifest version {source_version} does not match "
                f"appcast version {release.version}"
            )
            raise RuntimeError(msg)

        changelog = await fetch_url(
            session,
            github_raw_url(self.GITHUB_OWNER, self.GITHUB_REPO, commit, "CHANGELOG.md"),
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        release_notes = await fetch_url(
            session,
            release.notes_url,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        if changelog_notes(changelog, release.version) != _published_notes(
            release_notes
        ):
            msg = "Waku published release notes do not match the source changelog"
            raise RuntimeError(msg)

        return VersionInfo(
            version=release.version,
            metadata={
                "commit": commit,
                "tag": tag,
                "sourceRelationship": PROVENANCE_INFERENCE,
            },
        )

    @classmethod
    def _src_expr(cls, commit: str) -> str:
        return _build_fetch_from_github_expr(
            cls.GITHUB_OWNER,
            cls.GITHUB_REPO,
            rev=commit,
            fetch_submodules=False,
        )

    def _source_override(self, info: VersionInfo, *, src_hash: str) -> SourceEntry:
        return SourceEntry(
            version=info.version,
            commit=self._require_commit(info),
            hashes=HashCollection.from_value([
                HashEntry.create("srcHash", src_hash),
                HashEntry.create("cargoHash", self.config.fake_hash),
            ]),
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Hash the immutable source, then the exact Cargo dependency closure."""
        _ = (session, context)
        commit = self._require_commit(info)
        async for event in stream_fixed_output_hashes(
            self.name,
            steps=(
                FixedOutputHashStep(
                    hash_type="srcHash",
                    error="Missing srcHash output",
                    expr=lambda _resolved: self._src_expr(commit),
                ),
                FixedOutputHashStep(
                    hash_type="cargoHash",
                    error="Missing cargoHash output",
                    expr=lambda resolved: _build_package_path_attr_expr(
                        self.name,
                        ".cargoDeps",
                        system=self.DARWIN_PLATFORM,
                        source_overrides={
                            self.name: self._source_override(
                                info,
                                src_hash=resolved["srcHash"],
                            )
                        },
                    ),
                ),
            ),
            config=self.config,
        ):
            yield event

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist version, immutable source commit, and complete source hashes."""
        return SourceEntry(
            version=info.version,
            commit=self._require_commit(info),
            hashes=HashCollection.from_value(hashes),
        )
