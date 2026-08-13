"""Runtime packaging contract tests."""

from __future__ import annotations

import tomllib

from lib.update.paths import get_repo_root


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
        if override["include"] == ["lib/tests/**/*.py"]
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
        ["lib/tests/**/*.py"]
    ]
