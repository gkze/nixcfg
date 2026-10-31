"""Enforce the boundary between Python tests and Nix configuration data.

A pytest module may exercise Python behaviour, and it may assert on the Nix that
Python *emits*. It must not assert on the contents of a host or user
configuration file. Those files are data: their correctness is established by
evaluating and activating the real host, and a test that duplicates their
contents only guarantees that every config edit costs a second edit here.

The check is structural rather than textual. It parses each test module and
looks for any reference to a Nix declaration under ``darwin/`` or ``home/george/``,
or to one of those directories itself. Reusable libraries (``lib/``,
``modules/``, ``overlays/``, ``packages/``) and the composition root
(``flake.nix``) stay available, because those are code, not data.

Deliberately not in scope: scripts and data files that happen to live under
``darwin/`` or ``home/``. ``home/george/bin/git-ignore`` is Python that its test
executes, and ``darwin/george/build-volume-bun-migration.sh`` is shell that its
test sources, so only directories and ``.nix`` declarations are configuration.

The detector is covered by its own tests at the end of this module. A guard whose
only assertion is "the current tree is clean" keeps reporting success after it
silently stops matching, which is the failure mode this file exists to prevent.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

from lib.update.paths import REPO_ROOT

# Repository roots that hold per-host or per-user configuration data, and the
# directories that contain only that data. A fixture may legitimately spell a
# synthetic ``home/demo.nix``, so only the real per-user root is matched.
_CONFIGURATION_ROOTS = ("darwin", "home/george")
_CONFIGURATION_DIRECTORIES = ("darwin", "home", "home/george")

# The declaration extension that makes a path configuration rather than a
# script or a checked-in data file.
_CONFIGURATION_SUFFIX = ".nix"

# Every Python module that belongs to the suite, wherever the repository keeps
# it. Package-owned updater tests are part of the same suite and the same rule.
_TEST_MODULE_PATTERNS = (
    "lib/tests/test_*.py",
    "packages/**/*updater_test.py",
    "overlays/**/*updater_test.py",
)


def _iter_test_modules() -> list[Path]:
    """Return every Python test module owned by the suite."""
    return sorted({
        path for pattern in _TEST_MODULE_PATTERNS for path in REPO_ROOT.glob(pattern)
    })


def _is_configuration(relative: str, *, anchored: bool) -> bool:
    """Report whether ``relative`` names configuration data or its directory.

    ``anchored`` marks a path built from ``REPO_ROOT``, where naming the
    configuration directory itself is also a violation: any use of it reaches
    the declarations below. A bare ``"darwin"`` string is a platform name, not a
    path, and stays allowed.
    """
    path = relative.removeprefix("./").rstrip("/")
    if anchored and path in _CONFIGURATION_DIRECTORIES:
        return True
    return any(
        path.startswith(f"{root}/") and path.endswith(_CONFIGURATION_SUFFIX)
        for root in _CONFIGURATION_ROOTS
    )


def _repo_relative_join(node: ast.expr) -> str | None:
    """Return the path named by a ``REPO_ROOT / "a" / "b"`` expression."""
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.BinOp) and isinstance(current.op, ast.Div):
        right = current.right
        if not (isinstance(right, ast.Constant) and isinstance(right.value, str)):
            return None
        parts.insert(0, right.value)
        current = current.left
    if not (isinstance(current, ast.Name) and current.id == "REPO_ROOT"):
        return None
    return "/".join(parts)


def _spelled_paths(node: ast.AST) -> Iterator[tuple[str, bool]]:
    """Yield the repository-relative paths one AST node spells.

    Handles the forms in use: a bare string literal, a joined
    ``REPO_ROOT / "path"`` expression, and an f-string whose literal fragments
    together name a path, so ``f"home/george/{name}.nix"`` still matches. The
    flag reports whether the path is anchored at ``REPO_ROOT``.
    """
    if isinstance(node, ast.JoinedStr):
        yield (
            "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            ),
            False,
        )
        return
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value, False
    if isinstance(node, ast.BinOp):
        joined = _repo_relative_join(node)
        if joined is not None:
            yield joined, True


def configuration_references(tree: ast.AST, relative: str) -> list[str]:
    """Collect ``file:line`` for each configuration declaration reference."""
    return [
        f"{relative}:{node.lineno}: {path}"
        for node in ast.walk(tree)
        for path, anchored in _spelled_paths(node)
        if _is_configuration(path, anchored=anchored)
    ]


def test_python_tests_do_not_reference_repository_configuration() -> None:
    """Nix configuration data belongs to the host build, not to pytest."""
    violations = [
        reference
        for path in _iter_test_modules()
        for reference in configuration_references(
            ast.parse(path.read_text(encoding="utf-8")),
            path.relative_to(REPO_ROOT).as_posix(),
        )
    ]

    assert violations == [], (
        "pytest must not read repository configuration data. Assert on the Nix "
        "that Python emits, or move the property into a tests/nix check.\n"
        + "\n".join(violations)
    )


@pytest.mark.parametrize(
    "snippet",
    [
        'nix_source_fragment_expr("home/george/work.nix", "routing = ", ";")',
        'nix_file_expr("home/george/configuration.nix")',
        'REPO_ROOT / "darwin" / "george" / "build-volume-env.nix"',
        'Path("./home/george/appearance.nix")',
        'REPO_ROOT / "darwin"',
        '(REPO_ROOT / "home" / "george").glob("*.nix")',
        'f"home/george/{name}.nix"',
    ],
)
def test_configuration_references_flag_configuration_data(snippet: str) -> None:
    """Every shape that reaches a host declaration is reported."""
    assert configuration_references(ast.parse(snippet), "probe.py")


@pytest.mark.parametrize(
    "snippet",
    [
        'REPO_ROOT / "packages/registry.nix"',
        'REPO_ROOT / "lib" / "mac-apps.nix"',
        'REPO_ROOT / "home/george/bin/git-ignore"',
        'REPO_ROOT / "darwin/george/build-volume-bun-migration.sh"',
        '"darwin/arm64/Factory-0.154.0-arm64.dmg"',
        '"darwinDefaults"',
    ],
)
def test_configuration_references_allow_code_and_scripts(snippet: str) -> None:
    """Library code, executables, and unrelated strings stay readable."""
    assert configuration_references(ast.parse(snippet), "probe.py") == []
