{
  mkDenoApplication,
  inputs,
  outputs,
  lib,
  ...
}:
let
  slib = outputs.lib;
  version = slib.getFlakeVersion "linear-cli";
in
mkDenoApplication {
  pname = "linear-cli";
  inherit version;
  src = inputs.linear-cli;
  denoDepsSrc = ./deno-deps.json;
  entrypoint = "src/main.ts";
  denoFlags = "-A";
  preBuild = ''
    # Run codegen — generates src/__codegen__/graphql.ts via
    # npm:@graphql-codegen/cli which is already in the synthetic DENO_DIR.
    deno task codegen
  '';
  meta = with lib; {
    description = "Linear issue tracker CLI";
    homepage = "https://github.com/schpet/linear-cli";
    license = licenses.isc;
    mainProgram = "linear-cli";
  };
}
