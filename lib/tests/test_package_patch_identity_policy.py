"""Policy checks for release identity duplicated inside package or overlay patchers."""

import ast
import re
from collections.abc import Iterable
from pathlib import Path

from lib.update.paths import REPO_ROOT

_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_SRI_SHA256 = re.compile(r"^sha256-[A-Za-z0-9+/]{43}=$")

# A patcher listed here intentionally requires manual byte review and therefore
# does not support unattended updates. Keep the reason specific and actionable.
_MANUAL_REVIEW_EXEMPTIONS: dict[tuple[str, str], str] = {}


def _is_policy_source(path: Path) -> bool:
    return (
        path.is_file()
        and not path.name.endswith("_test.py")
        and not path.name.startswith("test_")
    )


def _local_module_files(
    base: Path,
    module_parts: tuple[str, ...],
    *,
    package_root: Path,
) -> tuple[Path, ...]:
    target = base.joinpath(*module_parts)
    candidates: list[Path] = []
    prefix = base
    for part in module_parts[:-1]:
        prefix /= part
        candidates.append(prefix / "__init__.py")
    candidates.append(target / "__init__.py")
    if module_parts:
        candidates.insert(0, target.with_suffix(".py"))

    resolved_package_root = package_root.resolve()
    return tuple(
        candidate
        for candidate in candidates
        if _is_policy_source(candidate)
        and candidate.resolve().is_relative_to(resolved_package_root)
    )


def _imported_policy_files(
    path: Path,
    owner_root: Path,
    *,
    collection_root: Path,
    root: Path,
) -> tuple[Path, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    absolute_bases = (path.parent, owner_root, collection_root, root)
    imported: set[Path] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_parts = tuple(alias.name.split("."))
                for base in absolute_bases:
                    imported.update(
                        _local_module_files(
                            base,
                            module_parts,
                            package_root=owner_root,
                        )
                    )
            continue
        if not isinstance(node, ast.ImportFrom):
            continue

        module_parts = tuple(node.module.split(".")) if node.module else ()
        if node.level:
            relative_base = path.parent
            for _ in range(node.level - 1):
                relative_base = relative_base.parent
            import_bases = (relative_base,)
        else:
            import_bases = absolute_bases

        for base in import_bases:
            imported.update(
                _local_module_files(
                    base,
                    module_parts,
                    package_root=owner_root,
                )
            )
            for alias in node.names:
                if alias.name == "*":
                    continue
                imported.update(
                    _local_module_files(
                        base,
                        (*module_parts, *alias.name.split(".")),
                        package_root=owner_root,
                    )
                )

    return tuple(sorted(imported))


def _package_policy_files(root: Path) -> tuple[Path, ...]:
    collections = (root / "packages", root / "overlays")
    pending: list[tuple[Path, Path, Path]] = []
    for collection_root in collections:
        for patcher in sorted(collection_root.glob("**/patch*.py")):
            if not _is_policy_source(patcher):
                continue
            relative_parts = patcher.relative_to(collection_root).parts
            owner_root = (
                collection_root / relative_parts[0]
                if len(relative_parts) > 1
                else collection_root
            )
            pending.append((patcher, owner_root, collection_root))

    policy_files: set[Path] = set()
    while pending:
        path, owner_root, collection_root = pending.pop()
        if path in policy_files:
            continue
        policy_files.add(path)
        pending.extend(
            (imported, owner_root, collection_root)
            for imported in _imported_policy_files(
                path,
                owner_root,
                collection_root=collection_root,
                root=root,
            )
            if imported not in policy_files
        )

    return tuple(sorted(policy_files))


def _sha256_literal(value: object) -> str | None:
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii")
        except UnicodeDecodeError:
            return None
    if not isinstance(value, str):
        return None
    if _HEX_SHA256.fullmatch(value) is None and _SRI_SHA256.fullmatch(value) is None:
        return None
    return value


def _identity_sites(
    paths: Iterable[Path],
    *,
    root: Path,
) -> tuple[tuple[str, int, str], ...]:
    sites: list[tuple[str, int, str]] = []
    for path in paths:
        relative_path = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant):
                continue
            digest = _sha256_literal(node.value)
            if digest is not None:
                sites.append((relative_path, node.lineno, digest))
    return tuple(sites)


def test_identity_audit_follows_a_patcher_package_policy_module(
    tmp_path: Path,
) -> None:
    """Moving an artifact digest behind an import must not bypass the policy."""
    package = tmp_path / "packages/demo"
    native = package / "native"
    native.mkdir(parents=True)
    patcher = native / "patch_runtime_policy.py"
    patcher.write_text(
        "from ..policy_contract import OPAQUE_IDENTITY\n",
        encoding="utf-8",
    )
    digest = "0123456789abcdef" * 4
    sri_digest = "sha256-" + "A" * 43 + "="
    policy_contract = package / "policy_contract.py"
    policy_contract.write_text(
        "from identity_contract import OPAQUE_IDENTITY, BYTE_IDENTITY\n",
        encoding="utf-8",
    )
    identity_contract = package / "identity_contract.py"
    identity_contract.write_text(
        f'OPAQUE_IDENTITY = "{digest}"\nBYTE_IDENTITY = b"{sri_digest}"\n',
        encoding="utf-8",
    )
    (package / "updater.py").write_text(
        f'UNRELATED_SOURCE_HASH = "{digest}"\n',
        encoding="utf-8",
    )

    policy_files = _package_policy_files(tmp_path)

    assert set(policy_files) == {patcher, policy_contract, identity_contract}
    assert _identity_sites(policy_files, root=tmp_path) == (
        ("packages/demo/identity_contract.py", 1, digest),
        ("packages/demo/identity_contract.py", 2, sri_digest),
    )


def test_identity_audit_follows_imported_package_initializers(tmp_path: Path) -> None:
    """Dotted imports must include every package initializer Python executes."""
    package = tmp_path / "packages/demo"
    native = package / "native"
    policy = package / "policy"
    native.mkdir(parents=True)
    policy.mkdir()
    patcher = native / "patch_runtime_policy.py"
    patcher.write_text("import policy.contract\n", encoding="utf-8")
    digest = "fedcba9876543210" * 4
    initializer = policy / "__init__.py"
    initializer.write_text(f'OPAQUE_IDENTITY = "{digest}"\n', encoding="utf-8")
    contract = policy / "contract.py"
    contract.write_text("SEMANTIC_ANCHOR = b'owned-by-nix'\n", encoding="utf-8")

    policy_files = _package_policy_files(tmp_path)

    assert set(policy_files) == {patcher, initializer, contract}
    assert _identity_sites(policy_files, root=tmp_path) == (
        ("packages/demo/policy/__init__.py", 1, digest),
    )


def test_identity_audit_follows_an_overlay_patcher_policy_module(
    tmp_path: Path,
) -> None:
    """Overlay patchers and their local policy imports must share the audit."""
    overlay = tmp_path / "overlays/demo"
    overlay.mkdir(parents=True)
    patcher = overlay / "patch_source.py"
    patcher.write_text(
        "from policy_contract import OPAQUE_IDENTITY\n",
        encoding="utf-8",
    )
    digest = "abcdef0123456789" * 4
    policy_contract = overlay / "policy_contract.py"
    policy_contract.write_text(
        f'OPAQUE_IDENTITY = "{digest}"\n',
        encoding="utf-8",
    )

    policy_files = _package_policy_files(tmp_path)

    assert set(policy_files) == {patcher, policy_contract}
    assert _identity_sites(policy_files, root=tmp_path) == (
        ("overlays/demo/policy_contract.py", 1, digest),
    )


def test_package_and_overlay_patchers_do_not_duplicate_release_identity() -> None:
    """Patchers should gate semantics while sources.json owns artifact bytes."""
    policy_files = _package_policy_files(REPO_ROOT)
    policy_paths = {path.relative_to(REPO_ROOT).as_posix() for path in policy_files}
    assert all(reason.strip() for reason in _MANUAL_REVIEW_EXEMPTIONS.values())
    assert not {
        path for path, _digest in _MANUAL_REVIEW_EXEMPTIONS if path not in policy_paths
    }

    identity_sites = _identity_sites(policy_files, root=REPO_ROOT)
    identity_keys = {(path, digest) for path, _line, digest in identity_sites}
    assert not set(_MANUAL_REVIEW_EXEMPTIONS).difference(identity_keys)

    findings = tuple(
        f"{path}:{line} {digest}"
        for path, line, digest in identity_sites
        if (path, digest) not in _MANUAL_REVIEW_EXEMPTIONS
    )
    assert not findings, (
        "package or overlay patchers duplicate release-specific identity owned by "
        f"sources.json: {findings}"
    )
