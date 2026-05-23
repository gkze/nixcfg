{
  mkSimpleDarwinApp,
  mkDmgApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkDmgApp;
  pname = "docker-desktop";
  appName = "Docker";
  bundleName = "Docker.app";
  executableName = "com.docker.backend";
  info = selfSource;
  makeBinary = false;
  description = "Docker Desktop for macOS";
  homepage = "https://www.docker.com/products/docker-desktop";
  platforms = [ "aarch64-darwin" ];
}
