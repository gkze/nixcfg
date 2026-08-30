{
  cmake,
  electronHeaders,
  lib,
  nodeAddonApiSrc,
  nodejs_24,
  onnxruntime,
  sherpa-onnx,
  src,
  stdenv,
  version,
  wrapperVersion,
  wrapperSrc,
}:
let
  runtimeLibraries = [
    {
      name = "libsherpa-onnx-c-api.dylib";
      source = "${lib.getLib sherpa-onnx}/lib/libsherpa-onnx-c-api.dylib";
    }
    {
      name = "libonnxruntime.1.dylib";
      source = "${lib.getLib onnxruntime}/lib/libonnxruntime.1.dylib";
    }
  ];
in
stdenv.mkDerivation {
  pname = "openchamber-sherpa-onnx-node";
  inherit src version;

  sourceRoot = "source/scripts/node-addon-api";
  nativeBuildInputs = [
    cmake
    nodejs_24
  ];
  buildInputs = [
    onnxruntime
    sherpa-onnx
  ];

  strictDeps = true;
  dontStrip = true;

  preConfigure = ''
    mkdir -p node_modules/node-addon-api
    tar -xzf ${nodeAddonApiSrc} \
      --strip-components=1 \
      -C node_modules/node-addon-api
    export SHERPA_ONNX_INSTALL_DIR=${sherpa-onnx}
  '';

  cmakeFlags = [
    "-DCMAKE_BUILD_TYPE=Release"
    "-DCMAKE_JS_INC=${electronHeaders}/include/node"
    "-DCMAKE_JS_LIB="
    "-DCMAKE_SHARED_LINKER_FLAGS=-Wl,-undefined,dynamic_lookup"
  ];

  installPhase = ''
    runHook preInstall

    wrapper="$out/node_modules/sherpa-onnx-node"
    platform="$out/node_modules/sherpa-onnx-darwin-arm64"
    mkdir -p "$wrapper" "$platform"
    tar -xzf ${wrapperSrc} --strip-components=1 -C "$wrapper"

    addon="$(find . -type f -name sherpa-onnx.node -print -quit)"
    if [ -z "$addon" ]; then
      echo "source-built sherpa-onnx.node was not produced" >&2
      exit 1
    fi
    install -m0755 "$addon" "$platform/sherpa-onnx.node"

    ${lib.concatMapStringsSep "\n" (library: ''
      if [ ! -f ${lib.escapeShellArg library.source} ]; then
        echo "missing expected sherpa runtime library: ${library.source}" >&2
        exit 1
      fi
      install -m0755 \
        ${lib.escapeShellArg library.source} \
        "$platform/${library.name}"
    '') runtimeLibraries}

    cp "$wrapper/package.json" "$platform/package.json"
    node - "$platform/package.json" \
      ${lib.escapeShellArg wrapperVersion} \
      ${lib.escapeShellArg version} <<'JS'
    const fs = require("fs")
    const path = process.argv[2]
    const wrapperVersion = process.argv[3]
    const addonVersion = process.argv[4]
    const manifest = JSON.parse(fs.readFileSync(path, "utf8"))
    const expected = {
      name: "sherpa-onnx-node",
      version: wrapperVersion,
      main: "sherpa-onnx.js",
    }
    for (const [field, value] of Object.entries(expected)) {
      if (manifest[field] !== value) {
        throw new Error(
          "unexpected sherpa wrapper " + field + ": " + JSON.stringify(manifest[field]),
        )
      }
    }
    manifest.name = "sherpa-onnx-darwin-arm64"
    manifest.version = addonVersion
    manifest.main = "sherpa-onnx.node"
    fs.writeFileSync(path, JSON.stringify(manifest, null, 2) + "\n")
    JS

    for binary in "$platform/sherpa-onnx.node" "$platform"/*.dylib "$platform"/*.dylib.*
    do
      if [ ! -e "$binary" ]; then
        continue
      fi
      case "$binary" in
        *.dylib|*.dylib.*)
          /usr/bin/install_name_tool -id "@loader_path/$(basename "$binary")" "$binary"
          ;;
      esac
      /usr/bin/otool -L "$binary" \
        | tail -n +2 \
        | awk '{ print $1 }' \
        | while IFS= read -r dependency
          do
            case "$dependency" in
              /nix/store/*)
                replacement="$platform/$(basename "$dependency")"
                if [ -e "$replacement" ]; then
                  /usr/bin/install_name_tool \
                    -change "$dependency" "@loader_path/$(basename "$dependency")" \
                    "$binary"
                elif [ ! -e "$dependency" ]; then
                  echo "missing managed sherpa dependency: $dependency" >&2
                  exit 1
                fi
                ;;
            esac
          done

      /usr/bin/otool -l "$binary" \
        | awk '
            $1 == "cmd" && $2 == "LC_RPATH" { in_rpath = 1; next }
            in_rpath && $1 == "path" { print $2; in_rpath = 0 }
          ' \
        | while IFS= read -r runtimeRpath
          do
            case "$runtimeRpath" in
              @loader_path|@loader_path/*)
                ;;
              *)
                /usr/bin/install_name_tool -delete_rpath "$runtimeRpath" "$binary"
                ;;
            esac
          done
    done

    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    platform="$out/node_modules/sherpa-onnx-darwin-arm64"
    nativeInventory="$TMPDIR/sherpa-native-inventory"
    : > "$nativeInventory"
    while IFS= read -r -d $'\0' candidate
    do
      description="$(/usr/bin/file -b "$candidate")"
      case "$description" in
        *Mach-O*)
          printf '%s\n' "$candidate" >> "$nativeInventory"
          architectures="$(/usr/bin/lipo -archs "$candidate")"
          if [ "$architectures" != arm64 ]; then
            echo "sherpa runtime is not arm64-only: $candidate ($architectures)" >&2
            exit 1
          fi
          candidateDirectory="$(dirname "$candidate")"
          /usr/bin/otool -L "$candidate" \
            | tail -n +2 \
            | sed 's/^[[:space:]]*//; s/ (compatibility version.*$//' \
            | while IFS= read -r dependency
              do
                case "$dependency" in
                  @loader_path/*)
                    linkedPath="$candidateDirectory/''${dependency#@loader_path/}"
                    if [ ! -e "$linkedPath" ]; then
                      echo "missing @loader_path sherpa dependency: $candidate -> $dependency" >&2
                      exit 1
                    fi
                    ;;
                  /nix/store/*)
                    if [ ! -e "$dependency" ]; then
                      echo "missing managed Nix-store sherpa dependency: $candidate -> $dependency" >&2
                      exit 1
                    fi
                    dependencyDescription="$(/usr/bin/file -b "$dependency")"
                    case "$dependencyDescription" in
                      *Mach-O*) ;;
                      *)
                        echo "managed sherpa dependency is not Mach-O: $candidate -> $dependency" >&2
                        exit 1
                        ;;
                    esac
                    dependencyArchitectures="$(/usr/bin/lipo -archs "$dependency")"
                    if [ "$dependencyArchitectures" != arm64 ]; then
                      echo "managed sherpa dependency is not arm64-only: $candidate -> $dependency ($dependencyArchitectures)" >&2
                      exit 1
                    fi
                    ;;
                esac
              done
          ;;
        *)
          case "$candidate" in
            *.node|*.dylib|*.dylib.*)
              echo "sherpa native-looking file is not Mach-O: $candidate" >&2
              exit 1
              ;;
          esac
          ;;
      esac
    done < <(find "$platform" -type f -print0)
    test -s "$nativeInventory"
    grep -Fqx "$platform/sherpa-onnx.node" "$nativeInventory"

    DYLD_LIBRARY_PATH="$platform" \
      ${lib.getExe nodejs_24} \
      -e "require('$out/node_modules/sherpa-onnx-node')"

    runHook postInstallCheck
  '';

  passthru = {
    nativeRuntimeFiles = [ "sherpa-onnx.node" ] ++ map (library: library.name) runtimeLibraries;
    runtimeProvenance = {
      addonSourceVersion = version;
      wrapperSourceVersion = wrapperVersion;
      sherpaNixpkgsVersion = sherpa-onnx.version;
      onnxruntimeNixpkgsVersion = onnxruntime.version;
      managedNixStoreDependencies = true;
      upstreamNpmPrebuiltUsed = false;
      byteIdentityWithUpstreamNpmPrebuiltClaimed = false;
    };
  };

  meta = {
    description = "Source-built sherpa-onnx Node-API runtime for OpenChamber";
    longDescription = ''
      Builds the v${version} Node-API source against the sherpa-onnx and
      onnxruntime packages selected by nixpkgs. Those linked packages and any
      nixpkgs patches determine the resulting bytes; this derivation neither
      uses nor claims byte identity with the upstream npm prebuilt runtime.
      Dynamic dependencies outside the three package-owned interface artifacts
      remain managed absolute paths in the Nix closure.
    '';
    homepage = "https://github.com/k2-fsa/sherpa-onnx";
    license = lib.licenses.asl20;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with lib.sourceTypes; [ fromSource ];
  };
}
