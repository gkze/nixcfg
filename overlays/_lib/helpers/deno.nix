{
  prev,
  slib,
  system,
  ...
}:
{
  mkDenoApplication =
    {
      pname,
      version,
      src,
      denoDepsSrc,
      entrypoint ? "src/main.ts",
      denoFlags ? "-A",
      deno ? prev.deno,
      preBuild ? "",
      meta ? { },
    }:
    let
      manifest = builtins.fromJSON (builtins.readFile denoDepsSrc);
      denortSource = slib.sourceHashEntryForPlatform pname "sha256" system;
      denortUrl =
        denortSource.url or (throw "mkDenoApplication: missing denort url for ${pname}:${system}");
      denortReleasePath = builtins.match ".*/release/([^/]+)/([^/?]+)(\\?.*)?$" denortUrl;
      denortVersionPath =
        if denortReleasePath == null then
          throw "mkDenoApplication: denort url must include /release/<version>/<file>: ${denortUrl}"
        else
          builtins.elemAt denortReleasePath 0;
      denortFileName =
        if denortReleasePath == null then
          throw "mkDenoApplication: denort url must include filename: ${denortUrl}"
        else
          builtins.elemAt denortReleasePath 1;
      denortZip = prev.fetchurl {
        name = denortFileName;
        url = denortUrl;
        inherit (denortSource) hash;
      };

      jsrFiles = builtins.concatMap (
        pkg:
        builtins.map (
          f:
          prev.fetchurl {
            inherit (f) url sha256;
            name =
              builtins.replaceStrings [ "/" "@" ] [ "_" "_" ]
                "${pkg.name}-${pkg.version}-${builtins.baseNameOf f.url}";
            curlOptsList = [ "--globoff" ];
            passthru = {
              inherit (f) cache_path media_type url;
            };
          }
        ) pkg.files
      ) (manifest.jsr_packages or [ ]);

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

      denoDeps = prev.stdenvNoCC.mkDerivation {
        name = "${pname}-deno-deps";
        nativeBuildInputs = [
          prev.gnutar
          prev.gzip
        ];
        dontUnpack = true;
        buildPhase = ''
          mkdir -p $out

          mkdir -p "$out/dl/release/${denortVersionPath}"
          cp ${denortZip} "$out/dl/release/${denortVersionPath}/${denortFileName}"

          while IFS=$'\t' read -r store_path cache_path media_type url; do
            [ -z "$store_path" ] && continue
            mkdir -p "$out/$(dirname "$cache_path")"
            cp "$store_path" "$out/$cache_path"
            chmod u+w "$out/$cache_path"
            printf '\n// denoCacheMetadata={"headers":{"content-type":"%s"},"time":0,"url":"%s"}' \
              "$media_type" "$url" >> "$out/$cache_path"
          done < ${jsrManifestFile}

          while IFS=$'\t' read -r store_path cache_path; do
            [ -z "$store_path" ] && continue
            mkdir -p "$out/$cache_path"
            tar xzf "$store_path" -C "$out/$cache_path" --strip-components=1
          done < ${npmManifestFile}
        '';
        installPhase = "true";
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
}
