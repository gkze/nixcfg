"""Zed ``settings`` crate2nix graph: deps exist; #1269 was an SVH mix."""

from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import expect_binding, parse_nix_expr
from lib.update.ci.warmup import SETTINGS_MEMBER_CRATES
from lib.update.paths import REPO_ROOT

_CARGO_NIX = "packages/zed-editor-nightly/Cargo.nix"


def _balanced_rec_binding(source: str, crate: str) -> str:
    """Return the quoted rec binding that names *crate*."""
    header = f'"{crate}" = rec {{'
    search_from = 0
    while True:
        start = source.find(header, search_from)
        if start < 0:
            msg = f"missing crate2nix rec binding for {crate}"
            raise AssertionError(msg)
        rec_open = start + len(header) - 1
        preview = source[rec_open + 1 : rec_open + 96]
        if f'crateName = "{crate}";' not in preview:
            search_from = rec_open + 1
            continue
        depth = 0
        for index, char in enumerate(source[rec_open:], rec_open):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return source[start : index + 1]
        msg = f"unbalanced crate2nix rec for {crate}"
        raise AssertionError(msg)


def _internal_crate(crate: str) -> AttributeSet:
    source = (REPO_ROOT / _CARGO_NIX).read_text(encoding="utf-8")
    binding = _balanced_rec_binding(source, crate)
    expr = parse_nix_expr("{ " + binding + "; }")
    attrs = expect_instance(expr, AttributeSet)
    return expect_instance(
        expect_binding(attrs.values, f'"{crate}"').value,
        AttributeSet,
    )


def _package_ids(deps: NixList) -> tuple[str, ...]:
    names: list[str] = []
    for item in deps.value:
        dep = expect_instance(item, AttributeSet)
        package_id = expect_instance(
            expect_binding(dep.values, "packageId").value,
            StringPrimitive,
        )
        names.append(package_id.value)
    return tuple(names)


def test_zed_settings_crate2nix_depends_on_content_cluster() -> None:
    """#1269 E0463 is not a missing crate2nix edge.

    The generated ``settings`` crate lists ``settings_content``,
    ``settings_json``, and ``settings_macros``. Hosted slot 3 fetched
    those rlibs from gkze and then rustc E0463'd — rustc-intern / SVH
    mix (nixpkgs#482646), same class as extension_host / settings_ui.
    """
    settings = _internal_crate("settings")
    crate_name = expect_instance(
        expect_binding(settings.values, "crateName").value,
        StringPrimitive,
    )
    assert crate_name.value == "settings"
    dependencies = expect_instance(
        expect_binding(settings.values, "dependencies").value,
        NixList,
    )
    package_ids = set(_package_ids(dependencies))
    assert {"settings_content", "settings_json", "settings_macros"} <= package_ids
    for crate in SETTINGS_MEMBER_CRATES:
        member = _internal_crate(crate)
        assert (
            expect_instance(
                expect_binding(member.values, "crateName").value,
                StringPrimitive,
            ).value
            == crate
        )
