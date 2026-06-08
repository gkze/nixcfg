{
  mkGoCli,
  ...
}:
mkGoCli {
  pname = "anthropic-cli";
  cmdName = "ant";
  completionCommand = "@completion";
  description = "The official CLI for the Claude Developer Platform";
  homepage = "https://github.com/anthropics/anthropic-cli";
}
