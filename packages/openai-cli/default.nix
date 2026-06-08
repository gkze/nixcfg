{
  lib,
  mkGoCli,
  ...
}:
mkGoCli {
  pname = "openai-cli";
  cmdName = "openai";
  completionCommand = "@completion";
  description = "The official CLI for the OpenAI API";
  homepage = "https://github.com/openai/openai-cli";
  license = lib.licenses.asl20;
}
