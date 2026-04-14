"""Regression checks for home package-set platform guards."""

from __future__ import annotations

from lib.update.paths import REPO_ROOT


def test_heavy_optional_goose_cli_is_guarded_to_aarch64_darwin() -> None:
    """Keep ``goose-cli`` out of unsupported Home Manager package-set evals."""
    module_text = (REPO_ROOT / "modules/home/packages.nix").read_text(encoding="utf-8")

    assert (
        "lib.optionals (stdenv.isDarwin && stdenv.hostPlatform.isAarch64) [\n"
        "          goose-cli\n"
        "        ]"
    ) in module_text
