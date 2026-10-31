{
  config,
  src ? ../..,
}:
# Properties of the rendered keymap artifacts, not of their contents.
#
# Golden hashes over rendered text would fail on any cosmetic edit and report
# no diff, so they are replaced by the invariants that actually matter: every
# declared scope is documented, every scope and section is well formed, and
# every keymap entry names a key.
let
  data = import (src + "/home/george/nvim-keymaps.nix");

  inherit (import ./lib.nix) assertEq;

  inherit (data) scopes;
  scopeIds = map (scope: scope.id) scopes;
  sections = builtins.concatMap (scope: scope.sections) scopes;
  items = builtins.concatMap (section: section.items) sections;

  # `replaceStrings` with an empty replacement shortens the haystack exactly
  # when the needle is present, which is all a substring probe needs.
  hasString =
    needle: haystack:
    builtins.stringLength (builtins.replaceStrings [ needle ] [ "" ] haystack)
    != builtins.stringLength haystack;

  unique =
    values:
    builtins.attrNames (
      builtins.listToAttrs (
        map (name: {
          inherit name;
          value = null;
        }) values
      )
    );

  doc = config.home.file.".config/nvim/doc/nvim-keymaps.md".text;
  picker = config.home.file.".config/nvim/lua/nvim-keymaps.lua".text;

  missingLabels = builtins.filter (scope: !(hasString scope.label doc)) scopes;

  checks = [
    (assertEq "every scope declares an id" [ ] (builtins.filter (id: id == "" || id == null) scopeIds))
    (assertEq "scope ids are unique" (builtins.length scopeIds) (builtins.length (unique scopeIds)))
    (assertEq "every scope declares a label" [ ] (
      builtins.filter (scope: (scope.label or "") == "") scopes
    ))
    (assertEq "every section declares a title and at least one item" [ ] (
      builtins.filter (section: (section.title or "") == "" || section.items == [ ]) sections
    ))
    (assertEq "every keymap entry names a key" [ ] (
      builtins.filter (item: (item.key or "") == "") items
    ))
    (assertEq "the documentation covers every scope label" [ ] (map (scope: scope.id) missingLabels))
    (assertEq "the documentation renders" true (builtins.stringLength doc > 0))
    (assertEq "the picker module renders" true (builtins.stringLength picker > 0))
  ];
in
builtins.deepSeq checks true
