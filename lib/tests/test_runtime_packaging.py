"""Runtime packaging contract tests."""

import tomllib
from pathlib import Path

from lib.update.paths import get_repo_root

_TEST_PATHS = [
    "lib/tests/**/*.py",
    "packages/*/updater_test.py",
    "overlays/*/updater_test.py",
]
_RUFF_TEST_GLOB = f"{{{','.join(_TEST_PATHS)}}}"
_COVERAGE_TEST_PATHS = [
    "lib/tests/*",
    "packages/*/updater_test.py",
    "overlays/*/updater_test.py",
]


def _direct_updaters(root: Path) -> list[Path]:
    return [
        *sorted((root / "packages").glob("*/updater.py")),
        *sorted((root / "overlays").glob("*/updater.py")),
    ]


def _sibling_updater_tests(root: Path) -> list[Path]:
    return [
        *sorted((root / "packages").glob("*/updater_test.py")),
        *sorted((root / "overlays").glob("*/updater_test.py")),
    ]


def test_python_distribution_excludes_test_packages() -> None:
    """Keep repository-only tests out of the installed runtime distribution."""
    config = tomllib.loads(
        (get_repo_root() / "pyproject.toml").read_text(encoding="utf-8")
    )

    package_find = config["tool"]["setuptools"]["packages"]["find"]
    assert "include-package-data" not in config["tool"]["setuptools"]
    assert package_find["include"] == ["lib*"]
    assert package_find["exclude"] == ["lib.tests*"]


def test_test_type_exceptions_are_rule_scoped() -> None:
    """Keep dynamic test doubles checked for every diagnostic they can support."""
    config = tomllib.loads(
        (get_repo_root() / "pyproject.toml").read_text(encoding="utf-8")
    )

    test_override = next(
        override
        for override in config["tool"]["ty"]["overrides"]
        if override["include"] == _TEST_PATHS
    )
    rules = test_override["rules"]
    assert "all" not in rules
    assert set(rules) == {
        "invalid-argument-type",
        "invalid-key",
        "invalid-method-override",
        "invalid-return-type",
        "missing-argument",
        "missing-typed-dict-key",
        "not-iterable",
        "not-subscriptable",
        "unknown-argument",
        "unresolved-attribute",
        "unsupported-operator",
    }
    assert [override["include"] for override in config["tool"]["ty"]["overrides"]] == [
        _TEST_PATHS
    ]


def test_sibling_updater_tests_use_standard_tooling_boundaries() -> None:
    """Discover sibling tests without weakening production updater checks."""
    config = tomllib.loads(
        (get_repo_root() / "pyproject.toml").read_text(encoding="utf-8")
    )

    pytest_config = config["tool"]["pytest"]["ini_options"]
    assert pytest_config["testpaths"] == ["lib", "packages", "overlays"]
    assert pytest_config["addopts"] == [
        "--strict-config",
        "--strict-markers",
        "--import-mode=importlib",
    ]
    assert pytest_config["python_files"] == ["test_*.py", "*_test.py"]

    per_file_ignores = config["tool"]["ruff"]["lint"]["per-file-ignores"]
    assert list(per_file_ignores) == [_RUFF_TEST_GLOB]
    assert "packages/*/updater.py" not in per_file_ignores
    assert "overlays/*/updater.py" not in per_file_ignores
    assert config["tool"]["coverage"]["report"]["omit"] == _COVERAGE_TEST_PATHS


def test_leaf_updater_tests_are_plain_sibling_modules() -> None:
    """Keep package-owned tests beside their updater without custom collection."""
    root = get_repo_root()
    centralized: list[str] = []
    for test_path in sorted((root / "lib/tests").glob("test_*_updater.py")):
        owner = test_path.stem.removeprefix("test_").removesuffix("_updater")
        directory_name = owner.replace("_", "-")
        if any(
            (root / test_root / directory_name / "updater.py").is_file()
            for test_root in ("packages", "overlays")
        ):
            centralized.append(test_path.name)

    assert centralized == []
    assert [
        *sorted((root / "packages").glob("*/tests/test_updater.py")),
        *sorted((root / "overlays").glob("*/tests/test_updater.py")),
    ] == []

    sibling_tests = _sibling_updater_tests(root)
    assert sibling_tests
    assert all((path.parent / "updater.py").is_file() for path in sibling_tests)
    assert all(path.name == "updater_test.py" for path in sibling_tests)

    forbidden_markers = (
        "# BEGIN NIXCFG EMBEDDED UPDATER TESTS",
        "# END NIXCFG EMBEDDED UPDATER TESTS",
    )
    for updater in _direct_updaters(root):
        source = updater.read_text(encoding="utf-8")
        assert all(marker not in source for marker in forbidden_markers)
