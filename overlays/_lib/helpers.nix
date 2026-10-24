args:
(import ./helpers/build-systems.nix args)
// (import ./helpers/electron.nix args)
// (import ./helpers/go.nix args)
// (import ./helpers/uv2nix.nix args)
// (import ./helpers/darwin-apps.nix args)
// (import ./helpers/deno.nix args)
// (import ./helpers/sources.nix args)
// {
  # `replaceStrings` for upstream phase text: evaluation fails when an anchor
  # is missing, so an override cannot silently stop applying after a bump.
  replaceStringsExact =
    from: to: text:
    let
      missing = builtins.filter (anchor: !args.prev.lib.hasInfix anchor text) from;
    in
    if missing == [ ] then
      builtins.replaceStrings from to text
    else
      throw "replaceStringsExact: anchor not found: ${builtins.concatStringsSep ", " missing}";
}
