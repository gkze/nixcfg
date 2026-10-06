{
  callPackage,
  mkDmgApp7zz,
  python3,
  selfSource,
  stdenv,
  ...
}:
(mkDmgApp7zz {
  pname = "ara";
  bundleName = "Reason.app";
  sourceName = "Reason_${selfSource.version}_aarch64.dmg";
  executableName = "Reason";
  info = selfSource;
  description = "AI-native desktop workspace";
  homepage = "https://reasonmachines.com/";
  postInstallApp = ''
    ${python3}/bin/python ${./validate_artifact.py} "$out/Applications/Reason.app/Contents/Info.plist" "${selfSource.version}"
  '';
  platforms = [ "aarch64-darwin" ];
}).overrideAttrs
  (_old: {
    src = callPackage ./fetch-dmg.nix {
      inherit (selfSource) version;
      url = selfSource.urls.${stdenv.hostPlatform.system};
      hash = selfSource.hashes.${stdenv.hostPlatform.system};
    };
  })
