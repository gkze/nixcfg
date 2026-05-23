{
  mkSimpleDarwinApp,
  mkDmgApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkDmgApp;
  pname = "comet";
  appName = "Comet";
  info = selfSource;
  description = "AI browser from Perplexity";
  homepage = "https://www.perplexity.ai/comet";
}
