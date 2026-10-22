"""The Nix-owned Brew Ruby bootstrap must not scan user platform-specific extensions."""

import os
import shutil
import subprocess

import pytest

from lib.tests._nix_eval import nix_eval_raw, nix_import
from lib.update.paths import REPO_ROOT


@pytest.mark.skipif(shutil.which("nix") is None, reason="requires Nix evaluation")
@pytest.mark.nix_eval(
    reason="Validate rendered Bash exports after Nix path interpolation without evaluating a host"
)
@pytest.mark.parametrize(
    "inherited", [{}, {"GEM_HOME": "/foreign/home", "GEM_PATH": "/foreign/path"}]
)
def test_bootstrap_isolates_gems_and_preserves_ruby_setup(
    inherited: dict[str, str],
) -> None:
    """Evaluate only the tiny helper; Bash validates its emitted environment semantics."""
    rendered = nix_eval_raw(
        nix_import(REPO_ROOT / "tests/nix/homebrew-ruby-bootstrap.nix")
    )
    probe = (
        rendered
        + '\nprintf "%s\\n" "$GEM_HOME" "$GEM_PATH" "$HOMEBREW_RUBY_DISABLE_OPTIONS" "$(find_ruby)"; system_ruby_supported\n'
    )
    result = subprocess.run(  # noqa: S603 -- Execute the owned, locally rendered bootstrap.
        ["/bin/bash", "-c", probe],
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": os.defpath, **inherited},
    )
    assert result.stderr == ""
    assert result.stdout.splitlines() == [
        "/nix/store/test-ruby/lib/ruby/gems/4.0.0",
        "/nix/store/test-ruby/lib/ruby/gems/4.0.0",
        "--disable=rubyopt",
        "/nix/store/test-ruby/bin/ruby",
    ]
