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
            key = "[b";
            mode = "n";
            action = ":BufferLineCyclePrev<CR>";
            desc = "Previous buffer";
          }
          {
            key = "]b";
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
            key = "<leader>T";
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
            key = "<leader>s";
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
            key = "<leader>I";
            mode = "n";
            action = ":LspInfo<CR>";
            desc = "LSP info";
          }
          {
            key = "<leader>R";
            mode = "n";
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
            mode = "n";
            action = {
              __raw = ''require("smart-splits").move_cursor_left'';
            };
            desc = "Focus left pane";
          }
          {
            key = "<leader>j";
            mode = "n";
            action = {
              __raw = ''require("smart-splits").move_cursor_down'';
            };
            desc = "Focus lower pane";
          }
          {
            key = "<leader>k";
            mode = "n";
            action = {
              __raw = ''require("smart-splits").move_cursor_up'';
            };
            desc = "Focus upper pane";
          }
          {
            key = "<leader>l";
            mode = "n";
            action = {
              __raw = ''require("smart-splits").move_cursor_right'';
            };
            desc = "Focus right pane";
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
            key = "<leader>x";
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
            key = "<leader>g";
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
            key = "<leader>G";
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
        title = "Diagnostics";
        items = [
          {
            key = "<leader>d";
            mode = "n";
            action = ":Trouble diagnostics<CR>";
            desc = "Trouble diagnostics";
          }
          {
            key = "[d";
            mode = "n";
            action = {
              __raw = "vim.diagnostic.goto_prev";
            };
            desc = "Previous diagnostic";
          }
          {
            key = "]d";
            mode = "n";
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
            key = "<leader>S";
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
            key = "gs";
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

  treesitterTextobjectsLegend = {
    id = "treesitter-textobjects-legend";
    label = "Treesitter textobjects legend";
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
            mode = "legend";
            action = "next start";
            desc = "Jump to the next start boundary for the given textobject letter.";
          }
          {
            key = "[s…";
            mode = "legend";
            action = "previous start";
            desc = "Jump to the previous start boundary for the given textobject letter.";
          }
          {
            key = "]e…";
            mode = "legend";
            action = "next end";
            desc = "Jump to the next end boundary for the given textobject letter.";
          }
          {
            key = "[e…";
            mode = "legend";
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
            mode = "legend";
            action = "attribute";
            desc = "Nav: [sa ]sa [ea ]ea.";
          }
          {
            key = "b";
            mode = "legend";
            action = "block";
            desc = "Nav: [sb ]sb [eb ]eb. Select: ab / ib.";
          }
          {
            key = "c";
            mode = "legend";
            action = "call";
            desc = "Nav: [sc ]sc [ec ]ec. Select: ac / ic.";
          }
          {
            key = "f";
            mode = "legend";
            action = "function";
            desc = "Nav: [sf ]sf [ef ]ef. Select: af / if.";
          }
          {
            key = "h";
            mode = "legend";
            action = "lhs";
            desc = "Assignment left-hand side. Nav: [sh ]sh [eh ]eh. Select: lv.";
          }
          {
            key = "i";
            mode = "legend";
            action = "conditional / if";
            desc = "Nav: [si ]si [ei ]ei. Select: ai / ii.";
          }
          {
            key = "o";
            mode = "legend";
            action = "loop";
            desc = "Uses o from loop to avoid l/left confusion. Nav: [so ]so [eo ]eo. Select: ao / io.";
          }
          {
            key = "p";
            mode = "legend";
            action = "parameter";
            desc = "Nav: [sp ]sp [ep ]ep. Select remains aa / ia.";
          }
          {
            key = "r";
            mode = "legend";
            action = "rhs";
            desc = "Assignment right-hand side. Nav: [sr ]sr [er ]er. Select: rv.";
          }
          {
            key = "s";
            mode = "legend";
            action = "statement";
            desc = "Nav: [ss ]ss [es ]es.";
          }
          {
            key = "t";
            mode = "legend";
            action = "class / type";
            desc = "Nav uses t for type: [st ]st [et ]et. Selection remains aC / iC.";
          }
          {
            key = "v";
            mode = "legend";
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
            mode = "legend";
            action = "select aa / ia; nav p";
            desc = "Selection keeps aa / ia to avoid conflicting with Vim paragraph objects; navigation uses p in [sp ]sp [ep ]ep.";
          }
          {
            key = "class";
            mode = "legend";
            action = "select aC / iC; nav t";
            desc = "Selection uses uppercase C because ac / ic are already call; navigation uses t for type in [st ]st [et ]et.";
          }
          {
            key = "loop";
            mode = "legend";
            action = "select ao / io; nav o";
            desc = "Both selection and navigation use o for loop to avoid l feeling like a directional key.";
          }
          {
            key = "lhs / rhs";
            mode = "legend";
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
          (mkTextobjectSelectItem "ao" "@loop.outer" "Select loop outer")
          (mkTextobjectSelectItem "av" "@assignment.outer" "Select assignment outer")
          (mkTextobjectSelectItem "iC" "@class.inner" "Select class inner")
          (mkTextobjectSelectItem "ia" "@parameter.inner" "Select parameter inner")
          (mkTextobjectSelectItem "ib" "@block.inner" "Select block inner")
          (mkTextobjectSelectItem "ic" "@call.inner" "Select call inner")
          (mkTextobjectSelectItem "if" "@function.inner" "Select function inner")
          (mkTextobjectSelectItem "ii" "@conditional.inner" "Select conditional inner")
          (mkTextobjectSelectItem "io" "@loop.inner" "Select loop inner")
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
