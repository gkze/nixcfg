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

  # ---------------------------------------------------------------------------
  # mkDenoApplication – deterministic Deno application builder
  # ---------------------------------------------------------------------------
  #
  # Builds a Deno application from source using a pre-resolved dependency
  # manifest (deno-deps.json) instead of a non-deterministic FOD.  Each
  # dependency is fetched individually via fetchurl, assembled into a
  # synthetic DENO_DIR, and deno compile runs with --cached-only.
  #
  mkDenoApplication =
    {
      pname,
      version,
      src,
      denoDepsSrc, # path to the deno-deps.json manifest
      entrypoint ? "src/main.ts",
      denoFlags ? "-A",
      deno ? prev.deno,
      preBuild ? "",
      meta ? { },
    }:
    let
      manifest = builtins.fromJSON (builtins.readFile denoDepsSrc);

      # Fetch all JSR source files individually (including meta.json files).
      jsrFiles = builtins.concatMap (
        pkg:
        builtins.map (
          f:
          prev.fetchurl {
            inherit (f) url sha256;
            name =
              builtins.replaceStrings [ "/" "@" ] [ "_" "_" ]
                "${pkg.name}-${pkg.version}-${builtins.baseNameOf f.url}";
            # Disable curl glob parsing so URLs with brackets (e.g.
            # testdata/glob/a[b]c/foo) are fetched correctly.
            curlOptsList = [ "--globoff" ];
            # Store the cache_path and media_type as passthru for the
            # assembly script.
            passthru = {
              inherit (f) cache_path media_type url;
            };
          }
        ) pkg.files
      ) (manifest.jsr_packages or [ ]);

      # Fetch all npm tarballs.
      npmTarballs = builtins.map (
        pkg:
        prev.fetchurl {
          url = pkg.tarball_url;
          hash = pkg.integrity;
          name = builtins.replaceStrings [ "/" "@" ] [ "_" "_" ] "${pkg.name}-${pkg.version}.tgz";
          passthru = {
            inherit (pkg) cache_path name version;
          };
        }
      ) (manifest.npm_packages or [ ]);

      # Manifest files describing where to place each fetched file.
      # Using writeText avoids inlining thousands of commands in buildPhase
      # which would exceed the OS argument-size limit (E2BIG).
      jsrManifestFile = prev.writeText "${pname}-jsr-manifest.tsv" (
        builtins.concatStringsSep "" (
          builtins.map (
            f:
            let
              p = f.passthru;
            in
            "${f}\t${p.cache_path}\t${p.media_type}\t${p.url}\n"
          ) jsrFiles
        )
      );

      npmManifestFile = prev.writeText "${pname}-npm-manifest.tsv" (
        builtins.concatStringsSep "" (
          builtins.map (
            t:
            let
              p = t.passthru;
            in
            "${t}\t${p.cache_path}\n"
          ) npmTarballs
        )
      );

      # Build the synthetic DENO_DIR.
      denoDeps = prev.stdenvNoCC.mkDerivation {
        name = "${pname}-deno-deps";
        nativeBuildInputs = [
          prev.gnutar
          prev.gzip
        ];
        dontUnpack = true;
        buildPhase = ''
          mkdir -p $out

          # --- JSR source files ---
          while IFS=$'\t' read -r store_path cache_path media_type url; do
            [ -z "$store_path" ] && continue
            mkdir -p "$out/$(dirname "$cache_path")"
            cp "$store_path" "$out/$cache_path"
            chmod u+w "$out/$cache_path"
            # Deno requires "time" in the metadata (unix seconds); 0 is fine.
            # No trailing newline — Deno's parser expects the file to end with '}'.
            printf '\n// denoCacheMetadata={"headers":{"content-type":"%s"},"time":0,"url":"%s"}' \
              "$media_type" "$url" >> "$out/$cache_path"
          done < ${jsrManifestFile}

          # --- NPM tarballs (extract to cache layout) ---
          while IFS=$'\t' read -r store_path cache_path; do
            [ -z "$store_path" ] && continue
            mkdir -p "$out/$cache_path"
            tar xzf "$store_path" -C "$out/$cache_path" --strip-components=1
          done < ${npmManifestFile}
        '';
        installPhase = "true"; # buildPhase writes directly to $out
      };
    in
    prev.stdenvNoCC.mkDerivation {
      inherit
        pname
        version
        src
        meta
        ;
      nativeBuildInputs = [
        deno
        prev.installShellFiles
      ];
      buildPhase = ''
        export DENO_DIR=$(mktemp -d)
        cp -r ${denoDeps}/* $DENO_DIR/
        chmod -R u+w $DENO_DIR
        export HOME=$TMPDIR

        ${preBuild}

        deno compile ${denoFlags} --cached-only --lock=deno.lock --output $pname ${entrypoint}
      '';
      installPhase = ''
        mkdir -p $out/bin
        cp $pname $out/bin/
      '';
      passthru = { inherit denoDeps; };
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
