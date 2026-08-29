{
  cctools,
  cmake,
  fetchFromGitHub,
  gitMinimal,
  lib,
  meshSrcHash,
  ninja,
  srcHash,
  stdenv,
}:
assert stdenv.hostPlatform.system == "aarch64-darwin";
let
  commit = "8190848bb36c7df4251db4352bd81bc07d0a4385";
  meshSource = fetchFromGitHub {
    owner = "Mesh-LLM";
    repo = "mesh-llm";
    rev = "3295c902d4c4f859aaadf9240042ffdaf06dd07e";
    hash = meshSrcHash;
    fetchSubmodules = false;
  };
  src = fetchFromGitHub {
    owner = "ggml-org";
    repo = "llama.cpp";
    rev = commit;
    hash = srcHash;
    fetchSubmodules = false;
  };
in
stdenv.mkDerivation {
  pname = "buzz-llama-cpp";
  version = builtins.substring 0 12 commit;
  inherit src;
  strictDeps = true;

  nativeBuildInputs = [
    cmake
    gitMinimal
    ninja
  ];

  patchPhase = ''
    runHook prePatch

    meshPatchSource=${lib.escapeShellArg "${meshSource}"}
    upstreamPin="$meshPatchSource/third_party/llama.cpp/upstream.txt"
    patchDirectory="$meshPatchSource/third_party/llama.cpp/patches"
    if [ ! -f "$upstreamPin" ]; then
      echo "missing Mesh llama.cpp upstream pin: $upstreamPin" >&2
      exit 1
    fi
    upstreamLineCount="$(LC_ALL=C awk 'END { print NR }' "$upstreamPin")"
    upstreamCommit="$(LC_ALL=C sed -n '1p' "$upstreamPin")"
    if [ "$upstreamLineCount" -ne 1 ] || [ "$upstreamCommit" != ${lib.escapeShellArg commit} ]; then
      echo "Mesh llama.cpp upstream pin does not match ${commit}" >&2
      exit 1
    fi
    if [ ! -d "$patchDirectory" ]; then
      echo "missing Mesh llama.cpp patch directory: $patchDirectory" >&2
      exit 1
    fi
    if find "$patchDirectory" -mindepth 1 -maxdepth 1 ! -type f -print -quit | grep -q .; then
      echo "Mesh llama.cpp patch queue contains a non-regular entry" >&2
      exit 1
    fi
    if find "$patchDirectory" -mindepth 1 -maxdepth 1 -type f ! -name '*.patch' -print -quit | grep -q .; then
      echo "Mesh llama.cpp patch queue contains a non-patch file" >&2
      exit 1
    fi

    patchQueue="$TMPDIR/buzz-llama-cpp-patches"
    find "$patchDirectory" -mindepth 1 -maxdepth 1 -type f -name '*.patch' -print0 |
      LC_ALL=C sort -z > "$patchQueue"
    if [ ! -s "$patchQueue" ]; then
      echo "Mesh source contains no llama.cpp patches" >&2
      exit 1
    fi
    while IFS= read -r -d "" meshPatch; do
      if [ ! -s "$meshPatch" ]; then
        echo "Mesh llama.cpp patch is empty: $meshPatch" >&2
        exit 1
      fi
      git apply --check "$meshPatch"
      git apply "$meshPatch"
    done < "$patchQueue"

    runHook postPatch
  '';

  preConfigure = ''
    sourcePrefixMap="-ffile-prefix-map=$NIX_BUILD_TOP=/build"
    export NIX_CFLAGS_COMPILE="$sourcePrefixMap ''${NIX_CFLAGS_COMPILE-}"
    export NIX_CXXFLAGS_COMPILE="$sourcePrefixMap ''${NIX_CXXFLAGS_COMPILE-}"
  '';

  cmakeBuildType = "Release";
  cmakeFlags = [
    (lib.cmakeFeature "CMAKE_OSX_ARCHITECTURES" "arm64")
    (lib.cmakeFeature "CMAKE_OSX_DEPLOYMENT_TARGET" "14.0")
    (lib.cmakeFeature "CMAKE_INSTALL_BINDIR" "bin")
    (lib.cmakeFeature "CMAKE_INSTALL_INCLUDEDIR" "include")
    (lib.cmakeFeature "CMAKE_INSTALL_LIBDIR" "lib")
    (lib.cmakeFeature "CMAKE_INSTALL_NAME_DIR" "@rpath")
    (lib.cmakeBool "BUILD_SHARED_LIBS" true)
    (lib.cmakeBool "GGML_NATIVE" false)
    (lib.cmakeBool "GGML_METAL" true)
    (lib.cmakeBool "LLAMA_BUILD_APP" false)
    (lib.cmakeBool "LLAMA_BUILD_EXAMPLES" false)
    (lib.cmakeBool "LLAMA_BUILD_SERVER" false)
    (lib.cmakeBool "LLAMA_BUILD_TESTS" false)
    (lib.cmakeBool "LLAMA_CURL" false)
    (lib.cmakeBool "LLAMA_OPENSSL" false)
    (lib.cmakeBool "FETCHCONTENT_FULLY_DISCONNECTED" true)
  ];

  buildPhase = ''
    runHook preBuild
    cmake --build . --config Release
    runHook postBuild
  '';

  doCheck = false;
  dontFixup = true;

  installPhase = ''
    runHook preInstall

    runtimeStage="$TMPDIR/buzz-llama-cpp-stage"
    mkdir -p "$runtimeStage" "$out/lib"
    cmake --install . --config Release --prefix "$runtimeStage"

    dylibQueue="$TMPDIR/buzz-llama-cpp-dylibs"
    find "$runtimeStage" -type f \( -name '*.dylib' -o -name '*.dylib.*' \) -print0 |
      LC_ALL=C sort -z > "$dylibQueue"
    if [ ! -s "$dylibQueue" ]; then
      echo "CMake installed no llama.cpp dynamic libraries" >&2
      exit 1
    fi
    while IFS= read -r -d "" sourceLibrary; do
      libraryName="$(basename "$sourceLibrary")"
      destinationLibrary="$out/lib/$libraryName"
      if [ -e "$destinationLibrary" ] || [ -L "$destinationLibrary" ]; then
        echo "duplicate CMake-installed dynamic library name: $libraryName" >&2
        exit 1
      fi
      install -m 755 "$sourceLibrary" "$destinationLibrary"
    done < "$dylibQueue"

    dylibLinkQueue="$TMPDIR/buzz-llama-cpp-dylib-links"
    find "$runtimeStage" -type l \( -name '*.dylib' -o -name '*.dylib.*' \) -print0 |
      LC_ALL=C sort -z > "$dylibLinkQueue"
    while IFS= read -r -d "" sourceLink; do
      linkName="$(basename "$sourceLink")"
      linkTarget="$(readlink "$sourceLink")"
      destinationLink="$out/lib/$linkName"
      case "$linkTarget" in
        "" | /* | */*)
          echo "CMake installed a non-local dynamic library link: $linkName -> $linkTarget" >&2
          exit 1
          ;;
      esac
      if [ -e "$destinationLink" ] || [ -L "$destinationLink" ]; then
        echo "duplicate CMake-installed dynamic library link name: $linkName" >&2
        exit 1
      fi
      ln -s "$linkTarget" "$destinationLink"
    done < "$dylibLinkQueue"
    while IFS= read -r -d "" sourceLink; do
      linkName="$(basename "$sourceLink")"
      linkTarget="$(readlink "$sourceLink")"
      if [ ! -f "$out/lib/$linkTarget" ]; then
        echo "CMake installed an unresolved dynamic library link: $linkName -> $linkTarget" >&2
        exit 1
      fi
    done < "$dylibLinkQueue"

    resourceQueue="$TMPDIR/buzz-llama-cpp-metal-resources"
    find "$runtimeStage" -type f \( -name '*.metal' -o -name '*.metallib' \) -print0 |
      LC_ALL=C sort -z > "$resourceQueue"
    if [ -s "$resourceQueue" ]; then
      echo "CMake installed an unpromoted llama.cpp Metal resource" >&2
      exit 1
    fi

    runHook postInstall

    outputDylibQueue="$TMPDIR/buzz-llama-cpp-output-dylibs"
    find "$out/lib" -type f \( -name '*.dylib' -o -name '*.dylib.*' \) -print0 |
      LC_ALL=C sort -z > "$outputDylibQueue"
    while IFS= read -r -d "" library; do
      libraryName="$(basename "$library")"
      ${cctools}/bin/install_name_tool -id "@rpath/$libraryName" "$library"
      while IFS= read -r dependency; do
        if [ "$dependency" = "@rpath/$libraryName" ]; then
          continue
        fi
        case "$dependency" in
          "/usr/lib/"* | "/System/Library/"*)
            continue
            ;;
          "/nix/store/"* | "/opt/homebrew/"* | "/usr/local/"*)
            echo "llama.cpp dynamic library has a forbidden dependency: $dependency" >&2
            exit 1
            ;;
          "@rpath/"* | "@loader_path/"* | "$NIX_BUILD_TOP/"* | "$runtimeStage/"*)
            dependencyName="$(basename "$dependency")"
            ;;
          /* | @*)
            echo "llama.cpp dynamic library has an unsupported dependency: $dependency" >&2
            exit 1
            ;;
          *)
            dependencyName="$(basename "$dependency")"
            ;;
        esac
        case "$dependencyName" in
          "" | */* | *[!A-Za-z0-9._+-]*)
            echo "llama.cpp dynamic library has an unsafe dependency name: $dependency" >&2
            exit 1
            ;;
        esac
        if [ ! -f "$out/lib/$dependencyName" ]; then
          echo "llama.cpp dynamic library dependency is outside the staged closure: $dependency" >&2
          exit 1
        fi
        ${cctools}/bin/install_name_tool -change "$dependency" "@loader_path/$dependencyName" "$library"
      done < <(${cctools}/bin/otool -L "$library" | LC_ALL=C awk 'NR > 1 { print $1 }')
    done < "$outputDylibQueue"

    while IFS= read -r -d "" library; do
      /usr/bin/codesign --force --sign - "$library"
    done < "$outputDylibQueue"
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    if find "$out" -mindepth 1 -maxdepth 1 ! -name lib -print -quit | grep -q .; then
      echo "llama.cpp output contains an unexpected top-level path" >&2
      exit 1
    fi
    if find "$out/lib" -mindepth 1 -maxdepth 1 \
      ! \( \( -type f -o -type l \) \( -name '*.dylib' -o -name '*.dylib.*' \) \) \
      -print -quit | grep -q .; then
      echo "llama.cpp lib output contains a non-dylib path" >&2
      exit 1
    fi
    linkValidationQueue="$TMPDIR/buzz-llama-cpp-validation-links"
    find "$out/lib" -type l \( -name '*.dylib' -o -name '*.dylib.*' \) -print0 |
      LC_ALL=C sort -z > "$linkValidationQueue"
    while IFS= read -r -d "" libraryLink; do
      linkName="$(basename "$libraryLink")"
      linkTarget="$(readlink "$libraryLink")"
      case "$linkTarget" in
        "" | /* | */*)
          echo "llama.cpp output contains a non-local dynamic library link: $linkName -> $linkTarget" >&2
          exit 1
          ;;
      esac
      if [ ! -f "$out/lib/$linkTarget" ]; then
        echo "llama.cpp output contains an unresolved dynamic library link: $linkName -> $linkTarget" >&2
        exit 1
      fi
    done < "$linkValidationQueue"
    validationQueue="$TMPDIR/buzz-llama-cpp-validation-dylibs"
    find "$out/lib" -type f \( -name '*.dylib' -o -name '*.dylib.*' \) -print0 |
      LC_ALL=C sort -z > "$validationQueue"
    if [ ! -s "$validationQueue" ]; then
      echo "llama.cpp output contains no dynamic libraries" >&2
      exit 1
    fi
    while IFS= read -r -d "" library; do
      libraryName="$(basename "$library")"
      architectures="$(${cctools}/bin/lipo -archs "$library")"
      if [ "$architectures" != "arm64" ]; then
        echo "expected arm64-only $libraryName, found: $architectures" >&2
        exit 1
      fi

      installId="$(${cctools}/bin/otool -D "$library" | LC_ALL=C awk 'NR == 2 { print $1; exit }')"
      if [ "$installId" != "@rpath/$libraryName" ]; then
        echo "unexpected llama.cpp install ID for $libraryName: $installId" >&2
        exit 1
      fi

      minimumVersions="$(${cctools}/bin/otool -l "$library" | LC_ALL=C awk '
        $1 == "cmd" {
          loadCommand = $2
          next
        }
        loadCommand == "LC_BUILD_VERSION" && $1 == "minos" {
          print $2
          loadCommand = ""
          next
        }
        loadCommand == "LC_VERSION_MIN_MACOSX" && $1 == "version" {
          print $2
          loadCommand = ""
        }
      ')"
      minimumVersionCount="$(printf '%s\n' "$minimumVersions" | LC_ALL=C awk 'NF { count++ } END { print count + 0 }')"
      if [ "$minimumVersionCount" -ne 1 ]; then
        echo "expected exactly one macOS deployment target in $libraryName" >&2
        exit 1
      fi
      minimumVersion="$(printf '%s\n' "$minimumVersions" | LC_ALL=C awk 'NF { print; exit }')"
      if ! LC_ALL=C awk -v version="$minimumVersion" '
        BEGIN {
          if (version !~ /^[0-9]+(\.[0-9]+)?(\.[0-9]+)?$/) {
            exit 1
          }
          split(version, component, ".")
          major = component[1] + 0
          minor = component[2] + 0
          patch = component[3] + 0
          if (major < 14 || (major == 14 && minor == 0 && patch == 0)) {
            exit 0
          }
          exit 1
        }
      '; then
        echo "llama.cpp dynamic library requires macOS newer than 14.0: $libraryName ($minimumVersion)" >&2
        exit 1
      fi

      while IFS= read -r dependency; do
        if [ "$dependency" = "@rpath/$libraryName" ]; then
          continue
        fi
        case "$dependency" in
          "/usr/lib/"* | "/System/Library/"*)
            continue
            ;;
          "@loader_path/"*)
            dependencyName="$(basename "$dependency")"
            if [ "$dependency" != "@loader_path/$dependencyName" ] \
              || [ ! -f "$out/lib/$dependencyName" ]; then
              echo "unresolved llama.cpp local dependency: $libraryName -> $dependency" >&2
              exit 1
            fi
            ;;
          "@rpath/"* | "@executable_path/"*)
            echo "llama.cpp dependency is not loader-relative: $libraryName -> $dependency" >&2
            exit 1
            ;;
          *)
            echo "llama.cpp dependency is outside its runtime closure: $libraryName -> $dependency" >&2
            exit 1
            ;;
        esac
      done < <(${cctools}/bin/otool -L "$library" | LC_ALL=C awk 'NR > 1 { print $1 }')

      /usr/bin/codesign --verify --strict "$library"
      referenceDump="$TMPDIR/buzz-llama-cpp-$libraryName.strings"
      ${stdenv.cc.bintools}/bin/strings -a "$library" > "$referenceDump"
      if grep -F "$NIX_BUILD_TOP/" "$referenceDump" >/dev/null \
        || grep -E \
          -e '/nix/store/' \
          -e '/nix/var/nix/builds/' \
          -e '/opt/homebrew/' \
          -e '/usr/local/' \
          "$referenceDump" >/dev/null; then
        echo "llama.cpp dynamic library contains a forbidden absolute path: $libraryName" >&2
        exit 1
      fi
    done < "$validationQueue"

    runHook postInstallCheck
  '';

  passthru = {
    libSubdir = "lib";
    resourceSubpaths = [ ];
    buzzNativeContract = {
      kind = "llama.cpp";
      commit = "8190848bb36c7df4251db4352bd81bc07d0a4385";
      target = "aarch64-apple-darwin";
      backend = "metal";
      linkMode = "dynamic";
      buildType = "Release";
      ggmlNative = false;
      cmakeOptions = {
        BUILD_SHARED_LIBS = true;
        GGML_METAL = true;
        LLAMA_BUILD_APP = false;
        LLAMA_BUILD_EXAMPLES = false;
        LLAMA_BUILD_SERVER = false;
        LLAMA_BUILD_TESTS = false;
        LLAMA_CURL = false;
        LLAMA_OPENSSL = false;
      };
    };
  };
}
