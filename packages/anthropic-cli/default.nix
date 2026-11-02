{
  go_1_27,
  mkGoCli,
  ...
}:
mkGoCli {
  pname = "anthropic-cli";
  # Upstream's go.mod floor (go 1.26.10 since v2.5.0) can outpace nixpkgs'
  # default Go. Intentional compatibility policy; the updater reads this
  # choice through package passthru.
  go = go_1_27;
  cmdName = "ant";
  completionCommand = "@completion";
  description = "The official CLI for the Claude Developer Platform";
  homepage = "https://github.com/anthropics/anthropic-cli";
}
