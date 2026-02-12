{
  sources,
  system,
  prev,
  ...
}:
{
  vscode-insiders =
    let
      info = sources.vscode-insiders;
      inherit (info) version;
      hash = info.hashes.${system};
      plat =
        {
          aarch64-darwin = "darwin-arm64";
          aarch64-linux = "linux-arm64";
          x86_64-linux = "linux-x64";
        }
        .${system};
      archive_fmt = if prev.stdenv.hostPlatform.isDarwin then "zip" else "tar.gz";
    in
    (prev.vscode.override { isInsiders = true; }).overrideAttrs {
      inherit version;
      src = prev.fetchurl {
        name = "VSCode-insiders-${version}-${plat}.${archive_fmt}";
        url = info.urls.${system};
        inherit hash;
      };
    };
}
