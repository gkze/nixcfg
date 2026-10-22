"""Evaluate the Darwin heredoc backport boundary without building Bash."""

import json

import pytest

from lib.tests._nix_ast import parse_nix_expr
from lib.tests._nix_eval import nix_eval_json
from lib.update.paths import REPO_ROOT


@pytest.mark.nix_eval(
    reason="Only evaluation verifies that the real overlay leaves fixed and Linux derivations untouched."
)
@pytest.mark.parametrize(
    ("version", "darwin", "patched"),
    [
        ("5.3p15", True, True),
        ("5.3p16", True, False),
        ("5.3p20", True, False),
        ("5.3p15", False, False),
    ],
)
def test_dynamic_pipe_backport_selection(
    version: str, *, darwin: bool, patched: bool
) -> None:
    """Patch the affected release; retain upstream fixes and platform behavior."""
    overlay = REPO_ROOT / "overlays/nix-prefetch-git/default.nix"
    expression = parse_nix_expr(
        f"""
        let
          prev = {{
            stdenv.hostPlatform.isDarwin = {str(darwin).lower()};
            bashNonInteractive = {{
              version = {json.dumps(version)};
              outPath = "/original-bash";
              overrideAttrs = f:
                let changes = f {{
                  patches = [ "existing-patch" ];
                  env.EXISTING = "preserved";
                  postPatch = "existing-post-patch";
                }};
                in changes // {{
                  outPath = "/backported-bash";
                  patchCount = builtins.length changes.patches;
                }};
            }};
          }};
          final = prev // (import {overlay} {{ inherit prev final; }});
          bash = final.bash-dynamic-pipe-heredoc;
        in {{
          selectedPath = bash.outPath;
          patchCount = bash.patchCount or 0;
          env = bash.env or {{}};
          postPatch = bash.postPatch or "";
        }}
        """
    )
    result = nix_eval_json(expression)
    if patched:
        assert result == {
            "selectedPath": "/backported-bash",
            "patchCount": 2,
            "env": {"EXISTING": "preserved", "BASH_COMPAT": "5.0"},
            "postPatch": "existing-post-patchtouch configure\n",
        }
    else:
        assert result == {
            "selectedPath": "/original-bash",
            "patchCount": 0,
            "env": {},
            "postPatch": "",
        }
