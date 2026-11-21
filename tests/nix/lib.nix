# Shared assertion helpers for the Nix test suite.
#
# Every test in this directory follows the same shape: build a small synthetic
# input, collect assertions in a list, and terminate with
# `builtins.deepSeq checks true`. A failing assertion throws during evaluation,
# so an eval-only check fails before its stamp derivation is built.
#
# The helpers are pure `builtins` functions so a test can import them without
# threading a real `lib` through. Import the module, not the file, so adding a
# helper never changes an existing test signature:
#
#   let
#     inherit (import ./lib.nix) assertEq;
#   in ...
let
  assertEq =
    label: expected: actual:
    if expected == actual then
      true
    else
      throw "${label}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";

  # Assert that evaluating `expression` throws. `builtins.tryEval` only catches
  # `throw` and `assert`, which is exactly the failure surface these tests
  # expect from validation code.
  assertThrows =
    label: expression:
    let
      result = builtins.tryEval expression;
    in
    if result.success then
      throw "${label}: expected an evaluation failure, got ${builtins.toJSON result.value}"
    else
      true;

  # Nix accepts space-separated list values, so split a rendered setting into
  # the words the daemon will read.
  wordsOf =
    value:
    let
      pieces = builtins.split "[[:space:]]+" value;
    in
    builtins.filter builtins.isString pieces;

  # Parse a rendered nix.conf, or a fragment of one, into `name -> [ value ]`.
  # Comments and directives are skipped, and repeated keys keep the last value,
  # which is how Nix resolves them.
  parseNixSettings =
    text:
    let
      lines = builtins.filter (line: line != "" && builtins.match "[[:space:]]*#.*" line == null) (
        builtins.filter builtins.isString (builtins.split "\n" text)
      );
      parseLine =
        line:
        let
          match = builtins.match "[[:space:]]*([^[:space:]=]+)[[:space:]]*=[[:space:]]*(.*)" line;
        in
        if match == null then
          null
        else
          {
            name = builtins.elemAt match 0;
            value = builtins.elemAt match 1;
          };
      entries = builtins.filter (entry: entry != null) (map parseLine lines);
    in
    builtins.foldl' (acc: entry: acc // { ${entry.name} = wordsOf entry.value; }) { } entries;

  # Nix appends `extra-` settings to their base setting, so fold them together
  # before comparing what a host actually applies.
  foldExtraSettings =
    settings:
    let
      names = builtins.attrNames settings;
      isExtra = name: builtins.substring 0 6 name == "extra-";
      stripped = name: builtins.substring 6 (builtins.stringLength name) name;
      base = builtins.listToAttrs (
        map (name: {
          inherit name;
          value = settings.${name};
        }) (builtins.filter (name: !isExtra name) names)
      );
    in
    builtins.foldl' (
      acc: name:
      let
        key = stripped name;
      in
      acc // { ${key} = (acc.${key} or [ ]) ++ settings.${name}; }
    ) base (builtins.filter isExtra names);

  # Encode a setting value the way nix.conf spells it. `toString` is not enough
  # here because Nix renders booleans as an empty string.
  confValue =
    value:
    if builtins.isBool value then
      (if value then "true" else "false")
    else if builtins.isList value then
      map confValue value
    else
      toString value;

  # The daemon settings a host actually applies. nix-darwin writes them when it
  # owns Nix; otherwise the host renders them elsewhere and that text is the
  # source of truth.
  effectiveNixSettings =
    host:
    foldExtraSettings (
      if host.nix.enable then
        builtins.mapAttrs (
          _: value: if builtins.isList value then map confValue value else [ (confValue value) ]
        ) host.nix.settings
      else
        parseNixSettings host.environment.etc."nix/nix.custom.conf".text
    );
in
{
  inherit
    assertEq
    assertThrows
    effectiveNixSettings
    parseNixSettings
    ;
}
