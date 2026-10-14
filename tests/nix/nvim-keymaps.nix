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
      "0c6502b7a99b218fb84434427df1aedf779458f8a82aee4e2f50f9925e0bbb3d"
      (hash (map normalizeScope data.scopes))
    )
    (assertEq "rendered keymap documentation"
      "15a10a825d9aba84097a0c3800802d12e12246557805a290d0277d0695eb360a"
      (hash config.home.file.".config/nvim/doc/nvim-keymaps.md".text)
    )
    (assertEq "rendered keymap picker module"
      "f01f14c128ef652a0781ceefe6c25c44fede533469dea764c1f6fa90422a162b"
      (hash config.home.file.".config/nvim/lua/nvim-keymaps.lua".text)
    )
    (assertEq "rendered Alpha layout" "554687120549cd1239f47b50980c76445fc23841bd9e3be84a4ab3508a1c6ebd"
      (hash config.programs.nixvim.plugins.alpha.settings.layout)
    )
  ];
in
builtins.deepSeq checks true
