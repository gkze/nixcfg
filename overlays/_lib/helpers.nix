{
  inputs,
  final,
  prev,
  system,
  slib,
  sources,
  ...
}:
{
  craneLib = inputs.crane.mkLib final;

  mkGoCliPackage =
    {
      pname,
      input,
      subPackages,
      cmdName ? pname,
      version ? null,
      meta ? { },
      go ? prev.go,
      ...
    }@args:
    let
      flakeRef = slib.flakeLock.${pname};
      finalVersion =
        if version != null then version else slib.stripVersionPrefix (flakeRef.original.ref or "");
      buildGoModule =
        if go == prev.go then prev.buildGoModule else prev.buildGoModule.override { inherit go; };
    in
    buildGoModule (
      {
        inherit pname subPackages;
        version = finalVersion;
        src = input;
        vendorHash = slib.sourceHash pname "vendorHash";
        doCheck = false;
        nativeBuildInputs = [ prev.installShellFiles ];
        postInstall = ''
          installShellCompletion --cmd ${cmdName} \
            --bash <($out/bin/${cmdName} completion bash) \
            --fish <($out/bin/${cmdName} completion fish) \
            --zsh <($out/bin/${cmdName} completion zsh)
        '';
        meta = {
          mainProgram = cmdName;
        }
        // meta;
      }
      // (builtins.removeAttrs args [
        "pname"
        "input"
        "subPackages"
        "cmdName"
        "version"
        "meta"
        "go"
      ])
    );

  mkUv2nixPackage =
    {
      name,
      src,
      pythonVersion ? prev.python314,
      mainProgram,
      packageName ? name,
      venvName ? name,
      uvLockHash ? slib.sourceHash name "uvLockHash",
      extraBuildPhase ? "",
      extraOverlays ? [ ],
    }:
    let
      uv = prev.lib.getExe prev.uv;
      python = prev.lib.getExe pythonVersion;

      # FOD that runs `uv lock` with network access, producing just the lockfile.
      uvLock = prev.stdenv.mkDerivation {
        name = "${name}-uv-lock";
        inherit src;
        nativeBuildInputs = [
          prev.uv
          pythonVersion
          prev.cacert
        ];
        buildPhase = ''
          export HOME=$TMPDIR
          export SSL_CERT_FILE=${prev.cacert}/etc/ssl/certs/ca-bundle.crt
          ${extraBuildPhase}
          UV_PYTHON=${python} ${uv} -n lock
        '';
        installPhase = "cp uv.lock $out";
        outputHashMode = "flat";
        outputHashAlgo = "sha256";
        outputHash = uvLockHash;
      };

      workspaceRoot = prev.runCommand "${name}-workspace" { } ''
        cp -r ${src} $out
        chmod -R u+w $out
        cp -f ${uvLock} $out/uv.lock
      '';

      workspace = inputs.uv2nix.lib.workspace.loadWorkspace {
        inherit workspaceRoot;
      };
      pySet =
        (prev.callPackage inputs.pyproject-nix.build.packages {
          python = pythonVersion;
        }).overrideScope
          (
            prev.lib.composeManyExtensions (
              [
                inputs.pyproject-build-systems.overlays.default
                (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
              ]
              ++ extraOverlays
            )
          );
    in
    (prev.callPackages inputs.pyproject-nix.build.util { }).mkApplication {
      venv = pySet.mkVirtualEnv venvName workspace.deps.all // {
        meta.mainProgram = mainProgram;
      };
      package = pySet.${packageName};
    };

  mkDmgApp =
    {
      pname,
      info,
      appName ? pname,
      meta ? { },
    }:
    let
      arch = if system == "aarch64-darwin" then "aarch64" else "x86_64";
      capitalizedAppName =
        (prev.lib.toUpper (builtins.substring 0 1 appName)) + builtins.substring 1 (-1) appName;
    in
    prev.stdenvNoCC.mkDerivation {
      inherit pname;
      inherit (info) version;
      inherit meta;

      src = prev.fetchurl {
        name = "${capitalizedAppName}_${info.version}_${arch}.dmg";
        url = info.urls.${system};
        hash = info.hashes.${system};
      };

      nativeBuildInputs = [ prev.undmg ];

      sourceRoot = ".";

      installPhase = ''
        runHook preInstall

        mkdir -p "$out/Applications"
        mkdir -p "$out/bin"
        cp -a ${capitalizedAppName}.app "$out/Applications"
        /usr/bin/xattr -cr "$out/Applications/${capitalizedAppName}.app"
        ln -s "$out/Applications/${capitalizedAppName}.app/Contents/MacOS/${capitalizedAppName}" "$out/bin/${pname}"

        runHook postInstall
      '';
    };

  mkSourceOverride =
    name: pkg:
    let
      info = sources.${name};
    in
    pkg.overrideAttrs {
      inherit (info) version;
      src = prev.fetchurl {
        url = info.urls.${system} or (throw "sources.${name}.urls missing ${system}");
        hash = info.hashes.${system};
      };
    };
}
