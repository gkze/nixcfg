{
  cctools,
  desktopUnsigned,
  lib,
  meshRuntimeBundle,
  patchedBuzzSource,
  python3,
  stdenv,
  version,
}:
assert stdenv.hostPlatform.system == "aarch64-darwin";
let
  expectedDesktopContract = {
    kind = "buzz-desktop-unsigned";
    commit = "95154bee4034ca7a40b33095c2ddbde8c9aa1614";
    version = "0.5.20";
    target = "aarch64-apple-darwin";
    rustVersion = "1.95.0";
    pnpmVersion = "11.4.0";
    cargoRoot = "desktop/src-tauri";
    buildAndTestSubdir = "desktop";
    cargoOffline = true;
    cargoFrozen = true;
    frontendBuildCommand = "pnpm build";
    cargoFeatures = [ "mesh-llm" ];
    sidecars = [
      "buzz-acp-aarch64-apple-darwin"
      "buzz-agent-aarch64-apple-darwin"
      "buzz-backend-kubernetes-aarch64-apple-darwin"
      "buzz-dev-mcp-aarch64-apple-darwin"
      "git-credential-nostr-aarch64-apple-darwin"
      "buzz-aarch64-apple-darwin"
    ];
    updaterEnabled = false;
    sherpaOnnxVersion = "1.13.4";
    minimumMacosVersion = "14.0";
    appSigned = false;
    runtimeBundleEmbedded = false;
  };
  expectedRuntimeContract = {
    kind = "mesh-native-runtime-bundle";
    meshVersion = "0.75.1";
    skippyAbi = "0.1.35";
    target = "aarch64-apple-darwin";
    platform = {
      os = "macos";
      arch = "aarch64";
    };
    backend = "metal";
    sourceInputs = [
      "meshLlm"
      "llamaCpp"
    ];
    manifestHasFileDigests = true;
    releaseArchiveAllowed = false;
  };
  expectedSourceContract = {
    kind = "buzz-runtime-policy-source";
    commit = "95154bee4034ca7a40b33095c2ddbde8c9aa1614";
    meshFeature = "dynamic-native-runtime";
    runtimeBundleEnvironment = "MESH_LLM_NATIVE_RUNTIME_BUNDLE_DIR";
    runtimeCacheEnvironment = "MESH_LLM_NATIVE_RUNTIME_CACHE_DIR";
    manifestUrlEnvironment = "MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL";
    requiresBothRuntimeEnvironmentValues = true;
    manifestUrlEnvironmentAllowed = false;
    allowDefaultManifestUrl = false;
    allowDownload = false;
    keyringProbePolicy = {
      preUiInteractionAllowed = false;
      interactionGuard = "security-framework-raii";
      interactionGuardScope = "tauri-setup";
      guardFailure = "keyring-locked";
      unexpectedReadFailure = "unreachable";
      managedAgentSecretLoadsAllowedInRecovery = false;
      identityResolutionUsesReadonlyLoad = true;
      identityResolutionLegacyMigrationAllowed = false;
      postUiInteractionAllowed = true;
      postUiRetryCommand = "retry_keyring_identity";
      postUiRetryUsesExistingIdentity = true;
      postUiRetryMutationAllowed = false;
      postUiRetrySerializedBy = "identity_mutation";
      postUiRetryRequiresRelaunch = true;
    };
    sherpaOnnxTtsEnabled = false;
    sherpaOnnxStaticLinkLibraries = [
      "sherpa-onnx-c-api"
      "sherpa-onnx-core"
      "kaldi-decoder-core"
      "sherpa-onnx-kaldifst-core"
      "sherpa-onnx-fstfar"
      "sherpa-onnx-fst"
      "kaldi-native-fbank-core"
      "kissfft-float"
      "onnxruntime"
      "ssentencepiece_core"
    ];
    updaterRequiresBothEnvironmentValues = true;
  };
  implementedContract = {
    kind = "buzz-desktop-candidate";
    commit = "95154bee4034ca7a40b33095c2ddbde8c9aa1614";
    version = "0.5.20";
    target = "aarch64-apple-darwin";
    minimumMacosVersion = "14.0";
    app = {
      bundleName = "Buzz.app";
      identifier = "xyz.block.buzz.app";
      launcherExecutable = "buzz-desktop";
      payloadExecutable = "buzz-desktop.real";
      sidecars = [
        "buzz-acp"
        "buzz-agent"
        "buzz-backend-kubernetes"
        "buzz-dev-mcp"
        "git-credential-nostr"
        "buzz"
      ];
    };
    launcher = {
      language = "c11";
      source = "buzz-launcher.c";
      handoff = "execv";
      runtimeBundleSubpath = "Contents/Resources/mesh-runtime";
      runtimeCacheSubpath = "Library/Caches/xyz.block.buzz.app/mesh-llm/native-runtimes";
      runtimeBundleEnvironment = "MESH_LLM_NATIVE_RUNTIME_BUNDLE_DIR";
      runtimeCacheEnvironment = "MESH_LLM_NATIVE_RUNTIME_CACHE_DIR";
      manifestUrlEnvironment = "MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL";
      manifestUrlUnset = true;
      createsCacheDirectory = false;
    };
    signing = {
      identity = "adhoc";
      deepSign = false;
      runtimeResigned = false;
      entitlementsSource = "patched-buzz-source";
    };
    appSigned = true;
    runtimeBundleEmbedded = true;
    exportReady = true;
  };
  runtimeValidator = ''
    import hashlib
    import json
    import re
    import sys
    from pathlib import Path, PurePosixPath

    DIGEST = re.compile(r"[0-9a-f]{64}")


    def fail(message):
        raise SystemExit(f"runtime validation failed: {message}")


    root = Path(sys.argv[1])
    if root.is_symlink() or not root.is_dir():
        fail(f"runtime root is not a regular directory: {root}")
    root = root.resolve(strict=True)
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        fail("manifest.json is not a regular file")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail(f"invalid manifest.json: {error}")
    if not isinstance(manifest, dict) or set(manifest) != {"runtime"}:
        fail("manifest top-level schema differs from the reviewed runtime")
    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict):
        fail("manifest has no runtime object")
    if set(runtime) != {
        "backend",
        "files",
        "id",
        "libraries",
        "mesh_version",
        "platform",
        "rank",
        "skippy_abi",
    }:
        fail("runtime schema differs from the reviewed runtime")
    if runtime.get("id") != "meshllm-native-runtime-darwin-aarch64-metal":
        fail("runtime.id differs from the reviewed runtime")
    if runtime.get("mesh_version") != "0.75.1":
        fail("runtime.mesh_version differs from the reviewed Mesh version")
    if runtime.get("skippy_abi") != "0.1.35":
        fail("runtime.skippy_abi differs from the reviewed ABI")
    if runtime.get("platform") != {
        "os": "macos",
        "arch": "aarch64",
        "target": "aarch64-apple-darwin",
    }:
        fail("runtime.platform differs from the reviewed target")
    if runtime.get("backend") != {"kind": "metal"}:
        fail("runtime.backend differs from the reviewed Metal backend")
    if type(runtime.get("rank")) is not int or runtime.get("rank") != 0:
        fail("runtime.rank differs from the reviewed runtime")
    files = runtime.get("files")
    if not isinstance(files, dict) or not files:
        fail("manifest runtime.files is not a nonempty object")
    libraries = runtime.get("libraries")
    if not isinstance(libraries, list) or not libraries or not all(
        isinstance(library, str) and library for library in libraries
    ):
        fail("runtime.libraries is not a nonempty string list")
    if len(set(libraries)) != len(libraries):
        fail("runtime.libraries contains duplicates")

    expected = set()
    for relative, expected_digest in files.items():
        if not isinstance(relative, str) or not relative:
            fail("manifest contains an invalid file path")
        normalized = PurePosixPath(relative)
        if normalized.is_absolute() or normalized.as_posix() != relative:
            fail(f"manifest file path is not normalized: {relative}")
        if ".." in normalized.parts or relative == "manifest.json":
            fail(f"manifest file path is unsafe: {relative}")
        if not isinstance(expected_digest, str) or DIGEST.fullmatch(expected_digest) is None:
            fail(f"manifest digest is invalid: {relative}")
        candidate = root.joinpath(*normalized.parts)
        if not candidate.is_file():
            fail(f"runtime file is missing: {relative}")
        try:
            candidate.resolve(strict=True).relative_to(root)
        except (OSError, ValueError):
            fail(f"runtime file escapes the bundle: {relative}")
        actual_digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if actual_digest != expected_digest:
            fail(f"runtime digest mismatch: {relative}")
        expected.add(relative)

    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if actual != expected:
        fail(
            "runtime file inventory differs from manifest: "
            f"missing={sorted(expected - actual)!r}, extra={sorted(actual - expected)!r}"
        )
    if any(library not in expected for library in libraries):
        fail("runtime libraries are not all covered by runtime.files")
  '';
  runtimeValidationCommand = "\"$PYTHON_TOOL\" -c ${lib.escapeShellArg runtimeValidator}";
  runtimeLoadValidator = ''
    import ctypes
    import json
    import sys
    from pathlib import Path, PurePosixPath

    EXPECTED_ABI = (0, 1, 35)


    class AbiVersion(ctypes.Structure):
        _fields_ = [
            ("major", ctypes.c_uint32),
            ("minor", ctypes.c_uint32),
            ("patch", ctypes.c_uint32),
        ]


    def fail(message):
        raise SystemExit(f"runtime load validation failed: {message}")


    root = Path(sys.argv[1])
    if root.is_symlink() or not root.is_dir():
        fail(f"runtime root is not a regular directory: {root}")
    root = root.resolve(strict=True)
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        fail("manifest.json is not a regular file")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail(f"invalid manifest.json: {error}")
    runtime = manifest.get("runtime") if isinstance(manifest, dict) else None
    libraries = runtime.get("libraries") if isinstance(runtime, dict) else None
    if not isinstance(libraries, list) or not libraries or not all(
        isinstance(library, str) and library for library in libraries
    ):
        fail("runtime.libraries is not a nonempty string list")

    handles = []
    for relative in libraries:
        normalized = PurePosixPath(relative)
        if normalized.is_absolute() or normalized.as_posix() != relative:
            fail(f"runtime library path is not normalized: {relative}")
        if ".." in normalized.parts:
            fail(f"runtime library path is unsafe: {relative}")
        candidate = root.joinpath(*normalized.parts)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError):
            fail(f"runtime library escapes the bundle: {relative}")
        if not resolved.is_file():
            fail(f"runtime library is not a file: {relative}")
        try:
            handles.append(ctypes.CDLL(str(resolved), mode=ctypes.RTLD_GLOBAL))
        except OSError as error:
            fail(f"could not load {relative}: {error}")

    abi_function = None
    for handle in reversed(handles):
        try:
            abi_function = handle.skippy_abi_version
        except AttributeError:
            continue
        break
    if abi_function is None:
        fail("native runtime symbol not found: skippy_abi_version")
    abi_function.restype = AbiVersion
    version = abi_function()
    actual_abi = (version.major, version.minor, version.patch)
    if actual_abi != EXPECTED_ABI:
        fail(
            "Skippy ABI differs from 0.1.35: "
            f"{actual_abi[0]}.{actual_abi[1]}.{actual_abi[2]}"
        )
  '';
  runtimeLoadValidationCommand = "\"$PYTHON_TOOL\" -c ${lib.escapeShellArg runtimeLoadValidator}";
  entitlementsValidator = ''
    import plistlib
    import sys

    expected = {
        "com.apple.security.cs.disable-library-validation": True,
        "com.apple.security.device.audio-input": True,
        "com.apple.security.device.camera": True,
    }
    try:
        with open(sys.argv[1], "rb") as plist_file:
            entitlements = plistlib.load(plist_file)
    except (OSError, plistlib.InvalidFileException) as error:
        raise SystemExit(
            f"{sys.argv[2]} entitlement plist is invalid: {error}"
        ) from error
    if entitlements != expected:
        raise SystemExit(f"{sys.argv[2]} entitlement contract differs")
  '';
  entitlementsValidationCommand = "\"$PYTHON_TOOL\" -c ${lib.escapeShellArg entitlementsValidator}";
  rpathValidator = ''
    import re
    import sys
    from pathlib import Path, PurePosixPath

    MAXIMUM_MACOS_VERSION = (14, 0, 0)


    def fail(message):
        raise SystemExit(f"Buzz candidate {message}")


    def read_lines(path, label):
        try:
            return Path(path).read_text(
                encoding="utf-8",
                errors="strict",
            ).splitlines()
        except (OSError, UnicodeError) as error:
            fail(f"cannot read {label}: {error}")


    def command_blocks(lines):
        blocks = []
        current = []
        for line in lines:
            if re.fullmatch(r"Load command [0-9]+", line):
                if current:
                    blocks.append(current)
                current = [line]
            elif current:
                current.append(line)
        if current:
            blocks.append(current)
        return blocks


    def command_name(block):
        names = [line.strip()[4:] for line in block if line.strip().startswith("cmd ")]
        return names[0] if len(names) == 1 else None


    def command_value(block, name):
        values = []
        for line in block:
            match = re.fullmatch(rf"\s*{name} (\S+)(?: \(offset [0-9]+\))?", line)
            if match is not None:
                values.append(match.group(1))
        return values[0] if len(values) == 1 else None


    def version_tuple(value):
        if re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,2}", value) is None:
            return None
        pieces = [int(piece) for piece in value.split(".")]
        return tuple((pieces + [0, 0])[:3])


    def app_local(path, edge, label, *, require_directory=False):
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(app)
        except (OSError, ValueError):
            fail(f"{label} escapes Buzz.app: {executable} -> {edge}")
        if require_directory:
            if not resolved.is_dir():
                fail(f"{label} is not an app-local directory: {executable} -> {edge}")
        elif not resolved.is_file():
            fail(f"{label} is not an app-local file: {executable} -> {edge}")
        return resolved


    def origin_path(value, label, *, require_directory=False):
        if value == "@loader_path" or value.startswith("@loader_path/"):
            origin = executable.parent
            suffix = value.removeprefix("@loader_path").removeprefix("/")
        elif value == "@executable_path" or value.startswith("@executable_path/"):
            origin = executable.parent
            suffix = value.removeprefix("@executable_path").removeprefix("/")
        else:
            return None
        relative = PurePosixPath(suffix)
        return app_local(
            origin.joinpath(*relative.parts),
            value,
            label,
            require_directory=require_directory,
        )


    app = Path(sys.argv[1]).resolve(strict=True)
    executable = Path(sys.argv[2]).resolve(strict=True)
    try:
        executable.relative_to(app)
    except ValueError:
        fail(f"executable escapes Buzz.app: {executable}")
    dependency_lines = read_lines(sys.argv[3], "otool -L output")
    load_commands = read_lines(sys.argv[4], "otool -l output")
    blocks = command_blocks(load_commands)

    minimum_versions = []
    rpaths = []
    for block in blocks:
        name = command_name(block)
        if name == "LC_BUILD_VERSION":
            if command_value(block, "platform") != "1":
                fail(f"is not a macOS executable: {executable}")
            value = command_value(block, "minos")
            if value is not None:
                minimum_versions.append(value)
            continue
        elif name == "LC_VERSION_MIN_MACOSX":
            value = command_value(block, "version")
            if value is not None:
                minimum_versions.append(value)
            continue
        if name != "LC_RPATH":
            continue
        value = command_value(block, "path")
        if value is None:
            fail(f"has a malformed LC_RPATH: {executable}")
        resolved = origin_path(value, "LC_RPATH", require_directory=True)
        if resolved is None:
            fail(f"has a forbidden LC_RPATH: {executable} -> {value}")
        rpaths.append(resolved)

    if len(minimum_versions) != 1:
        fail(f"has no unique macOS deployment target: {executable}")
    minimum_version = version_tuple(minimum_versions[0])
    if minimum_version is None:
        fail(f"has no unique macOS deployment target: {executable}")
    if minimum_version > MAXIMUM_MACOS_VERSION:
        fail(
            "requires macOS newer than 14.0: "
            f"{executable} -> {minimum_versions[0]}"
        )

    dependencies = []
    for line in dependency_lines[1:]:
        match = re.fullmatch(r"\s*(.+?) \([^)]*\)", line)
        if match is None:
            fail(f"could not parse otool -L output: {executable}")
        dependencies.append(match.group(1))

    for dependency in dependencies:
        normalized = PurePosixPath(dependency)
        if dependency.startswith(("/usr/lib/", "/System/Library/")):
            if normalized.as_posix() != dependency or ".." in normalized.parts:
                fail(f"has a forbidden dynamic-library edge: {executable} -> {dependency}")
            continue
        resolved = origin_path(dependency, "dynamic-library edge")
        if resolved is not None:
            continue
        if dependency == "@rpath" or dependency.startswith("@rpath/"):
            suffix = PurePosixPath(dependency.removeprefix("@rpath").removeprefix("/"))
            escaped = False
            for rpath in rpaths:
                try:
                    candidate = rpath.joinpath(*suffix.parts).resolve(strict=True)
                    candidate.relative_to(app)
                except ValueError:
                    escaped = True
                    continue
                except OSError:
                    continue
                if candidate.is_file():
                    break
            else:
                if escaped:
                    fail(f"dynamic-library edge escapes Buzz.app: {executable} -> {dependency}")
                fail(f"has an unresolved @rpath dynamic-library edge: {executable} -> {dependency}")
            continue
        fail(f"has a forbidden dynamic-library edge: {executable} -> {dependency}")
  '';
  rpathValidationCommand = "\"$PYTHON_TOOL\" -c ${lib.escapeShellArg rpathValidator}";
  assemblyScript = ''
    set -o pipefail

    sourceApp="$DESKTOP_UNSIGNED/Applications/Buzz.app"
    app="$out/Applications/Buzz.app"
    macos="$app/Contents/MacOS"
    launcher="$macos/buzz-desktop"
    payload="$macos/buzz-desktop.real"
    infoPlist="$app/Contents/Info.plist"
    runtimeSource="$MESH_RUNTIME_BUNDLE"
    runtimeDestination="$app/Contents/Resources/mesh-runtime"
    entitlements="$PATCHED_BUZZ_SOURCE/desktop/src-tauri/Entitlements.plist"

    if [ ! -d "$sourceApp" ] || [ -L "$sourceApp" ]; then
      echo "Buzz unsigned desktop app is not a regular directory" >&2
      exit 1
    fi
    if [ ! -d "$runtimeSource" ] || [ -L "$runtimeSource" ]; then
      echo "Buzz Mesh runtime source is not a regular directory" >&2
      exit 1
    fi
    if [ ! -f "$runtimeSource/manifest.json" ] || [ -L "$runtimeSource/manifest.json" ]; then
      echo "Buzz Mesh runtime source has no regular manifest" >&2
      exit 1
    fi
    if [ ! -f "$entitlements" ] || [ -L "$entitlements" ]; then
      echo "Buzz patched source has no regular entitlements file" >&2
      exit 1
    fi
    ${entitlementsValidationCommand} "$entitlements" source
    if [ ! -x "$BUZZ_LAUNCHER" ] || [ -L "$BUZZ_LAUNCHER" ]; then
      echo "Buzz launcher is not a regular executable" >&2
      exit 1
    fi

    mkdir -p "$out/Applications"
    cp -R "$sourceApp" "$app"
    find "$app" -type d -exec chmod u+w {} +
    find "$app" -type f -exec chmod u+w {} +
    "$XATTR_TOOL" -cr "$app"

    if [ ! -f "$infoPlist" ] || [ -L "$infoPlist" ]; then
      echo "Buzz unsigned app has no regular Info.plist" >&2
      exit 1
    fi
    if ! "$PLISTBUDDY_TOOL" -c 'Set :LSMinimumSystemVersion 14.0' "$infoPlist"; then
      echo "Buzz candidate could not set its minimum macOS version" >&2
      exit 1
    fi

    if [ ! -x "$launcher" ] || [ -L "$launcher" ]; then
      echo "Buzz unsigned app has no regular main executable" >&2
      exit 1
    fi
    if [ -e "$payload" ] || [ -L "$payload" ]; then
      echo "Buzz payload destination already exists" >&2
      exit 1
    fi
    mv "$launcher" "$payload"
    install -m0755 "$BUZZ_LAUNCHER" "$launcher"

    if ! payloadDependencyListing="$("$OTOOL_TOOL" -L "$payload")"; then
      echo "Buzz candidate could not inspect payload dependencies" >&2
      exit 1
    fi
    iconvDependency=""
    while IFS= read -r dependencyLine; do
      dependency="$(printf '%s\n' "$dependencyLine" | LC_ALL=C awk '{ print $1 }')"
      case "$dependency" in
        /nix/store/*-libiconv-*/lib/libiconv.2.dylib)
          if [ -n "$iconvDependency" ]; then
            echo "Buzz payload has multiple Nix libiconv edges" >&2
            exit 1
          fi
          case "$dependencyLine" in
            *' (compatibility version 7.0.0, '*) ;;
            *)
              echo "Buzz payload libiconv ABI differs from macOS" >&2
              exit 1
              ;;
          esac
          iconvDependency="$dependency"
          ;;
      esac
    done < <(printf '%s\n' "$payloadDependencyListing" | LC_ALL=C awk 'NR > 1')
    if [ -z "$iconvDependency" ]; then
      echo "Buzz payload has no relocatable Nix libiconv edge" >&2
      exit 1
    fi
    "$INSTALL_NAME_TOOL" \
      -change "$iconvDependency" /usr/lib/libiconv.2.dylib "$payload"

    if [ -e "$runtimeDestination" ] || [ -L "$runtimeDestination" ]; then
      echo "Buzz runtime destination already exists" >&2
      exit 1
    fi
    mkdir -p "$runtimeDestination"
    cp -R "$runtimeSource/." "$runtimeDestination/"
    ${runtimeValidationCommand} "$runtimeDestination"

    expectedInventory="$TMPDIR/buzz-candidate-macos.expected"
    actualInventory="$TMPDIR/buzz-candidate-macos.actual"
    unsupportedInventory="$TMPDIR/buzz-candidate-macos.unsupported"
    printf '%s\n' \
      buzz \
      buzz-acp \
      buzz-agent \
      buzz-backend-kubernetes \
      buzz-desktop \
      buzz-desktop.real \
      buzz-dev-mcp \
      git-credential-nostr > "$expectedInventory"
    if ! find "$macos" -mindepth 1 -maxdepth 1 ! -type f \
      -print > "$unsupportedInventory"
    then
      echo "Buzz candidate failed to inspect unsupported MacOS entries" >&2
      exit 1
    fi
    if [ -s "$unsupportedInventory" ]; then
      echo "Buzz candidate contains a non-file MacOS entry" >&2
      exit 1
    fi
    if ! find "$macos" -mindepth 1 -maxdepth 1 -type f \
      -exec basename {} \; | LC_ALL=C sort > "$actualInventory"
    then
      echo "Buzz candidate failed to enumerate MacOS inventory" >&2
      exit 1
    fi
    if ! cmp -s "$expectedInventory" "$actualInventory"; then
      echo "Buzz candidate MacOS inventory is not exact" >&2
      exit 1
    fi

    "$CODESIGN_TOOL" --force --sign - --timestamp=none \
      --entitlements "$entitlements" "$payload"
    "$CODESIGN_TOOL" --force --sign - --timestamp=none "$macos/buzz-acp"
    "$CODESIGN_TOOL" --force --sign - --timestamp=none "$macos/buzz-agent"
    "$CODESIGN_TOOL" --force --sign - --timestamp=none \
      "$macos/buzz-backend-kubernetes"
    "$CODESIGN_TOOL" --force --sign - --timestamp=none "$macos/buzz-dev-mcp"
    "$CODESIGN_TOOL" --force --sign - --timestamp=none \
      "$macos/git-credential-nostr"
    "$CODESIGN_TOOL" --force --sign - --timestamp=none "$macos/buzz"
    "$CODESIGN_TOOL" --force --sign - --timestamp=none \
      --entitlements "$entitlements" "$launcher"
    "$CODESIGN_TOOL" --force --sign - --timestamp=none \
      --entitlements "$entitlements" "$app"

    ${runtimeValidationCommand} "$runtimeDestination"
  '';
  buildPhase = ''
    runHook preBuild
    "$CC" \
      -std=c11 \
      -Wall \
      -Wextra \
      -Werror \
      -Os \
      -mmacosx-version-min=14.0 \
      ${./buzz-launcher.c} \
      -o buzz-launcher
    runHook postBuild
  '';
  installPhase = ''
    runHook preInstall
    export BUZZ_LAUNCHER="$PWD/buzz-launcher"
    export CODESIGN_TOOL=/usr/bin/codesign
    export DESKTOP_UNSIGNED=${desktopUnsigned}
    export INSTALL_NAME_TOOL=${cctools}/bin/install_name_tool
    export MESH_RUNTIME_BUNDLE=${meshRuntimeBundle}
    export OTOOL_TOOL=${cctools}/bin/otool
    export PATCHED_BUZZ_SOURCE=${patchedBuzzSource}
    export PLISTBUDDY_TOOL=/usr/libexec/PlistBuddy
    export PYTHON_TOOL=${python3}/bin/python3
    export XATTR_TOOL=/usr/bin/xattr
    ${assemblyScript}
    runHook postInstall
  '';
  installCheckPhase = ''
    runHook preInstallCheck
    set -o pipefail

    app="$out/Applications/Buzz.app"
    macos="$app/Contents/MacOS"
    launcher="$macos/buzz-desktop"
    payload="$macos/buzz-desktop.real"
    runtime="$app/Contents/Resources/mesh-runtime"
    infoPlist="$app/Contents/Info.plist"
    export PYTHON_TOOL=${python3}/bin/python3

    test -d "$app"
    test -x "$launcher"
    test -x "$payload"
    test ! -e "$app/Contents/embedded.provisionprofile"
    ${runtimeValidationCommand} "$runtime"

    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$infoPlist")" = \
      buzz-desktop
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$infoPlist")" = \
      xyz.block.buzz.app
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleName' "$infoPlist")" = \
      Buzz
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$infoPlist")" = \
      0.5.20
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$infoPlist")" = \
      0.5.20
    test "$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$infoPlist")" = \
      14.0

    expectedInventory="$TMPDIR/buzz-candidate-install-check.expected"
    actualInventory="$TMPDIR/buzz-candidate-install-check.actual"
    unsupportedInventory="$TMPDIR/buzz-candidate-install-check.unsupported"
    printf '%s\n' \
      buzz \
      buzz-acp \
      buzz-agent \
      buzz-backend-kubernetes \
      buzz-desktop \
      buzz-desktop.real \
      buzz-dev-mcp \
      git-credential-nostr > "$expectedInventory"
    if ! find "$macos" -mindepth 1 -maxdepth 1 ! -type f \
      -print > "$unsupportedInventory"
    then
      echo "Buzz candidate failed to inspect unsupported MacOS entries" >&2
      exit 1
    fi
    if [ -s "$unsupportedInventory" ]; then
      echo "Buzz candidate contains a non-file MacOS entry" >&2
      exit 1
    fi
    if ! find "$macos" -mindepth 1 -maxdepth 1 -type f \
      -exec basename {} \; | LC_ALL=C sort > "$actualInventory"
    then
      echo "Buzz candidate failed to enumerate MacOS inventory" >&2
      exit 1
    fi
    cmp -s "$expectedInventory" "$actualInventory"

    for executable in \
      "$launcher" \
      "$payload" \
      "$macos/buzz-acp" \
      "$macos/buzz-agent" \
      "$macos/buzz-backend-kubernetes" \
      "$macos/buzz-dev-mcp" \
      "$macos/git-credential-nostr" \
      "$macos/buzz"
    do
      /usr/bin/file "$executable" | grep -F 'Mach-O 64-bit executable arm64'
      if ! architectures="$(${cctools}/bin/lipo -archs "$executable")"; then
        echo "Buzz candidate lipo failed: $executable" >&2
        exit 1
      fi
      if [ "$architectures" != arm64 ]; then
        echo "Buzz candidate architectures differ from arm64: $executable -> $architectures" >&2
        exit 1
      fi
      dependencyOutput="$TMPDIR/$(basename "$executable").dependencies"
      if ! ${cctools}/bin/otool -L "$executable" > "$dependencyOutput"; then
        echo "Buzz candidate otool -L failed: $executable" >&2
        exit 1
      fi
      loadCommands="$TMPDIR/$(basename "$executable").load-commands"
      if ! ${cctools}/bin/otool -l "$executable" > "$loadCommands"; then
        echo "Buzz candidate otool -l failed: $executable" >&2
        exit 1
      fi
      ${rpathValidationCommand} \
        "$app" "$executable" "$dependencyOutput" "$loadCommands"
      /usr/bin/codesign --verify --strict "$executable"
      signatureDetails="$(/usr/bin/codesign -dv --verbose=4 "$executable" 2>&1)"
      printf '%s\n' "$signatureDetails" | grep -Fx 'Signature=adhoc'
    done

    runtimeLibraryInventory="$TMPDIR/buzz-candidate-runtime-dylibs"
    if ! find "$runtime/lib" -type f -name '*.dylib' | \
      LC_ALL=C sort > "$runtimeLibraryInventory"
    then
      echo "Buzz candidate failed to enumerate runtime dylibs" >&2
      exit 1
    fi
    if [ ! -s "$runtimeLibraryInventory" ]; then
      echo "Buzz candidate runtime contains no dylibs" >&2
      exit 1
    fi
    while IFS= read -r runtimeLibrary; do
      /usr/bin/codesign --verify --strict "$runtimeLibrary"
    done < "$runtimeLibraryInventory"
    ${runtimeLoadValidationCommand} "$runtime"

    for entitledExecutable in "$launcher" "$payload" "$app"; do
      entitlementDump="$TMPDIR/$(basename "$entitledExecutable").entitlements.plist"
      /usr/bin/codesign -d --entitlements - --xml \
        "$entitledExecutable" > "$entitlementDump" 2>/dev/null
      ${entitlementsValidationCommand} "$entitlementDump" final
    done

    /usr/bin/codesign --verify --deep --strict --verbose=2 "$app"
    runHook postInstallCheck
  '';
in
assert version == "0.5.20";
assert (desktopUnsigned.passthru.buzzNativeContract or null) == expectedDesktopContract;
assert (meshRuntimeBundle.passthru.buzzNativeContract or null) == expectedRuntimeContract;
assert (meshRuntimeBundle.passthru.manifestSubpath or null) == "manifest.json";
assert
  (meshRuntimeBundle.passthru.runtimeId or null) == "meshllm-native-runtime-darwin-aarch64-metal";
assert (patchedBuzzSource.passthru.buzzNativeContract or null) == expectedSourceContract;
stdenv.mkDerivation {
  pname = "buzz-desktop-candidate";
  inherit version;
  __structuredAttrs = true;
  strictDeps = true;
  dontUnpack = true;
  dontConfigure = true;
  dontFixup = true;

  nativeBuildInputs = [
    cctools
    python3
  ];

  env.MACOSX_DEPLOYMENT_TARGET = "14.0";
  inherit
    buildPhase
    installCheckPhase
    installPhase
    ;
  doInstallCheck = true;
  outputChecks.out.allowedReferences = [ ];

  passthru = {
    buzzNativeContract = implementedContract;
    macApp = {
      bundleId = "xyz.block.buzz.app";
      bundleName = "Buzz.app";
      bundleRelPath = "Applications/Buzz.app";
      installMode = "copy";
    };
  };

  meta = {
    description = "Source-built Buzz desktop app with an embedded offline Mesh runtime";
    homepage = "https://github.com/block/buzz";
    license = lib.licenses.asl20;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = [ lib.sourceTypes.fromSource ];
  };
}
