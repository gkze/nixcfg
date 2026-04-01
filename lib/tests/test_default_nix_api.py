"""Public API smoke tests for ``default.nix`` exports."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from lib.update.paths import REPO_ROOT


def _nix_eval(*, expr: str, mode: str = "raw") -> str:
    """Evaluate a Nix expression and return stdout."""
    nix = shutil.which("nix")
    assert nix is not None
    command = [nix, "eval", "--impure"]
    if mode == "json":
        command.append("--json")
    else:
        command.append("--raw")
    command.extend(["--expr", expr])
    result = subprocess.run(  # noqa: S603
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_mkpackages_injects_self_source_for_source_backed_wrappers() -> None:
    """MkPackages should supply ``selfSource`` for source-backed package wrappers."""
    root = Path(REPO_ROOT).resolve()
    expr = f"""
let
  nixpkgs = builtins.getFlake "nixpkgs";
  lib = nixpkgs.lib;
  flake = import {root}/default.nix {{
    src = {root};
    inherit lib;
  }};

  outputsArg = rec {{
    lib = rec {{
      sources = {{
        wispr-flow = {{
          version = "1.4.661";
          urls = {{
            "aarch64-darwin" = "https://example.invalid/wispr-flow.dmg";
          }};
          hashes = {{
            "aarch64-darwin" = "sha256-HNPxj7QZOj5tzuSSQaUp9JvaCi6dpQEy5GY5Xv76QjU=";
          }};
        }};
      }};

      optionalAttrs = cond: attrs: if cond then attrs else {{ }};
      sourceEntry = name: sources.${{name}};
    }};
  }};

  fakeLib = {{
    licenses = {{
      unfree = "unfree";
    }};
    platforms = {{
      darwin = [ "aarch64-darwin" ];
    }};
    sourceTypes = {{
      binaryNativeCode = "binaryNativeCode";
    }};
  }};

  fakePkgs = {{
    stdenv.hostPlatform.system = "aarch64-darwin";
    callPackage = path: args: import path (args // {{
      mkDmgApp = attrs: attrs;
      lib = fakeLib;
    }});
  }};
in
  ((flake.mkPackages {{
    pkgs = fakePkgs;
    system = "aarch64-darwin";
    inherit outputsArg;
  }}).wispr-flow).info.version
"""

    assert _nix_eval(expr=expr) == "1.4.661"


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_package_self_source_helper_reuses_call_and_function_injection_paths() -> None:
    """Shared selfSource helper should serve both callPackage and function wrapping."""
    root = Path(REPO_ROOT).resolve()
    expr = f"""
let
  nixpkgs = builtins.getFlake "nixpkgs";
  helper = import {root}/lib/package-self-source.nix {{
    lib = nixpkgs.lib;
    outputs = rec {{
      lib = rec {{
        sources.demo = {{ version = "1.2.3"; }};
        sourceEntry = name: sources.${{name}};
      }};
    }};
  }};
  wrapped = helper.injectIntoFunction "demo" (
    {{ selfSource, suffix ? "" }}: selfSource.version + suffix
  );
in {{
  callPackageVersion = (helper.callPackageArgs "demo").selfSource.version;
  wrappedDefault = wrapped {{ suffix = "-ok"; }};
  wrappedOverride = wrapped {{
    selfSource = {{ version = "override"; }};
  }};
}}
"""

    assert json.loads(_nix_eval(expr=expr, mode="json")) == {
        "callPackageVersion": "1.2.3",
        "wrappedDefault": "1.2.3-ok",
        "wrappedOverride": "override",
    }
