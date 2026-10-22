"""Track released AWS CLI v2 sources and their exact AWS CRT dependency."""

import ast
import re
import tomllib
from typing import TYPE_CHECKING

from packaging.requirements import Requirement

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import ignore_event
from lib.update.net import fetch_url, github_raw_url
from lib.update.nix import _build_fetch_from_github_expr, compute_fixed_output_hash
from lib.update.process import compute_url_hashes
from lib.update.updaters import GitHubReleaseUpdater, VersionInfo, register_updater
from lib.update.updaters.metadata import require_metadata_str

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceHashes
    from lib.update.events import EventSink
    from lib.update.updaters import UpdateContext

_V2_VERSION = re.compile(r"2\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")


def release_version(source: bytes) -> str:
    """Read one literal v2 version from the vendor's module without importing it."""
    values = [
        statement.value
        for statement in ast.parse(source).body
        if isinstance(statement, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in statement.targets
        )
    ]
    if (
        len(values) != 1
        or not isinstance(values[0], ast.Constant)
        or not isinstance(values[0].value, str)
        or _V2_VERSION.fullmatch(values[0].value) is None
    ):
        msg = "AWS CLI v2 must declare one literal stable version"
        raise ValueError(msg)
    return values[0].value


def exact_dependency_version(requirements: list[Requirement], name: str) -> str:
    """Require a single unconditional exact dependency pin."""
    matches = [requirement for requirement in requirements if requirement.name == name]
    if len(matches) != 1 or matches[0].marker or matches[0].url or matches[0].extras:
        msg = f"AWS CLI must declare one unconditional {name} requirement"
        raise ValueError(msg)
    specifiers = list(matches[0].specifier)
    if (
        len(specifiers) != 1
        or specifiers[0].operator != "=="
        or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", specifiers[0].version) is None
    ):
        msg = f"AWS CLI must pin one exact {name} release"
        raise ValueError(msg)
    return specifiers[0].version


def crt_version(source: bytes) -> str:
    """Read the runtime CRT pin from the selected CLI release."""
    return exact_dependency_version(
        [
            Requirement(value)
            for value in tomllib.loads(source.decode())["project"]["dependencies"]
        ],
        "awscrt",
    )


def localstub_version(source: bytes) -> str:
    """Read the test-only local HTTP server pin from upstream requirements."""
    return exact_dependency_version(
        [
            Requirement(line)
            for raw in source.decode().splitlines()
            if (line := raw.strip()) and not line.startswith("#")
        ],
        "localstub",
    )


@register_updater
class AwsCli2CurrentUpdater(GitHubReleaseUpdater):
    """Use AWS's v2 release branch, then require its corresponding immutable tag."""

    name = "awscli2-current"
    GITHUB_OWNER = "aws"
    GITHUB_REPO = "aws-cli"
    TAG_PREFIX = ""
    RESOLVE_TAG_COMMIT = True
    supported_platforms = ("aarch64-darwin",)
    derivation_validations = (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}",
            mode="build",
            systems=("aarch64-darwin",),
        ),
    )

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Resolve the released tag; AWS does not publish GitHub release objects."""
        _ = context
        source = await fetch_url(
            session,
            github_raw_url("aws", "aws-cli", "v2", "awscli/__init__.py"),
            config=self.config,
        )
        version = release_version(source)
        commit = await self._resolve_release_tag_commit(session, version)
        dependencies = await fetch_url(
            session,
            github_raw_url("aws", "aws-cli", commit, "pyproject.toml"),
            config=self.config,
        )
        test_dependencies = await fetch_url(
            session,
            github_raw_url("aws", "aws-cli", commit, "requirements-test-blackbox.txt"),
            config=self.config,
        )
        return VersionInfo(
            version=version,
            metadata={
                "commit": commit,
                "awscrtVersion": crt_version(dependencies),
                "localstubVersion": localstub_version(test_dependencies),
            },
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext,
        emit: EventSink = ignore_event,
    ) -> SourceHashes:
        """Hash the immutable source and the exact required CRT source archive."""
        _ = (session, context)
        commit = self._require_commit(info)
        urls = []
        for name in ("awscrt", "localstub"):
            version = require_metadata_str(
                info.metadata, f"{name}Version", context="AWS dependency"
            )
            urls.append(
                f"https://files.pythonhosted.org/packages/source/{name[0]}/{name}/{name}-{version}.tar.gz"
            )
        source_hash = await compute_fixed_output_hash(
            self.name,
            _build_fetch_from_github_expr("aws", "aws-cli", rev=commit),
            config=self.config,
            emit=emit,
        )
        hashes = await compute_url_hashes(
            self.name, urls, config=self.config, emit=emit
        )
        return [
            HashEntry.create("srcHash", source_hash),
            *(HashEntry.create("sha256", hashes[url], url=url) for url in urls),
        ]

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Keep the CLI and CRT identities together for the package-local override."""
        return SourceEntry(
            version=info.version,
            commit=self._require_commit(info),
            hashes=HashCollection.from_value(hashes),
            pins={
                key: require_metadata_str(info.metadata, key, context="AWS dependency")
                for key in ("awscrtVersion", "localstubVersion")
            },
        )
