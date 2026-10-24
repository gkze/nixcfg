{
  darwin,
  mkSimpleDarwinApp,
  mkZipApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkZipApp;
  pname = "airfoil";
  appName = "Airfoil";
  info = selfSource;
  sourceAppPath = "Airfoil/Airfoil.app";
  # The archive ships the receiver companion beside the main app. It is part
  # of the same product, so it is installed alongside rather than routed as a
  # managed app of its own.
  postInstallApp = ''
    cp -R "$unpack_dir/Airfoil/Airfoil Satellite.app" "$out/Applications/Airfoil Satellite.app"
    chmod -R u+w "$out/Applications/Airfoil Satellite.app"
    ${darwin.xattr}/bin/xattr -cr "$out/Applications/Airfoil Satellite.app"
  '';
  description = "Send audio from your Mac to compatible receivers";
  homepage = "https://rogueamoeba.com/airfoil/mac/";
}
