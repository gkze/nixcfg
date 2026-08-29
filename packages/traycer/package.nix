{
  bun2nix,
  cctools,
  fetchFromGitHub,
  fetchurl,
  inputs,
  lib,
  libarchive,
  makeWrapper,
  minisign,
  nixcfgElectron,
  nodejs_24,
  pkgs,
  python3,
  runCommand,
  stdenv,
  stdenvNoCC,
  unzip,
  ...
}:
let
  pname = "traycer";
  appName = "Traycer";
  appBundleName = "${appName}.app";
  appExecutableName = appName;
  appId = "ai.traycer.desktop";
  hostRuntimeRelativeExecutable = "host-runtime/traycer-host";
  minimumMacOSVersion = "14.0";
  desktopEntitlementsRelativePath = "clients/desktop/resources/bundle/entitlements.mac.plist";

  sourceData = builtins.fromJSON (builtins.readFile ./sources.json);
  expectedVersion = "1.2.0";
  expectedPublicCommit = "85ee596fffab4c9aa72b6bddc73a0020839ed5ae";
  expectedElectronVersion = "42.9.1";
  expectedBunVersion = "1.3.12";
  expectedBunUrl = "https://github.com/oven-sh/bun/releases/download/bun-v1.3.12/bun-darwin-aarch64.zip";
  expectedBunSize = 22264502;
  expectedBunSha256 = "6c4bb87dd013ed1a8d6a16e357a3d094959fd5530b4d7061f7f3680c3c7cea1c";
  expectedHostArchiveUrl = "https://github.com/traycerai/traycer/releases/download/host-v1.2.0/traycer-host-macos-arm64.tar.gz";
  expectedHostArchiveSize = 76162681;
  expectedHostArchiveSha256 = "66cf81e799d8251466e34ec13b6159007cbb1069dc091d6dc75e10a28d546939";
  # This value is bundled into the renderer and is therefore not a credential.
  # Preserve the official pre-cutover value so existing encrypted local state
  # remains readable, and keep it stable across upgrades.
  desktopLocalStorageKey = "wlsH9H7PTa51Kk7FENFF/WBJDWiwCZqvIM4KzTUvHOBF/BmZMSEmi7wyjur8FIjJ";
  expectedHostArchiveMemberCount = 2954;
  expectedHostArchiveFileCount = 2748;
  expectedHostMachOCount = 14;
  expectedHostUniversalMachOCount = 0;
  expectedHostThinX8664MachOCount = 0;
  hostRipgrepRelativeExecutable = "host-runtime/resources/providers/ripgrep/darwin-arm64/rg";
  expectedHostRipgrepVersion = "15.2.0";
  expectedHostRipgrepPcre2Feature = "features:+pcre2";
  expectedHostSignatureUrl = "${expectedHostArchiveUrl}.minisig";
  expectedHostSignatureSize = 293;
  expectedHostSignatureSha256 = "556fafe5c3bc5f6a2a7bce55f6cb2c6c61b139a947a72e44f65dbd9dca23439d";
  expectedHostMinisignPublicKey = "RWSEfvU5EZoZYQTQUOVHeQFv3poThl1VM7FZLkNQr0Zu0FyL2x+u2O2l";
  expectedHostMinisignKeyId = "847ef539119a1961";
  expectedHostMinisignTrustedComment = "traycer-host 1.2.0 darwin-arm64";
  expectedHostInstallId = "608ac4aa-4c3c-558e-94a9-679ab22baccc";
  # Deterministic epoch sentinels mean "verified before store materialization";
  # they deliberately do not claim a wall-clock install or verification time.
  expectedHostInstallSentinelTimestamp = "1970-01-01T00:00:00.000Z";
  unverifiedPrivateBuildCommit = "5198516d395fedc25c5f702263a3e4a72b05a655";

  version = sourceData.version or "unknown";
  urls = sourceData.urls or { };
  bunUrl = urls.bun or "";
  hostArchiveUrl = urls.hostArchive or "";
  hostSignatureUrl = urls.hostSignature or "";
  hashEntryFor =
    hashType: url: platform:
    lib.findFirst (
      entry:
      entry.hashType == hashType
      && (url == null || (entry.url or null) == url)
      && (platform == null || (entry.platform or null) == platform)
    ) null (sourceData.hashes or [ ]);
  sourceHash = hashEntryFor "srcHash" null null;
  bunHash = hashEntryFor "sha256" bunUrl "aarch64-darwin";
  hostArchiveHash = hashEntryFor "sha256" hostArchiveUrl "aarch64-darwin";
  hostSignatureHash = hashEntryFor "sha256" hostSignatureUrl "aarch64-darwin";

  electronBuild = nixcfgElectron.sourceBuildFor expectedElectronVersion;

  # Empirical claims are promoted only by editing this file after an authorized
  # acquisition audit. They are intentionally not function arguments and
  # cannot be attested by a caller.
  verifiedHostCodesignIdentity = {
    teamIdentifier = "7YVZ56DZ74";
    identifier = "traycer-host";
    designatedRequirement = ''identifier "traycer-host" and anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] /* exists */ and certificate leaf[field.1.2.840.113635.100.6.1.13] /* exists */ and certificate leaf[subject.OU] = "7YVZ56DZ74"'';
    executableSha256 = "4977de1ec618e272c4701e004de9aee0efea32b3b72fe42012ef0016fe6bf48c";
  };
  desktopBundleValidationComplete = true;

  unresolvedBuildGates =
    lib.optional (
      stdenv.hostPlatform.system != "aarch64-darwin"
    ) "Traycer is supported only on aarch64-darwin"
    ++ lib.optional (version != expectedVersion) "Traycer source version must be ${expectedVersion}"
    ++ lib.optional (
      (sourceData.commit or "") != expectedPublicCommit
    ) "Traycer public source commit must be ${expectedPublicCommit}"
    ++ lib.optional (
      (sourceData.electronVersion or "") != expectedElectronVersion
    ) "Electron must be exactly ${expectedElectronVersion}"
    ++ lib.optional (
      electronBuild.runtimeVersion != expectedElectronVersion
    ) "Electron runtime and source-built headers must match ${expectedElectronVersion}"
    ++ lib.optional (
      bunUrl != expectedBunUrl
    ) "Bun asset URL must remain the official ${expectedBunVersion} darwin-arm64 asset"
    ++ lib.optional (hostArchiveUrl != expectedHostArchiveUrl) "Traycer Host archive URL drifted"
    ++ lib.optional (hostSignatureUrl != expectedHostSignatureUrl) "Traycer Host signature URL drifted"
    ++ lib.optional (sourceHash == null) "Traycer public source hash is missing"
    ++ lib.optional (bunHash == null) "Bun ${expectedBunVersion} asset hash is missing"
    ++ lib.optional (hostArchiveHash == null) "Traycer Host archive hash is missing"
    ++ lib.optional (hostSignatureHash == null) "Traycer Host signature hash is missing"
    ++
      lib.optional (verifiedHostCodesignIdentity == null)
        "Traycer Host Team ID, signing identifier, and designated requirement are not empirically verified"
    ++ lib.optional (
      !desktopBundleValidationComplete
    ) "Traycer source-built Desktop/CLI bundle has not completed the empirical closure audit";

  mixedProvenance = {
    desktopAndCli = {
      provenance = "public-source-build";
      repository = "traycerai/traycer";
      commit = expectedPublicCommit;
      version = expectedVersion;
      vendorShippedByteIdentityClaimed = false;
    };
    host = {
      provenance = "official-vendor-signed-closed-runtime";
      version = expectedVersion;
      archive = {
        url = expectedHostArchiveUrl;
        size = expectedHostArchiveSize;
        sha256 = expectedHostArchiveSha256;
        sri = if hostArchiveHash == null then null else hostArchiveHash.hash;
      };
      signature = {
        url = expectedHostSignatureUrl;
        size = expectedHostSignatureSize;
        sha256 = expectedHostSignatureSha256;
        sri = if hostSignatureHash == null then null else hostSignatureHash.hash;
        publicKey = expectedHostMinisignPublicKey;
        signerKeyId = expectedHostMinisignKeyId;
        trustedComment = expectedHostMinisignTrustedComment;
      };
      codesign = {
        observedTeamIdentifier = "7YVZ56DZ74";
        verifiedIdentity = verifiedHostCodesignIdentity;
        evidencePromoted = verifiedHostCodesignIdentity != null;
      };
    };
    privateBuildReference = {
      repository = "traycerai/traycer-internal";
      commit = unverifiedPrivateBuildCommit;
      provenance = "unsigned-release-metadata-advisory";
      verified = false;
      sourceOrBinaryIdentityClaimed = false;
    };
    bun = {
      version = expectedBunVersion;
      url = expectedBunUrl;
      size = expectedBunSize;
      sha256 = expectedBunSha256;
      sri = if bunHash == null then null else bunHash.hash;
    };
    electron = {
      version = expectedElectronVersion;
      provenance = "official-prebuilt-runtime";
      sourceBuilt = false;
    };
  };

  # This is both the only allowed Mach-O inventory and the deterministic
  # inside-out signing order. A bundle target is signed only after every
  # nested loose executable in the preceding entries has been signed.
  desktopNativeCodeObjects = [
    {
      path = "Contents/Resources/cli/darwin-arm64/traycer";
      identifier = "traycer";
      architectures = [ "arm64" ];
      signTarget = "Contents/Resources/cli/darwin-arm64/traycer";
    }
    {
      path = "Contents/Resources/app.asar.unpacked/node_modules/font-list/libs/darwin/fontlist";
      identifier = "fontlist";
      architectures = [
        "arm64"
        "x86_64"
      ];
      signTarget = "Contents/Resources/app.asar.unpacked/node_modules/font-list/libs/darwin/fontlist";
    }
    {
      path = "Contents/Frameworks/Electron Framework.framework/Versions/A/Helpers/chrome_crashpad_handler";
      identifier = "chrome_crashpad_handler";
      architectures = [ "arm64" ];
      signTarget = "Contents/Frameworks/Electron Framework.framework/Versions/A/Helpers/chrome_crashpad_handler";
    }
    {
      path = "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libEGL.dylib";
      identifier = "libEGL";
      architectures = [ "arm64" ];
      signTarget = "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libEGL.dylib";
    }
    {
      path = "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libGLESv2.dylib";
      identifier = "libGLESv2";
      architectures = [ "arm64" ];
      signTarget = "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libGLESv2.dylib";
    }
    {
      path = "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libffmpeg.dylib";
      identifier = "libffmpeg";
      architectures = [ "arm64" ];
      signTarget = "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libffmpeg.dylib";
    }
    {
      path = "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libvk_swiftshader.dylib";
      identifier = "libvk_swiftshader";
      architectures = [ "arm64" ];
      signTarget = "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libvk_swiftshader.dylib";
    }
    {
      path = "Contents/Frameworks/Mantle.framework/Versions/A/Mantle";
      identifier = "com.electron.mantle";
      architectures = [ "arm64" ];
      signTarget = "Contents/Frameworks/Mantle.framework";
    }
    {
      path = "Contents/Frameworks/ReactiveObjC.framework/Versions/A/ReactiveObjC";
      identifier = "com.electron.reactive";
      architectures = [ "arm64" ];
      signTarget = "Contents/Frameworks/ReactiveObjC.framework";
    }
    {
      path = "Contents/Frameworks/Squirrel.framework/Versions/A/Resources/ShipIt";
      identifier = "ShipIt";
      architectures = [ "arm64" ];
      signTarget = "Contents/Frameworks/Squirrel.framework/Versions/A/Resources/ShipIt";
    }
    {
      path = "Contents/Frameworks/Squirrel.framework/Versions/A/Squirrel";
      identifier = "com.github.Squirrel";
      architectures = [ "arm64" ];
      signTarget = "Contents/Frameworks/Squirrel.framework";
    }
    {
      path = "Contents/Frameworks/Electron Framework.framework/Versions/A/Electron Framework";
      identifier = "com.github.Electron.framework";
      architectures = [ "arm64" ];
      signTarget = "Contents/Frameworks/Electron Framework.framework";
    }
    {
      path = "Contents/Frameworks/Traycer Helper.app/Contents/MacOS/Traycer Helper";
      identifier = "ai.traycer.desktop.helper";
      architectures = [ "arm64" ];
      signTarget = "Contents/Frameworks/Traycer Helper.app";
    }
    {
      path = "Contents/Frameworks/Traycer Helper (GPU).app/Contents/MacOS/Traycer Helper (GPU)";
      identifier = "ai.traycer.desktop.helper.GPU";
      architectures = [ "arm64" ];
      signTarget = "Contents/Frameworks/Traycer Helper (GPU).app";
    }
    {
      path = "Contents/Frameworks/Traycer Helper (Plugin).app/Contents/MacOS/Traycer Helper (Plugin)";
      identifier = "ai.traycer.desktop.helper.Plugin";
      architectures = [ "arm64" ];
      signTarget = "Contents/Frameworks/Traycer Helper (Plugin).app";
    }
    {
      path = "Contents/Frameworks/Traycer Helper (Renderer).app/Contents/MacOS/Traycer Helper (Renderer)";
      identifier = "ai.traycer.desktop.helper.Renderer";
      architectures = [ "arm64" ];
      signTarget = "Contents/Frameworks/Traycer Helper (Renderer).app";
    }
    {
      path = "Contents/MacOS/Traycer";
      identifier = "ai.traycer.desktop";
      architectures = [ "arm64" ];
      signTarget = ".";
    }
  ];
  desktopPreSignedNativePath = "Contents/Resources/app.asar.unpacked/node_modules/font-list/libs/darwin/fontlist";
  desktopPostFixupSigningCodeObjects = lib.filter (
    codeObject: codeObject.path != desktopPreSignedNativePath
  ) desktopNativeCodeObjects;
  desktopSigningCommands = lib.concatMapStringsSep "\n" (codeObject: ''
    /usr/bin/codesign \
      --force \
      --sign - \
      --identifier ${lib.escapeShellArg codeObject.identifier} \
      --options runtime \
      --entitlements "$entitlements" \
      "$app/${codeObject.signTarget}"
  '') desktopPostFixupSigningCodeObjects;
  desktopNativeInventory = lib.concatMapStringsSep "\n" (
    codeObject:
    "${codeObject.path}|${codeObject.identifier}|${lib.concatStringsSep "," codeObject.architectures}"
  ) desktopNativeCodeObjects;

  commonPassthru = {
    inherit bun2nix mixedProvenance;
    traycerBuildGates = unresolvedBuildGates;
    hostOwnership = {
      runtimeStoreOwner = "nix";
      serviceRegistrationOwner = "nix";
      lifecycleIntegrationComplete = true;
      packageExported = true;
      mutableHostInstallAllowed = false;
      mutableCliUpgradeAllowed = false;
      desktopSelfUpdateAllowed = false;
    };
    macApp = {
      bundleId = appId;
      bundleName = appBundleName;
      bundleRelPath = "Applications/${appBundleName}";
      installMode = "copy";
    };
  };

  blockedPackage = stdenvNoCC.mkDerivation {
    inherit pname version;
    dontUnpack = true;
    buildPhase = ''
      echo "Traycer is intentionally unbuildable:" >&2
      ${lib.concatMapStringsSep "\n" (
        gate: "echo ${lib.escapeShellArg "- ${gate}"} >&2"
      ) unresolvedBuildGates}
      exit 1
    '';
    installPhase = "exit 1";
    passthru = commonPassthru // {
      inherit hostRuntime;
      desktopAuditPackage = realPackage;
    };
    meta = {
      broken = true;
      description = "Gated mixed-provenance Traycer Desktop, CLI, and Host foundation";
      homepage = "https://github.com/traycerai/traycer";
      license = [
        lib.licenses.mit
        lib.licenses.unfree
      ];
      platforms = [ "aarch64-darwin" ];
    };
  };

  traycerSource = fetchFromGitHub {
    owner = "traycerai";
    repo = "traycer";
    rev = expectedPublicCommit;
    inherit (sourceHash) hash;
  };
  bunDeps = pkgs.callPackage ./bun-cache.nix {
    inherit bun2nix traycerSource;
    bun = bunExact;
    bun2nixSource = inputs.bun2nix;
  };
  bunAsset = fetchurl {
    url = bunUrl;
    inherit (bunHash) hash;
  };
  hostArchive = fetchurl {
    url = hostArchiveUrl;
    inherit (hostArchiveHash) hash;
  };
  hostSignature = fetchurl {
    url = hostSignatureUrl;
    inherit (hostSignatureHash) hash;
  };

  bunExact = stdenvNoCC.mkDerivation {
    pname = "bun";
    version = expectedBunVersion;
    src = bunAsset;
    dontUnpack = true;
    nativeBuildInputs = [
      python3
      unzip
    ];
    installPhase = ''
      runHook preInstall
      ${lib.getExe python3} - ${toString expectedBunSize} ${lib.escapeShellArg expectedBunSha256} "$src" <<'PY'
      import hashlib
      import pathlib
      import sys

      expected_size = int(sys.argv[1])
      expected_digest = sys.argv[2]
      payload = pathlib.Path(sys.argv[3]).read_bytes()
      if len(payload) != expected_size:
          raise SystemExit(f"Bun asset size mismatch: {len(payload)} != {expected_size}")
      actual_digest = hashlib.sha256(payload).hexdigest()
      if actual_digest != expected_digest:
          raise SystemExit(f"Bun asset digest mismatch: {actual_digest}")
      PY
      mkdir unpacked "$out"
      unzip -q "$src" -d unpacked
      install -Dm0755 unpacked/bun-darwin-aarch64/bun "$out/bin/bun"
      test "$("$out/bin/bun" --version)" = "${expectedBunVersion}"
      runHook postInstall
    '';
    meta.mainProgram = "bun";
  };

  hostRuntime = stdenvNoCC.mkDerivation {
    pname = "traycer-host-runtime";
    version = expectedVersion;
    dontUnpack = true;
    dontFixup = true;
    nativeBuildInputs = [
      libarchive
      minisign
      python3
    ];
    buildPhase = ''
      runHook preBuild
      archive=${hostArchive}
      signature=${hostSignature}
      ${lib.getExe python3} - \
        ${toString expectedHostArchiveSize} \
        ${lib.escapeShellArg expectedHostArchiveSha256} \
        ${toString expectedHostSignatureSize} \
        ${lib.escapeShellArg expectedHostSignatureSha256} \
        ${lib.escapeShellArg expectedHostMinisignKeyId} \
        ${lib.escapeShellArg expectedHostMinisignPublicKey} \
        ${lib.escapeShellArg expectedHostMinisignTrustedComment} \
        "$archive" "$signature" <<'PY'
      import base64
      import hashlib
      import pathlib
      import sys

      archive_size = int(sys.argv[1])
      archive_digest = sys.argv[2]
      signature_size = int(sys.argv[3])
      signature_digest = sys.argv[4]
      signer_key_id = sys.argv[5]
      public_key = sys.argv[6]
      trusted_comment = sys.argv[7]

      def file_digest(path):
          digest = hashlib.sha256()
          with path.open("rb") as stream:
              for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                  digest.update(chunk)
          return digest.hexdigest()

      archive_path = pathlib.Path(sys.argv[8])
      signature_path = pathlib.Path(sys.argv[9])
      if archive_path.stat().st_size != archive_size or file_digest(archive_path) != archive_digest:
          raise SystemExit("Traycer Host archive size or digest mismatch")
      signature = signature_path.read_bytes()
      if len(signature) != signature_size or hashlib.sha256(signature).hexdigest() != signature_digest:
          raise SystemExit("Traycer Host minisig size or digest mismatch")
      lines = signature.decode("utf-8").splitlines()
      if len(lines) != 4 or lines[2] != f"trusted comment: {trusted_comment}":
          raise SystemExit("Traycer Host minisig trusted comment mismatch")
      public_key_packet = base64.b64decode(public_key, validate=True)
      if len(public_key_packet) != 42 or public_key_packet[:2] != b"Ed":
          raise SystemExit("Traycer Host minisign public key packet is invalid")
      public_key_id = public_key_packet[2:10]
      if public_key_id.hex() != signer_key_id:
          raise SystemExit("Traycer Host minisign public key ID mismatch")
      signature_packet = base64.b64decode(lines[1], validate=True)
      if (
          len(signature_packet) != 74
          or signature_packet[:2] != b"ED"
          or signature_packet[2:10] != public_key_id
      ):
          raise SystemExit("Traycer Host minisig signer key ID mismatch")
      PY
      ${lib.getExe minisign} \
        -Vm "$archive" \
        -x "$signature" \
        -P ${lib.escapeShellArg expectedHostMinisignPublicKey}

      ${lib.getExe python3} - \
        "$archive" \
        ${toString expectedHostArchiveMemberCount} \
        ${toString expectedHostArchiveFileCount} <<'PY'
      import pathlib
      import sys
      import tarfile

      archive_path = pathlib.Path(sys.argv[1])
      expected_member_count = int(sys.argv[2])
      expected_file_count = int(sys.argv[3])
      member_count = 0
      file_count = 0
      with tarfile.open(archive_path, mode="r|gz") as archive_stream:
          for member in archive_stream:
              member_count += 1
              member_path = pathlib.PurePosixPath(member.name)
              if member_path.is_absolute() or ".." in member_path.parts:
                  raise SystemExit("Traycer Host archive contains an unsafe path")
              if member.isfile():
                  file_count += 1
              elif not member.isdir():
                  raise SystemExit("Traycer Host archive contains links or special files")
      if member_count != expected_member_count:
          raise SystemExit(
              f"Traycer Host archive member count mismatch: "
              f"{member_count} != {expected_member_count}"
          )
      if file_count != expected_file_count:
          raise SystemExit(
              f"Traycer Host archive file count mismatch: "
              f"{file_count} != {expected_file_count}"
          )
      PY

      mkdir extracted
      ${lib.getExe' libarchive "bsdtar"} -xzf "$archive" -C extracted
      mapfile -t hostBinaries < <(find extracted -type f -name traycer-host -perm -0100 -print)
      if [ "''${#hostBinaries[@]}" -ne 1 ]; then
        echo "Traycer Host archive must contain exactly one executable named traycer-host" >&2
        exit 1
      fi
      hostRoot="$(dirname "''${hostBinaries[0]}")"
      ${lib.getExe python3} - \
        "$hostRoot/version.json" \
        ${lib.escapeShellArg expectedVersion} \
        "$hostRoot/traycer-host" \
        ${lib.escapeShellArg verifiedHostCodesignIdentity.executableSha256} <<'PY'
      import hashlib
      import json
      import pathlib
      import sys

      payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
      if payload.get("version") != sys.argv[2]:
          raise SystemExit("Traycer Host version.json does not match the pinned version")
      digest = hashlib.sha256()
      with pathlib.Path(sys.argv[3]).open("rb") as executable:
          for chunk in iter(lambda: executable.read(1024 * 1024), b""):
              digest.update(chunk)
      if digest.hexdigest() != sys.argv[4]:
          raise SystemExit("Traycer Host executable digest mismatch")
      PY
      mkdir -p "$out/host-runtime"
      cp -R "$hostRoot"/. "$out/host-runtime"
      test -x "$out/${hostRuntimeRelativeExecutable}"
      ${lib.getExe python3} - \
        "$hostRoot" \
        "$out/host-runtime" \
        ${toString expectedHostArchiveFileCount} <<'PY'
      import hashlib
      import pathlib
      import stat
      import sys

      expected_file_count = int(sys.argv[3])

      def file_digest(path):
          digest = hashlib.sha256()
          with path.open("rb") as stream:
              for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                  digest.update(chunk)
          return digest.hexdigest()

      def tree_manifest(root):
          manifest = {}
          file_count = 0
          for path in sorted(root.rglob("*")):
              relative_path = path.relative_to(root).as_posix()
              metadata = path.lstat()
              mode = stat.S_IMODE(metadata.st_mode)
              if stat.S_ISDIR(metadata.st_mode):
                  manifest[relative_path] = ("directory", mode)
              elif stat.S_ISREG(metadata.st_mode):
                  file_count += 1
                  manifest[relative_path] = (
                      "file",
                      mode,
                      metadata.st_size,
                      file_digest(path),
                  )
              else:
                  raise SystemExit(
                      f"Traycer Host tree contains links or special files: {relative_path}"
                  )
          return manifest, file_count

      source_manifest, source_file_count = tree_manifest(pathlib.Path(sys.argv[1]))
      output_manifest, output_file_count = tree_manifest(pathlib.Path(sys.argv[2]))
      if source_file_count != expected_file_count:
          raise SystemExit(
              f"Traycer Host source file count mismatch: "
              f"{source_file_count} != {expected_file_count}"
          )
      if output_file_count != expected_file_count:
          raise SystemExit(
              f"Traycer Host output file count mismatch: "
              f"{output_file_count} != {expected_file_count}"
          )
      if output_manifest != source_manifest:
          raise SystemExit("Traycer Host output tree differs from authenticated source")
      PY

      machOCount=0
      universalMachOCount=0
      thinX8664MachOCount=0
      while IFS= read -r -d $'\0' candidate; do
        fileDescription="$(/usr/bin/file -b "$candidate")"
        case "$fileDescription" in
          Mach-O*)
            machOCount=$((machOCount + 1))
            case "$fileDescription" in
              "Mach-O universal binary"*)
                universalMachOCount=$((universalMachOCount + 1))
                ;;
              *x86_64*)
                thinX8664MachOCount=$((thinX8664MachOCount + 1))
                ;;
            esac
            /usr/bin/codesign --verify --strict --verbose=2 "$candidate"
            candidateCodesignDetails="$(/usr/bin/codesign -d --verbose=4 "$candidate" 2>&1)"
            candidateTeamIdentifier="$(printf '%s\n' "$candidateCodesignDetails" | sed -n 's/^TeamIdentifier=//p')"
            test "$candidateTeamIdentifier" = ${lib.escapeShellArg verifiedHostCodesignIdentity.teamIdentifier}
            ;;
        esac
      done < <(find "$out/host-runtime" -type f -print0)
      test "$machOCount" -eq ${toString expectedHostMachOCount}
      test "$universalMachOCount" -eq ${toString expectedHostUniversalMachOCount}
      test "$thinX8664MachOCount" -eq ${toString expectedHostThinX8664MachOCount}

      outputHostExecutable="$out/${hostRuntimeRelativeExecutable}"
      codesignDetails="$(/usr/bin/codesign -d --verbose=4 "$outputHostExecutable" 2>&1)"
      actualIdentifier="$(printf '%s\n' "$codesignDetails" | sed -n 's/^Identifier=//p')"
      test "$actualIdentifier" = ${lib.escapeShellArg verifiedHostCodesignIdentity.identifier}
      actualRequirement="$(/usr/bin/codesign -d -r- "$outputHostExecutable" 2>&1 | sed -n 's/^designated => //p')"
      test "$actualRequirement" = ${lib.escapeShellArg verifiedHostCodesignIdentity.designatedRequirement}

      outputHostRipgrep="$out/${hostRipgrepRelativeExecutable}"
      test -x "$outputHostRipgrep"
      hostRipgrepVersionOutput="$(/usr/bin/env -i "$outputHostRipgrep" --version)"
      hostRipgrepVersionLine="$(printf '%s\n' "$hostRipgrepVersionOutput" | sed -n '1p')"
      read -r hostRipgrepName hostRipgrepVersionNumber hostRipgrepRevision <<< "$hostRipgrepVersionLine"
      test "$hostRipgrepName" = ripgrep
      test "$hostRipgrepVersionNumber" = ${lib.escapeShellArg expectedHostRipgrepVersion}
      hostRipgrepFeatures="$(printf '%s\n' "$hostRipgrepVersionOutput" | sed -n '/^features:/p')"
      test "$hostRipgrepFeatures" = ${lib.escapeShellArg expectedHostRipgrepPcre2Feature}
      hostRipgrepPcre2Result="$(
        printf 'traycer-pcre2\n' | /usr/bin/env -i "$outputHostRipgrep" --no-config --color=never --no-heading --no-filename --no-line-number -P '^traycer-(?=pcre2$)pcre2$'
      )"
      test "$hostRipgrepPcre2Result" = traycer-pcre2

      ${lib.getExe python3} - \
        "$out/install.json" \
        "$out/${hostRuntimeRelativeExecutable}" \
        ${lib.escapeShellArg expectedHostInstallId} \
        ${lib.escapeShellArg expectedVersion} \
        ${lib.escapeShellArg expectedHostArchiveSha256} \
        ${lib.escapeShellArg expectedHostMinisignKeyId} \
        ${toString expectedHostArchiveSize} \
        ${lib.escapeShellArg expectedHostInstallSentinelTimestamp} <<'PY'
      import json
      import os
      import pathlib
      import sys

      record_path = pathlib.Path(sys.argv[1])
      executable_path = pathlib.Path(sys.argv[2])
      expected_executable_path = record_path.parent / "host-runtime/traycer-host"
      if record_path.name != "install.json" or executable_path != expected_executable_path:
          raise SystemExit("Traycer Host production layout mismatch")
      if not executable_path.is_file() or not os.access(executable_path, os.X_OK):
          raise SystemExit("Traycer Host record executable is absent or not executable")

      version = sys.argv[4]
      sentinel_timestamp = sys.argv[8]
      install_record = {
          "installId": sys.argv[3],
          "version": version,
          "runtimeVersion": version,
          "platform": "darwin",
          "arch": "arm64",
          "installedAt": sentinel_timestamp,
          "source": {"kind": "registry", "value": version},
          "archiveSha256": sys.argv[5],
          "signatureVerifiedAt": sentinel_timestamp,
          "signatureKeyId": sys.argv[6],
          "sizeBytes": int(sys.argv[7]),
          "executablePath": str(executable_path),
      }
      with record_path.open("x", encoding="utf-8") as record_stream:
          json.dump(install_record, record_stream, indent=2)
          record_stream.write("\n")
      record_path.chmod(0o444)
      PY
      runHook postBuild
    '';
    installPhase = "true";
    passthru = {
      inherit hostRuntimeRelativeExecutable mixedProvenance;
    };
    meta = {
      license = lib.licenses.unfree;
      platforms = [ "aarch64-darwin" ];
    };
  };

  # Keep source preparation metadata-only. bun2nix.hook materializes the exact
  # generated cache before the package's source build starts.
  srcWithBun = stdenvNoCC.mkDerivation {
    pname = "${pname}-src-with-bun";
    inherit version;
    src = traycerSource;
    dontUnpack = true;
    dontFixup = true;
    installPhase = ''
      runHook preInstall
      mkdir -p "$out"
      cp -R "$src"/. "$out"
      chmod -R u+w "$out"
      cp ${./bun.lock} "$out/bun.lock"
      runHook postInstall
    '';
  };

  realPackage = stdenv.mkDerivation {
    inherit pname version;
    src = srcWithBun;
    nativeBuildInputs = [
      bunExact
      bun2nix.hook
      cctools
      makeWrapper
      nodejs_24
      python3
    ];
    strictDeps = true;
    dontRunLifecycleScripts = true;
    dontStrip = true;
    disallowedReferences = [
      bunDeps
      bunExact
      srcWithBun
      traycerSource
    ]
    ++ bunDeps.nixcfg.shardOutputs;
    inherit bunDeps;
    bunInstallFlags = [
      "--offline"
      "--linker=isolated"
      "--backend=symlink"
      "--frozen-lockfile"
    ];
    preBunNodeModulesInstallPhase = ''
      export PATH="${bunExact}/bin:$PATH"
      hash -r
      test "$(command -v bun)" = "${lib.getExe bunExact}"
      test "$(bun --version)" = "${expectedBunVersion}"
    '';
    env = electronBuild.commonEnv // {
      CI = "1";
      CSC_IDENTITY_AUTO_DISCOVERY = "false";
      ELECTRON_SKIP_BINARY_DOWNLOAD = "1";
      MACOSX_DEPLOYMENT_TARGET = minimumMacOSVersion;
      VITE_DESKTOP_LOCAL_STORAGE_KEY = desktopLocalStorageKey;
      NODE_OPTIONS = "--max-old-space-size=6144";
    };
    postPatch = ''
      ${lib.getExe python3} ${./patch_nix_managed.py} "$PWD" ${hostRuntime}
      test "$(${lib.getExe python3} ${./patch_nix_managed.py} --check "$PWD" ${hostRuntime})" = \
        "validated 0 Traycer Nix policy patches"
      install -Dm0644 \
        ${./tests/cli/nix-managed-command-policy.test.ts} \
        clients/traycer-cli/src/__tests__/nix-managed-command-policy.test.ts
      install -Dm0644 \
        ${./tests/desktop/nix-managed-updater-policy.test.ts} \
        clients/desktop/src/electron-main/app/__tests__/nix-managed-updater-policy.test.ts
      install -Dm0644 \
        ${./tests/desktop/nix-managed-host-controller-policy.test.ts} \
        clients/desktop/src/electron-main/host/__tests__/nix-managed-host-controller-policy.test.ts
    '';
    buildPhase = ''
      runHook preBuild
      export HOME="$TMPDIR/traycer-build-home"
      mkdir -p "$HOME"
      unset SENTRY_AUTH_TOKEN TRAYCER_CLI_SENTRY_DSN TRAYCER_DESKTOP_SENTRY_DSN TRAYCER_DESKTOP_SENTRY_RENDERER_DSN

      bun clients/traycer-cli/scripts/set-deploy-target.cjs \
        --target=production \
        --version=${version} \
        --supported-host-version=${version}
      bun clients/desktop/scripts/set-deploy-target.cjs \
        --target=production \
        --version=${version}
      bun run --cwd clients/traycer-cli compile
      bun run --cwd clients/desktop compile
      (
        cd clients/traycer-cli
        ../../node_modules/.bin/vitest run \
          src/__tests__/nix-managed-command-policy.test.ts
      )
      (
        cd clients/desktop
        ../../node_modules/.bin/vitest run \
          src/electron-main/app/__tests__/nix-managed-updater-policy.test.ts
        ../../node_modules/.bin/vitest run \
          src/electron-main/host/__tests__/nix-managed-host-controller-policy.test.ts
      )
      TRAYCER_CLI_VERSION=${version} node clients/traycer-cli/scripts/build-cli-sea.cjs

      cliResource=clients/desktop/resources/cli/darwin-arm64
      mkdir -p "$cliResource"
      install -m0755 clients/traycer-cli/dist-sea/traycer "$cliResource/traycer"
      printf '%s\n' ${
        lib.escapeShellArg (builtins.toJSON { inherit version; })
      } > "$cliResource/version.json"

      bun run --cwd clients/desktop build:app
      bun clients/desktop/scripts/prepack/check-cli-resource.cjs --platform darwin --arch arm64
      bun clients/desktop/scripts/prepack/check-bundle-icons.cjs
      bun clients/desktop/scripts/prepack/check-tray-assets.cjs

      fontListModule="clients/desktop/node_modules/font-list"
      fontListIsolatedTarget="../../../node_modules/.bun/font-list@2.1.0/node_modules/font-list"
      fontListInstalled="$(realpath "$fontListModule")"
      fontListBuildHelper="$fontListModule/libs/darwin/fontlist"
      entitlements="${traycerSource}/${desktopEntitlementsRelativePath}"
      test -L "$fontListModule"
      test "$(readlink "$fontListModule")" = "$fontListIsolatedTarget"
      test -d "$fontListInstalled"
      test "$(${lib.getExe python3} -c 'import json, sys; data = json.load(open(sys.argv[1])); print(data.get("name", "") + "@" + data.get("version", ""))' "$fontListInstalled/package.json")" = "font-list@2.1.0"
      fontListWritable="$TMPDIR/traycer-font-list-packaging"
      test ! -e "$fontListWritable"
      mkdir "$fontListWritable"
      cp -R "$fontListInstalled"/. "$fontListWritable"/
      chmod -R u+w "$fontListWritable"
      test "$(${lib.getExe python3} -c 'import json, sys; data = json.load(open(sys.argv[1])); print(data.get("name", "") + "@" + data.get("version", ""))' "$fontListWritable/package.json")" = "font-list@2.1.0"
      rm "$fontListModule"
      mv "$fontListWritable" "$fontListModule"
      test -d "$fontListModule"
      test ! -L "$fontListModule"
      test -x "$fontListBuildHelper"
      /usr/bin/codesign --force --sign - --identifier fontlist --options runtime --entitlements "$entitlements" "$fontListBuildHelper"
      /usr/bin/codesign --verify --strict --verbose=2 "$fontListBuildHelper"

      ${lib.getExe python3} - clients/desktop/package.json <<'PY'
      import json
      import pathlib
      import sys

      manifest_path = pathlib.Path(sys.argv[1])
      manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
      expected_dependencies = {
          "@sentry/browser": "catalog:",
          "@sentry/electron": "catalog:",
          "electron-log": "catalog:",
          "electron-updater": "^6.8.9",
          "encrypt-storage": "catalog:",
          "font-list": "^2.1.0",
          "react": "catalog:",
          "react-dom": "catalog:",
      }
      if manifest.get("dependencies") != expected_dependencies:
          raise SystemExit(
              "Traycer desktop dependency manifest drifted before packaging: "
              f"{manifest.get('dependencies')!r}"
          )
      packaged_dependencies = {"font-list": "^2.1.0"}
      manifest["dependencies"] = packaged_dependencies
      manifest_path.write_text(
          json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
          encoding="utf-8",
      )
      if json.loads(manifest_path.read_text(encoding="utf-8")).get(
          "dependencies"
      ) != packaged_dependencies:
          raise SystemExit("Traycer desktop dependency narrowing did not persist")
      PY
      ${electronBuild.copyDist}
      defaultApp="$electronDistDir/Electron.app/Contents/Resources/default_app.asar"
      test -f "$defaultApp"
      rm "$defaultApp"
      (
        cd clients/desktop
        bun x --no-install electron-builder \
          --mac \
          --arm64 \
          --dir \
          --publish never \
          -c.extraMetadata.version=${version} \
          -c.mac.identity=null \
          -c.mac.notarize=false \
          -c.npmRebuild=false \
          ${electronBuild.electronBuilderConfigFlags}
      )
      runHook postBuild
    '';
    installPhase = ''
      runHook preInstall
      appBundle="clients/desktop/release/mac-arm64/${appBundleName}"
      test -d "$appBundle"
      mkdir -p "$out/Applications" "$out/bin" "$out/share/licenses/${pname}"
      cp -R "$appBundle" "$out/Applications/${appBundleName}"
      install -m0644 LICENSE "$out/share/licenses/${pname}/LICENSE"
      install -m0644 clients/desktop/node_modules/font-list/LICENSE "$out/share/licenses/${pname}/font-list-LICENSE"
      ln -s \
        "../Applications/${appBundleName}/Contents/Resources/cli/darwin-arm64/traycer" \
        "$out/bin/traycer"
      makeWrapper \
        "$out/Applications/${appBundleName}/Contents/MacOS/${appExecutableName}" \
        "$out/bin/traycer-desktop"
      runHook postInstall
    '';
    postFixup = ''
      app="$out/Applications/${appBundleName}"
      entitlements=${traycerSource}/${desktopEntitlementsRelativePath}
      test -f "$entitlements"
      /usr/bin/xattr -cr "$app"
      ${desktopSigningCommands}
    '';
    doInstallCheck = true;
    installCheckPhase = ''
      runHook preInstallCheck
      app="$out/Applications/${appBundleName}"
      resources="$app/Contents/Resources"
      plist="$app/Contents/Info.plist"
      cli="$resources/cli/darwin-arm64/traycer"
      fontListHelper="$resources/app.asar.unpacked/node_modules/font-list/libs/darwin/fontlist"
      fontListLicense="$out/share/licenses/${pname}/font-list-LICENSE"
      test -f "$resources/app.asar"
      test -d "$resources/renderer"
      test -x "$cli"
      test -x "$fontListHelper"
      test -f "$fontListLicense"
      ${lib.getExe python3} ${./validate_renderer_storage_key.py} "$resources/renderer"
      test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$plist")" = "${appId}"
      test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$plist")" = "${version}"
      test "$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$plist")" = "${minimumMacOSVersion}"
      test "$(readlink "$out/bin/traycer")" = "../Applications/${appBundleName}/Contents/Resources/cli/darwin-arm64/traycer"
      test "$(realpath "$out/bin/traycer")" = "$(realpath "$cli")"
      test "$("$cli" --version)" = "${version}"
      ${lib.getExe python3} - "$cli" "$resources/app.asar" "$resources" "$plist" <<'PY'
      import hashlib
      import json
      import pathlib
      import plistlib
      import struct
      import sys

      forbidden_artifact_tokens = (
          b"/nix/var/nix/builds/",
          b"/private/tmp/",
          b"traycer-src-with-bun",
          b".bun/",
      )
      for argument in sys.argv[1:3]:
          candidate = pathlib.Path(argument)
          contents = candidate.read_bytes()
          leaked = next(
              (token for token in forbidden_artifact_tokens if token in contents),
              None,
          )
          if leaked is not None:
              raise SystemExit(
                  "Traycer artifact retains a source/cache/build path token: "
                  f"{candidate}: {leaked.decode('utf-8', errors='replace')}"
              )

      asar_path = pathlib.Path(sys.argv[2])
      resources_path = pathlib.Path(sys.argv[3])
      expected_asar_files = {
          "dist/main/index.js",
          "dist/preload/index.js",
          "package.json",
          "node_modules/font-list/index.js",
          "node_modules/font-list/libs/core.js",
          "node_modules/font-list/libs/darwin/fontlist",
          "node_modules/font-list/libs/darwin/index.js",
          "node_modules/font-list/libs/standardize.js",
          "node_modules/font-list/package.json",
      }
      with asar_path.open("rb") as archive:
          prefix = archive.read(16)
          if len(prefix) != 16 or struct.unpack_from("<I", prefix, 0)[0] != 4:
              raise SystemExit("Traycer app.asar has an invalid pickle prefix")
          header_pickle_size = struct.unpack_from("<I", prefix, 4)[0]
          header_data_size = struct.unpack_from("<I", prefix, 8)[0]
          header_json_size = struct.unpack_from("<I", prefix, 12)[0]
          padding_size = (-header_json_size) % 4
          if (
              header_pickle_size != header_data_size + 4
              or header_data_size != 4 + header_json_size + padding_size
              or 8 + header_pickle_size > asar_path.stat().st_size
          ):
              raise SystemExit("Traycer app.asar has inconsistent header sizes")
          header_json = archive.read(header_json_size)
          header_padding = archive.read(padding_size)
          if (
              len(header_json) != header_json_size
              or len(header_padding) != padding_size
              or any(header_padding)
          ):
              raise SystemExit("Traycer app.asar has invalid header padding")
          header = json.loads(header_json)

      integrity = plistlib.loads(pathlib.Path(sys.argv[4]).read_bytes()).get(
          "ElectronAsarIntegrity"
      )
      expected_integrity = {
          "Resources/app.asar": {
              "algorithm": "SHA256",
              "hash": hashlib.sha256(header_json).hexdigest(),
          }
      }
      if integrity != expected_integrity:
          raise SystemExit(
              "Traycer ElectronAsarIntegrity drifted: "
              f"expected={expected_integrity!r}, actual={integrity!r}"
          )

      actual_asar_files = {}

      def collect_asar_files(node, prefix=""):
          children = node.get("files")
          if not isinstance(children, dict):
              raise SystemExit(f"Traycer app.asar directory is malformed: {prefix}")
          for name, child in children.items():
              if not isinstance(name, str) or "/" in name or not isinstance(child, dict):
                  raise SystemExit(f"Traycer app.asar entry is malformed: {prefix}/{name}")
              relative = f"{prefix}/{name}" if prefix else name
              if "files" in child:
                  collect_asar_files(child, relative)
              else:
                  actual_asar_files[relative] = child

      collect_asar_files(header)
      if set(actual_asar_files) != expected_asar_files:
          raise SystemExit(
              "Traycer app.asar inventory drifted: "
              f"missing={sorted(expected_asar_files - set(actual_asar_files))}, "
              f"extra={sorted(set(actual_asar_files) - expected_asar_files)}"
          )

      asar_data_offset = 8 + header_pickle_size
      asar_size = asar_path.stat().st_size
      packed_ranges = []
      for relative_path, metadata in actual_asar_files.items():
          if "link" in metadata:
              raise SystemExit(f"Traycer app.asar contains a link: {relative_path}")
          payload_size = metadata.get("size")
          if type(payload_size) is not int or payload_size < 0:
              raise SystemExit(
                  f"Traycer app.asar has an invalid size for {relative_path}: "
                  f"{payload_size!r}"
              )
          integrity = metadata.get("integrity")
          if not isinstance(integrity, dict) or set(integrity) != {
              "algorithm",
              "hash",
              "blockSize",
              "blocks",
          }:
              raise SystemExit(
                  f"Traycer app.asar has invalid integrity metadata for {relative_path}"
              )
          block_size = integrity.get("blockSize")
          blocks = integrity.get("blocks")
          if (
              integrity.get("algorithm") != "SHA256"
              or type(block_size) is not int
              or block_size <= 0
              or not isinstance(blocks, list)
              or not all(isinstance(block, str) for block in blocks)
          ):
              raise SystemExit(
                  f"Traycer app.asar has malformed SHA256 metadata for {relative_path}"
              )
          if metadata.get("unpacked") is True:
              if "offset" in metadata:
                  raise SystemExit(
                      f"Traycer unpacked ASAR entry has an offset: {relative_path}"
                  )
              payload_path = resources_path / "app.asar.unpacked" / relative_path
              if payload_path.is_symlink() or not payload_path.is_file():
                  raise SystemExit(
                      f"Traycer unpacked ASAR payload is missing: {relative_path}"
                  )
              payload = payload_path.read_bytes()
          else:
              offset = metadata.get("offset")
              if not isinstance(offset, str) or not offset.isdecimal():
                  raise SystemExit(
                      f"Traycer packed ASAR entry has an invalid offset: {relative_path}"
                  )
              packed_offset = int(offset)
              payload_start = asar_data_offset + packed_offset
              payload_end = payload_start + payload_size
              if payload_start < asar_data_offset or payload_end > asar_size:
                  raise SystemExit(
                      f"Traycer packed ASAR entry exceeds the archive: {relative_path}"
                  )
              packed_ranges.append(
                  (packed_offset, packed_offset + payload_size, relative_path)
              )
              with asar_path.open("rb") as archive:
                  archive.seek(payload_start)
                  payload = archive.read(payload_size)
          if len(payload) != payload_size:
              raise SystemExit(
                  f"Traycer ASAR payload size drifted for {relative_path}: "
                  f"expected={payload_size}, actual={len(payload)}"
              )
          expected_hash = hashlib.sha256(payload).hexdigest()
          expected_blocks = [
              hashlib.sha256(payload[start : start + block_size]).hexdigest()
              for start in range(0, len(payload), block_size)
          ]
          if integrity.get("hash") != expected_hash or blocks != expected_blocks:
              raise SystemExit(
                  f"Traycer ASAR payload integrity drifted for {relative_path}"
              )

      packed_ranges.sort()
      packed_data_size = asar_size - asar_data_offset
      if not packed_ranges or packed_ranges[0][0] != 0:
          raise SystemExit("Traycer packed ASAR data does not start at offset zero")
      for previous, current in zip(packed_ranges, packed_ranges[1:], strict=False):
          if previous[1] != current[0]:
              raise SystemExit(
                  "Traycer packed ASAR data contains a gap or overlap: "
                  f"{previous[2]} ends at {previous[1]}, "
                  f"{current[2]} starts at {current[0]}"
              )
      if packed_ranges[-1][1] != packed_data_size:
          raise SystemExit(
              "Traycer packed ASAR data does not consume the complete archive: "
              f"last_end={packed_ranges[-1][1]}, data_size={packed_data_size}"
          )

      font_list_unpacked_files = {
          "node_modules/font-list/libs/darwin/fontlist",
      }
      actual_unpacked_entries = {
          path
          for path, metadata in actual_asar_files.items()
          if metadata.get("unpacked") is True
      }
      if actual_unpacked_entries != font_list_unpacked_files:
          raise SystemExit(
              "Traycer app.asar unpacked-entry metadata drifted: "
              f"expected={sorted(font_list_unpacked_files)}, "
              f"actual={sorted(actual_unpacked_entries)}"
          )

      unpacked_root = resources_path / "app.asar.unpacked"
      actual_unpacked_files = set()
      for candidate in unpacked_root.rglob("*"):
          if candidate.is_symlink():
              raise SystemExit(f"Traycer unpacked payload contains a symlink: {candidate}")
          if candidate.is_file():
              actual_unpacked_files.add(candidate.relative_to(unpacked_root).as_posix())
      if actual_unpacked_files != font_list_unpacked_files:
          raise SystemExit(
              "Traycer unpacked payload inventory drifted: "
              f"missing={sorted(font_list_unpacked_files - actual_unpacked_files)}, "
              f"extra={sorted(actual_unpacked_files - font_list_unpacked_files)}"
          )

      tray_root = resources_path / "tray"
      expected_tray_files = {
          "tray.png",
          "tray@2x.png",
          "trayTemplate.png",
          "trayTemplate@2x.png",
      }
      actual_tray_files = set()
      for candidate in tray_root.rglob("*"):
          if candidate.is_symlink():
              raise SystemExit(f"Traycer tray payload contains a symlink: {candidate}")
          if candidate.is_file():
              actual_tray_files.add(candidate.relative_to(tray_root).as_posix())
      if actual_tray_files != expected_tray_files:
          raise SystemExit(
              "Traycer tray payload inventory drifted: "
              f"missing={sorted(expected_tray_files - actual_tray_files)}, "
              f"extra={sorted(actual_tray_files - expected_tray_files)}"
          )
      for forbidden_path in (
          resources_path / "default_app.asar",
          resources_path / "host",
      ):
          if forbidden_path.exists() or forbidden_path.is_symlink():
              raise SystemExit(f"Traycer retains a forbidden resource: {forbidden_path}")
      PY

      expectedNativeInventory="$TMPDIR/traycer-expected-native-inventory"
      cat > "$expectedNativeInventory" <<'EOF'
      ${desktopNativeInventory}
      EOF
      ${lib.getExe python3} - \
        "$app" \
        "$expectedNativeInventory" \
        ${lib.escapeShellArg minimumMacOSVersion} \
        ${traycerSource}/${desktopEntitlementsRelativePath} <<'PY'
      import pathlib
      import plistlib
      import re
      import subprocess
      import sys

      app = pathlib.Path(sys.argv[1])
      inventory_path = pathlib.Path(sys.argv[2])
      minimum_macos = tuple(int(part) for part in sys.argv[3].split("."))
      entitlements_path = pathlib.Path(sys.argv[4])

      required_entitlements = {
          "com.apple.security.cs.allow-jit": True,
          "com.apple.security.cs.allow-unsigned-executable-memory": True,
          "com.apple.security.cs.allow-dyld-environment-variables": True,
          "com.apple.security.cs.disable-library-validation": True,
          "com.apple.security.device.audio-input": True,
      }
      with entitlements_path.open("rb") as stream:
          source_entitlements = plistlib.load(stream)
      if source_entitlements != required_entitlements:
          raise SystemExit("Traycer upstream entitlement contract drifted")

      expected = []
      for line in inventory_path.read_text(encoding="utf-8").splitlines():
          relative, identifier, architectures = line.split("|", 2)
          expected.append((relative, identifier, frozenset(architectures.split(","))))

      def run(*args: str) -> str:
          result = subprocess.run(
              args,
              check=False,
              stdout=subprocess.PIPE,
              stderr=subprocess.STDOUT,
              text=True,
          )
          if result.returncode != 0:
              raise SystemExit(
                  f"Traycer native audit command failed ({result.returncode}): "
                  f"{' '.join(args)}\n{result.stdout}"
              )
          return result.stdout

      def run_otool(path: pathlib.Path, *flags: str) -> str:
          # Apple's classic otool treats parentheses in a quoted pathname as
          # archive-member syntax. Passing the already-open file descriptor
          # keeps exact helper names such as "Traycer Helper (GPU)" intact.
          with path.open("rb") as stream:
              result = subprocess.run(
                  [
                      "/usr/bin/otool",
                      *flags,
                      f"/dev/fd/{stream.fileno()}",
                  ],
                  check=False,
                  pass_fds=(stream.fileno(),),
                  stdout=subprocess.PIPE,
                  stderr=subprocess.STDOUT,
                  text=True,
              )
          if result.returncode != 0:
              raise SystemExit(
                  f"Traycer native audit otool failed ({result.returncode}): "
                  f"{path} flags={flags!r}\n{result.stdout}"
              )
          return result.stdout

      def is_macho(path: pathlib.Path) -> bool:
          return "Mach-O" in run("/usr/bin/file", "-b", str(path))

      actual = []
      for candidate in app.rglob("*"):
          if candidate.is_symlink() or not candidate.is_file():
              continue
          if is_macho(candidate):
              actual.append(str(candidate.relative_to(app)))
      expected_paths = [entry[0] for entry in expected]
      if sorted(actual) != sorted(expected_paths):
          missing = sorted(set(expected_paths) - set(actual))
          extra = sorted(set(actual) - set(expected_paths))
          raise SystemExit(
              f"Traycer native inventory mismatch: missing={missing!r} extra={extra!r}"
          )

      def version_tuple(value: str) -> tuple[int, ...]:
          return tuple(int(part) for part in value.split("."))

      load_command_cache = {}

      def macho_load_commands(path: pathlib.Path) -> list[str]:
          if path not in load_command_cache:
              load_command_cache[path] = run_otool(path, "-l").splitlines()
          return load_command_cache[path]

      def audit_minos(path: pathlib.Path) -> None:
          lines = macho_load_commands(path)
          versions = []
          for index, line in enumerate(lines):
              command = line.strip()
              if command not in {"cmd LC_BUILD_VERSION", "cmd LC_VERSION_MIN_MACOSX"}:
                  continue
              key = "minos" if command == "cmd LC_BUILD_VERSION" else "version"
              for detail in lines[index + 1 : index + 12]:
                  match = re.match(rf"^\s*{key}\s+([0-9.]+)(?:\s|$)", detail)
                  if match is not None:
                      versions.append(version_tuple(match.group(1)))
                      break
          if not versions:
              raise SystemExit(f"Traycer native object has no macOS deployment target: {path}")
          if any(version > minimum_macos for version in versions):
              raise SystemExit(
                  f"Traycer native object requires macOS newer than {sys.argv[3]}: "
                  f"{path} {versions!r}"
              )

      def macho_rpaths(path: pathlib.Path) -> tuple[str, ...]:
          lines = macho_load_commands(path)
          rpaths = []
          for index, line in enumerate(lines):
              if line.strip() != "cmd LC_RPATH":
                  continue
              for detail in lines[index + 1 : index + 8]:
                  match = re.match(r"^\s*path (.+) \(offset [0-9]+\)$", detail)
                  if match is not None:
                      rpaths.append(match.group(1))
                      break
          return tuple(rpaths)

      dependency_commands = {
          "cmd LC_LAZY_LOAD_DYLIB",
          "cmd LC_LOAD_DYLIB",
          "cmd LC_LOAD_UPWARD_DYLIB",
          "cmd LC_LOAD_WEAK_DYLIB",
          "cmd LC_REEXPORT_DYLIB",
      }

      def macho_dependencies(path: pathlib.Path) -> tuple[str, ...]:
          lines = macho_load_commands(path)
          dependencies = []
          for index, line in enumerate(lines):
              if line.strip() not in dependency_commands:
                  continue
              for detail in lines[index + 1 : index + 8]:
                  match = re.match(r"^\s*name (.+) \(offset [0-9]+\)$", detail)
                  if match is not None:
                      dependencies.append(match.group(1))
                      break
          return tuple(dependencies)

      def expand_macho_path(
          value: str,
          *,
          loader_dir: pathlib.Path,
          executable_dir: pathlib.Path,
      ) -> pathlib.Path:
          if value == "@loader_path":
              return loader_dir
          if value.startswith("@loader_path/"):
              return loader_dir / value.removeprefix("@loader_path/")
          if value == "@executable_path":
              return executable_dir
          if value.startswith("@executable_path/"):
              return executable_dir / value.removeprefix("@executable_path/")
          if value.startswith("@"):
              raise SystemExit(f"Traycer native path uses an unknown token: {value}")
          path = pathlib.Path(value)
          if not path.is_absolute():
              raise SystemExit(f"Traycer native path is relative: {value}")
          return path

      def is_system_dependency(path: pathlib.Path) -> bool:
          value = str(path)
          return value.startswith(("/usr/lib/", "/System/Library/"))

      def resolve_dependency(
          dependency: str,
          *,
          loader_dir: pathlib.Path,
          executable_dir: pathlib.Path,
          rpaths: tuple[pathlib.Path, ...],
      ) -> pathlib.Path:
          if dependency.startswith("@rpath/"):
              suffix = dependency.removeprefix("@rpath/")
              candidates = [rpath / suffix for rpath in rpaths]
          else:
              candidates = [
                  expand_macho_path(
                      dependency,
                      loader_dir=loader_dir,
                      executable_dir=executable_dir,
                  )
              ]
          for candidate in candidates:
              # System libraries may be supplied only by the dyld shared cache,
              # so their filesystem path is an approved terminal dependency.
              if is_system_dependency(candidate) or candidate.exists():
                  return candidate
          raise SystemExit(
              f"Traycer native dependency does not resolve: "
              f"{dependency} candidates={[str(path) for path in candidates]!r}"
          )

      def unique_paths(paths: list[pathlib.Path]) -> tuple[pathlib.Path, ...]:
          return tuple(dict.fromkeys(paths))

      def is_executable(path: pathlib.Path) -> bool:
          header = run_otool(path, "-hv")
          return re.search(r"\bEXECUTE\b", header) is not None

      application_executable = app / "Contents/MacOS/Traycer"
      application_executable_dir = application_executable.parent
      application_rpaths = unique_paths([
          expand_macho_path(
              rpath,
              loader_dir=application_executable.parent,
              executable_dir=application_executable_dir,
          )
          for rpath in macho_rpaths(application_executable)
      ])

      def initial_dependency_context(
          path: pathlib.Path,
      ) -> tuple[pathlib.Path, tuple[pathlib.Path, ...]]:
          if is_executable(path):
              return path.parent, ()
          # Bundle dylibs are reached through the application's dyld run-path
          # stack. Seed independently audited native payloads with that same
          # executable context rather than treating each dylib as a process.
          return application_executable_dir, application_rpaths

      pending = []
      for relative in expected_paths:
          candidate = app / relative
          executable_dir, inherited_rpaths = initial_dependency_context(candidate)
          pending.append((candidate, executable_dir, inherited_rpaths))
      seen = set()
      while pending:
          candidate, executable_dir, inherited_rpaths = pending.pop()
          resolved = candidate.resolve()
          current_rpaths = unique_paths([
              expand_macho_path(
                  rpath,
                  loader_dir=resolved.parent,
                  executable_dir=executable_dir,
              )
              for rpath in macho_rpaths(resolved)
          ] + list(inherited_rpaths))
          state = (resolved, executable_dir.resolve(), current_rpaths)
          if state in seen:
              continue
          seen.add(state)
          audit_minos(resolved)
          for dependency in macho_dependencies(resolved):
              dependency_path = resolve_dependency(
                  dependency,
                  loader_dir=resolved.parent,
                  executable_dir=executable_dir,
                  rpaths=current_rpaths,
              )
              if is_system_dependency(dependency_path):
                  continue
              dependency_resolved = dependency_path.resolve()
              if not (
                  dependency_resolved.is_relative_to(app.resolve())
                  or str(dependency_resolved).startswith("/nix/store/")
              ):
                  raise SystemExit(
                      f"Traycer native dependency escaped approved roots: "
                      f"{resolved} -> {dependency_resolved}"
                  )
              if not is_macho(dependency_resolved):
                  raise SystemExit(
                      f"Traycer native dependency is not Mach-O: "
                      f"{resolved} -> {dependency_resolved}"
                  )
              pending.append((
                  dependency_resolved,
                  executable_dir,
                  current_rpaths,
              ))

      for relative, expected_identifier, expected_architectures in expected:
          candidate = app / relative
          actual_architectures = frozenset(
              run("/usr/bin/lipo", "-archs", str(candidate)).strip().split()
          )
          if actual_architectures != expected_architectures:
              raise SystemExit(
                  f"Traycer architecture mismatch: {relative} "
                  f"expected={sorted(expected_architectures)!r} "
                  f"actual={sorted(actual_architectures)!r}"
              )
          details = run("/usr/bin/codesign", "-d", "--verbose=4", str(candidate))
          identifier_match = re.search(r"^Identifier=(.+)$", details, re.MULTILINE)
          if identifier_match is None or identifier_match.group(1) != expected_identifier:
              raise SystemExit(
                  f"Traycer signing identifier mismatch: {relative} "
                  f"expected={expected_identifier!r}"
              )
          flags_match = re.search(r"^CodeDirectory .* flags=[^()]+\(([^)]+)\)", details, re.MULTILINE)
          if flags_match is None or set(flags_match.group(1).split(",")) != {"adhoc", "runtime"}:
              raise SystemExit(f"Traycer hardened ad-hoc flags mismatch: {relative}")
          entitlement_result = subprocess.run(
              [
                  "/usr/bin/codesign",
                  "-d",
                  "--entitlements",
                  "-",
                  "--xml",
                  str(candidate),
              ],
              check=False,
              stdout=subprocess.PIPE,
              stderr=subprocess.PIPE,
          )
          if entitlement_result.returncode != 0:
              raise SystemExit(
                  f"Traycer entitlements command failed: {relative}: "
                  f"{entitlement_result.stderr.decode(errors='replace')}"
              )
          entitlement_payload = entitlement_result.stdout
          if is_executable(candidate):
              try:
                  actual_entitlements = plistlib.loads(entitlement_payload)
              except Exception as error:
                  raise SystemExit(
                      f"Traycer entitlements could not be decoded: {relative}: {error}"
                  ) from error
              if actual_entitlements != required_entitlements:
                  raise SystemExit(f"Traycer entitlement mismatch: {relative}")
          elif entitlement_payload:
              raise SystemExit(
                  f"Traycer dylib unexpectedly carries entitlements: {relative}"
              )
      PY

      /usr/bin/codesign --verify --deep --strict "$app"
      ${lib.getExe python3} - "$out" <<'PY'
      import os
      import pathlib
      import sys

      output = pathlib.Path(sys.argv[1]).resolve(strict=True)
      for link in sorted(path for path in output.rglob("*") if path.is_symlink()):
          target = pathlib.Path(os.readlink(link))
          if target.is_absolute():
              raise SystemExit(
                  f"Traycer output contains an absolute symlink: {link} -> {target}"
              )
          try:
              resolved = link.resolve(strict=True)
          except FileNotFoundError as error:
              raise SystemExit(
                  f"Traycer output symlink does not resolve: {link} -> {target}"
              ) from error
          if not resolved.is_relative_to(output):
              raise SystemExit(
                  f"Traycer output symlink escapes the output: {link} -> {resolved}"
              )
      PY
      if find "$app" -path '*/Contents/Library/LaunchAgents/*' -print -quit | grep -q .; then
        echo "source-built Traycer unexpectedly bundled a mutable Host login item" >&2
        exit 1
      fi

      policyRoot="$TMPDIR/traycer-policy"
      mkdir -p \
        "$policyRoot/home/.traycer/host/install-staging" \
        "$policyRoot/home/.traycer/host/staged" \
        "$policyRoot/home/.traycer/host/download-cache" \
        "$policyRoot/home/.traycer/cli" \
        "$policyRoot/home/Library/LaunchAgents" \
        "$policyRoot/fake-bin" \
        "$policyRoot/results" \
        "$policyRoot/tmp" \
        "$policyRoot/xdg-config" \
        "$policyRoot/xdg-state" \
        "$policyRoot/xdg-cache"
      printf '%s\n' sentinel > "$policyRoot/home/.traycer/host/install-staging/.sentinel"
      printf '%s\n' sentinel > "$policyRoot/home/.traycer/host/staged/.sentinel"
      printf '%s\n' sentinel > "$policyRoot/home/.traycer/host/download-cache/.sentinel"
      printf '%s\n' sentinel > "$policyRoot/home/.traycer/cli/manifest.json"
      printf '%s\n' sentinel > "$policyRoot/home/.traycer/cli/post-finalize.json"
      : > "$policyRoot/home/.traycer/cli/cli.log"
      chmod 0600 "$policyRoot/home/.traycer/cli/cli.log"
      printf '%s\n' sentinel > "$policyRoot/home/Library/LaunchAgents/.sentinel"
      cat > "$policyRoot/fake-bin/launchctl" <<'SH'
      #!/bin/sh
      : > "$TRAYCER_LAUNCHCTL_LOG"
      exit 99
      SH
      chmod 0755 "$policyRoot/fake-bin/launchctl"

      export HOME="$policyRoot/home"
      export XDG_CONFIG_HOME="$policyRoot/xdg-config"
      export XDG_STATE_HOME="$policyRoot/xdg-state"
      export XDG_CACHE_HOME="$policyRoot/xdg-cache"
      export TMPDIR="$policyRoot/tmp"
      export TRAYCER_LAUNCHCTL_LOG="$policyRoot/launchctl.log"
      export CI=1
      export TRAYCER_NONINTERACTIVE=1
      export NO_COLOR=1
      export PATH="$policyRoot/fake-bin:$PATH"

      snapshotPolicySurfaces() {
        ${lib.getExe python3} - "$1" \
          "$HOME/.traycer/host/install-staging" \
          "$HOME/.traycer/host/staged" \
          "$HOME/.traycer/host/download-cache" \
          "$HOME/.traycer/cli" \
          "$HOME/Library/LaunchAgents" <<'PY'
      import hashlib
      import json
      import os
      import pathlib
      import stat
      import sys

      def describe(root):
          root = pathlib.Path(root)
          entries = []
          candidates = [root]
          if root.is_dir():
              candidates.extend(sorted(root.rglob("*")))
          for path in candidates:
              metadata = path.lstat()
              relative = "." if path == root else str(path.relative_to(root))
              if stat.S_ISLNK(metadata.st_mode):
                  payload = {"kind": "symlink", "target": os.readlink(path)}
              elif stat.S_ISREG(metadata.st_mode):
                  if path == root / "cli.log":
                      payload = {"kind": "mutable-log"}
                  else:
                      payload = {
                          "kind": "file",
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      }
              elif stat.S_ISDIR(metadata.st_mode):
                  payload = {"kind": "directory"}
              else:
                  payload = {"kind": "other"}
              entries.append({
                  "path": relative,
                  "mode": stat.S_IMODE(metadata.st_mode),
                  **payload,
              })
          return entries

      snapshot = {path: describe(path) for path in sys.argv[2:]}
      pathlib.Path(sys.argv[1]).write_text(
          json.dumps(snapshot, sort_keys=True, separators=(",", ":")) + "\n",
          encoding="utf-8",
      )
      PY
      }

      runManagedProbe() {
        label="$1"
        shift
        stdout="$policyRoot/results/$label.stdout"
        stderr="$policyRoot/results/$label.stderr"
        status=0
        "$cli" --json "$@" >"$stdout" 2>"$stderr" || status=$?
        if test "$status" -ne 1; then
          echo "Nix-managed Traycer CLI probe $label exited $status, expected 1" >&2
          exit 1
        fi
        ${lib.getExe python3} - "$stdout" <<'PY'
      import json
      import pathlib
      import sys

      events = [
          json.loads(line)
          for line in pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
          if line
      ]
      results = [event for event in events if event.get("type") == "result"]
      if len(results) != 1 or events[-1] is not results[0]:
          raise SystemExit("Traycer managed probe lacks one final result envelope")
      result = results[0]
      if set(result) != {"type", "status", "error", "timestamp"}:
          raise SystemExit(f"Traycer managed probe result shape drifted: {result!r}")
      expected_error = {
          "code": "E_INVALID_ARGUMENT",
          "message": (
              "This Traycer build is managed by Nix; change Desktop, CLI, "
              "and Host bytes through nixcfg."
          ),
          "details": {"packageManager": "nix"},
      }
      if result["status"] != "error" or result["error"] != expected_error:
          raise SystemExit(f"Traycer managed probe refusal drifted: {result!r}")
      PY
      }

      snapshotPolicySurfaces "$policyRoot/before.json"
      runManagedProbe host-install host install --from "$policyRoot/missing-host.tar.gz"
      runManagedProbe host-apply host apply
      runManagedProbe host-purge-stage host purge-stage --expected-stage-fingerprint synthetic-stage
      runManagedProbe host-stamp-runtime host stamp-runtime --expected-install-generation synthetic-install --observed-pid 1 --observed-started-at 1970-01-01T00:00:00.000Z --observed-runtime-version ${version}
      runManagedProbe host-update host update
      runManagedProbe host-download host download latest
      runManagedProbe host-uninstall host uninstall
      runManagedProbe service-install host service install
      runManagedProbe service-uninstall host service uninstall
      runManagedProbe cli-upgrade cli upgrade --dry-run --target ${version}
      runManagedProbe cli-mark-source cli mark-source --source desktop --binary-path "$cli" --installed-version ${version}
      runManagedProbe cli-finalize-upgrade cli finalize-upgrade
      runManagedProbe cli-re-anchor cli re-anchor --binary-path "$cli" --installed-version ${version}
      snapshotPolicySurfaces "$policyRoot/after.json"
      diff -u "$policyRoot/before.json" "$policyRoot/after.json"
      test ! -e "$TRAYCER_LAUNCHCTL_LOG"
      runHook postInstallCheck
    '';
    passthru = commonPassthru // {
      inherit
        bunDeps
        bunExact
        electronBuild
        hostRuntime
        srcWithBun
        ;
      # The system app route excludes the app-bearing package from Home
      # Manager. Keep the CLI colocated with the app resources while exposing
      # an app-free view for PATH and the declarative Host supervisor.
      cliPackage = runCommand "${pname}-cli-${version}" { } ''
        mkdir -p "$out/bin"
        ln -s \
          "${realPackage}/Applications/${appBundleName}/Contents/Resources/cli/darwin-arm64/traycer" \
          "$out/bin/${pname}"
      '';
    };
    meta = {
      description = "AI coding agent with source-built Desktop/CLI and a vendor-signed Host runtime";
      homepage = "https://github.com/traycerai/traycer";
      license = [
        lib.licenses.mit
        lib.licenses.unfree
      ];
      mainProgram = pname;
      platforms = [ "aarch64-darwin" ];
    };
  };
in
if unresolvedBuildGates == [ ] then realPackage else blockedPackage
