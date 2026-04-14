"""Public API smoke tests for ``default.nix`` exports."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from nix_manipulator.expressions.binding import Binding

from lib.update.flake import nixpkgs_expression
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


def _nixpkgs_binding(name: str = "nixpkgs") -> str:
    """Return a nix-manipulator binding for the active nixpkgs expression."""
    return Binding(name=name, value=nixpkgs_expression()).rebuild()


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_default_nix_public_export_metadata_stays_in_sync() -> None:
    """Default, exports, and modules entrypoints should stay perfectly symmetrical."""
    root = Path(REPO_ROOT).resolve()
    expr = f"""
let
  exports = import {root}/lib/exports.nix {{
    src = {root};
  }};
  flake = import {root}/default.nix {{
    src = {root};
  }};
  moduleEntryPoint = import {root}/modules {{
    src = {root};
  }};
  stringifyModuleSet = builtins.mapAttrs (_: modulePath: toString modulePath);
  stringifyModuleSets = builtins.mapAttrs (_: moduleSet: stringifyModuleSet moduleSet);
  topLevelModuleSets = {{
    inherit (flake) darwinModules homeModules nixosModules;
  }};
in {{
  canonicalApiVersion = exports.api.version;
  flakeApiVersion = flake.api.version;
  flakeTopLevelApiVersion = flake.apiVersion;

  canonicalConstructorNames = exports.constructorNames;
  canonicalApiConstructorNames = exports.api.constructorNames;
  flakeApiConstructorNames = flake.api.constructorNames;
  flakeTopLevelConstructorNames = flake.constructorNames;

  canonicalModuleSets = stringifyModuleSets exports.moduleSets;
  canonicalTopLevelModuleSets = stringifyModuleSets {{
    inherit (exports) darwinModules homeModules nixosModules;
  }};
  flakeApiModuleSets = stringifyModuleSets flake.api.moduleSets;
  flakeTopLevelModuleSets = stringifyModuleSets topLevelModuleSets;
  flakeModulesAlias = stringifyModuleSets flake.modules;
  moduleEntryPoint = stringifyModuleSets moduleEntryPoint;
}}
"""

    result = json.loads(_nix_eval(expr=expr, mode="json"))

    assert (
        result["canonicalApiVersion"]
        == result["flakeApiVersion"]
        == result["flakeTopLevelApiVersion"]
    )
    assert (
        result["canonicalConstructorNames"]
        == result["canonicalApiConstructorNames"]
        == result["flakeApiConstructorNames"]
        == result["flakeTopLevelConstructorNames"]
    )
    canonical_module_sets = result["canonicalModuleSets"]
    assert result["canonicalTopLevelModuleSets"] == canonical_module_sets
    assert result["flakeApiModuleSets"] == canonical_module_sets
    assert result["flakeTopLevelModuleSets"] == canonical_module_sets
    assert result["flakeModulesAlias"] == canonical_module_sets
    assert result["moduleEntryPoint"] == canonical_module_sets


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_default_nix_constructors_follow_export_list() -> None:
    """Constructors attrset should be derived from the public constructor export list."""
    root = Path(REPO_ROOT).resolve()
    expr = f"""
let
  {_nixpkgs_binding()}
  flake = import {root}/default.nix {{
    src = {root};
    lib = nixpkgs.lib;
    pkgsFor = {{ }};
  }};
in {{
  expected = builtins.sort builtins.lessThan flake.constructorNames;
  actual = builtins.sort builtins.lessThan (builtins.attrNames flake.constructors);
  allFunctions = nixpkgs.lib.all (
    name: builtins.isFunction (builtins.getAttr name flake.constructors)
  ) flake.constructorNames;
}}
"""

    result = json.loads(_nix_eval(expr=expr, mode="json"))

    assert result["expected"] == result["actual"]
    assert result["allFunctions"] is True


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_mkpackages_injects_self_source_for_source_backed_wrappers() -> None:
    """MkPackages should supply ``selfSource`` for source-backed package wrappers."""
    root = Path(REPO_ROOT).resolve()
    expr = f"""
let
  {_nixpkgs_binding()}
  flake = import {root}/default.nix {{
    src = {root};
  }};

  outputsArg = rec {{
    lib = nixpkgs.lib // rec {{
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
    callPackage = pkg: args:
      let
        resolvedArgs = args // {{
          mkDmgApp = attrs: attrs;
          lib = fakeLib;
        }};
      in
      if builtins.isPath pkg then import pkg resolvedArgs else pkg resolvedArgs;
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
def test_mkpackages_allows_extra_package_args_to_override_self_source() -> None:
    """MkPackages should preserve explicit ``selfSource`` overrides."""
    root = Path(REPO_ROOT).resolve()
    expr = f"""
let
  {_nixpkgs_binding()}
  flake = import {root}/default.nix {{
    src = {root};
  }};

  outputsArg = rec {{
    lib = nixpkgs.lib // rec {{
      sources.wispr-flow = {{ version = "1.4.661"; }};
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
    callPackage = pkg: args:
      let
        resolvedArgs = args // {{
          mkDmgApp = attrs: attrs;
          lib = fakeLib;
        }};
      in
      if builtins.isPath pkg then import pkg resolvedArgs else pkg resolvedArgs;
  }};
in
  ((flake.mkPackages {{
    pkgs = fakePkgs;
    system = "aarch64-darwin";
    inherit outputsArg;
    extraPackageArgs = {{
      selfSource = {{ version = "override"; }};
    }};
  }}).wispr-flow).info.version
"""

    assert _nix_eval(expr=expr) == "override"


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_package_materialization_helper_keeps_mkpackages_and_flakelight_in_sync() -> (
    None
):
    """Shared package materialization should drive both mkPackages and flakelight."""
    root = Path(REPO_ROOT).resolve()
    expr = f"""
let
  {_nixpkgs_binding()}
  outputsArg = rec {{
    lib = nixpkgs.lib // rec {{
      sources = {{
        wispr-flow = {{ version = "1.4.661"; }};
      }};
      sourceEntry = name: sources.${{name}};
    }};
  }};
  baseHelper = import {root}/lib/package-materialization.nix {{
    src = {root};
  }};
  helper = import {root}/lib/package-materialization.nix {{
    src = {root};
    lib = outputsArg.lib;
    outputs = outputsArg;
  }};
  registry = import {root}/packages/registry.nix {{
    src = {root};
  }};
  flake = import {root}/default.nix {{
    src = {root};
  }};
  flakelightPackages = import {root}/packages/default.nix {{
    system = "aarch64-darwin";
    lib = outputsArg.lib;
    outputs = outputsArg;
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
    callPackage = pkg: args:
      let
        resolvedArgs = args // {{
          mkDmgApp = attrs: attrs;
          lib = fakeLib;
        }};
      in
      if builtins.isPath pkg then import pkg resolvedArgs else pkg resolvedArgs;
  }};

  helperPackages = helper.packageFunctionsForSystem "aarch64-darwin";
  mkPackages = flake.mkPackages {{
    pkgs = fakePkgs;
    system = "aarch64-darwin";
    inherit outputsArg;
  }};
  names = attrs: builtins.sort builtins.lessThan (builtins.attrNames attrs);
in {{
  registryPathNames = names registry.packagePaths;
  baseHelperNames = baseHelper.packageNames;
  baseHelperPathNames = names baseHelper.packagePaths;
  flakePackageNames = flake.packageNames;
  flakePackagePathNames = names flake.packagePaths;
  helperNames = names helperPackages;
  mkPackagesNames = names mkPackages;
  flakelightNames = names flakelightPackages;
  selectedPaths = {{
    registry = toString registry.packagePaths.wispr-flow;
    baseHelper = toString baseHelper.packagePaths.wispr-flow;
    flake = toString flake.packagePaths.wispr-flow;
  }};
  helperWisprVersion = (helperPackages.wispr-flow {{
    mkDmgApp = attrs: attrs;
    lib = fakeLib;
  }}).info.version;
  mkPackagesWisprVersion = mkPackages.wispr-flow.info.version;
  flakelightWisprVersion = (flakelightPackages.wispr-flow {{
    mkDmgApp = attrs: attrs;
    lib = fakeLib;
  }}).info.version;
}}
"""

    result = json.loads(_nix_eval(expr=expr, mode="json"))

    assert result["registryPathNames"] == result["baseHelperNames"]
    assert result["registryPathNames"] == result["baseHelperPathNames"]
    assert result["registryPathNames"] == result["flakePackageNames"]
    assert result["registryPathNames"] == result["flakePackagePathNames"]
    assert result["registryPathNames"] == result["helperNames"]
    assert result["helperNames"] == result["mkPackagesNames"]
    assert result["helperNames"] == result["flakelightNames"]
    assert result["selectedPaths"]["registry"] == result["selectedPaths"]["baseHelper"]
    assert result["selectedPaths"]["registry"] == result["selectedPaths"]["flake"]
    assert "wispr-flow" in result["helperNames"]
    assert result["helperWisprVersion"] == "1.4.661"
    assert result["helperWisprVersion"] == result["mkPackagesWisprVersion"]
    assert result["helperWisprVersion"] == result["flakelightWisprVersion"]


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_package_self_source_helper_reuses_call_and_function_injection_paths() -> None:
    """Shared selfSource helper should serve both callPackage and function wrapping."""
    root = Path(REPO_ROOT).resolve()
    expr = f"""
let
  {_nixpkgs_binding()}
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
