{
  cmake,
  closureContract ? (builtins.fromJSON (builtins.readFile ./native-lock.json)).sherpaOnnx,
  electronHeaders,
  lib,
  nodeAddonApiSrc,
  nodejs_24,
  onnxruntimeExact,
  sherpaExact,
  src,
  stdenv,
  wrapperSrc,
}:
stdenv.mkDerivation {
  pname = "paseo-sherpa-onnx-node";
  inherit (closureContract) version;
  inherit src;

  sourceRoot = "source/scripts/node-addon-api";
  nativeBuildInputs = [
    cmake
    nodejs_24
  ];
  buildInputs = [
    onnxruntimeExact
    sherpaExact
  ];

  strictDeps = true;
  dontStrip = true;

  env = {
    PASEO_MANIFEST_REWRITER = lib.getExe nodejs_24;
    PASEO_MANIFEST_REWRITE_SCRIPT = "${./rewrite-sherpa-platform-manifest.mjs}";
  };

  preConfigure = ''
    mkdir -p node_modules/node-addon-api
    tar -xzf ${nodeAddonApiSrc} --strip-components=1 -C node_modules/node-addon-api
    export SHERPA_ONNX_INSTALL_DIR=${sherpaExact}
  '';

  cmakeFlags = [
    "-DCMAKE_BUILD_TYPE=Release"
    "-DCMAKE_JS_INC=${electronHeaders}/include/node"
    "-DCMAKE_JS_LIB="
    "-DCMAKE_SHARED_LINKER_FLAGS=-Wl,-undefined,dynamic_lookup"
  ];

  installPhase = ''
    runHook preInstall

    libraryNames="$TMPDIR/paseo-sherpa-library-names"
    : >"$libraryNames"
    for libraryRoot in ${lib.getLib sherpaExact}/lib ${lib.getLib onnxruntimeExact}/lib
    do
      for library in "$libraryRoot"/*.dylib "$libraryRoot"/*.dylib.*
      do
        if [ -e "$library" ]; then
          basename "$library" >>"$libraryNames"
        fi
      done
    done
    duplicateLibraryNames="$(LC_ALL=C sort "$libraryNames" | uniq -d)"
    if [ -n "$duplicateLibraryNames" ]; then
      while IFS= read -r duplicateLibraryName
      do
        echo "duplicate sherpa runtime library basename: $duplicateLibraryName" >&2
      done <<<"$duplicateLibraryNames"
      exit 1
    fi
    runtimeSourceRecords="$TMPDIR/paseo-sherpa-runtime-sources"
    : >"$runtimeSourceRecords"

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

    for libraryRoot in ${lib.getLib sherpaExact}/lib ${lib.getLib onnxruntimeExact}/lib
    do
      for library in "$libraryRoot"/*.dylib "$libraryRoot"/*.dylib.*
      do
        if [ -e "$library" ]; then
          libraryName="$(basename "$library")"
          printf '%s\t%s\n' "$libraryName" "$library" >>"$runtimeSourceRecords"
          cp -L "$library" "$platform/$libraryName"
        fi
      done
    done

    cp "$wrapper/package.json" "$platform/package.json"
    "$PASEO_MANIFEST_REWRITER" "$PASEO_MANIFEST_REWRITE_SCRIPT" \
      "$platform/package.json"

    runtimeProcessed="$TMPDIR/paseo-sherpa-runtime-processed"
    : >"$runtimeProcessed"
    while true
    do
      binary=""
      for candidate in "$platform"/*
      do
        if [ -f "$candidate" ] && \
          /usr/bin/file -b "$candidate" | grep -q 'Mach-O' && \
          ! grep -F -x -q "$candidate" "$runtimeProcessed"
        then
          binary="$candidate"
          break
        fi
      done
      if [ -z "$binary" ]; then
        break
      fi
      echo "$binary" >>"$runtimeProcessed"
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
                dependencyName="$(basename "$dependency")"
                replacement="$platform/$dependencyName"
                recordedSource="$(
                  awk -F '\t' -v name="$dependencyName" \
                    '$1 == name { print substr($0, length($1) + 2); exit }' \
                    "$runtimeSourceRecords"
                )"
                if [ -n "$recordedSource" ]; then
                  if ! cmp -s "$dependency" "$recordedSource"; then
                    echo "sherpa dependency basename collision: $dependencyName" >&2
                    exit 1
                  fi
                else
                  if [ -e "$replacement" ]; then
                    echo "untracked sherpa dependency basename collision: $dependencyName" >&2
                    exit 1
                  fi
                  cp -L "$dependency" "$replacement"
                  printf '%s\t%s\n' \
                    "$dependencyName" "$dependency" >>"$runtimeSourceRecords"
                fi
                /usr/bin/install_name_tool \
                  -change "$dependency" "@loader_path/$dependencyName" \
                  "$binary"
                ;;
            esac
          done
      while IFS= read -r runtimeRpath
      do
        case "$runtimeRpath" in
          /nix/store/*)
            /usr/bin/install_name_tool -delete_rpath "$runtimeRpath" "$binary"
            ;;
        esac
      done < <(
        /usr/bin/otool -l "$binary" |
          awk '
            $1 == "cmd" && $2 == "LC_RPATH" { inRpath = 1; next }
            inRpath && $1 == "path" { print $2; inRpath = 0 }
          '
      )
    done

    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    platform="$out/node_modules/sherpa-onnx-darwin-arm64"
    /usr/bin/lipo "$platform/sherpa-onnx.node" -verify_arch arm64
    for binary in "$platform/sherpa-onnx.node" "$platform"/*.dylib "$platform"/*.dylib.*
    do
      if [ -e "$binary" ] && \
        /usr/bin/otool -L "$binary" | \
          tail -n +2 | \
          grep -q '/nix/store/'
      then
        echo "sherpa runtime retains a Nix-store dynamic-library path: $binary" >&2
        exit 1
      fi
      if [ -e "$binary" ] && \
        /usr/bin/otool -l "$binary" | \
          awk '
            $1 == "cmd" && $2 == "LC_RPATH" { inRpath = 1; next }
            inRpath && $1 == "path" { print $2; inRpath = 0 }
          ' | grep -q '/nix/store/'
      then
        echo "sherpa runtime retains a Nix-store LC_RPATH: $binary" >&2
        exit 1
      fi
    done
    DYLD_LIBRARY_PATH="$platform" \
      ${lib.getExe nodejs_24} \
      -e "require('$out/node_modules/sherpa-onnx-node')"

    runHook postInstallCheck
  '';

  meta = {
    description = "Source-built sherpa-onnx Node-API runtime for Paseo";
    homepage = "https://github.com/k2-fsa/sherpa-onnx";
    license = lib.licenses.asl20;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with lib.sourceTypes; [ fromSource ];
  };
}
