{
  autoconf,
  automake,
  cctools,
  coreutils,
  dpkg,
  fetchPnpmDeps ? null,
  fetchurl,
  git,
  inputs,
  lib,
  libiconv,
  libsecret,
  libutempter,
  nodejs_22,
  openssl,
  outputs,
  patchelf,
  perl,
  pkg-config,
  pnpmConfigHook,
  pnpm_10,
  python3,
  rpm,
  runCommand,
  sqlite,
  stdenv,
  zlib,
  ...
}:
let
  pname = "emdash";
  slib = outputs.lib;
  version = slib.getFlakeVersion pname;
  src = inputs.emdash;
  nodejs = nodejs_22;
  pnpm = pnpm_10.override { inherit nodejs; };
  npmDepsHash =
    let
      perPlatformHash = builtins.tryEval (slib.sourceHashForPlatform pname "npmDepsHash" system);
    in
    if perPlatformHash.success then perPlatformHash.value else slib.sourceHash pname "npmDepsHash";

  electronVersion = "30.5.1";
  electronTargets = {
    aarch64-darwin = "darwin-arm64";
    aarch64-linux = "linux-arm64";
    x86_64-linux = "linux-x64";
  };
  electronZipHashes = {
    aarch64-darwin = "sha256-0xJUTqKYRM8yi0S5294S9P3O2Qy0Qt/KbfNsCY27bno=";
    aarch64-linux = "sha256-6zFHDA181uI+fODYnMk6I1bJ2si8yZfjNTU7iqmVr6A=";
    x86_64-linux = "sha256-7EcHeD056GAF9CiZ4wrlnlDdXZx/KFMe1JTrQ/I2FAM=";
  };
  supportedSystems = builtins.attrNames electronTargets;
  inherit (stdenv.hostPlatform) system;
  electronTarget = electronTargets.${system} or (throw "Unsupported system ${system} for ${pname}");

  electronZip = fetchurl {
    url = "https://github.com/electron/electron/releases/download/v${electronVersion}/electron-v${electronVersion}-${electronTarget}.zip";
    hash = electronZipHashes.${system};
  };

  electronHeadersTarball = fetchurl {
    url = "https://www.electronjs.org/headers/v${electronVersion}/node-v${electronVersion}-headers.tar.gz";
    hash = "sha256-Q+c8G4nIRoJL/0uAYVYY2hrnFgvmkKB6RC3nxJtFYzU=";
  };

  electronDistDir = runCommand "${pname}-electron-dist-${electronTarget}" { } ''
    mkdir -p "$out"
    cp ${electronZip} "$out/electron-v${electronVersion}-${electronTarget}.zip"
  '';

  pnpmDeps =
    if fetchPnpmDeps != null then
      fetchPnpmDeps {
        inherit
          pname
          version
          src
          pnpm
          ;
        fetcherVersion = 1;
        hash = npmDepsHash;
      }
    else
      pnpm.fetchDeps {
        inherit
          pname
          version
          src
          ;
        fetcherVersion = 1;
        hash = npmDepsHash;
      };
in
stdenv.mkDerivation {
  inherit
    pname
    version
    src
    pnpmDeps
    ;

  nativeBuildInputs = [
    autoconf
    automake
    coreutils
    git
    nodejs
    perl
    pkg-config
    pnpm
    pnpmConfigHook
    python3
  ]
  ++ lib.optionals stdenv.hostPlatform.isDarwin [
    cctools
    libiconv
  ]
  ++ lib.optionals stdenv.hostPlatform.isLinux [
    dpkg
    patchelf
    rpm
  ];

  buildInputs = [
    openssl
  ]
  ++ lib.optionals stdenv.hostPlatform.isLinux [
    libsecret
    libutempter
    sqlite
    zlib
  ];

  strictDeps = true;

  env = {
    CI = "1";
    EMDASH_NIXCFG_BUILD_REV = "1";
    ELECTRON_SKIP_BINARY_DOWNLOAD = "1";
    npm_config_build_from_source = "true";
    npm_config_manage_package_manager_versions = "false";
    npm_config_node_linker = "hoisted";
  };

  postPatch = ''
    substituteInPlace src/main/main.ts \
      --replace-fail " -ilc " " -lc "

    substituteInPlace src/main/utils/shellEnv.ts \
      --replace-fail " -ilc " " -lc "
  '';

  buildPhase = ''
        runHook preBuild

        export HOME="$TMPDIR/emdash-home"
        mkdir -p "$HOME"
        pnpm config set manage-package-manager-versions false

        # Pre-seed Electron headers to keep native rebuilds offline/reproducible.
        electron_gyp_dir="$HOME/.electron-gyp/${electronVersion}"
        mkdir -p "$electron_gyp_dir"
        tar -xzf ${electronHeadersTarball} --strip-components=1 -C "$electron_gyp_dir"

        export npm_config_runtime=electron
        export npm_config_target=${electronVersion}
        export npm_config_nodedir="$electron_gyp_dir"

        # Work around keytar's bundled node-addon-api constant-expression issue
        # with the Apple toolchain in this build environment.
        if [ -f node_modules/node-addon-api/napi.h ]; then
          perl -0pi -e 's/static const napi_typedarray_type unknown_array_type = static_cast<napi_typedarray_type>\(-1\);/static const napi_typedarray_type unknown_array_type = static_cast<napi_typedarray_type>(0);/g' \
            node_modules/node-addon-api/napi.h
        fi
        if [ -f node_modules/keytar/node_modules/node-addon-api/napi.h ]; then
          perl -0pi -e 's/static const napi_typedarray_type unknown_array_type = static_cast<napi_typedarray_type>\(-1\);/static const napi_typedarray_type unknown_array_type = static_cast<napi_typedarray_type>(0);/g' \
            node_modules/keytar/node_modules/node-addon-api/napi.h
        fi

        # pnpmConfigHook installs dependencies without relying on upstream postinstall
        # scripts, so rebuild native Electron modules explicitly for runtime.
        pnpm exec electron-rebuild -f -v ${electronVersion} --only=sqlite3,keytar

        pnpm run build

        python -c 'from pathlib import Path; Path("dist/main/main/ms-shim.cjs").write_text("""var s = 1000;
    var m = s * 60;
    var h = m * 60;
    var d = h * 24;
    var w = d * 7;
    var y = d * 365.25;

    module.exports = function (val, options) {
      options = options || {};
      var type = typeof val;
      if (type === "string" && val.length > 0) {
        return parse(val);
      }
      if (type === "number" && isFinite(val)) {
        return options.long ? fmtLong(val) : fmtShort(val);
      }

      throw new Error(
        "val is not a non-empty string or a valid number. val=" +
          JSON.stringify(val)
      );
    };

    function parse(str) {
      str = String(str);
      if (str.length > 100) {
        return;
      }

      var match = /^(-?(?:\\d+)?\\.?\\d+) *(milliseconds?|msecs?|ms|seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|w|years?|yrs?|y)?$/i.exec(
        str
      );
      if (!match) {
        return;
      }

      var n = parseFloat(match[1]);
      var unit = (match[2] || "ms").toLowerCase();
      switch (unit) {
        case "years":
        case "year":
        case "yrs":
        case "yr":
        case "y":
          return n * y;
        case "weeks":
        case "week":
        case "w":
          return n * w;
        case "days":
        case "day":
        case "d":
          return n * d;
        case "hours":
        case "hour":
        case "hrs":
        case "hr":
        case "h":
          return n * h;
        case "minutes":
        case "minute":
        case "mins":
        case "min":
        case "m":
          return n * m;
        case "seconds":
        case "second":
        case "secs":
        case "sec":
        case "s":
          return n * s;
        case "milliseconds":
        case "millisecond":
        case "msecs":
        case "msec":
        case "ms":
          return n;
      }
    }

    function fmtShort(ms) {
      var msAbs = Math.abs(ms);
      if (msAbs >= d) {
        return Math.round(ms / d) + "d";
      }
      if (msAbs >= h) {
        return Math.round(ms / h) + "h";
      }
      if (msAbs >= m) {
        return Math.round(ms / m) + "m";
      }
      if (msAbs >= s) {
        return Math.round(ms / s) + "s";
      }
      return ms + "ms";
    }

    function fmtLong(ms) {
      var msAbs = Math.abs(ms);
      if (msAbs >= d) {
        return plural(ms, msAbs, d, "day");
      }
      if (msAbs >= h) {
        return plural(ms, msAbs, h, "hour");
      }
      if (msAbs >= m) {
        return plural(ms, msAbs, m, "minute");
      }
      if (msAbs >= s) {
        return plural(ms, msAbs, s, "second");
      }
      return ms + " ms";
    }

    function plural(ms, msAbs, n, name) {
      var isPlural = msAbs >= n * 1.5;
      return Math.round(ms / n) + " " + name + (isPlural ? "s" : "");
    }
    """)'

        substituteInPlace node_modules/debug/src/common.js \
          --replace-fail "require('ms')" "require('../../../dist/main/main/ms-shim.cjs')"

        extra_electron_builder_flags=()
        ${lib.optionalString stdenv.hostPlatform.isDarwin "extra_electron_builder_flags+=(-c.mac.identity=null)"}

        pnpm exec electron-builder --${if stdenv.hostPlatform.isDarwin then "mac" else "linux"} --dir \
          -c.electronDist=${electronDistDir} \
          -c.electronVersion=${electronVersion} \
          "''${extra_electron_builder_flags[@]}"

        runHook postBuild
  '';

  installPhase =
    if stdenv.hostPlatform.isDarwin then
      ''
        runHook preInstall

        distDir="$PWD/release"
        appDir="$distDir/mac-arm64/Emdash.app"

        if [ ! -d "$appDir" ]; then
          echo "Expected Emdash.app output from electron-builder, got nothing at $appDir" >&2
          exit 1
        fi

        install -d "$out/Applications"
        cp -R "$appDir" "$out/Applications/"

        install -d "$out/bin"
        cat <<'EOF' > "$out/bin/emdash"
        #!${stdenv.shell}
        set -euo pipefail

        if [ -z "''${SSH_AUTH_SOCK:-}" ]; then
          ssh_auth_sock="$(launchctl getenv SSH_AUTH_SOCK 2>/dev/null || true)"
          if [ -n "$ssh_auth_sock" ]; then
            export SSH_AUTH_SOCK="$ssh_auth_sock"
          fi
        fi

        exec "@out@/Applications/Emdash.app/Contents/MacOS/Emdash" "$@"
        EOF
        substituteInPlace "$out/bin/emdash" --replace-fail "@out@" "$out"
        chmod +x "$out/bin/emdash"

        runHook postInstall
      ''
    else
      ''
        runHook preInstall

        distDir="$PWD/release"
        unpackedDir="$distDir/linux-unpacked"

        if [ ! -d "$unpackedDir" ]; then
          echo "Expected linux-unpacked output from electron-builder, got nothing at $unpackedDir" >&2
          exit 1
        fi

        install -d "$out/share/emdash"
        cp -R "$unpackedDir" "$out/share/emdash/"

        install -d "$out/bin"
        cat <<'EOF' > "$out/bin/emdash"
        #!${stdenv.shell}
        set -euo pipefail

        app_root="@out@/share/emdash/linux-unpacked"
        exec "$app_root/emdash" "$@"
        EOF
        substituteInPlace "$out/bin/emdash" --replace-fail "@out@" "$out"
        chmod +x "$out/bin/emdash"

        runHook postInstall
      '';

  meta = with lib; {
    description = "Agentic development environment for parallel coding agents";
    homepage = "https://github.com/generalaction/emdash";
    license = licenses.mit;
    platforms = supportedSystems;
    mainProgram = pname;
  };
}
