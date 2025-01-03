{
  lib,
  pkgs,
  inputs,
  hostPlatform,
  ...
}:
let
  inherit (builtins) concatStringsSep;
  inherit (lib.attrsets) mapAttrsToList;
in
{
  programs.nixvim = {
    # extraConfigLuaPre = ''
    #   require'plenary.profile'.start("profile.log")
    # '';
    # extraConfigLuaPost = ''
    #   require'plenary.profile'.stop()
    # '';
    config = {
      enable = true;
      enableMan = true;
      # Remap leader key to spacebar
      globals.mapleader = " ";
      # Set Space key to be leader
      opts = {
        # Rulers at 80 and 100 characters
        colorcolumn = [
          80
          100
        ];
        # Highlight cursor line
        cursorline = true;
        # Highlight cursor column
        cursorcolumn = true;
        # TODO: figure out
        # Folding
        foldlevel = 99; # Folds with a level higher than this number will be closed
        foldcolumn = "1";
        foldenable = true;
        foldlevelstart = -1;
        fillchars = {
          horiz = "━";
          horizup = "┻";
          horizdown = "┳";
          vert = "┃";
          vertleft = "┫";
          vertright = "┣";
          verthoriz = "╋";
          eob = " ";
          diff = "╱";
          fold = " ";
          foldopen = "";
          foldclose = "";
          msgsep = "‾";
        };
        # Mouse
        mouse = "a";
        # Line numbers
        number = true;
        # Relative line numbers
        relativenumber = true;
        # List mode (display non-printing characters)
        list = true;
        # Set printing characters for non-printing characters
        listchars = {
          eol = "↵";
          extends = ">";
          nbsp = "°";
          precedes = "<";
          space = "·";
          tab = ">-";
          trail = ".";
        };
        # Update debounce
        updatetime = 200;
        # Keep sign column rendered so that errors popping up don't trigger a
        # redraw
        signcolumn = "yes";
      };
      autoCmd = [
        # {
        #   event = [ "LspAttach" ];
        #   callback.__raw = ''
        #     function(args)
        #       local client = vim.lsp.get_client_by_id(args.data.client_id)
        #       require("lsp_signature").on_attach({ bind = true }, args.buf)
        #     end
        #   '';
        # }
      ];
      colorschemes.catppuccin = {
        enable = true;
        settings = {
          flavour = "frappe";
          integrations = {
            aerial = true;
            alpha = true;
            barbecue = {
              alt_background = true;
              bold_basename = true;
              dim_context = true;
              dim_dirname = true;
            };
            # cmp = true;
            dap = {
              enabled = true;
              enable_ui = true;
            };
            gitsigns = true;
            lsp_saga = true;
            native_lsp = {
              enabled = true;
              inlay_hints.background = true;
            };
            neogit = true;
            neotree = true;
            telescope.enabled = true;
            treesitter = true;
            treesitter_context = true;
            which_key = true;
          };
          show_end_of_buffer = true;
          term_colors = true;
        };
      };
      # Editor-agnostic configuration
      editorconfig.enable = true;
      plugins = {
        # Greeter (home page)
        alpha = {
          enable = true;
          layout =
            let
              button = val: shortcut: cmd: {
                type = "button";
                inherit val;
                on_press.__raw = "function() vim.cmd[[${cmd}]] end";
                opts = {
                  inherit shortcut;
                  align_shortcut = "right";
                  keymap = [
                    "n"
                    shortcut
                    ":${cmd}<CR>"
                    { }
                  ];
                  position = "center";
                  width = 50;
                };
              };
              padding = v: {
                type = "padding";
                val = v;
                opts.position = "center";
              };
            in
            [
              (padding 2)
              {
                type = "text";
                val = [
                  "███╗   ██╗██╗██╗  ██╗██╗   ██╗██╗███╗   ███╗"
                  "████╗  ██║██║╚██╗██╔╝██║   ██║██║████╗ ████║"
                  "██╔██╗ ██║██║ ╚███╔╝ ██║   ██║██║██╔████╔██║"
                  "██║╚██╗██║██║ ██╔██╗ ╚██╗ ██╔╝██║██║╚██╔╝██║"
                  "██║ ╚████║██║██╔╝ ██╗ ╚████╔╝ ██║██║ ╚═╝ ██║"
                  "╚═╝  ╚═══╝╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚═╝     ╚═╝"
                ];
                opts = {
                  position = "center";
                  hl = "Type";
                };
              }
              (padding 2)
              {
                type = "group";
                val = [
                  (button " New file" "e" "ene")
                  (padding 1)
                  (button "󰈞 Find file(s)" "f" "Telescope find_files")
                  (padding 1)
                  (button "󰈞 Find text" "t" "Telescope live_grep")
                  (padding 1)
                  (button " Quit Neovim" "q" "qall")
                ];
              }
              (padding 2)
              {
                type = "text";
                val = "Crankenstein";
                opts = {
                  position = "center";
                  hl = "Keyword";
                };
              }
            ];
        };
        # Buffer line (top, tabs)
        bufferline = {
          enable = true;
          settings.options = {
            diagnostics = "nvim_lsp";
            enforce_regular_tabs = false;
            offsets = [
              {
                filetype = "neo-tree";
                text = "Neo-tree";
                separator = true;
                textAlign = "left";
              }
            ];
          };
        };
        blink-cmp = {
          enable = true;
          settings = {
            completion = {
              trigger.prefetch_on_insert = true;
            };
            documentation = {
              auto_show = true;
              auto_show_delay_ms = 100;
            };
            keymap = {
              "<Enter>" = [
                "select_and_accept"
                "fallback"
              ];
              "<Tab>" = [
                "select_next"
                "fallback"
              ];
              "<S-Tab>" = [
                "select_prev"
                "fallback"
              ];
              "<C-d>" = [ "scroll_documentation_down" ];
              "<C-f>" = [ "scroll_documentation_up" ];
              "<C-Tab>" = [
                "snippet_forward"
                "fallback"
              ];
              "<C-S-Tab>" = [
                "snippet_backward"
                "fallback"
              ];
            };
            signature.enabled = true;
          };
        };
        # LSP completion
        # cmp = {
        #   enable = true;
        #
        #   settings = {
        #     extraOptions.autoEnableSources = true;
        #     snippet.expand = ''
        #       function(args)
        #         require('luasnip').lsp_expand(args.body)
        #       end
        #     '';
        #     sources = map (s: { name = s; }) [
        #       "nvim_lsp"
        #       "treesitter"
        #       "luasnip"
        #       "path"
        #       "buffer"
        #       "cmp-dbee"
        #     ];
        #     mapping = {
        #       "<C-d>" = "cmp.mapping.scroll_docs(-4)";
        #       "<C-f>" = "cmp.mapping.scroll_docs(4)";
        #       "<C-Space>" = "cmp.mapping.complete()";
        #       "<C-e>" = "cmp.mapping.close()";
        #       "<Tab>" = "cmp.mapping(cmp.mapping.select_next_item(), {'i', 's'})";
        #       "<S-Tab>" = "cmp.mapping(cmp.mapping.select_prev_item(), {'i', 's'})";
        #       "<CR>" = "cmp.mapping.confirm({ select = true })";
        #     };
        #   };
        # };
        # Code snapshotting
        codesnap = {
          enable = true;
          settings.watermark = "";
        };
        # Formatting
        conform-nvim = {
          enable = true;
          settings = {
            formatters_by_ft = {
              javascript = [ "prettier" ];
              javascriptreact = [ "prettier" ];
              lua = [ "stylua" ];
              typescript = [ "prettier" ];
              typescriptreact = [ "prettier" ];
            };
            format_on_save.lsp_format = "fallback";
          };
        };
        # Debug Adapter Protocol
        dap = {
          enable = true;
          extensions = {
            dap-python.enable = true;
            dap-ui.enable = true;
          };
        };
        # Shareable file permalinks
        gitlinker = {
          enable = true;
          mappings = "<C-c>l";
          callbacks = {
            "bitbucket.org" = "get_bitbucket_type_url";
            "codeberg.org" = "get_gitea_type_url";
            "git.kernel.org" = "get_cgit_type_url";
            "git.launchpad.net" = "get_launchpad_type_url";
            "git.savannah.gnu.org" = "get_cgit_type_url";
            "git.sr.ht" = "get_srht_type_url";
            "github.com" = "get_github_type_url";
            "gitlab.com" = "get_gitlab_type_url";
            "repo.or.cz" = "get_repoorcz_type_url";
            "try.gitea.io" = "get_gitea_type_url";
            "try.gogs.io" = "get_gogs_type_url";
          };
        };
        # Git worktree integration
        # TODO: figure out
        git-worktree = {
          enable = true;
        };
        # Git information
        gitsigns = {
          enable = true;
          settings = {
            current_line_blame = true;
            current_line_blame_opts.delay = 300;
          };
        };
        # Language Server Protocol client
        lsp = {
          enable = true;
          keymaps = {
            extra = [
              {
                action = "<CMD>Lspsaga hover_doc<Enter>";
                key = "K";
              }
            ];
            lspBuf = {
              "<C-k>" = "signature_help";
              # "K" = "hover";
              "gD" = "references";
              "gd" = "definition";
              "gi" = "implementation";
              "gt" = "type_definition";
            };
          };
          servers = {
            bashls.enable = true;
            # TypeScript & JavaScript
            # TODO: Re-enable at some point...
            biome.enable = false;
            cssls.enable = true;
            dockerls.enable = true;
            # Generic language server proxy for multiple tools
            efm.enable = true;
            eslint.enable = true;
            gopls.enable = true;
            html.enable = true;
            jinja_lsp = {
              enable = true;
              package = pkgs.jinja-lsp;
              extraOptions = { };
            };
            jsonls.enable = true;
            # TODO: needs package
            # kulala_ls.enable = true;
            # Nix (nil with nixpkgs-fmt)
            # TODO: determine if nil or nixd is better
            # nixd = {
            #   enable = true;
            #   settings = {
            #     formatting.command = [ "${pkgs.nixfmt-rfc-style}/bin/nixfmt" ];
            #   };
            # };
            nickel_ls.enable = true;
            nil_ls = {
              enable = true;
              settings.formatting.command = [ "${pkgs.nixfmt-rfc-style}/bin/nixfmt" ];
            };
            # TODO: https://github.com/supabase-community/postgres_lsp/issues/136
            postgres_lsp = {
              enable = true;
              settings = { };
            };
            pyright.enable = true;
            ruff_lsp.enable = false;
            rust_analyzer = {
              enable = true;
              installCargo = true;
              installRustc = true;
            };
            scheme_langserver.enable = !pkgs.stdenv.isDarwin;
            # TODO: figure out project-local config
            # sqls = {
            #   enable = true;
            #   settings.sqls.connections = [
            #     {
            #       driver = "postgresql";
            #       proto = "unix";
            #       host = "/home/george/src/git.usebasis.co/basis/basis/.tmp/postgres/run/.s.PGSQL.5432";
            #       dbName = "basis_dev";
            #     }
            #   ];
            # };
            # TOML
            taplo.enable = true;
            tailwindcss.enable = true;
            # tsserver.enable = true;
            typos_lsp.enable = true;
            yamlls = {
              enable = true;
              extraOptions.settings.yaml.customTags = [
                "!And sequence"
                "!Base64 scalar"
                "!Cidr scalar"
                "!Condition scalar"
                "!Equals sequence"
                "!FindInMap sequence"
                "!GetAZs scalar"
                "!GetAtt scalar"
                "!GetAtt sequence"
                "!If sequence"
                "!ImportValue scalar"
                "!Join sequence"
                "!Not sequence"
                "!Or sequence"
                "!Ref scalar"
                "!Select sequence"
                "!Split sequence"
                "!Sub scalar"
                "!Transform mapping"
              ];
            };
          };
        };
        # LSP signature help
        # lsp-signature = {
        #   enable = true;
        #   settings.hint_scheme = "String";
        # };
        # Status line (bottom)
        lualine = {
          enable = true;
          settings.options = {
            component_separators = {
              left = "";
              right = "";
            };
            section_separators = {
              left = "";
              right = "";
            };
          };
        };
        # Symbol navigation popup
        navbuddy = {
          enable = true;
          lsp.autoAttach = true;
        };
        # File explorer
        neo-tree = {
          enable = true;
          closeIfLastWindow = true;
          filesystem = {
            filteredItems = {
              hideDotfiles = false;
              hideGitignored = false;
            };
            followCurrentFile = {
              enabled = true;
              leaveDirsOpen = true;
            };
            useLibuvFileWatcher = true;
          };
          sourceSelector.winbar = true;
          window.mappings = {
            "<A-S-[>" = "prev_source";
            "<A-S-]>" = "next_source";
          };
        };
        # Neovim git interface
        neogit = {
          enable = true;
          settings.integrations.diffview = true;
        };
        # Display colors for color codes
        nvim-colorizer = {
          enable = true;
          fileTypes.typescriptreact.tailwind = "both";
        };
        # Status column
        statuscol = {
          enable = true;
          settings = {
            relculright = true;
            ft_ignore = [
              "NeogitStatus"
              "neo-tree"
              "aerial"
            ];
            segments = [
              {
                hl = "FoldColumn";
                text = [ { __raw = "require('statuscol.builtin').foldfunc"; } ];
                click = "v:lua.ScFa";
              }
              {
                text = null;
                sign = {
                  name = [ "Diagnostic" ];
                  maxwidth = 1;
                  colwidth = 2;
                  auto = false;
                };
                click = "v:lua.ScSa";
              }
              {
                text = [
                  {
                    __raw = ''
                      function(_)
                        -- buffer being rendered
                        local buf = vim.api.nvim_win_get_buf(vim.g.statusline_winid)
                        local lines = vim.api.nvim_buf_line_count(buf)
                        local width = math.floor(math.log10(lines)) + 1

                        return " %l %=%" .. width .. "." .. width .. "r "
                      end
                    '';
                  }
                ];
                click = "v:lua.ScLa";
              }
              {
                text = null;
                sign = {
                  name = [ ".*" ];
                  namespace = [ ".*" ];
                  maxwidth = 1;
                  colwidth = 2;
                  auto = false;
                };
                click = "v:lua.ScSa";
              }
            ];
          };
        };
        # File finder (popup)
        telescope = {
          enable = true;
          settings.defaults.layout_config.preview_width = 0.5;
        };
        # Built-in terminal
        toggleterm = {
          enable = true;
          settings = {
            size = 10;
            float_opts = {
              height = 45;
              width = 170;
            };
          };
        };
        # Parser generator & incremental parsing toolkit
        treesitter = {
          enable = true;
          folding = true;
          nixvimInjections = true;
          settings = {
            highlight = {
              enable = true;
              disable = [ "alpha" ];
              additional_vim_regex_highlighting = true;
            };
            incremental_selection = {
              enable = true;
              keymaps = {
                init_selection = "gnn";
                node_incremental = "grn";
                scope_incremental = "grc";
                node_decremental = "grm";
              };
            };
          };
        };
        # Tree-sitter text objects
        # TODO: figure out
        treesitter-textobjects = {
          enable = true;
          lspInterop.enable = true;
          move = {
            enable = true;
            gotoNextStart = {
              "]]" = "@class.outer";
              "]a" = "@attribute.outer";
              "]b" = "@block.outer";
              "]c" = "@call.outer";
              "]f" = "@function.outer";
              "]i" = "@conditional.outer";
              "]p" = "@parameter.outer";
              "]s" = "@statement.outer";
              "]v" = "@assignment.outer";
            };
            gotoNextEnd = {
              "]A" = "@attribute.inner";
              "]B" = "@block.outer";
              "]C" = "@call.outer";
              "]F" = "@function.outer";
              "]I" = "@conditional.outer";
              "]P" = "@parameter.outer";
              "]S" = "@statement.outer";
              "]V" = "@assignment.outer";
              "][" = "@class.outer";
            };
            gotoPreviousStart = {
              "[[" = "@class.outer";
              "[a" = "@attribute.outer";
              "[b" = "@block.outer";
              "[c" = "@call.outer";
              "[f" = "@function.outer";
              "[i" = "@conditional.outer";
              "[p" = "@parameter.outer";
              "[s" = "@statement.outer";
              "[v" = "@assignment.outer";
            };
            gotoPreviousEnd = {
              "[A" = "@attribute.outer";
              "[B" = "@block.outer";
              "[C" = "@call.outer";
              "[F" = "@function.outer";
              "[I" = "@conditional.outer";
              "[P" = "@parameter.outer";
              "[S" = "@statement.outer";
              "[V" = "@assignment.outer";
              "[]" = "@class.outer";
            };
          };
          select = {
            enable = true;
            lookahead = true;
            keymaps = {
              "aC" = "@class.outer";
              "aa" = "@parameter.outer";
              "ab" = "@block.outer";
              "ac" = "@call.outer";
              "af" = "@function.outer";
              "ai" = "@conditional.outer";
              "al" = "@loop.outer";
              "av" = "@assignment.outer";
              "iC" = "@class.inner";
              "ia" = "@parameter.inner";
              "ib" = "@block.inner";
              "ic" = "@call.inner";
              "if" = "@function.inner";
              "ii" = "@conditional.inner";
              "il" = "@loop.inner";
              "iv" = "@assignment.inner";
              "lv" = "@assignment.lhs";
              "rv" = "@assignment.rhs";
            };
          };
        };
        # The TypeScript integration NeoVim deserves
        typescript-tools = {
          enable = true;
          settings.expose_as_code_action = "all";
        };
        # LLM integration
        avante.enable = true;
        # File / AST breadcrumbs
        barbecue.enable = true;
        # Buffer delete helper preserving window layout
        bufdelete.enable = true;
        # nvim-cmp LSP signature help source
        # cmp-nvim-lsp-signature-help = { enable = true;  };
        # Treesitter completion source for CMP
        # cmp-treesitter = { enable = true;  };
        # Code commenting
        comment.enable = true;
        # GitHub Copilot coding assistant
        copilot-vim.enable = true;
        # Diff view
        diffview.enable = true;
        # UI improvements
        dressing.enable = true;
        # LSP & notification UI
        fidget.enable = true;
        # Neovim within text input on web pages
        firenvim.enable = true;
        # Git tools
        fugitive.enable = true;
        # Git conflict resolution tooling
        git-conflict.enable = true;
        # Hex editing
        hex.enable = true;
        # Highlight other occurrences of word under cursor
        illuminate.enable = true;
        # Incremental rename
        inc-rename.enable = true;
        # Indentation guide
        indent-blankline.enable = true;
        # Snippet engine
        luasnip.enable = true;
        # LSP formatting
        lsp-format.enable = true;
        # LSP pictograms
        lspkind.enable = true;
        # Multi-faceted LSP UX improvements
        lspsaga.enable = true;
        # Markdown preview
        markdown-preview.enable = true;
        # (Neo)Vim markers enhancer
        marks.enable = true;
        # Mini library collection - alignment
        mini.modules.align = { };
        # Enable Nix language support
        nix.enable = true;
        # UI component library
        nui.enable = true;
        # Automatically manage character pairs
        nvim-autopairs.enable = true;
        # Character (sequence) pair helper
        nvim-surround.enable = true;
        # File explorer
        oil.enable = true;
        # Undo tree visualizer
        undotree.enable = true;
        # Live Markdown previre
        render-markdown.enable = true;
        # HTTP client
        rest.enable = true;
        # Tab-scoped buffers
        scope.enable = true;
        # Schemastore
        schemastore.enable = true;
        # Global search and replace
        spectre.enable = true;
        # Better split management
        smart-splits.enable = true;
        # Enable working with TODO: code comments
        todo-comments.enable = true;
        # Diagnostics, etc.
        trouble.enable = true;
        # Icons
        which-key.enable = true;
        # Keybinding hint viewer
        web-devicons.enable = true;
      };
      extraPlugins =
        with pkgs.vimPlugins;
        [
          aerial-nvim
          bufresize-nvim
          # cmp-dbee
          gitlab-nvim
          kulala-nvim
          neoconf-nvim
          nvim-treehopper
          nvim-treesitter-parsers.nickel
          nvim-treesitter-textsubjects
          overseer-nvim
          treewalker-nvim
          vim-bazel
          vim-bundle-mako
          vim-jinja
          vim-nickel
        ]
        ++ lib.lists.optionals (!pkgs.stdenv.isDarwin) [ nvim-dbee ];
      extraConfigLua =
        let
          helpers = inputs.nixvim.lib.${hostPlatform}.helpers;
          extraPluginsConfig = {
            # TODO: need a solution for keeping buffer / window sizes intact when resizing
            # surrounding terminal
            # bufresize = { };
            gitlab = { };
            nvim-surround = { };
            overseer = { };
            aerial = {
              autojump = true;
              filter_kind = false;
            };
            "nvim-treesitter.configs".textsubjects = {
              enable = true;
              rrev_selection = ",";
              keymaps = {
                "." = "textsubjects-smart";
                ";" = "textsubjects-container-outer";
                "i;" = "textsubjects-container-inner";
              };
            };
          } // (lib.attrsets.optionalAttrs (!pkgs.stdenv.isDarwin) { dbee = { }; });
        in
        (concatStringsSep "\n" (
          (mapAttrsToList (n: v: ''require("${n}").setup(${helpers.toLuaObject v})'') extraPluginsConfig)
          ++ [
            ''
              if vim.g.neovide then
                -- vim.g.neovide_scale_factor = 0.7
                vim.o.guifont = "Hack Nerd Font Mono:h10"
              end
            ''
          ]
        ));
      keymaps = [
        {
          key = ";";
          action = ":";
        }
        {
          key = "<leader>{";
          action = ":BufferLineMovePrev<CR>";
        }
        {
          key = "<leader>}";
          action = ":BufferLineMoveNext<CR>";
        }
        {
          key = "<leader>w";
          action = ":write<CR>";
        }
        {
          key = "<leader>W";
          action = ":wall<CR>";
        }
        {
          key = "<leader>x";
          action = ":Bdelete<CR>";
        }
        {
          key = "<leader>T";
          action = ":tabnew<CR>";
        }
        {
          key = "<leader>C";
          action = ":tabclose<CR>";
        }
        {
          key = "<leader>[";
          action = ":BufferLineCyclePrev<CR>";
        }
        {
          key = "<leader>]";
          action = ":BufferLineCycleNext<CR>";
        }
        {
          key = "<leader>\>";
          action = ":tabnext<CR>";
        }
        {
          key = "<leader>\<";
          action = ":tabprevious<CR>";
        }
        {
          key = "<C-A-h>";
          action = ":Treewalker Left<CR>";
        }
        {
          key = "<C-A-j>";
          action = ":Treewalker Down<CR>";
        }
        {
          key = "<C-A-k>";
          action = ":Treewalker Up<CR>";
        }
        {
          key = "<C-A-l>";
          action = ":Treewalker Right<CR>";
        }
        {
          key = "<leader>i";
          action = ":set invlist<CR>";
        }
        {
          key = "<C-l>i";
          action = ":LspInfo<CR>";
        }
        {
          key = "<C-l>r";
          action = ":LspRestart<CR>";
        }
        {
          key = "<S-s>";
          action = ":'<,'>sort<CR>";
        }
        {
          key = "<leader>c";
          action = ":ToggleTerm<CR>";
        }
        {
          key = "<leader>d";
          action = ":DiffviewOpen<CR>";
        }
        {
          key = "<leader>D";
          action = ":DiffviewClose<CR>";
        }
        {
          key = "<leader>f";
          action = ":Telescope find_files<CR>";
        }
        {
          key = "<leader>F";
          action = ":Telescope find_files hidden=true<CR>";
        }
        {
          key = "<leader>G";
          action = ":Telescope live_grep<CR>";
        }
        {
          key = "<leader>at";
          action = ":AerialToggle<CR>";
        }
        {
          key = "<leader>aO";
          action = ":AerialOpenAll<CR>";
        }
        {
          key = "<leader>aC";
          action = ":AerialCloseAll<CR>";
        }
        {
          key = "<leader>z";
          action = ":nohlsearch<CR>";
        }
        {
          key = "<leader>h";
          action.__raw = ''require("smart-splits").move_cursor_left'';
        }
        {
          key = "<leader>j";
          action.__raw = ''require("smart-splits").move_cursor_down'';
        }
        {
          key = "<leader>k";
          action.__raw = ''require("smart-splits").move_cursor_up'';
        }
        {
          key = "<leader>l";
          action.__raw = ''require("smart-splits").move_cursor_right'';
        }
        {
          key = "<leader>H";
          action.__raw = ''require("smart-splits").resize_left'';
        }
        {
          key = "<leader>J";
          action.__raw = ''require("smart-splits").resize_down'';
        }
        {
          key = "<leader>K";
          action.__raw = ''require("smart-splits").resize_up'';
        }
        {
          key = "<leader>L";
          action.__raw = ''require("smart-splits").resize_right'';
        }
        {
          key = "<leader>m";
          action = ":Telescope keymaps<CR>";
        }
        {
          key = "<leader>s";
          action = ":Neotree focus<CR>";
        }
        {
          key = "<leader>p";
          action = ":Trouble diagnostics<CR>";
        }
        {
          key = "<leader>e";
          action = ":Neotree reveal<CR>";
        }
        {
          key = "<leader>r";
          action = ":IncRename ";
        }
        {
          key = "<leader>n";
          action = ":Navbuddy<CR>";
        }
        {
          key = "<leader>t";
          action = ":Neotree toggle filesystem<CR>";
        }
        {
          key = "<leader>v";
          action = ":Neotree toggle git_status<CR>";
        }
        {
          key = "<leader>gc";
          action = ":Neogit<CR>";
        }
        {
          key = "<leader>gb";
          action = ":Neogit branch<CR>";
        }
      ];
    };
  };
}
