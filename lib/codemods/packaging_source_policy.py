"""Policy audits for package and overlay source modifications."""

import ast
import re
from dataclasses import dataclass, fields, is_dataclass
from typing import TYPE_CHECKING, Final
from urllib.parse import parse_qsl, unquote, urlsplit

from nix_manipulator import parse
from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.path import NixPath
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.raw import RawExpression
from nix_manipulator.expressions.select import Select

from lib.check_python_compile import iter_target_paths
from lib.codemods.errors import CodemodError
from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from pathlib import Path

    from nix_manipulator.expressions.expression import NixExpression

type NixSubstituteSite = tuple[str, str]
type PythonRewriteSite = tuple[str, str]

_NIX_AST_NON_SEMANTIC_FIELDS: Final = frozenset({
    "after",
    "before",
    "inner_trivia",
    "scope",
})
_SRI_HASH_PATTERN: Final = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?P<algorithm>md5|sha1|sha256|sha512)-"
    r"(?P<digest>[A-Za-z0-9+/]+={0,2})"
    r"(?![A-Za-z0-9+/=])",
)
_RAW_HEX_DIGEST_PATTERN: Final = re.compile(
    r"(?<![0-9A-Fa-f])(?P<digest>[0-9A-Fa-f]{128}|[0-9A-Fa-f]{64}|[0-9A-Fa-f]{40}|[0-9A-Fa-f]{32})"
    r"(?![0-9A-Fa-f])",
)
_NIX_BASE32_DIGEST_PATTERN: Final = re.compile(
    r"(?<![0-9abcdfghijklmnpqrsvwxyz])"
    r"(?P<digest>[0-9abcdfghijklmnpqrsvwxyz]{103}|"
    r"[0-9abcdfghijklmnpqrsvwxyz]{52}|"
    r"[0-9abcdfghijklmnpqrsvwxyz]{32}|"
    r"[0-9abcdfghijklmnpqrsvwxyz]{26})"
    r"(?![0-9abcdfghijklmnpqrsvwxyz])",
)
_RAW_BASE64_DIGEST_PATTERN: Final = re.compile(
    r"(?<![A-Za-z0-9+/=])"
    r"(?P<digest>[A-Za-z0-9+/]{86}(?:==)?|"
    r"[A-Za-z0-9+/]{43}=?|"
    r"[A-Za-z0-9+/]{27}=?|"
    r"[A-Za-z0-9+/]{22}(?:==)?)"
    r"(?![A-Za-z0-9+/=])",
)
_RAW_DIGEST_ALGORITHM_BY_FORMAT_AND_LENGTH: Final = {
    ("hex", 32): "md5",
    ("hex", 40): "sha1",
    ("hex", 64): "sha256",
    ("hex", 128): "sha512",
    ("nix-base32", 26): "md5",
    ("nix-base32", 32): "sha1",
    ("nix-base32", 52): "sha256",
    ("nix-base32", 103): "sha512",
    ("base64", 22): "md5",
    ("base64", 27): "sha1",
    ("base64", 43): "sha256",
    ("base64", 86): "sha512",
}
_UNCONTEXTUAL_RAW_DIGEST_FORMATS: Final = frozenset({
    ("hex", "sha256"),
    ("hex", "sha512"),
    ("nix-base32", "sha256"),
})
_BINDING_SEGMENT_PATTERN: Final = re.compile(r'"(?:[^"\\]|\\.)*"|[^.]+')
_HASH_BINDING_SUFFIXES: Final = (
    "checksum",
    "checksums",
    "digest",
    "digests",
    "hash",
    "hashes",
)
_HASH_ALGORITHMS: Final = ("sha512", "sha256", "sha1", "md5")
_NIX_STORE_PATH_PATTERN: Final = re.compile(
    r"/nix/store/[0-9abcdfghijklmnpqrsvwxyz]{32}-[A-Za-z0-9+._?=-]+",
)
_COMMIT_PATTERN: Final = re.compile(
    r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{40}(?![0-9A-Fa-f])",
)
_SHORT_COMMIT_PATTERN: Final = re.compile(r"[0-9A-Fa-f]{7,39}")
_VERSION_PATTERN: Final = re.compile(
    r"(?<![A-Za-z0-9])v?\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.-]+)?(?![A-Za-z0-9])",
)
_SOURCE_VERSION_LITERAL_PATTERN: Final = re.compile(
    r"v?\d+(?:\.\d+){0,2}(?:[-+][A-Za-z0-9.-]+)?",
)
_VERSION_BINDING_ASSIGNMENT_PATTERN: Final = re.compile(
    r'\b[A-Za-z0-9_-]*[Vv]ersion(?:s)?\s*(?:=|\?)\s*"v?\d+"',
)
_PACKAGE_SPEC_PATTERN: Final = re.compile(
    r"(?<![A-Za-z0-9._/-])"
    r"(?:@[A-Za-z0-9._-]+/)?[A-Za-z][A-Za-z0-9._-]*"
    r"(?:@|==)v?\d+(?:\.\d+)+(?:[-+]?[A-Za-z][A-Za-z0-9.-]*)?"
    r"(?![A-Za-z0-9.-])",
)
_PACKAGE_MANIFEST_SPEC_PATTERN: Final = re.compile(
    r'["\'](?P<package>(?:@[A-Za-z0-9._-]+/)?[A-Za-z][A-Za-z0-9._-]*)["\']'
    r"\s*:\s*"
    r'["\'](?P<constraint>[~^]v?\d+(?:\.\d+)+(?:[-+]?[A-Za-z][A-Za-z0-9.-]*)?)["\']',
)
_SOURCE_REFERENCE_PATTERN: Final = re.compile(r"(?<![A-Za-z0-9_-])sources\s*\.")
_REFERENCE_PIN_FIELD_PATTERN: Final = re.compile(
    r"\b(?:commit|gitRev|ref|rev|revision|sourceRev|sourceRevision|tag)\s*=\s*\"",
)
_REFERENCE_PIN_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+:-]*")
_SELECTED_VERSION_PATTERN: Final = re.compile(
    r'(?:^|\.)"(?P<version>v?\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.-]+)?)"'
    r"(?:\.|$)",
)
_REFERENCE_PIN_BINDINGS: Final = frozenset(
    {
        "commit",
        "gitrev",
        "ref",
        "rev",
        "revision",
        "sourcerev",
        "sourcerevision",
        "tag",
    },
)
_TRAILING_FORMAL_COMMA_PATTERN: Final = re.compile(
    r",(?=\s*}(?:@[A-Za-z_][A-Za-z0-9_'-]*)?\s*:)",
)
_HTTP_URL_PATTERN: Final = re.compile(r"https?://")
_SOURCE_ARCHIVE_SUFFIXES: Final = (
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tar.zst",
    ".tgz",
    ".tbz2",
    ".txz",
    ".zip",
)
_SOURCE_URL_PATH_MARKERS: Final = frozenset({
    "archive",
    "archives",
    "download",
    "downloads",
    "releases",
    "tags",
    "tarball",
    "zipball",
})
_SOURCE_URL_REFERENCE_KEYS: Final = frozenset({
    "commit",
    "ref",
    "rev",
    "revision",
    "tag",
})
_SOURCE_URL_BINDINGS: Final = frozenset({
    "archiveurl",
    "downloadurl",
    "sourceurl",
    "srcurl",
    "tarballurl",
    "url",
    "urls",
})

SUBSTITUTE_IN_PLACE_PATTERN: Final = re.compile(r"\bsubstituteInPlace\b")
PYTHON_AD_HOC_REWRITE_ATTRS: Final = frozenset({"replace", "sub", "subn"})
SOURCE_PATCH_SCRIPT_NAMES: Final = frozenset(
    {
        "normalize_cargo_nix.py",
        "patch_allocator_weak_linkage.py",
        "patch_node_addon_api.py",
        "patch_node_addon_api_binding_gyp.py",
        "patch_source.py",
        "patch_sources.py",
    },
)


def _rewrite_function_formals_for_parser(text: str) -> str:
    return _TRAILING_FORMAL_COMMA_PATTERN.sub("", text)


def parse_nix_expr_for_policy(source: str, *, context: str) -> NixExpression:
    """Parse a Nix expression for source-modification policy audits."""
    parsed = parse(source)
    if parsed.contains_error:
        parsed = parse(_rewrite_function_formals_for_parser(source))
    if parsed.contains_error or parsed.expr is None:
        msg = f"Unable to parse Nix source for policy audit: {context}"
        raise CodemodError(msg)
    return parsed.expr


@dataclass(frozen=True, order=True)
class NixSourceIdentitySite:
    """One literal source or attestation identity embedded in Nix."""

    path: str
    binding: str
    kind: str
    value: str


def _nix_string_payloads(
    value: object,
    *,
    binding: str = "<expression>",
    seen: set[int] | None = None,
) -> tuple[tuple[str, str], ...]:
    """Return string payloads from a parsed Nix AST with their owning binding."""
    visited = set() if seen is None else seen
    if isinstance(value, str | bytes | int | float | bool) or value is None:
        payloads: tuple[tuple[str, str], ...] = ()
    elif id(value) in visited:
        payloads = ()
    elif isinstance(value, Binding):
        visited.add(id(value))
        child_binding = (
            value.name if binding == "<expression>" else f"{binding}.{value.name}"
        )
        binding_name_payloads = (
            ((child_binding, value.name),) if value.name.startswith('"') else ()
        )
        payloads = binding_name_payloads + _nix_string_payloads(
            value.value,
            binding=child_binding,
            seen=visited,
        )
    elif isinstance(value, Identifier):
        visited.add(id(value))
        payloads = (
            _nix_string_payloads(
                value.default_value,
                binding=value.name if binding == "<expression>" else binding,
                seen=visited,
            )
            if value.default_value is not None
            else ()
        )
    elif isinstance(value, StringPrimitive | IndentedString):
        visited.add(id(value))
        payloads = ((binding, value.value),)
    elif isinstance(value, NixPath):
        visited.add(id(value))
        payloads = ((binding, value.path),)
    elif isinstance(value, RawExpression):
        visited.add(id(value))
        payloads = ((binding, value.text),)
    elif isinstance(value, dict):
        visited.add(id(value))
        payloads = tuple(
            payload
            for item in value.values()
            for payload in _nix_string_payloads(item, binding=binding, seen=visited)
        )
    elif isinstance(value, list | tuple):
        visited.add(id(value))
        payloads = tuple(
            payload
            for item in value
            for payload in _nix_string_payloads(item, binding=binding, seen=visited)
        )
    elif is_dataclass(value):
        visited.add(id(value))
        payloads = tuple(
            payload
            for field in fields(value)
            if field.name not in _NIX_AST_NON_SEMANTIC_FIELDS
            for payload in _nix_string_payloads(
                getattr(value, field.name),
                binding=binding,
                seen=visited,
            )
        )
    else:
        payloads = ()
    return payloads


def _strip_source_archive_suffix(value: str) -> str:
    """Remove one recognized source-archive suffix from a URL path segment."""
    lowered = value.casefold()
    for suffix in _SOURCE_ARCHIVE_SUFFIXES:
        if lowered.endswith(suffix):
            return value[: -len(suffix)]
    return value


def _normalized_binding_segments(binding: str) -> tuple[str, ...]:
    """Return normalized Nix attribute segments without splitting quoted dots."""
    return tuple(
        match.group(0).strip('"').casefold().replace("-", "").replace("_", "")
        for match in _BINDING_SEGMENT_PATTERN.finditer(binding)
    )


def _binding_is_cryptographic_identity(binding: str) -> bool:
    """Return whether a binding identifies a signing key or fingerprint."""
    segments = set(_normalized_binding_segments(binding))
    return bool(segments & {"gpg", "pgp"}) or any(
        segment.endswith(("fingerprint", "fingerprints")) for segment in segments
    )


def _binding_hash_owner(binding: str) -> str | None:
    """Return the first attribute segment that names a hash value or container."""
    return next(
        (
            segment
            for segment in _normalized_binding_segments(binding)
            if any(algorithm in segment for algorithm in _HASH_ALGORITHMS)
            or segment.endswith(_HASH_BINDING_SUFFIXES)
        ),
        None,
    )


def _binding_hash_algorithm(binding: str) -> str | None:
    """Return an algorithm named by the hash-owning attribute, when present."""
    owner = _binding_hash_owner(binding)
    if owner is None:
        return None
    return next(
        (algorithm for algorithm in _HASH_ALGORITHMS if algorithm in owner),
        None,
    )


def _binding_owns_hash(binding: str) -> bool:
    """Return whether a binding names a digest value or container."""
    return _binding_hash_owner(binding) is not None


def _raw_digest_identities(binding: str, payload: str) -> tuple[tuple[str, str], ...]:
    """Return raw digest kinds and values without store-path submatches."""
    if _binding_is_cryptographic_identity(binding):
        return ()
    excluded_spans = tuple(
        match.span() for match in _NIX_STORE_PATH_PATTERN.finditer(payload)
    ) + tuple(match.span() for match in _SRI_HASH_PATTERN.finditer(payload))
    candidates: dict[tuple[int, int, str], set[tuple[str, str]]] = {}
    for encoding, pattern in (
        ("hex", _RAW_HEX_DIGEST_PATTERN),
        ("nix-base32", _NIX_BASE32_DIGEST_PATTERN),
        ("base64", _RAW_BASE64_DIGEST_PATTERN),
    ):
        for match in pattern.finditer(payload):
            start, end = match.span()
            if any(
                start < store_end and store_start < end
                for store_start, store_end in excluded_spans
            ):
                continue
            digest = match.group("digest")
            digest_length = (
                len(digest.rstrip("=")) if encoding == "base64" else len(digest)
            )
            algorithm = _RAW_DIGEST_ALGORITHM_BY_FORMAT_AND_LENGTH[
                (encoding, digest_length)
            ]
            candidates.setdefault((start, end, digest), set()).add((
                encoding,
                algorithm,
            ))

    algorithm_hint = _binding_hash_algorithm(binding)
    binding_owns_hash = _binding_owns_hash(binding)
    identities: set[tuple[str, str]] = set()
    for (_, _, digest), formats in candidates.items():
        if algorithm_hint is not None:
            selected = {
                digest_format
                for digest_format in formats
                if digest_format[1] == algorithm_hint
            }
        elif binding_owns_hash:
            selected = formats
        else:
            selected = formats & _UNCONTEXTUAL_RAW_DIGEST_FORMATS

        if len(selected) > 1:
            identities.add(("raw-digest", digest))
        else:
            identities.update(
                (f"{encoding}-{algorithm}", digest) for encoding, algorithm in selected
            )
    return tuple(sorted(identities))


def _source_url_pins(binding: str, payload: str) -> tuple[tuple[str, str], ...]:
    """Return versions and revisions encoded in a literal source archive URL."""
    leaf = binding.rsplit(".", maxsplit=1)[-1].strip('"').casefold()
    normalized_leaf = leaf.replace("-", "").replace("_", "")
    if normalized_leaf not in _SOURCE_URL_BINDINGS or not payload.startswith((
        "http://",
        "https://",
    )):
        return ()

    parsed = urlsplit(payload)
    path = unquote(parsed.path)
    path_segments = tuple(segment for segment in path.split("/") if segment)
    lowered_segments = {segment.casefold() for segment in path_segments}
    archive_like = any(
        path.casefold().endswith(suffix) for suffix in _SOURCE_ARCHIVE_SUFFIXES
    ) or bool(lowered_segments & _SOURCE_URL_PATH_MARKERS)
    if not archive_like:
        return ()

    pins: set[tuple[str, str]] = {
        ("version-pin", match.group(0)) for match in _VERSION_PATTERN.finditer(path)
    }
    revision_candidates = {
        _strip_source_archive_suffix(segment) for segment in path_segments
    }
    revision_candidates.update(
        value
        for key, value in parse_qsl(parsed.query, keep_blank_values=False)
        if key.casefold() in _SOURCE_URL_REFERENCE_KEYS
    )
    if parsed.fragment:
        revision_candidates.add(parsed.fragment)
    pins.update(
        ("commit-pin", candidate)
        for candidate in revision_candidates
        if _SHORT_COMMIT_PATTERN.fullmatch(candidate) is not None
    )
    return tuple(sorted(pins))


def _nix_source_references(
    value: object,
    *,
    binding: str = "<expression>",
    seen: set[int] | None = None,
) -> tuple[tuple[str, str], ...]:
    """Return direct ``sources.<name>`` references and owning bindings."""
    visited = set() if seen is None else seen
    if isinstance(value, str | bytes | int | float | bool) or value is None:
        references: tuple[tuple[str, str], ...] = ()
    elif id(value) in visited:
        references = ()
    elif isinstance(value, Binding):
        visited.add(id(value))
        child_binding = (
            value.name if binding == "<expression>" else f"{binding}.{value.name}"
        )
        references = _nix_source_references(
            value.value,
            binding=child_binding,
            seen=visited,
        )
    elif isinstance(value, Select) and isinstance(value.expression, Identifier):
        visited.add(id(value))
        source_name = _first_static_attr_segment(value.attribute)
        direct = (
            ((binding, source_name),)
            if value.expression.name == "sources" and source_name is not None
            else ()
        )
        references = direct + tuple(
            reference
            for field in fields(value)
            if field.name not in _NIX_AST_NON_SEMANTIC_FIELDS
            for reference in _nix_source_references(
                getattr(value, field.name),
                binding=binding,
                seen=visited,
            )
        )
    elif isinstance(value, dict):
        visited.add(id(value))
        references = tuple(
            reference
            for item in value.values()
            for reference in _nix_source_references(
                item,
                binding=binding,
                seen=visited,
            )
        )
    elif isinstance(value, list | tuple):
        visited.add(id(value))
        references = tuple(
            reference
            for item in value
            for reference in _nix_source_references(
                item,
                binding=binding,
                seen=visited,
            )
        )
    elif is_dataclass(value):
        visited.add(id(value))
        references = tuple(
            reference
            for field in fields(value)
            if field.name not in _NIX_AST_NON_SEMANTIC_FIELDS
            for reference in _nix_source_references(
                getattr(value, field.name),
                binding=binding,
                seen=visited,
            )
        )
    else:
        references = ()
    return references


def _first_static_attr_segment(attribute: str) -> str | None:
    """Return the first static segment from a rendered Nix attr path."""
    if not attribute or attribute.startswith("${"):
        return None
    if not attribute.startswith('"'):
        return attribute.split(".", maxsplit=1)[0]

    closing_quote = attribute.find('"', 1)
    if closing_quote < 0:
        return None
    return attribute[1:closing_quote]


def _nix_selected_versions(
    value: object,
    *,
    binding: str = "<expression>",
    seen: set[int] | None = None,
) -> tuple[tuple[str, str], ...]:
    """Return literal version attributes selected from Nix attrsets."""
    visited = set() if seen is None else seen
    if isinstance(value, str | bytes | int | float | bool) or value is None:
        versions: tuple[tuple[str, str], ...] = ()
    elif id(value) in visited:
        versions = ()
    elif isinstance(value, Binding):
        visited.add(id(value))
        child_binding = (
            value.name if binding == "<expression>" else f"{binding}.{value.name}"
        )
        versions = _nix_selected_versions(
            value.value,
            binding=child_binding,
            seen=visited,
        )
    elif isinstance(value, Select):
        visited.add(id(value))
        direct = tuple(
            (binding, match.group("version"))
            for match in _SELECTED_VERSION_PATTERN.finditer(value.attribute)
        )
        versions = direct + tuple(
            version
            for field in fields(value)
            if field.name not in _NIX_AST_NON_SEMANTIC_FIELDS
            for version in _nix_selected_versions(
                getattr(value, field.name),
                binding=binding,
                seen=visited,
            )
        )
    elif isinstance(value, dict):
        visited.add(id(value))
        versions = tuple(
            version
            for item in value.values()
            for version in _nix_selected_versions(
                item,
                binding=binding,
                seen=visited,
            )
        )
    elif isinstance(value, list | tuple):
        visited.add(id(value))
        versions = tuple(
            version
            for item in value
            for version in _nix_selected_versions(
                item,
                binding=binding,
                seen=visited,
            )
        )
    elif is_dataclass(value):
        visited.add(id(value))
        versions = tuple(
            version
            for field in fields(value)
            if field.name not in _NIX_AST_NON_SEMANTIC_FIELDS
            for version in _nix_selected_versions(
                getattr(value, field.name),
                binding=binding,
                seen=visited,
            )
        )
    else:
        versions = ()
    return versions


def _nix_literal_version_comparisons(
    value: object,
    *,
    binding: str = "<expression>",
    seen: set[int] | None = None,
) -> tuple[tuple[str, str], ...]:
    """Return literal comparisons against a Nix ``*.version`` attribute."""
    visited = set() if seen is None else seen
    if isinstance(value, str | bytes | int | float | bool) or value is None:
        comparisons: tuple[tuple[str, str], ...] = ()
    elif id(value) in visited:
        comparisons = ()
    elif isinstance(value, Binding):
        visited.add(id(value))
        child_binding = (
            value.name if binding == "<expression>" else f"{binding}.{value.name}"
        )
        comparisons = _nix_literal_version_comparisons(
            value.value,
            binding=child_binding,
            seen=visited,
        )
    elif isinstance(value, BinaryExpression):
        visited.add(id(value))
        operands = ((value.left, value.right), (value.right, value.left))
        direct = tuple(
            (binding, literal.value)
            for selected, literal in operands
            if value.operator.name == "=="
            and isinstance(selected, Select)
            and selected.attribute.casefold().endswith("version")
            and isinstance(literal, StringPrimitive)
            and _VERSION_PATTERN.fullmatch(literal.value) is not None
        )
        comparisons = direct + tuple(
            comparison
            for field in fields(value)
            if field.name not in _NIX_AST_NON_SEMANTIC_FIELDS
            for comparison in _nix_literal_version_comparisons(
                getattr(value, field.name),
                binding=binding,
                seen=visited,
            )
        )
    elif isinstance(value, dict):
        visited.add(id(value))
        comparisons = tuple(
            comparison
            for item in value.values()
            for comparison in _nix_literal_version_comparisons(
                item,
                binding=binding,
                seen=visited,
            )
        )
    elif isinstance(value, list | tuple):
        visited.add(id(value))
        comparisons = tuple(
            comparison
            for item in value
            for comparison in _nix_literal_version_comparisons(
                item,
                binding=binding,
                seen=visited,
            )
        )
    elif is_dataclass(value):
        visited.add(id(value))
        comparisons = tuple(
            comparison
            for field in fields(value)
            if field.name not in _NIX_AST_NON_SEMANTIC_FIELDS
            for comparison in _nix_literal_version_comparisons(
                getattr(value, field.name),
                binding=binding,
                seen=visited,
            )
        )
    else:
        comparisons = ()
    return comparisons


@dataclass(frozen=True)
class NixSourceIdentityAudit:
    """Find literal source identities in handwritten production Nix."""

    root: Path = REPO_ROOT
    roots: tuple[Path, ...] = ()
    excluded_paths: frozenset[Path] = frozenset()

    def current_hash_sites(self) -> tuple[NixSourceIdentitySite, ...]:
        """Return every literal fixed-output identity in audited Nix."""
        sites: set[NixSourceIdentitySite] = set()
        for path in self._files():
            relative_path = path.resolve().relative_to(self.root.resolve()).as_posix()
            expression = parse_nix_expr_for_policy(
                path.read_text(encoding="utf-8"),
                context=relative_path,
            )
            for binding, payload in _nix_string_payloads(expression):
                for match in _NIX_STORE_PATH_PATTERN.finditer(payload):
                    sites.add(
                        NixSourceIdentitySite(
                            path=relative_path,
                            binding=binding,
                            kind="nix-store-path",
                            value=match.group(0),
                        ),
                    )
                for match in _SRI_HASH_PATTERN.finditer(payload):
                    sites.add(
                        NixSourceIdentitySite(
                            path=relative_path,
                            binding=binding,
                            kind=f"sri-{match.group('algorithm')}",
                            value=match.group(0),
                        ),
                    )
                for kind, digest in _raw_digest_identities(binding, payload):
                    sites.add(
                        NixSourceIdentitySite(
                            path=relative_path,
                            binding=binding,
                            kind=kind,
                            value=digest,
                        ),
                    )
        return tuple(sorted(sites))

    def current_pin_sites(self) -> tuple[NixSourceIdentitySite, ...]:
        """Return literal source versions and commits in audited Nix expressions."""
        sites: set[NixSourceIdentitySite] = set()
        for path in self._pin_files():
            relative_path = path.resolve().relative_to(self.root.resolve()).as_posix()
            expression = parse_nix_expr_for_policy(
                path.read_text(encoding="utf-8"),
                context=relative_path,
            )
            for binding, payload in _nix_string_payloads(expression):
                commit_matches = (
                    ()
                    if self._is_cryptographic_identity_binding(binding)
                    or self._is_hash_binding(binding)
                    else _COMMIT_PATTERN.finditer(payload)
                )
                for match in commit_matches:
                    sites.add(
                        NixSourceIdentitySite(
                            path=relative_path,
                            binding=binding,
                            kind="commit-pin",
                            value=match.group(0),
                        ),
                    )
                for kind, value in _source_url_pins(binding, payload):
                    sites.add(
                        NixSourceIdentitySite(
                            path=relative_path,
                            binding=binding,
                            kind=kind,
                            value=value,
                        ),
                    )
                if (
                    self._is_reference_pin_binding(binding)
                    and _COMMIT_PATTERN.fullmatch(payload) is None
                    and _REFERENCE_PIN_PATTERN.fullmatch(payload) is not None
                ):
                    sites.add(
                        NixSourceIdentitySite(
                            path=relative_path,
                            binding=binding,
                            kind="reference-pin",
                            value=payload,
                        ),
                    )
                version_payload = payload.strip('"')
                if self._is_source_version_binding(binding) and (
                    match := _SOURCE_VERSION_LITERAL_PATTERN.fullmatch(version_payload)
                ):
                    sites.add(
                        NixSourceIdentitySite(
                            path=relative_path,
                            binding=binding,
                            kind="version-pin",
                            value=match.group(0),
                        ),
                    )
            for binding, version in _nix_literal_version_comparisons(expression):
                sites.add(
                    NixSourceIdentitySite(
                        path=relative_path,
                        binding=binding,
                        kind="version-pin",
                        value=version,
                    ),
                )
            for binding, version in _nix_selected_versions(expression):
                sites.add(
                    NixSourceIdentitySite(
                        path=relative_path,
                        binding=binding,
                        kind="version-pin",
                        value=version,
                    ),
                )
        return tuple(sorted(sites))

    def current_package_spec_sites(self) -> tuple[NixSourceIdentitySite, ...]:
        """Return exact npm/Python package specs embedded in audited Nix."""
        sites: set[NixSourceIdentitySite] = set()
        for path in self._package_spec_files():
            relative_path = path.resolve().relative_to(self.root.resolve()).as_posix()
            expression = parse_nix_expr_for_policy(
                path.read_text(encoding="utf-8"),
                context=relative_path,
            )
            for binding, payload in _nix_string_payloads(expression):
                for match in _PACKAGE_SPEC_PATTERN.finditer(payload):
                    sites.add(
                        NixSourceIdentitySite(
                            path=relative_path,
                            binding=binding,
                            kind="package-spec-pin",
                            value=match.group(0),
                        ),
                    )
                for match in _PACKAGE_MANIFEST_SPEC_PATTERN.finditer(payload):
                    sites.add(
                        NixSourceIdentitySite(
                            path=relative_path,
                            binding=binding,
                            kind="package-spec-pin",
                            value=(
                                f"{match.group('package')}@{match.group('constraint')}"
                            ),
                        ),
                    )
        return tuple(sorted(sites))

    def current_source_reference_sites(self) -> tuple[NixSourceIdentitySite, ...]:
        """Return direct references to named updater source metadata."""
        sites: set[NixSourceIdentitySite] = set()
        for path in self._source_reference_files():
            relative_path = path.resolve().relative_to(self.root.resolve()).as_posix()
            expression = parse_nix_expr_for_policy(
                path.read_text(encoding="utf-8"),
                context=relative_path,
            )
            for binding, source_name in _nix_source_references(expression):
                sites.add(
                    NixSourceIdentitySite(
                        path=relative_path,
                        binding=binding,
                        kind="source-reference",
                        value=source_name,
                    ),
                )
        return tuple(sorted(sites))

    def _source_reference_files(self) -> tuple[Path, ...]:
        roots = self._audit_roots()
        excluded = {path.resolve() for path in self.excluded_paths}
        return tuple(
            sorted(
                path
                for source_root in roots
                if source_root.is_dir()
                for path in source_root.rglob("*.nix")
                if "tests" not in path.relative_to(source_root).parts
                if path.name != "flake.nix"
                if path.resolve() not in excluded
                if _SOURCE_REFERENCE_PATTERN.search(path.read_text(encoding="utf-8"))
            ),
        )

    def _files(self) -> tuple[Path, ...]:
        roots = self._audit_roots()
        excluded = {path.resolve() for path in self.excluded_paths}
        return tuple(
            sorted(
                path
                for source_root in roots
                if source_root.is_dir()
                for path in source_root.rglob("*.nix")
                if "tests" not in path.relative_to(source_root).parts
                if path.name != "flake.nix"
                if path.resolve() not in excluded
                if self._might_contain_hash(path)
            ),
        )

    def _pin_files(self) -> tuple[Path, ...]:
        roots = self._audit_roots()
        excluded = {path.resolve() for path in self.excluded_paths}
        return tuple(
            sorted(
                path
                for source_root in roots
                if source_root.is_dir()
                for path in source_root.rglob("*.nix")
                if "tests" not in path.relative_to(source_root).parts
                if path.name != "flake.nix"
                if path.resolve() not in excluded
                if self._might_contain_pin(path)
            ),
        )

    def _package_spec_files(self) -> tuple[Path, ...]:
        roots = self._audit_roots()
        excluded = {path.resolve() for path in self.excluded_paths}
        return tuple(
            sorted(
                path
                for source_root in roots
                if source_root.is_dir()
                for path in source_root.rglob("*.nix")
                if "tests" not in path.relative_to(source_root).parts
                if path.name != "flake.nix"
                if path.resolve() not in excluded
                if (
                    _PACKAGE_SPEC_PATTERN.search(path.read_text(encoding="utf-8"))
                    or _PACKAGE_MANIFEST_SPEC_PATTERN.search(
                        path.read_text(encoding="utf-8")
                    )
                )
            ),
        )

    def _audit_roots(self) -> tuple[Path, ...]:
        return self.roots or (
            self.root / "packages",
            self.root / "overlays",
            self.root / "lib",
            self.root / "home",
            self.root / "modules",
        )

    @staticmethod
    def _might_contain_hash(path: Path) -> bool:
        """Route only digest-bearing files through the semantic parser."""
        source = path.read_text(encoding="utf-8")
        return bool(
            _SRI_HASH_PATTERN.search(source)
            or _RAW_HEX_DIGEST_PATTERN.search(source)
            or _NIX_BASE32_DIGEST_PATTERN.search(source)
            or _RAW_BASE64_DIGEST_PATTERN.search(source)
            or _NIX_STORE_PATH_PATTERN.search(source)
        )

    @staticmethod
    def _might_contain_pin(path: Path) -> bool:
        source = path.read_text(encoding="utf-8")
        return bool(
            _COMMIT_PATTERN.search(source)
            or _VERSION_PATTERN.search(source)
            or _HTTP_URL_PATTERN.search(source)
            or _REFERENCE_PIN_FIELD_PATTERN.search(source)
            or _VERSION_BINDING_ASSIGNMENT_PATTERN.search(source)
        )

    @staticmethod
    def _is_cryptographic_identity_binding(binding: str) -> bool:
        """Distinguish signing fingerprints from source revisions."""
        return _binding_is_cryptographic_identity(binding)

    @staticmethod
    def _is_hash_binding(binding: str) -> bool:
        """Return whether the binding semantically owns a digest value."""
        return _binding_owns_hash(binding)

    @staticmethod
    def _is_reference_pin_binding(binding: str) -> bool:
        leaf = binding.rsplit(".", maxsplit=1)[-1].strip('"').casefold()
        normalized = leaf.replace("-", "").replace("_", "")
        return normalized in _REFERENCE_PIN_BINDINGS

    @staticmethod
    def _is_source_version_binding(binding: str) -> bool:
        segments = binding.casefold().split(".")
        leaf = segments[-1].strip('"')
        compatibility_container = any(
            "minimum" in segment and "version" in segment for segment in segments[:-1]
        )
        compatibility_leaf = "version" in leaf and (
            leaf.startswith(("min", "max"))
            or "_min_" in leaf
            or "_max_" in leaf
            or "minimum" in leaf
            or "maximum" in leaf
        )
        version_map = any(
            segment.strip('"').endswith(("versions", "versionmap"))
            for segment in segments[:-1]
        )
        return (
            (leaf.endswith("version") or version_map)
            and not compatibility_leaf
            and not compatibility_container
        )


@dataclass(frozen=True)
class NixSubstituteAudit:
    """Audit existing Nix shell-level source rewrites."""

    allowed_sites: tuple[NixSubstituteSite, ...]
    pattern: re.Pattern[str] = SUBSTITUTE_IN_PLACE_PATTERN
    roots: tuple[Path, ...] = (REPO_ROOT / "packages", REPO_ROOT / "overlays")

    def current_sites(self) -> tuple[NixSubstituteSite, ...]:
        """Return all Nix substituteInPlace sites under the configured roots."""
        return tuple(site for path in self._files() for site in self._sites_for(path))

    def _files(self) -> tuple[Path, ...]:
        return tuple(
            sorted(
                path
                for root in self.roots
                for path in root.rglob("*.nix")
                if self.pattern.search(path.read_text(encoding="utf-8"))
            ),
        )

    def _sites_for(self, path: Path) -> tuple[NixSubstituteSite, ...]:
        expr = parse_nix_expr_for_policy(
            path.read_text(encoding="utf-8"),
            context=self._relative_path(path),
        )
        lines = expr.rebuild().splitlines()
        return tuple(
            (self._relative_path(path), self._command_from(lines, index))
            for index, line in enumerate(lines)
            if self.pattern.search(line)
        )

    @staticmethod
    def _command_from(lines: list[str], start: int) -> str:
        command_parts: list[str] = []
        for line in lines[start:]:
            stripped = line.strip()
            command_parts.append(stripped.removesuffix("\\").strip())
            if not stripped.endswith("\\"):
                break
        return " ".join(command_parts)

    @staticmethod
    def _relative_path(path: Path) -> str:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


@dataclass(frozen=True)
class PythonRewriteAudit:
    """Audit existing Python source-mutating ad hoc rewrites."""

    allowed_sites: tuple[PythonRewriteSite, ...]
    patch_script_names: frozenset[str] = SOURCE_PATCH_SCRIPT_NAMES
    rewrite_attrs: frozenset[str] = PYTHON_AD_HOC_REWRITE_ATTRS
    target_patterns: tuple[str, ...] = ("packages/**/*.py", "overlays/**/*.py")

    def current_sites(self) -> tuple[PythonRewriteSite, ...]:
        """Return all Python ad hoc source-rewrite call sites."""
        return tuple(
            site
            for path in sorted(iter_target_paths(self.target_patterns, root=REPO_ROOT))
            if not self._is_sibling_updater_test(path)
            for site in self._sites_for(path)
        )

    @staticmethod
    def _is_sibling_updater_test(path: Path) -> bool:
        relative_path = path.resolve().relative_to(REPO_ROOT.resolve())
        match relative_path.parts:
            case ("packages" | "overlays", _, "updater_test.py"):
                return True
            case _:
                return False

    def _sites_for(self, path: Path) -> tuple[PythonRewriteSite, ...]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if path.name in self.patch_script_names:
            return self._patch_script_sites(path, tree)
        seen_calls: set[ast.Call] = set()
        return tuple(
            sorted(
                [
                    site
                    for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                    for site in self._function_sites(
                        path,
                        node,
                        seen_calls=seen_calls,
                    )
                ],
            ),
        )

    def _patch_script_sites(
        self,
        path: Path,
        tree: ast.AST,
    ) -> tuple[PythonRewriteSite, ...]:
        relative_path = self._relative_path(path)
        return tuple(
            sorted((relative_path, call) for call in self._rewrite_calls(tree)),
        )

    def _function_sites(
        self,
        path: Path,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        seen_calls: set[ast.Call] | None = None,
    ) -> tuple[PythonRewriteSite, ...]:
        relative_path = self._relative_path(path)
        observed_calls = set() if seen_calls is None else seen_calls
        assigned_rewrites: dict[str, list[str]] = {}
        inline_write_calls: list[str] = []
        written_names: set[str] = set()

        for node in ast.walk(function):
            if isinstance(node, ast.Assign):
                self._record_assignment(
                    node.value,
                    node.targets,
                    assigned_rewrites,
                    seen_calls=observed_calls,
                )
            elif isinstance(node, ast.AnnAssign):
                self._record_assignment(
                    node.value,
                    (node.target,),
                    assigned_rewrites,
                    seen_calls=observed_calls,
                )
            elif isinstance(node, ast.Call) and self._is_write_text_call(node):
                inline_write_calls.extend(
                    self._rewrite_calls(node, seen_calls=observed_calls)
                )
                if payload_name := self._write_text_payload_name(node):
                    written_names.add(payload_name)

        assigned_write_sites = [
            (relative_path, call)
            for variable in sorted(written_names & assigned_rewrites.keys())
            for call in assigned_rewrites[variable]
        ]
        inline_write_sites = [(relative_path, call) for call in inline_write_calls]
        return tuple(sorted([*inline_write_sites, *assigned_write_sites]))

    def _rewrite_call(self, node: ast.Call) -> str | None:
        match node.func:
            case ast.Attribute(attr=attr) if attr in self.rewrite_attrs:
                return ast.unparse(node)
            case _:
                return None

    def _rewrite_calls(
        self,
        node: ast.AST,
        *,
        seen_calls: set[ast.Call] | None = None,
    ) -> tuple[str, ...]:
        calls: list[str] = []
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            if seen_calls is not None and child in seen_calls:
                continue
            call = self._rewrite_call(child)
            if call is None:
                continue
            if seen_calls is not None:
                seen_calls.add(child)
            calls.append(call)
        return tuple(calls)

    def _record_assignment(
        self,
        value: ast.expr | None,
        targets: tuple[ast.expr, ...] | list[ast.expr],
        assigned_rewrites: dict[str, list[str]],
        *,
        seen_calls: set[ast.Call] | None = None,
    ) -> None:
        if value is None:
            return
        rewrite_calls = self._rewrite_calls(value, seen_calls=seen_calls)
        if not rewrite_calls:
            return
        for target in targets:
            for name in self._assigned_names(target):
                assigned_rewrites.setdefault(name, []).extend(rewrite_calls)

    @staticmethod
    def _assigned_names(target: ast.expr) -> tuple[str, ...]:
        match target:
            case ast.Name(id=name):
                return (name,)
            case ast.Tuple(elts=elts) | ast.List(elts=elts):
                return tuple(
                    name
                    for element in elts
                    for name in PythonRewriteAudit._assigned_names(element)
                )
            case _:
                return ()

    @staticmethod
    def _is_write_text_call(node: ast.Call) -> bool:
        return isinstance(node.func, ast.Attribute) and node.func.attr == "write_text"

    @staticmethod
    def _relative_path(path: Path) -> str:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()

    @staticmethod
    def _write_text_payload_name(node: ast.Call) -> str | None:
        match node.func:
            case ast.Attribute(attr="write_text") if node.args:
                match node.args[0]:
                    case ast.Name(id=name):
                        return name
                    case _:
                        return None
            case _:
                return None


__all__ = [
    "NixSourceIdentityAudit",
    "NixSourceIdentitySite",
    "NixSubstituteAudit",
    "NixSubstituteSite",
    "PythonRewriteAudit",
    "PythonRewriteSite",
    "parse_nix_expr_for_policy",
]
