{
  mkSimpleDarwinApp,
  mkZipApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkZipApp;
  pname = "framer";
  appName = "Framer";
  info = selfSource;
  description = "Interactive design and publishing app";
  homepage = "https://www.framer.com/";
}
