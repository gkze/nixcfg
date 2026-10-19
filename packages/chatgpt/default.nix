{
  mkZipApp,
  selfSource,
  lib,
  stdenv,
  fetchurl,
  dpkg,
  autoPatchelfHook,
  makeWrapper,
  alsa-lib,
  at-spi2-atk,
  at-spi2-core,
  atk,
  cairo,
  cups,
  dbus,
  expat,
  gdk-pixbuf,
  glib,
  gtk3,
  libdrm,
  libgbm,
  libnotify,
  libusb1,
  libxkbcommon,
  mesa,
  nspr,
  nss,
  openssl,
  pango,
  systemd,
  tpm2-tss,
  vulkan-loader,
  xz,
  libx11,
  libxcb,
  libxcomposite,
  libxdamage,
  libxext,
  libxfixes,
  libxrandr,
  libxscrnsaver,
  libxtst,
  ...
}:
let
  inherit (stdenv.hostPlatform) isDarwin system;

  meta = with lib; {
    description = "ChatGPT desktop app (unified ChatGPT and Codex)";
    homepage = "https://developers.openai.com/codex/app";
    license = licenses.unfree;
    platforms = [
      "aarch64-darwin"
      "x86_64-darwin"
      "aarch64-linux"
      "x86_64-linux"
    ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "chatgpt";
  };

  # Linux ships from a Debian repository beside the macOS Sparkle appcasts. The
  # pool filename pins the release, so read the version from it instead of
  # reusing the Darwin short/build pair from sources.json.
  debUrl = selfSource.urls.${system};
  debVersionMatch = builtins.match ".*chatgpt_([0-9.]+)_[a-z0-9]+\\.deb" debUrl;
  debVersion =
    if debVersionMatch == null then
      throw "Unexpected ChatGPT deb pool URL: ${debUrl}"
    else
      builtins.head debVersionMatch;
in
if isDarwin then
  mkZipApp {
    pname = "chatgpt";
    appName = "ChatGPT";
    info = selfSource;
    dontFixup = true;
    # Leave sourceName unset so fetchurl keeps the ZIP basename. Prepare
    # prefetches that same name into Cachix; a versioned rename misses the cache
    # and re-fetches OpenAI's short-version URL, which can change bytes before
    # Darwin closures (Update 37586805620).
    inherit meta;
  }
else
  stdenv.mkDerivation {
    pname = "chatgpt";
    version = debVersion;

    src = fetchurl {
      url = debUrl;
      hash = selfSource.hashes.${system};
    };

    nativeBuildInputs = [
      dpkg
      autoPatchelfHook
      makeWrapper
    ];

    # Runtime dependencies declared by the deb control file, mapped to nixpkgs.
    buildInputs = [
      alsa-lib
      at-spi2-atk
      at-spi2-core
      atk
      cairo
      cups
      dbus
      expat
      gdk-pixbuf
      glib
      gtk3
      libdrm
      libgbm
      libnotify
      libusb1
      libxkbcommon
      mesa
      nspr
      nss
      openssl
      pango
      systemd
      tpm2-tss
      vulkan-loader
      xz
      libx11
      libxcb
      libxcomposite
      libxdamage
      libxext
      libxfixes
      libxrandr
      libxscrnsaver
      libxtst
    ];

    # Electron dlopens the Qt shims only when the host provides a Qt platform
    # theme. Vendored node-hid / serialport prebuilds also ship Android NDK and
    # musl objects that can never load on glibc Linux (38564429681 validate-x86:
    # libc.musl-x86_64.so.1 on chatgpt-26.1007.71326). None may fail patchelf.
    autoPatchelfIgnoreMissingDeps = [
      "libQt5Core.so.5"
      "libQt5Gui.so.5"
      "libQt5Widgets.so.5"
      "libQt6Core.so.6"
      "libQt6Gui.so.6"
      "libQt6Widgets.so.6"
      "liblog.so"
      "libc++_shared.so"
      "libc.musl-x86_64.so.1"
      "libc.musl-aarch64.so.1"
    ];

    dontConfigure = true;
    dontBuild = true;
    dontStrip = true;

    unpackPhase = ''
      runHook preUnpack

      mkdir unpack
      dpkg-deb -x "$src" unpack

      runHook postUnpack
    '';

    installPhase = ''
      runHook preInstall

      mkdir -p "$out/lib" "$out/bin" "$out/share"
      cp -a unpack/usr/lib/chatgpt "$out/lib/chatgpt"
      chmod +x "$out/lib/chatgpt/ChatGPT" \
        "$out/lib/chatgpt/codex-launcher" \
        "$out/lib/chatgpt/browser_crashpad_handler"

      install -Dm644 unpack/usr/share/applications/chatgpt.desktop \
        "$out/share/applications/chatgpt.desktop"
      for share_dir in pixmaps metainfo; do
        if [ -d "unpack/usr/share/$share_dir" ]; then
          mkdir -p "$out/share/$share_dir"
          cp -a "unpack/usr/share/$share_dir/." "$out/share/$share_dir/"
        fi
      done

      # The bundle entrypoint is lib/chatgpt/ChatGPT; upstream's usr/bin/chatgpt
      # is only a two-line shell launcher around it.
      makeWrapper "$out/lib/chatgpt/ChatGPT" "$out/bin/chatgpt" \
        --prefix LD_LIBRARY_PATH : "$out/lib/chatgpt"

      runHook postInstall
    '';

    inherit meta;
  }
