{
  lib,
  python314,
  outputs,
  selfSource,
  stdenv,
  ...
}:
let
  parts = lib.splitVersion selfSource.version;
  python =
    (python314.override (oldArgs: {
      self = python;
      # This is a native user interpreter. Keep the native packaging helpers on
      # the same source release; upstream filters sourceVersion from splices.
      __splices = {
        pythonOnBuildForBuild = python;
        pythonOnBuildForHost = python;
        pythonOnBuildForTarget = python;
        pythonOnHostForHost = python;
        pythonOnTargetForTarget = python;
      };
      sourceVersion = {
        major = builtins.elemAt parts 0;
        minor = builtins.elemAt parts 1;
        patch = builtins.elemAt parts 2;
        suffix = "";
      };
      hash = outputs.lib.sourceHash "python-current" "sha256";
      packageOverrides = lib.composeExtensions (oldArgs.packageOverrides or (_: _: { })) (
        _: prev:
        lib.optionalAttrs stdenv.hostPlatform.isDarwin {
          pytest-xdist = prev.pytest-xdist.overridePythonAttrs (old: {
            # Worker debug callbacks can outlive pytest's temporary capture stream.
            patches = (old.patches or [ ]) ++ [ ./pytest-xdist-stderr.patch ];
          });
        }
      );
    })).overrideAttrs
      (old: {
        # Preserve the Windows security branch while pinning Unix MIME data to Nix.
        patches = map (
          patch: if baseNameOf patch == "mimetypes.patch" then ./mimetypes-security-branch.patch else patch
        ) old.patches;
      });
in
assert stdenv.buildPlatform == stdenv.hostPlatform;
assert lib.versions.majorMinor selfSource.version == lib.versions.majorMinor python314.version;
python
