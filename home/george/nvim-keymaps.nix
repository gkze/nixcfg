rec {
  global = {
    id = "global";
    label = "Global";
    attrPath = [
      "programs"
      "nixvim"
      "keymaps"
    ];
    kind = "keymap-list";
    context = "normal/visual mode";
    sections = [
      {
        title = "Core editing";
        items = [
          {
            key = "<leader>w";
            mode = "n";
            action = ":write<CR>";
            desc = "Write buffer";
          }
          {
            key = "<leader>W";
            mode = "n";
            action = ":wall<CR>";
            desc = "Write all buffers";
          }
          {
            key = "<leader>r";
            mode = "n";
            action = ":IncRename ";
            desc = "Rename symbol";
          }
          {
            key = "<leader>i";
            mode = "n";
            action = ":set invlist<CR>";
            desc = "Toggle list chars";
          }
          {
            key = "<leader>z";
            mode = "n";
            action = ":nohlsearch<CR>";
            desc = "Clear search highlight";
          }
          {
            key = ";";
            mode = "n";
            action = ":";
            desc = "Open command line";
          }
          {
            key = "<leader>s";
            mode = "v";
            action = ":'<,'>sort<CR>";
            desc = "Sort selection";
          }
        ];
      }
      {
        title = "Buffers";
        items = [
          {
            key = "<leader>,";
            mode = "n";
            action = ":BufferLineCyclePrev<CR>";
            desc = "Previous buffer";
          }
          {
            key = "<leader>.";
            mode = "n";
            action = ":BufferLineCycleNext<CR>";
            desc = "Next buffer";
          }
          {
            key = "<leader><";
            mode = "n";
            action = ":BufferLineMovePrev<CR>";
            desc = "Move buffer left";
          }
          {
            key = "<leader>>";
            mode = "n";
            action = ":BufferLineMoveNext<CR>";
            desc = "Move buffer right";
          }
          {
            key = "<leader>b";
            mode = "n";
            action = ":Bdelete<CR>";
            desc = "Delete buffer";
          }
        ];
      }
      {
        title = "Tabs";
        items = [
          {
            key = "<leader>t";
            mode = "n";
            action = ":tabnew<CR>";
            desc = "New tab";
          }
          {
            key = "<leader>q";
            mode = "n";
            action = ":tabclose<CR>";
            desc = "Close tab";
          }
        ];
      }
      {
        title = "Search and pickers";
        items = [
          {
            key = "<leader>f";
            mode = "n";
            action = ":Telescope find_files<CR>";
            desc = "Find files";
          }
          {
            key = "<leader>F";
            mode = "n";
            action = ":Telescope find_files hidden=true<CR>";
            desc = "Find hidden files";
          }
          {
            key = "<leader>/";
            mode = "n";
            action = ":Telescope live_grep<CR>";
            desc = "Live grep";
          }
          {
            key = "<leader>?";
            mode = "n";
            action = ":Telescope keymaps<CR>";
            desc = "Find keymaps";
          }
          {
            key = "<leader>m";
            mode = "n";
            action = ":NvimKeymaps<CR>";
            desc = "Browse keymap cheat sheet";
          }
          {
            key = "<leader>M";
            mode = "n";
            action = ":NvimKeymapsDoc<CR>";
            desc = "Open keymap doc";
          }
        ];
      }
      {
        title = "LSP utilities";
        items = [
          {
            key = "<C-l>i";
            mode = "n";
            action = ":LspInfo<CR>";
            desc = "LSP info";
          }
          {
            key = "<C-l>r";
            mode = "n";
            action = ":LspRestart<CR>";
            desc = "Restart LSP";
          }
        ];
      }
      {
        title = "Window movement / resizing";
        items = [
          {
            key = "<leader>h";
            mode = "n";
            action = {
              __raw = ''require("smart-splits").move_cursor_left'';
            };
            desc = "Move to left split";
          }
          {
            key = "<leader>j";
            mode = "n";
            action = {
              __raw = ''require("smart-splits").move_cursor_down'';
            };
            desc = "Move to lower split";
          }
          {
            key = "<leader>k";
            mode = "n";
            action = {
              __raw = ''require("smart-splits").move_cursor_up'';
            };
            desc = "Move to upper split";
          }
          {
            key = "<leader>l";
            mode = "n";
            action = {
              __raw = ''require("smart-splits").move_cursor_right'';
            };
            desc = "Move to right split";
          }
          {
            key = "<leader>H";
            mode = "n";
            action = {
              __raw = ''require("smart-splits").resize_left'';
            };
            desc = "Resize split left";
          }
          {
            key = "<leader>J";
            mode = "n";
            action = {
              __raw = ''require("smart-splits").resize_down'';
            };
            desc = "Resize split down";
          }
          {
            key = "<leader>K";
            mode = "n";
            action = {
              __raw = ''require("smart-splits").resize_up'';
            };
            desc = "Resize split up";
          }
          {
            key = "<leader>L";
            mode = "n";
            action = {
              __raw = ''require("smart-splits").resize_right'';
            };
            desc = "Resize split right";
          }
          {
            key = "<C-A-h>";
            mode = "n";
            action = ":Treewalker Left<CR>";
            desc = "Treewalker left";
          }
          {
            key = "<C-A-j>";
            mode = "n";
            action = ":Treewalker Down<CR>";
            desc = "Treewalker down";
          }
          {
            key = "<C-A-k>";
            mode = "n";
            action = ":Treewalker Up<CR>";
            desc = "Treewalker up";
          }
          {
            key = "<C-A-l>";
            mode = "n";
            action = ":Treewalker Right<CR>";
            desc = "Treewalker right";
          }
        ];
      }
      {
        title = "Navigation panes";
        items = [
          {
            key = "<leader>N";
            mode = "n";
            action = ":Neotree focus<CR>";
            desc = "Neo-tree focus";
          }
          {
            key = "<leader>E";
            mode = "n";
            action = ":Neotree reveal<CR>";
            desc = "Neo-tree reveal";
          }
          {
            key = "<leader>e";
            mode = "n";
            action = ":Neotree toggle filesystem<CR>";
            desc = "Neo-tree filesystem";
          }
          {
            key = "<leader>g";
            mode = "n";
            action = ":Neotree toggle git_status<CR>";
            desc = "Neo-tree git status";
          }
          {
            key = "<leader>n";
            mode = "n";
            action = ":Navbuddy<CR>";
            desc = "Navbuddy";
          }
          {
            key = "<leader>A";
            mode = "n";
            action = ":AerialToggle<CR>";
            desc = "Toggle Aerial";
          }
          {
            key = "<leader>v";
            mode = "n";
            action = ":AerialOpenAll<CR>";
            desc = "Open all Aerial symbols";
          }
          {
            key = "<leader>V";
            mode = "n";
            action = ":AerialCloseAll<CR>";
            desc = "Close all Aerial symbols";
          }
        ];
      }
      {
        title = "Git";
        items = [
          {
            key = "<leader>G";
            mode = "n";
            action = ":Neogit<CR>";
            desc = "Open Neogit";
          }
          {
            key = "<leader>B";
            mode = "n";
            action = ":Neogit branch<CR>";
            desc = "Neogit branch";
          }
          {
            key = "<leader>d";
            mode = "n";
            action = ":DiffviewOpen<CR>";
            desc = "Open Diffview";
          }
          {
            key = "<leader>D";
            mode = "n";
            action = ":DiffviewClose<CR>";
            desc = "Close Diffview";
          }
        ];
      }
      {
        title = "Problems";
        items = [
          {
            key = "<leader>p";
            mode = "n";
            action = ":Trouble diagnostics<CR>";
            desc = "Trouble diagnostics";
          }
        ];
      }
      {
        title = "Terminal and AI";
        items = [
          {
            key = "<leader>T";
            mode = "n";
            action = ":ToggleTerm<CR>";
            desc = "Toggle terminal";
          }
          {
            key = "<leader>c";
            mode = "n";
            action = ":CodeCompanionChat Toggle<CR>";
            desc = "Toggle CodeCompanion chat";
          }
          {
            key = "<leader>a";
            mode = "n";
            action = ":CodeCompanionActions<CR>";
            desc = "CodeCompanion actions";
          }
          {
            key = "<leader>C";
            mode = "n";
            action = ":CodeCompanion<CR>";
            desc = "CodeCompanion inline";
          }
          {
            key = "ga";
            mode = "v";
            action = ":CodeCompanionChat Add<CR>";
            desc = "Add selection to CodeCompanion";
          }
          {
            key = "<leader>O";
            mode = "n";
            action = {
              __raw = ''function() require("opencode").ask("@this: ", { submit = true }) end'';
            };
            desc = "OpenCode ask";
          }
          {
            key = "<leader>s";
            mode = "n";
            action = {
              __raw = ''function() require("opencode").select() end'';
            };
            desc = "OpenCode select";
          }
          {
            key = "<leader>o";
            mode = "n";
            action = {
              __raw = ''function() require("opencode").toggle() end'';
            };
            desc = "OpenCode toggle";
          }
        ];
      }
    ];
  };

  lsp = {
    id = "lsp";
    label = "LSP";
    attrPath = [
      "programs"
      "nixvim"
      "plugins"
      "lsp"
      "keymaps"
    ];
    kind = "keymap-list";
    context = "buffer-local on LspAttach";
    sections = [
      {
        title = "Docs / diagnostics";
        items = [
          {
            key = "K";
            mode = "n";
            action = "<CMD>Lspsaga hover_doc<Enter>";
            desc = "Hover docs";
          }
          {
            key = "gl";
            mode = "n";
            action = "<CMD>Lspsaga show_line_diagnostics<Enter>";
            desc = "Show line diagnostics";
          }
          {
            key = "gL";
            mode = "n";
            action = "<CMD>Lspsaga show_cursor_diagnostics<Enter>";
            desc = "Show cursor diagnostics";
          }
        ];
      }
      {
        title = "Navigation";
        items = [
          {
            key = "gd";
            mode = "n";
            action = "definition";
            displayAction = "<CMD>Lspsaga goto_definition<Enter>";
            desc = "Go to definition";
          }
          {
            key = "gr";
            mode = "n";
            action = "references";
            displayAction = "<CMD>Lspsaga finder_ref<Enter>";
            desc = "Find references";
          }
          {
            key = "gD";
            mode = "n";
            action = "declaration";
            displayAction = "vim.lsp.buf.declaration()";
            desc = "Go to declaration";
          }
          {
            key = "gi";
            mode = "n";
            action = "implementation";
            displayAction = "vim.lsp.buf.implementation()";
            desc = "Go to implementation";
          }
          {
            key = "gt";
            mode = "n";
            action = "type_definition";
            displayAction = "vim.lsp.buf.type_definition()";
            desc = "Go to type definition";
          }
          {
            key = "<C-k>";
            mode = "n";
            action = "signature_help";
            displayAction = "vim.lsp.buf.signature_help()";
            desc = "Signature help";
          }
        ];
      }
    ];
  };

  treesitterSelection = {
    id = "treesitter-selection";
    label = "Treesitter incremental selection";
    attrPath = [
      "programs"
      "nixvim"
      "plugins"
      "treesitter"
      "settings"
      "incremental_selection"
      "keymaps"
    ];
    kind = "keymap-attrset";
    context = "normal mode";
    sections = [
      {
        title = "Selection";
        items = [
          {
            key = "init_selection";
            mode = "n";
            action = "gnn";
            desc = "Init selection";
          }
          {
            key = "node_incremental";
            mode = "n";
            action = "grn";
            desc = "Node incremental";
          }
          {
            key = "scope_incremental";
            mode = "n";
            action = "grc";
            desc = "Scope incremental";
          }
          {
            key = "node_decremental";
            mode = "n";
            action = "grm";
            desc = "Node decremental";
          }
        ];
      }
    ];
  };

  blinkCmp = {
    id = "blink-cmp";
    label = "Blink completion";
    attrPath = [
      "programs"
      "nixvim"
      "plugins"
      "blink-cmp"
      "settings"
      "keymap"
    ];
    kind = "keymap-attrset";
    context = "insert mode";
    sections = [
      {
        title = "Completion";
        items = [
          {
            key = "<Enter>";
            mode = "i";
            action = [
              "select_and_accept"
              "fallback"
            ];
            displayAction = "select_and_accept, fallback";
            desc = "Accept completion";
          }
          {
            key = "<Tab>";
            mode = "i";
            action = [
              "select_next"
              "fallback"
            ];
            displayAction = "select_next, fallback";
            desc = "Next completion";
          }
          {
            key = "<S-Tab>";
            mode = "i";
            action = [
              "select_prev"
              "fallback"
            ];
            displayAction = "select_prev, fallback";
            desc = "Previous completion";
          }
          {
            key = "<C-d>";
            mode = "i";
            action = [ "scroll_documentation_down" ];
            displayAction = "scroll_documentation_down";
            desc = "Scroll docs down";
          }
          {
            key = "<C-f>";
            mode = "i";
            action = [ "scroll_documentation_up" ];
            displayAction = "scroll_documentation_up";
            desc = "Scroll docs up";
          }
          {
            key = "<C-Tab>";
            mode = "i";
            action = [
              "snippet_forward"
              "fallback"
            ];
            displayAction = "snippet_forward, fallback";
            desc = "Snippet forward";
          }
          {
            key = "<C-S-Tab>";
            mode = "i";
            action = [
              "snippet_backward"
              "fallback"
            ];
            displayAction = "snippet_backward, fallback";
            desc = "Snippet backward";
          }
        ];
      }
    ];
  };

  telescope = {
    id = "telescope";
    label = "Telescope";
    attrPath = [
      "programs"
      "nixvim"
      "plugins"
      "telescope"
      "settings"
      "defaults"
      "mappings"
      "i"
    ];
    kind = "nested-map";
    context = "insert mode in Telescope prompt";
    sections = [
      {
        title = "Prompt";
        items = [
          {
            key = "<CR>";
            mode = "i";
            action = {
              __raw = ''
                function(prompt_bufnr)
                  local picker = require('telescope.actions.state').get_current_picker(prompt_bufnr)
                  local multi = picker:get_multi_selection()
                  if not vim.tbl_isempty(multi) then
                    require('telescope.actions').close(prompt_bufnr)
                    for _, j in pairs(multi) do
                      if j.path ~= nil then
                        vim.cmd(string.format('%s %s', 'edit', j.path))
                      end
                    end
                  else
                    require('telescope.actions').select_default(prompt_bufnr)
                  end
                end'';
            };
            displayAction = "function(prompt_bufnr) ... end";
            desc = "Select multi or default";
          }
        ];
      }
    ];
  };

  gitlinker = {
    id = "gitlinker";
    label = "GitLinker";
    attrPath = [
      "programs"
      "nixvim"
      "plugins"
      "gitlinker"
      "settings"
      "opts"
      "mappings"
    ];
    kind = "string-binding";
    context = "normal mode";
    sections = [
      {
        title = "Linking";
        items = [
          {
            key = "mappings";
            mode = "n";
            action = "<C-c>l";
            desc = "Copy Git link mapping";
          }
        ];
      }
    ];
  };

  mkMapItem = key: action: desc: {
    inherit key action desc;
    mode = "n";
  };

  mkTextobjectSelectItem = key: action: desc: {
    inherit key action desc;
    mode = "x/o";
  };

  treesitterTextobjectsMove = {
    id = "treesitter-textobjects-move";
    label = "Treesitter textobjects move";
    attrPath = [
      "programs"
      "nixvim"
      "plugins"
      "treesitter-textobjects"
      "settings"
      "move"
    ];
    kind = "nested-map";
    context = "normal mode";
    sections = [
      {
        title = "gotoNextStart";
        items = [
          (mkMapItem "]]" "@class.outer" "Next class outer")
          (mkMapItem "]a" "@attribute.outer" "Next attribute outer")
          (mkMapItem "]b" "@block.outer" "Next block outer")
          (mkMapItem "]c" "@call.outer" "Next call outer")
          (mkMapItem "]f" "@function.outer" "Next function outer")
          (mkMapItem "]i" "@conditional.outer" "Next conditional outer")
          (mkMapItem "]p" "@parameter.outer" "Next parameter outer")
          (mkMapItem "]s" "@statement.outer" "Next statement outer")
          (mkMapItem "]v" "@assignment.outer" "Next assignment outer")
        ];
      }
      {
        title = "gotoNextEnd";
        items = [
          (mkMapItem "]A" "@attribute.inner" "Next attribute inner")
          (mkMapItem "]B" "@block.outer" "Next block outer")
          (mkMapItem "]C" "@call.outer" "Next call outer")
          (mkMapItem "]F" "@function.outer" "Next function outer")
          (mkMapItem "]I" "@conditional.outer" "Next conditional outer")
          (mkMapItem "]P" "@parameter.outer" "Next parameter outer")
          (mkMapItem "]S" "@statement.outer" "Next statement outer")
          (mkMapItem "]V" "@assignment.outer" "Next assignment outer")
          (mkMapItem "][" "@class.outer" "Next class outer")
        ];
      }
      {
        title = "gotoPreviousStart";
        items = [
          (mkMapItem "[[" "@class.outer" "Previous class outer")
          (mkMapItem "[a" "@attribute.outer" "Previous attribute outer")
          (mkMapItem "[b" "@block.outer" "Previous block outer")
          (mkMapItem "[c" "@call.outer" "Previous call outer")
          (mkMapItem "[f" "@function.outer" "Previous function outer")
          (mkMapItem "[i" "@conditional.outer" "Previous conditional outer")
          (mkMapItem "[p" "@parameter.outer" "Previous parameter outer")
          (mkMapItem "[s" "@statement.outer" "Previous statement outer")
          (mkMapItem "[v" "@assignment.outer" "Previous assignment outer")
        ];
      }
      {
        title = "gotoPreviousEnd";
        items = [
          (mkMapItem "[A" "@attribute.outer" "Previous attribute outer")
          (mkMapItem "[B" "@block.outer" "Previous block outer")
          (mkMapItem "[C" "@call.outer" "Previous call outer")
          (mkMapItem "[F" "@function.outer" "Previous function outer")
          (mkMapItem "[I" "@conditional.outer" "Previous conditional outer")
          (mkMapItem "[P" "@parameter.outer" "Previous parameter outer")
          (mkMapItem "[S" "@statement.outer" "Previous statement outer")
          (mkMapItem "[V" "@assignment.outer" "Previous assignment outer")
          (mkMapItem "[]" "@class.outer" "Previous class outer")
        ];
      }
    ];
  };

  treesitterTextobjectsSelect = {
    id = "treesitter-textobjects-select";
    label = "Treesitter textobjects select";
    attrPath = [
      "programs"
      "nixvim"
      "plugins"
      "treesitter-textobjects"
      "settings"
      "select"
      "keymaps"
    ];
    kind = "keymap-attrset";
    context = "operator-pending / visual textobjects";
    sections = [
      {
        title = "keymaps";
        items = [
          (mkTextobjectSelectItem "aC" "@class.outer" "Select class outer")
          (mkTextobjectSelectItem "aa" "@parameter.outer" "Select parameter outer")
          (mkTextobjectSelectItem "ab" "@block.outer" "Select block outer")
          (mkTextobjectSelectItem "ac" "@call.outer" "Select call outer")
          (mkTextobjectSelectItem "af" "@function.outer" "Select function outer")
          (mkTextobjectSelectItem "ai" "@conditional.outer" "Select conditional outer")
          (mkTextobjectSelectItem "al" "@loop.outer" "Select loop outer")
          (mkTextobjectSelectItem "av" "@assignment.outer" "Select assignment outer")
          (mkTextobjectSelectItem "iC" "@class.inner" "Select class inner")
          (mkTextobjectSelectItem "ia" "@parameter.inner" "Select parameter inner")
          (mkTextobjectSelectItem "ib" "@block.inner" "Select block inner")
          (mkTextobjectSelectItem "ic" "@call.inner" "Select call inner")
          (mkTextobjectSelectItem "if" "@function.inner" "Select function inner")
          (mkTextobjectSelectItem "ii" "@conditional.inner" "Select conditional inner")
          (mkTextobjectSelectItem "il" "@loop.inner" "Select loop inner")
          (mkTextobjectSelectItem "iv" "@assignment.inner" "Select assignment inner")
          (mkTextobjectSelectItem "lv" "@assignment.lhs" "Select assignment lhs")
          (mkTextobjectSelectItem "rv" "@assignment.rhs" "Select assignment rhs")
        ];
      }
    ];
  };

  alpha = {
    id = "alpha";
    label = "Alpha dashboard";
    attrPath = [
      "programs"
      "nixvim"
      "plugins"
      "alpha"
      "settings"
      "layout"
    ];
    kind = "ui-node";
    context = "dashboard buttons";
    sections = [
      {
        title = "Buttons";
        items = [
          {
            key = "e";
            label = " New file";
            mode = "n";
            action = "ene";
            desc = "New file";
          }
          {
            key = "f";
            label = "󰈞 Find file(s)";
            mode = "n";
            action = "Telescope find_files";
            desc = "Find file(s)";
          }
          {
            key = "t";
            label = "󰈞 Find text";
            mode = "n";
            action = "Telescope live_grep";
            desc = "Find text";
          }
          {
            key = "q";
            label = " Quit Neovim";
            mode = "n";
            action = "qall";
            desc = "Quit Neovim";
          }
        ];
      }
    ];
  };

  scopes = [
    global
    lsp
    treesitterSelection
    treesitterTextobjectsMove
    treesitterTextobjectsSelect
    blinkCmp
    telescope
    gitlinker
    alpha
  ];
}
