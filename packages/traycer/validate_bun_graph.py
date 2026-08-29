"""Validate that Traycer's generated ``bun.nix`` exactly matches ``bun.lock``."""

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Never, cast

from nix_manipulator import parse
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.path import NixPath
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

if TYPE_CHECKING:
    from collections.abc import Sequence

_WORKSPACE_MARKER = "@workspace:"
_ARCHIVE_LOCK_ENTRY_LENGTH = 4
_SUPPORTED_LOCKFILE_VERSION = 1
_SUPPORTED_CONFIG_VERSION = 1


class BunGraphValidationError(ValueError):
    """Report a semantic mismatch between the lock and generated graph."""


@dataclass(frozen=True)
class BunGraphSummary:
    """Counts established by a successful graph validation."""

    raw_package_count: int
    unique_package_count: int
    archive_count: int
    workspace_count: int


@dataclass(frozen=True)
class _Archive:
    url: str
    hash: str
    name: str | None


@dataclass(frozen=True)
class _Workspace:
    path: str


type _GraphEntry = _Archive | _Workspace


def _invalid(message: str) -> Never:
    raise BunGraphValidationError(message)


def _strip_jsonc_comments(source: str) -> str:
    """Remove JSONC comments while preserving strings and source line breaks."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(source):
        character = source[index]
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            output.append(character)
            index += 1
            continue
        if character != "/" or index + 1 >= len(source):
            output.append(character)
            index += 1
            continue

        marker = source[index + 1]
        if marker == "/":
            output.extend((" ", " "))
            index += 2
            while index < len(source) and source[index] not in "\r\n":
                output.append(" ")
                index += 1
            continue
        if marker == "*":
            output.extend((" ", " "))
            index += 2
            while index + 1 < len(source) and source[index : index + 2] != "*/":
                output.append(source[index] if source[index] in "\r\n" else " ")
                index += 1
            if index + 1 >= len(source):
                _invalid("unterminated JSONC block comment")
            output.extend((" ", " "))
            index += 2
            continue
        output.append(character)
        index += 1
    return "".join(output)


def _strip_jsonc_trailing_commas(source: str) -> str:
    """Remove only commas whose next JSON token closes an array or object."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(source):
        character = source[index]
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            output.append(character)
            index += 1
            continue
        if character == ",":
            next_index = index + 1
            while next_index < len(source) and source[next_index].isspace():
                next_index += 1
            if next_index < len(source) and source[next_index] in "}]":
                index += 1
                continue
        output.append(character)
        index += 1
    return "".join(output)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate object keys instead of accepting JSON's last value."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _invalid(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _load_lock(path: Path) -> dict[str, object]:
    """Load Bun's JSONC-shaped lock without evaluating project code."""
    try:
        source = path.read_text(encoding="utf-8")
        normalized = _strip_jsonc_trailing_commas(_strip_jsonc_comments(source))
        value = json.loads(normalized, object_pairs_hook=_unique_object)
    except (OSError, json.JSONDecodeError) as error:
        _invalid(f"unable to parse {path}: {error}")
    if not isinstance(value, dict):
        _invalid(f"{path} must contain a top-level object")
    return cast("dict[str, object]", value)


def _object_field(root: dict[str, object], name: str, path: Path) -> dict[str, object]:
    value = root.get(name)
    if not isinstance(value, dict):
        _invalid(f"{path} field {name!r} must be an object")
    return cast("dict[str, object]", value)


def _require_lock_version(
    lock: dict[str, object],
    name: str,
    expected: int,
    path: Path,
) -> None:
    value = lock.get(name)
    if type(value) is not int or value != expected:
        _invalid(f"{path} {name} must be {expected}, got {value!r}")


def _registry_archive(
    package_id: str,
    tarball_url: object,
    integrity: object,
) -> _Archive:
    name, separator, version = package_id.rpartition("@")
    if not separator or not name or not version:
        _invalid(f"invalid registry package identity: {package_id!r}")
    if not isinstance(tarball_url, str):
        _invalid(f"invalid tarball URL for {package_id!r}")
    if not isinstance(integrity, str) or not integrity.startswith("sha512-"):
        _invalid(f"invalid integrity for {package_id!r}")
    tarball_name = name.rsplit("/", maxsplit=1)[-1]
    return _Archive(
        url=(
            tarball_url
            or f"https://registry.npmjs.org/{name}/-/{tarball_name}-{version}.tgz"
        ),
        hash=integrity,
        name=f"{tarball_name}-{version}.tgz" if tarball_url else None,
    )


def _expected_graph(path: Path) -> tuple[dict[str, _GraphEntry], int]:
    lock = _load_lock(path)
    _require_lock_version(
        lock,
        "lockfileVersion",
        _SUPPORTED_LOCKFILE_VERSION,
        path,
    )
    _require_lock_version(
        lock,
        "configVersion",
        _SUPPORTED_CONFIG_VERSION,
        path,
    )
    raw_packages = _object_field(lock, "packages", path)
    raw_workspaces = _object_field(lock, "workspaces", path)

    expected_workspaces: dict[str, _Workspace] = {}
    for workspace_path, metadata in raw_workspaces.items():
        if workspace_path == "":
            continue
        if not isinstance(metadata, dict):
            _invalid(f"workspace {workspace_path!r} in {path} must declare a name")
        workspace_metadata = cast("dict[str, object]", metadata)
        workspace_name = workspace_metadata.get("name")
        if not isinstance(workspace_name, str):
            _invalid(f"workspace {workspace_path!r} in {path} must declare a name")
        if workspace_name in expected_workspaces:
            _invalid(f"duplicate workspace name: {workspace_name!r}")
        expected_workspaces[workspace_name] = _Workspace(workspace_path)

    archives: dict[str, _Archive] = {}
    locked_workspaces: dict[str, _Workspace] = {}
    for lock_key, raw_entry in raw_packages.items():
        if not isinstance(raw_entry, list) or not raw_entry:
            _invalid(f"invalid lock entry {lock_key!r}")
        package_id = raw_entry[0]
        if not isinstance(package_id, str):
            _invalid(f"lock entry {lock_key!r} has no package identity")

        if _WORKSPACE_MARKER in package_id:
            workspace_name, marker, workspace_path = package_id.rpartition(
                _WORKSPACE_MARKER
            )
            if (
                not marker
                or len(raw_entry) != 1
                or lock_key != workspace_name
                or not workspace_path
            ):
                _invalid(f"invalid workspace lock entry {lock_key!r}")
            locked_workspaces[workspace_name] = _Workspace(workspace_path)
            continue

        if len(raw_entry) != _ARCHIVE_LOCK_ENTRY_LENGTH:
            _invalid(f"unsupported archive lock entry {lock_key!r}")
        if not isinstance(raw_entry[2], dict):
            _invalid(f"invalid registry metadata for {package_id!r}")
        archive = _registry_archive(package_id, raw_entry[1], raw_entry[-1])
        previous = archives.setdefault(package_id, archive)
        if previous != archive:
            _invalid(f"conflicting archive records for {package_id!r}")

    if locked_workspaces != expected_workspaces:
        _invalid(
            "workspace records differ between workspaces and packages: "
            f"expected={expected_workspaces!r}, locked={locked_workspaces!r}"
        )

    graph: dict[str, _GraphEntry] = {**archives, **expected_workspaces}
    return graph, len(raw_packages)


def _string_binding(arguments: AttributeSet, name: str, package_name: str) -> str:
    matching = [
        binding
        for binding in arguments.values
        if isinstance(binding, Binding) and binding.name == name
    ]
    if len(matching) != 1 or not isinstance(matching[0].value, StringPrimitive):
        _invalid(f"generated archive {package_name!r} must have one string {name!r}")
    return matching[0].value.value


def _generated_graph(path: Path) -> dict[str, _GraphEntry]:
    try:
        source = parse(path.read_text(encoding="utf-8"))
    except OSError as error:
        _invalid(f"unable to read {path}: {error}")
    if source.contains_error or len(source.expressions) != 1:
        _invalid(f"{path} is not a single valid Nix expression")
    function = source.expressions[0]
    if not isinstance(function, FunctionDefinition) or not isinstance(
        function.output, AttributeSet
    ):
        _invalid(f"{path} must generate an attribute set function")

    graph: dict[str, _GraphEntry] = {}
    for raw_binding in function.output.values:
        if not isinstance(raw_binding, Binding):
            _invalid(f"{path} contains a non-binding graph entry")
        try:
            package_name = json.loads(raw_binding.name)
        except json.JSONDecodeError:
            _invalid(
                f"generated package name is not a quoted string: {raw_binding.name!r}"
            )
        if not isinstance(package_name, str):
            _invalid(f"generated package name is not a string: {raw_binding.name!r}")
        if package_name in graph:
            _invalid(f"duplicate generated package {package_name!r}")

        call = raw_binding.value
        if not isinstance(call, FunctionCall):
            _invalid(f"generated package {package_name!r} must be a function call")
        callee = call.name.name
        if callee == "fetchurl" and isinstance(call.argument, AttributeSet):
            argument_names = {
                binding.name
                for binding in call.argument.values
                if isinstance(binding, Binding)
            }
            if argument_names not in ({"hash", "url"}, {"hash", "name", "url"}):
                _invalid(f"generated archive {package_name!r} has unexpected fields")
            graph[package_name] = _Archive(
                url=_string_binding(call.argument, "url", package_name),
                hash=_string_binding(call.argument, "hash", package_name),
                name=(
                    _string_binding(call.argument, "name", package_name)
                    if "name" in argument_names
                    else None
                ),
            )
            continue
        if callee == "copyPathToStore" and isinstance(call.argument, NixPath):
            raw_path = call.argument.path
            if not raw_path.startswith("./"):
                _invalid(f"workspace {package_name!r} must use a relative path")
            workspace_path = raw_path.removeprefix("./")
            normalized_path = PurePosixPath(workspace_path)
            if not workspace_path or ".." in normalized_path.parts:
                _invalid(f"workspace {package_name!r} has an unsafe path")
            graph[package_name] = _Workspace(workspace_path)
            continue
        _invalid(
            f"generated package {package_name!r} uses unsupported constructor {callee!r}"
        )
    return graph


def validate_bun_graph(lock_path: Path, nix_path: Path) -> BunGraphSummary:
    """Prove exact unique archive and workspace correspondence."""
    expected, raw_package_count = _expected_graph(lock_path)
    generated = _generated_graph(nix_path)
    missing = sorted(expected.keys() - generated.keys())
    extra = sorted(generated.keys() - expected.keys())
    if missing or extra:
        _invalid(f"generated graph key mismatch: missing={missing!r}, extra={extra!r}")

    altered = sorted(
        package_name
        for package_name, expected_entry in expected.items()
        if generated[package_name] != expected_entry
    )
    if altered:
        _invalid(f"generated graph entries differ: {altered!r}")

    workspace_count = sum(isinstance(entry, _Workspace) for entry in expected.values())
    archive_count = len(expected) - workspace_count
    return BunGraphSummary(
        raw_package_count=raw_package_count,
        unique_package_count=len(expected),
        archive_count=archive_count,
        workspace_count=workspace_count,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Validate paths supplied by an updater or the package-local defaults."""
    package_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lock", type=Path, nargs="?", default=package_dir / "bun.lock")
    parser.add_argument("nix", type=Path, nargs="?", default=package_dir / "bun.nix")
    arguments = parser.parse_args(argv)
    summary = validate_bun_graph(arguments.lock, arguments.nix)
    sys.stdout.write(f"{json.dumps(asdict(summary), sort_keys=True)}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover -- exercised through the main API
    raise SystemExit(main())
