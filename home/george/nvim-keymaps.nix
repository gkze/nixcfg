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
            action = ":write<CR>";
            desc = "Write buffer";
          }
          {
            key = "<leader>W";
            action = ":wall<CR>";
            desc = "Write all buffers";
          }
          {
            key = "<leader>r";
            action = ":IncRename ";
            desc = "Rename symbol";
          }
          {
            key = "<leader>i";
            action = ":set invlist<CR>";
            desc = "Toggle list chars";
          }
          {
            key = "<leader>z";
            action = ":nohlsearch<CR>";
            desc = "Clear search highlight";
          }
          {
            key = ";";
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
            key = "[b";
            action = ":BufferLineCyclePrev<CR>";
            desc = "Previous buffer";
          }
          {
            key = "]b";
            action = ":BufferLineCycleNext<CR>";
            desc = "Next buffer";
          }
          {
            key = "<leader><";
            action = ":BufferLineMovePrev<CR>";
            desc = "Move buffer left";
          }
          {
            key = "<leader>>";
            action = ":BufferLineMoveNext<CR>";
            desc = "Move buffer right";
          }
          {
            key = "<leader>b";
            action = ":Bdelete<CR>";
            desc = "Delete buffer";
          }
        ];
      }
      {
        title = "Tabs";
        items = [
          {
            key = "<leader>T";
            action = ":tabnew<CR>";
            desc = "New tab";
          }
          {
            key = "<leader>q";
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
            action = ":Telescope find_files<CR>";
            desc = "Find files";
          }
          {
            key = "<leader>F";
            action = ":Telescope find_files hidden=true<CR>";
            desc = "Find hidden files";
          }
          {
            key = "<leader>s";
            action = ":Telescope live_grep<CR>";
            desc = "Live grep";
          }
          {
            key = "<leader>?";
            action = ":Telescope keymaps<CR>";
            desc = "Find keymaps";
          }
          {
            key = "<leader>m";
            action = ":NvimKeymaps<CR>";
            desc = "Browse keymap cheat sheet";
          }
          {
            key = "<leader>M";
            action = ":NvimKeymapsDoc<CR>";
            desc = "Open keymap doc";
          }
        ];
      }
      {
        title = "LSP utilities";
        items = [
          {
            key = "<leader>I";
            action = ":LspInfo<CR>";
            desc = "LSP info";
          }
          {
            key = "<leader>R";
            action = ":LspRestart<CR>";
            desc = "Restart LSP";
          }
        ];
      }
      {
        title = "Panes and sidebars";
        items = [
          {
            key = "<leader>h";
            action = {
              __raw = ''require("smart-splits").move_cursor_left'';
            };
            desc = "Focus left pane";
          }
          {
            key = "<leader>j";
            action = {
              __raw = ''require("smart-splits").move_cursor_down'';
            };
            desc = "Focus lower pane";
          }
          {
            key = "<leader>k";
            action = {
              __raw = ''require("smart-splits").move_cursor_up'';
            };
            desc = "Focus upper pane";
          }
          {
            key = "<leader>l";
            action = {
              __raw = ''require("smart-splits").move_cursor_right'';
            };
            desc = "Focus right pane";
          }
          {
            key = "<C-A-h>";
            action = ":Treewalker Left<CR>";
            desc = "Treewalker left";
          }
          {
            key = "<C-A-j>";
            action = ":Treewalker Down<CR>";
            desc = "Treewalker down";
          }
          {
            key = "<C-A-k>";
            action = ":Treewalker Up<CR>";
            desc = "Treewalker up";
          }
          {
            key = "<C-A-l>";
            action = ":Treewalker Right<CR>";
            desc = "Treewalker right";
          }
          {
            key = "<leader>N";
            action = ":Neotree focus<CR>";
            desc = "Neo-tree focus";
          }
          {
            key = "<leader>E";
            action = ":Neotree reveal<CR>";
            desc = "Neo-tree reveal";
          }
          {
            key = "<leader>e";
            action = ":Neotree toggle filesystem<CR>";
            desc = "Neo-tree filesystem";
          }
          {
            key = "<leader>x";
            action = ":Neotree toggle git_status<CR>";
            desc = "Neo-tree git status";
          }
          {
            key = "<leader>n";
            action = ":Navbuddy<CR>";
            desc = "Navbuddy";
          }
          {
            key = "<leader>A";
            action = ":AerialToggle<CR>";
            desc = "Toggle Aerial";
          }
          {
            key = "<leader>v";
            action = ":AerialOpenAll<CR>";
            desc = "Open all Aerial symbols";
          }
          {
            key = "<leader>V";
            action = ":AerialCloseAll<CR>";
            desc = "Close all Aerial symbols";
          }
        ];
      }
      {
        title = "Git";
        items = [
          {
            key = "<leader>g";
            action = ":Neogit<CR>";
            desc = "Open Neogit";
          }
          {
            key = "<leader>B";
            action = ":Neogit branch<CR>";
            desc = "Neogit branch";
          }
          {
            key = "<leader>G";
            action = ":DiffviewOpen<CR>";
            desc = "Open Diffview";
          }
          {
            key = "<leader>D";
            action = ":DiffviewClose<CR>";
            desc = "Close Diffview";
          }
        ];
      }
      {
        title = "Diagnostics";
        items = [
          {
            key = "<leader>d";
            action = ":Trouble diagnostics<CR>";
            desc = "Trouble diagnostics";
          }
          {
            key = "[d";
            action = {
              __raw = "vim.diagnostic.goto_prev";
            };
            desc = "Previous diagnostic";
          }
          {
            key = "]d";
            action = {
              __raw = "vim.diagnostic.goto_next";
            };
            desc = "Next diagnostic";
          }
        ];
      }
      {
        title = "Terminal and AI";
        items = [
          {
            key = "<leader>t";
            action = ":ToggleTerm<CR>";
            desc = "Toggle terminal";
          }
          {
            key = "<leader>c";
            action = ":CodeCompanionChat Toggle<CR>";
            desc = "Toggle CodeCompanion chat";
          }
          {
            key = "<leader>a";
            action = ":CodeCompanionActions<CR>";
            desc = "CodeCompanion actions";
          }
          {
            key = "<leader>C";
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
            action = {
              __raw = ''function() require("opencode").ask("@this: ", { submit = true }) end'';
            };
            desc = "OpenCode ask";
          }
          {
            key = "<leader>S";
            action = {
              __raw = ''function() require("opencode").select() end'';
            };
            desc = "OpenCode select";
          }
          {
            key = "<leader>o";
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
            action = "<CMD>Lspsaga hover_doc<Enter>";
            desc = "Hover docs";
          }
          {
            key = "gl";
            action = "<CMD>Lspsaga show_line_diagnostics<Enter>";
            desc = "Show line diagnostics";
          }
          {
            key = "gL";
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
            action = "definition";
            displayAction = "<CMD>Lspsaga goto_definition<Enter>";
            desc = "Go to definition";
          }
          {
            key = "gr";
            action = "references";
            displayAction = "<CMD>Lspsaga finder_ref<Enter>";
            desc = "Find references";
          }
          {
            key = "gD";
            action = "declaration";
            displayAction = "vim.lsp.buf.declaration()";
            desc = "Go to declaration";
          }
          {
            key = "gi";
            action = "implementation";
            displayAction = "vim.lsp.buf.implementation()";
            desc = "Go to implementation";
          }
          {
            key = "gt";
            action = "type_definition";
            displayAction = "vim.lsp.buf.type_definition()";
            desc = "Go to type definition";
          }
          {
            key = "gs";
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
            action = "gnn";
            desc = "Init selection";
          }
          {
            key = "node_incremental";
            action = "grn";
            desc = "Node incremental";
          }
          {
            key = "scope_incremental";
            action = "grc";
            desc = "Scope incremental";
          }
          {
            key = "node_decremental";
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
    mode = "i";
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
            action = [
              "select_and_accept"
              "fallback"
            ];
            displayAction = "select_and_accept, fallback";
            desc = "Accept completion";
          }
          {
            key = "<Tab>";
            action = [
              "select_next"
              "fallback"
            ];
            displayAction = "select_next, fallback";
            desc = "Next completion";
          }
          {
            key = "<S-Tab>";
            action = [
              "select_prev"
              "fallback"
            ];
            displayAction = "select_prev, fallback";
            desc = "Previous completion";
          }
          {
            key = "<C-d>";
            action = [ "scroll_documentation_down" ];
            displayAction = "scroll_documentation_down";
            desc = "Scroll docs down";
          }
          {
            key = "<C-f>";
            action = [ "scroll_documentation_up" ];
            displayAction = "scroll_documentation_up";
            desc = "Scroll docs up";
          }
          {
            key = "<C-Tab>";
            action = [
              "snippet_forward"
              "fallback"
            ];
            displayAction = "snippet_forward, fallback";
            desc = "Snippet forward";
          }
          {
            key = "<C-S-Tab>";
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
    mode = "i";
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
            action = "<C-c>l";
            desc = "Copy Git link mapping";
          }
        ];
      }
    ];
  };

  mkMapItem = key: action: desc: {
    inherit key action desc;
  };

  treesitterTextobjectsLegend = {
    id = "treesitter-textobjects-legend";
    label = "Treesitter textobjects legend";
    mode = "legend";
    attrPath = [
      "home"
      "george"
      "nvim-keymaps"
      "treesitterTextobjectsLegend"
    ];
    kind = "cheat-sheet";
    context = "manifest / docs only";
    sections = [
      {
        title = "Navigation grammar";
        items = [
          {
            key = "]s…";
            action = "next start";
            desc = "Jump to the next start boundary for the given textobject letter.";
          }
          {
            key = "[s…";
            action = "previous start";
            desc = "Jump to the previous start boundary for the given textobject letter.";
          }
          {
            key = "]e…";
            action = "next end";
            desc = "Jump to the next end boundary for the given textobject letter.";
          }
          {
            key = "[e…";
            action = "previous end";
            desc = "Jump to the previous end boundary for the given textobject letter.";
          }
        ];
      }
      {
        title = "Object letters";
        items = [
          {
            key = "a";
            action = "attribute";
            desc = "Nav: [sa ]sa [ea ]ea.";
          }
          {
            key = "b";
            action = "block";
            desc = "Nav: [sb ]sb [eb ]eb. Select: ab / ib.";
          }
          {
            key = "c";
            action = "call";
            desc = "Nav: [sc ]sc [ec ]ec. Select: ac / ic.";
          }
          {
            key = "f";
            action = "function";
            desc = "Nav: [sf ]sf [ef ]ef. Select: af / if.";
          }
          {
            key = "h";
            action = "lhs";
            desc = "Assignment left-hand side. Nav: [sh ]sh [eh ]eh. Select: lv.";
          }
          {
            key = "i";
            action = "conditional / if";
            desc = "Nav: [si ]si [ei ]ei. Select: ai / ii.";
          }
          {
            key = "o";
            action = "loop";
            desc = "Uses o from loop to avoid l/left confusion. Nav: [so ]so [eo ]eo. Select: ao / io.";
          }
          {
            key = "p";
            action = "parameter";
            desc = "Nav: [sp ]sp [ep ]ep. Select remains aa / ia.";
          }
          {
            key = "r";
            action = "rhs";
            desc = "Assignment right-hand side. Nav: [sr ]sr [er ]er. Select: rv.";
          }
          {
            key = "s";
            action = "statement";
            desc = "Nav: [ss ]ss [es ]es.";
          }
          {
            key = "t";
            action = "class / type";
            desc = "Nav uses t for type: [st ]st [et ]et. Selection remains aC / iC.";
          }
          {
            key = "v";
            action = "assignment";
            desc = "Nav: [sv ]sv [ev ]ev. Select: av / iv.";
          }
        ];
      }
      {
        title = "Selection vs navigation differences";
        items = [
          {
            key = "parameter";
            action = "select aa / ia; nav p";
            desc = "Selection keeps aa / ia to avoid conflicting with Vim paragraph objects; navigation uses p in [sp ]sp [ep ]ep.";
          }
          {
            key = "class";
            action = "select aC / iC; nav t";
            desc = "Selection uses uppercase C because ac / ic are already call; navigation uses t for type in [st ]st [et ]et.";
          }
          {
            key = "loop";
            action = "select ao / io; nav o";
            desc = "Both selection and navigation use o for loop to avoid l feeling like a directional key.";
          }
          {
            key = "lhs / rhs";
            action = "select lv / rv; nav h / r";
            desc = "Selection keeps lv / rv under assignment, while navigation uses h for left-hand side and r for right-hand side.";
          }
        ];
      }
    ];
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
          (mkMapItem "]sa" "@attribute.outer" "Next attribute start")
          (mkMapItem "]sb" "@block.outer" "Next block start")
          (mkMapItem "]sc" "@call.outer" "Next call start")
          (mkMapItem "]sf" "@function.outer" "Next function start")
          (mkMapItem "]sh" "@assignment.lhs" "Next assignment lhs start")
          (mkMapItem "]si" "@conditional.outer" "Next conditional start")
          (mkMapItem "]so" "@loop.outer" "Next loop start")
          (mkMapItem "]sp" "@parameter.outer" "Next parameter start")
          (mkMapItem "]sr" "@assignment.rhs" "Next assignment rhs start")
          (mkMapItem "]ss" "@statement.outer" "Next statement start")
          (mkMapItem "]st" "@class.outer" "Next class start")
          (mkMapItem "]sv" "@assignment.outer" "Next assignment start")
        ];
      }
      {
        title = "gotoNextEnd";
        items = [
          (mkMapItem "]ea" "@attribute.outer" "Next attribute end")
          (mkMapItem "]eb" "@block.outer" "Next block end")
          (mkMapItem "]ec" "@call.outer" "Next call end")
          (mkMapItem "]ef" "@function.outer" "Next function end")
          (mkMapItem "]eh" "@assignment.lhs" "Next assignment lhs end")
          (mkMapItem "]ei" "@conditional.outer" "Next conditional end")
          (mkMapItem "]eo" "@loop.outer" "Next loop end")
          (mkMapItem "]ep" "@parameter.outer" "Next parameter end")
          (mkMapItem "]er" "@assignment.rhs" "Next assignment rhs end")
          (mkMapItem "]es" "@statement.outer" "Next statement end")
          (mkMapItem "]et" "@class.outer" "Next class end")
          (mkMapItem "]ev" "@assignment.outer" "Next assignment end")
        ];
      }
      {
        title = "gotoPreviousStart";
        items = [
          (mkMapItem "[sa" "@attribute.outer" "Previous attribute start")
          (mkMapItem "[sb" "@block.outer" "Previous block start")
          (mkMapItem "[sc" "@call.outer" "Previous call start")
          (mkMapItem "[sf" "@function.outer" "Previous function start")
          (mkMapItem "[sh" "@assignment.lhs" "Previous assignment lhs start")
          (mkMapItem "[si" "@conditional.outer" "Previous conditional start")
          (mkMapItem "[so" "@loop.outer" "Previous loop start")
          (mkMapItem "[sp" "@parameter.outer" "Previous parameter start")
          (mkMapItem "[sr" "@assignment.rhs" "Previous assignment rhs start")
          (mkMapItem "[ss" "@statement.outer" "Previous statement start")
          (mkMapItem "[st" "@class.outer" "Previous class start")
          (mkMapItem "[sv" "@assignment.outer" "Previous assignment start")
        ];
      }
      {
        title = "gotoPreviousEnd";
        items = [
          (mkMapItem "[ea" "@attribute.outer" "Previous attribute end")
          (mkMapItem "[eb" "@block.outer" "Previous block end")
          (mkMapItem "[ec" "@call.outer" "Previous call end")
          (mkMapItem "[ef" "@function.outer" "Previous function end")
          (mkMapItem "[eh" "@assignment.lhs" "Previous assignment lhs end")
          (mkMapItem "[ei" "@conditional.outer" "Previous conditional end")
          (mkMapItem "[eo" "@loop.outer" "Previous loop end")
          (mkMapItem "[ep" "@parameter.outer" "Previous parameter end")
          (mkMapItem "[er" "@assignment.rhs" "Previous assignment rhs end")
          (mkMapItem "[es" "@statement.outer" "Previous statement end")
          (mkMapItem "[et" "@class.outer" "Previous class end")
          (mkMapItem "[ev" "@assignment.outer" "Previous assignment end")
        ];
      }
    ];
  };

  treesitterTextobjectsSelect = {
    id = "treesitter-textobjects-select";
    label = "Treesitter textobjects select";
    mode = "x/o";
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
          (mkMapItem "aC" "@class.outer" "Select class outer")
          (mkMapItem "aa" "@parameter.outer" "Select parameter outer")
          (mkMapItem "ab" "@block.outer" "Select block outer")
          (mkMapItem "ac" "@call.outer" "Select call outer")
          (mkMapItem "af" "@function.outer" "Select function outer")
          (mkMapItem "ai" "@conditional.outer" "Select conditional outer")
          (mkMapItem "ao" "@loop.outer" "Select loop outer")
          (mkMapItem "av" "@assignment.outer" "Select assignment outer")
          (mkMapItem "iC" "@class.inner" "Select class inner")
          (mkMapItem "ia" "@parameter.inner" "Select parameter inner")
          (mkMapItem "ib" "@block.inner" "Select block inner")
          (mkMapItem "ic" "@call.inner" "Select call inner")
          (mkMapItem "if" "@function.inner" "Select function inner")
          (mkMapItem "ii" "@conditional.inner" "Select conditional inner")
          (mkMapItem "io" "@loop.inner" "Select loop inner")
          (mkMapItem "iv" "@assignment.inner" "Select assignment inner")
          (mkMapItem "lv" "@assignment.lhs" "Select assignment lhs")
          (mkMapItem "rv" "@assignment.rhs" "Select assignment rhs")
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
            action = "ene";
            desc = "New file";
          }
          {
            key = "f";
            label = "󰈞 Find file(s)";
            action = "Telescope find_files";
            desc = "Find file(s)";
          }
          {
            key = "t";
            label = "󰈞 Find text";
            action = "Telescope live_grep";
            desc = "Find text";
          }
          {
            key = "q";
            label = " Quit Neovim";
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
    treesitterTextobjectsLegend
    treesitterSelection
    treesitterTextobjectsMove
    treesitterTextobjectsSelect
    blinkCmp
    telescope
    gitlinker
    alpha
  ];
}
