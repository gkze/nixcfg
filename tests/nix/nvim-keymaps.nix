{
  config,
  src ? ../..,
}:
let
  data = import (src + "/home/george/nvim-keymaps.nix");

  assertEq =
    label: expected: actual:
    if expected == actual then
      true
    else
      throw "${label}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";

  hash = value: builtins.hashString "sha256" (builtins.toJSON value);
  itemMode = scope: item: item.mode or (scope.mode or "n");
  normalizeScope =
    scope:
    (builtins.removeAttrs scope [ "mode" ])
    // {
      sections = map (
        section:
        section
        // {
          items = map (item: item // { mode = itemMode scope item; }) section.items;
        }
      ) scope.sections;
    };

  checks = [
    (assertEq "canonical keymap scope order" [
      "global"
      "lsp"
      "treesitter-textobjects-legend"
      "treesitter-selection"
      "treesitter-textobjects-move"
      "treesitter-textobjects-select"
      "blink-cmp"
      "telescope"
      "gitlinker"
      "alpha"
    ] (map (scope: scope.id) data.scopes))
    (assertEq "normalized keymap semantics"
      "f786e6c95c94a9d57c7a69c23eeae4a903b99336e3ef98f35e3fba6d3270dbb7"
      (hash (map normalizeScope data.scopes))
    )
    (assertEq "rendered keymap documentation"
      "465e15e97a57880678d49d62cc059436134b416167b0c6b2895da76e921f83be"
      (hash config.home.file.".config/nvim/doc/nvim-keymaps.md".text)
    )
    (assertEq "rendered keymap picker module"
      "95c54e28a2d2ba77358509b628982581571351f9ec49e2a214ed2605104b2601"
      (hash config.home.file.".config/nvim/lua/nvim-keymaps.lua".text)
    )
    (assertEq "rendered Alpha layout" "554687120549cd1239f47b50980c76445fc23841bd9e3be84a4ab3508a1c6ebd"
      (hash config.programs.nixvim.plugins.alpha.settings.layout)
    )
  ];
in
builtins.deepSeq checks true
