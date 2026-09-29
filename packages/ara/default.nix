{
  mkDmgApp7zz,
  python3,
  selfSource,
  ...
}:
mkDmgApp7zz {
  pname = "ara";
  bundleName = "Reason.app";
  sourceName = "Reason_${selfSource.version}_aarch64.dmg";
  executableName = "Ara";
  info = selfSource;
  description = "AI-native desktop workspace";
  homepage = "https://reasonmachines.com/";
  postInstallApp = ''
    ${python3}/bin/python ${./validate_artifact.py} "$out/Applications/Reason.app/Contents/Info.plist" "${selfSource.version}"
  '';
  platforms = [ "aarch64-darwin" ];
}
