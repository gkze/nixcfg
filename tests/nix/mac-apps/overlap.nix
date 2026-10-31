# `managedAppsNotInPackageListsAssertion` is the guard that stops a managed
# application bundle from also arriving through `home.packages` or a system
# package list. The conflict report is user-visible activation output, so it is
# asserted on evaluated values rather than on the function's source shape.
{
  system ? "aarch64-darwin",
}:
let
  inherit (import ../lib.nix) assertEq;

  context = import ./eval-context.nix { };
  inherit (context) macApps;

  fakePackage =
    {
      pname,
      outPath,
      bundleName,
      bundleRelPath ? null,
      installMode ? null,
    }:
    {
      inherit pname outPath;
      passthru.macApp = {
        inherit bundleName;
        bundleRelPath = if bundleRelPath != null then bundleRelPath else "Applications/${bundleName}";
      }
      // (if installMode != null then { inherit installMode; } else { });
    };

  # The managed entry the configuration declares.
  managedEntry = package: bundleName: {
    inherit package bundleName;
    mode = "copy";
  };

  managedCursor = managedEntry (fakePackage {
    pname = "cursor";
    outPath = "/nix/store/fake-cursor";
    bundleName = "Cursor.app";
  }) "Cursor.app";

  assertionFor =
    entries: packageLists:
    macApps.managedAppsNotInPackageListsAssertion {
      inherit entries packageLists;
    };

  baseMessage = "nixcfg.macApps.applications packages must not also appear in other installed package lists.";

  # A package in another list that carries the same store path.
  sameOutPath = label: package: {
    inherit label;
    packages = [ package ];
  };

  conflict =
    assertionFor
      [ managedCursor ]
      [
        (sameOutPath "home.packages" (fakePackage {
          pname = "cursor-wrapper";
          outPath = "/nix/store/fake-cursor";
          bundleName = "Cursor.app";
        }))
      ];

  distinct =
    assertionFor
      [ managedCursor ]
      [
        (sameOutPath "home.packages" (fakePackage {
          pname = "unrelated";
          outPath = "/nix/store/fake-unrelated";
          bundleName = "Unrelated.app";
        }))
      ];

  # Matching on bundle name alone is still a conflict: the same bundle would be
  # materialized twice from different derivations.
  sameBundleName =
    assertionFor
      [ managedCursor ]
      [
        (sameOutPath "home.packages" (fakePackage {
          pname = "cursor-other";
          outPath = "/nix/store/fake-cursor-other";
          bundleName = "Cursor.app";
        }))
      ];

  # An unreadable output path is ignored instead of failing the assertion.
  unevaluable =
    assertionFor
      [ managedCursor ]
      [
        (sameOutPath "home.packages" {
          pname = "broken";
          outPath = throw "outPath must not be forced";
          passthru.macApp.bundleName = "Broken.app";
        })
      ];

  ordering =
    assertionFor
      [ managedCursor ]
      [
        (sameOutPath "system.packages" (fakePackage {
          pname = "cursor-system";
          outPath = "/nix/store/fake-cursor";
          bundleName = "Cursor.app";
        }))
        (sameOutPath "home.packages" (fakePackage {
          pname = "cursor-home";
          outPath = "/nix/store/fake-cursor";
          bundleName = "Cursor.app";
        }))
      ];

  # A store path carrying string context still compares equal to a plain one.
  drv = builtins.derivation {
    name = "fake-cursor";
    inherit system;
    builder = "/bin/false";
  };
  contextual =
    assertionFor
      [
        (managedEntry {
          pname = "cursor";
          inherit (drv) outPath;
          passthru.macApp.bundleName = "Cursor.app";
        } "Cursor.app")
      ]
      [
        (sameOutPath "home.packages" (fakePackage {
          pname = "cursor-wrapper";
          outPath = "${drv}";
          bundleName = "Cursor.app";
        }))
      ];

  checks = [
    (assertEq "a package that also arrives through another list is a conflict" {
      assertion = false;
      message = "${baseMessage}\n- Cursor.app (cursor) also appears in home.packages as cursor-wrapper.";
    } conflict)
    (assertEq "a distinct package list is accepted" {
      assertion = true;
      message = baseMessage;
    } distinct)
    (assertEq "an identical bundle name alone is a conflict" {
      assertion = false;
      message = "${baseMessage}\n- Cursor.app (cursor) also appears in home.packages as cursor-other.";
    } sameBundleName)
    (assertEq "an unevaluable package output is ignored" {
      assertion = true;
      message = baseMessage;
    } unevaluable)
    (assertEq "conflicts keep package-list order" {
      assertion = false;
      message = "${baseMessage}\n- Cursor.app (cursor) also appears in system.packages as cursor-system.\n- Cursor.app (cursor) also appears in home.packages as cursor-home.";
    } ordering)
    (assertEq "a context-carrying store path compares equal" false contextual.assertion)
  ];
in
builtins.deepSeq checks true
