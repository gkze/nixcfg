{
  cctools,
  lib,
  makeRustPlatform,
  nativeLock ? builtins.fromJSON (builtins.readFile ../native-lock.json),
  patchedBuzzSource,
  rootCargoDeps,
  rustToolchain,
  sidecarSpecs,
  stdenv,
  version,
}:
let
  buzzCommit = nativeLock.buzz.commit or null;
  rustVersion = nativeLock.buzz.rustVersion or null;
in
assert stdenv.hostPlatform.system == "aarch64-darwin";
assert builtins.isString buzzCommit && builtins.match "[0-9a-f]{40}" buzzCommit != null;
assert builtins.isString rustVersion;
assert
  sidecarSpecs == [
    {
      package = "buzz-acp";
      binary = "buzz-acp";
    }
    {
      package = "buzz-agent";
      binary = "buzz-agent";
    }
    {
      package = "buzz-backend-kubernetes";
      binary = "buzz-backend-kubernetes";
    }
    {
      package = "buzz-dev-mcp";
      binary = "buzz-dev-mcp";
    }
    {
      package = "git-credential-nostr";
      binary = "git-credential-nostr";
    }
    {
      package = "buzz-cli";
      binary = "buzz";
    }
  ];
assert
  (rustToolchain.passthru.buzzNativeContract or null) == {
    kind = "rust-toolchain";
    channel = rustVersion;
    profile = "default";
    target = "aarch64-apple-darwin";
  };
let
  target = "aarch64-apple-darwin";
  implementedContract = {
    kind = "buzz-sidecars";
    commit = buzzCommit;
    target = "aarch64-apple-darwin";
    profile = "release";
    cargoOffline = true;
    cargoFrozen = true;
    sidecars = [
      {
        package = "buzz-acp";
        binary = "buzz-acp";
      }
      {
        package = "buzz-agent";
        binary = "buzz-agent";
      }
      {
        package = "buzz-backend-kubernetes";
        binary = "buzz-backend-kubernetes";
      }
      {
        package = "buzz-dev-mcp";
        binary = "buzz-dev-mcp";
      }
      {
        package = "git-credential-nostr";
        binary = "git-credential-nostr";
      }
      {
        package = "buzz-cli";
        binary = "buzz";
      }
    ];
    installedBinaries = [
      "buzz-acp-aarch64-apple-darwin"
      "buzz-agent-aarch64-apple-darwin"
      "buzz-backend-kubernetes-aarch64-apple-darwin"
      "buzz-dev-mcp-aarch64-apple-darwin"
      "git-credential-nostr-aarch64-apple-darwin"
      "buzz-aarch64-apple-darwin"
    ];
    binaryFormat = "Mach-O 64-bit executable arm64";
    dylibPolicy = "system-or-loader-relative";
    signature = "adhoc-after-fixup";
  };
  sidecarsRustPlatform = makeRustPlatform {
    cargo = rustToolchain;
    rustc = rustToolchain;
  };
  sidecarNames = lib.escapeShellArgs implementedContract.installedBinaries;
  validationScript = ''
    if [ ! -d "$out/bin" ]; then
      echo "Buzz sidecar output is missing its bin directory" >&2
      exit 1
    fi
    if find "$out" -mindepth 1 -maxdepth 1 ! -name bin -print -quit | grep -q .; then
      echo "Buzz sidecar output contains an unexpected top-level path" >&2
      exit 1
    fi
    if find "$out/bin" -mindepth 1 -maxdepth 1 ! -type f -print -quit | grep -q .; then
      echo "Buzz sidecar bin directory contains a non-file entry" >&2
      exit 1
    fi

    expectedInventory='buzz-aarch64-apple-darwin
    buzz-acp-aarch64-apple-darwin
    buzz-agent-aarch64-apple-darwin
    buzz-backend-kubernetes-aarch64-apple-darwin
    buzz-dev-mcp-aarch64-apple-darwin
    git-credential-nostr-aarch64-apple-darwin'
    actualInventory="$(
      find "$out/bin" -mindepth 1 -maxdepth 1 -type f -exec basename {} \; |
        LC_ALL=C sort
    )"
    if [ "$actualInventory" != "$expectedInventory" ]; then
      echo "Buzz sidecar output inventory differs from the exact six binaries" >&2
      diff -u <(printf '%s\n' "$expectedInventory") <(printf '%s\n' "$actualInventory") >&2 || true
      exit 1
    fi

    for sidecarName in \
      buzz-acp-aarch64-apple-darwin \
      buzz-agent-aarch64-apple-darwin \
      buzz-backend-kubernetes-aarch64-apple-darwin \
      buzz-dev-mcp-aarch64-apple-darwin \
      git-credential-nostr-aarch64-apple-darwin \
      buzz-aarch64-apple-darwin; do
      sidecar="$out/bin/$sidecarName"
      if [ ! -x "$sidecar" ]; then
        echo "Buzz sidecar is not executable: $sidecarName" >&2
        exit 1
      fi

      fileDescription="$("$FILE_TOOL" "$sidecar")"
      case "$fileDescription" in
        *"Mach-O 64-bit executable arm64"*) ;;
        *)
          echo "Buzz sidecar is not an arm64 Mach-O executable: $sidecarName" >&2
          exit 1
          ;;
      esac
      architectures="$("$LIPO_TOOL" -archs "$sidecar")"
      if [ "$architectures" != arm64 ]; then
        echo "Buzz sidecar is not arm64-only: $sidecarName ($architectures)" >&2
        exit 1
      fi

      if ! dependencyListing="$("$OTOOL_TOOL" -L "$sidecar")"; then
        echo "Buzz sidecar could not inspect dynamic-library edges: $sidecarName" >&2
        exit 1
      fi
      while IFS= read -r dependency; do
        case "$dependency" in
          /usr/lib/* | /System/Library/* | @loader_path/*) ;;
          *)
            echo "Buzz sidecar has a forbidden dynamic-library edge: $sidecarName -> $dependency" >&2
            exit 1
            ;;
        esac
      done < <(printf '%s\n' "$dependencyListing" | LC_ALL=C awk 'NR > 1 { print $1 }')

      "$CODESIGN_TOOL" --verify --strict "$sidecar"
      signatureDetails="$("$CODESIGN_TOOL" -dv --verbose=4 "$sidecar" 2>&1)"
      if ! printf '%s\n' "$signatureDetails" | grep -Fxq 'Signature=adhoc'; then
        echo "Buzz sidecar does not have an ad-hoc signature: $sidecarName" >&2
        exit 1
      fi
    done
  '';
in
sidecarsRustPlatform.buildRustPackage {
  pname = "buzz-sidecars";
  inherit version;
  strictDeps = true;
  src = patchedBuzzSource;
  cargoDeps = rootCargoDeps;
  buildType = "release";
  env = {
    CARGO_NET_OFFLINE = "true";
  };
  cargoBuildFlags = [
    "--frozen"
  ]
  ++ lib.concatMap (spec: [
    "--package"
    spec.package
    "--bin"
    spec.binary
  ]) sidecarSpecs;
  doCheck = false;
  installPhase = ''
    runHook preInstall
    mkdir -p "$out/bin"
    ${lib.concatMapStringsSep "\n" (spec: ''
      install -m0755 \
        "target/${target}/release/${spec.binary}" \
        "$out/bin/${spec.binary}-${target}"
    '') sidecarSpecs}
    runHook postInstall
  '';
  postFixup = ''
    for sidecarName in ${sidecarNames}; do
      sidecar="$out/bin/$sidecarName"
      dependencyListing="$(${cctools}/bin/otool -L "$sidecar")"
      iconvDependency=""
      while IFS= read -r dependencyLine; do
        dependency="$(printf '%s\n' "$dependencyLine" | LC_ALL=C awk '{ print $1 }')"
        case "$dependency" in
          /nix/store/*-libiconv-*/lib/libiconv.2.dylib)
            if [ -n "$iconvDependency" ]; then
              echo "Buzz sidecar has multiple Nix libiconv edges: $sidecarName" >&2
              exit 1
            fi
            case "$dependencyLine" in
              *' (compatibility version 7.0.0, '*) ;;
              *)
                echo "Buzz sidecar libiconv ABI differs from macOS: $sidecarName" >&2
                exit 1
                ;;
            esac
            iconvDependency="$dependency"
            ;;
        esac
      done < <(printf '%s\n' "$dependencyListing" | LC_ALL=C awk 'NR > 1')
      if [ -z "$iconvDependency" ]; then
        echo "Buzz sidecar has no relocatable Nix libiconv edge: $sidecarName" >&2
        exit 1
      fi
      ${cctools}/bin/install_name_tool \
        -change "$iconvDependency" /usr/lib/libiconv.2.dylib "$sidecar"
      /usr/bin/codesign --force --sign - "$sidecar"
    done
  '';
  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    export FILE_TOOL=/usr/bin/file
    export LIPO_TOOL=${cctools}/bin/lipo
    export OTOOL_TOOL=${cctools}/bin/otool
    export CODESIGN_TOOL=/usr/bin/codesign
    ${validationScript}
    runHook postInstallCheck
  '';
  passthru = {
    buzzNativeContract = implementedContract;
  };
  meta = {
    description = "Source-built native sidecars for Buzz";
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = [ lib.sourceTypes.fromSource ];
  };
}
