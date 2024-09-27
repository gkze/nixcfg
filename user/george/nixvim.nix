{ lib, pkgs, inputs, hostPlatform, ... }:
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
      options = {
        # Text width helper
        colorcolumn = [ 80 100 ];
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
        # Rulers at 80 and 100 characters
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
        # Keep sign column rendered so that errors popping up don't trigger a
        # redraw
        signcolumn = "yes";
      };
      autoCmd = [
        {
          event = [ "LspAttach" ];
          callback.__raw = ''
            function(args)
              local client = vim.lsp.get_client_by_id(args.data.client_id)
              require("lsp_signature").on_attach({ bind = true }, args.buf)
            end
          '';
        }
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
            cmp = true;
            dap = { enabled = true; enable_ui = true; };
            gitsigns = true;
            lsp_saga = true;
            native_lsp = { enabled = true; inlay_hints.background = true; };
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
                  keymap = [ "n" shortcut ":${cmd}<CR>" { } ];
                  position = "center";
                  width = 50;
                };
              };
              padding = v: { type = "padding"; val = v; opts.position = "center"; };
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
                opts = { position = "center"; hl = "Type"; };
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
                opts = { position = "center"; hl = "Keyword"; };
              }
            ];
        };
        # Buffer line (top, tabs)
        bufferline = {
          enable = true;
          settings.options = {
            diagnostics = "nvim_lsp";
            enforce_regular_tabs = false;
            offsets = [{
              filetype = "neo-tree";
              text = "Neo-tree";
              separator = true;
              textAlign = "left";
            }];
          };
        };
        # LSP completion
        cmp = {
          enable = true;
          settings = {
            extraOptions.autoEnableSources = true;
            snippet.expand = ''
              function(args)
                require('luasnip').lsp_expand(args.body)
              end
            '';
            sources = map (s: { name = s; }) [
              "nvim_lsp"
              "treesitter"
              "luasnip"
              "path"
              "buffer"
            ];
            mapping = {
              "<C-d>" = "cmp.mapping.scroll_docs(-4)";
              "<C-f>" = "cmp.mapping.scroll_docs(4)";
              "<C-Space>" = "cmp.mapping.complete()";
              "<C-e>" = "cmp.mapping.close()";
              "<Tab>" = "cmp.mapping(cmp.mapping.select_next_item(), {'i', 's'})";
              "<S-Tab>" = "cmp.mapping(cmp.mapping.select_prev_item(), {'i', 's'})";
              "<CR>" = "cmp.mapping.confirm({ select = true })";
            };
          };
        };
        # Formatting
        conform-nvim = {
          enable = true;
          settings = {
            formatters_by_ft = {
              javascript = [ "prettier" ];
              javascriptreact = [ "prettier" ];
              typescript = [ "prettier" ];
              typescriptreact = [ "prettier" ];
            };
            format_on_save.lsp_format = "fallback";
          };
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
          keymaps.lspBuf = {
            "<C-k>" = "signature_help";
            "K" = "hover";
            "gD" = "references";
            "gd" = "definition";
            "gi" = "implementation";
            "gt" = "type_definition";
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
            jsonls.enable = true;
            # Nix (nil with nixpkgs-fmt)
            # TODO: determine if nil or nixd is better
            # nixd = {
            #   enable = true;
            #   settings.formatting.command = "${pkgs.nixpkgs-fmt}/bin/nixpkgs-fmt";
            # };
            nickel-ls.enable = true;
            nil-ls = {
              enable = true;
              settings.formatting.command = [ "${pkgs.nixpkgs-fmt}/bin/nixpkgs-fmt" ];
            };
            pyright.enable = true;
            ruff-lsp.enable = false;
            rust-analyzer = {
              enable = true;
              installCargo = true;
              installRustc = true;
            };
            # TOML
            taplo.enable = true;
            tailwindcss.enable = true;
            # tsserver.enable = true;
            typos-lsp.enable = true;
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
        # Status line (bottom)
        lualine = {
          enable = true;
          settings.options = {
            component_separators = { left = ""; right = ""; };
            section_separators = { left = ""; right = ""; };
          };
        };
        # File explorer
        neo-tree = {
          enable = true;
          closeIfLastWindow = true;
          filesystem = {
            filteredItems = { hideDotfiles = false; hideGitignored = false; };
            followCurrentFile = { enabled = true; leaveDirsOpen = true; };
            useLibuvFileWatcher = true;
          };
          sourceSelector.winbar = true;
          window.mappings = { "<A-S-{>" = "prev_source"; "<A-S-}>" = "next_source"; };
        };
        # Display colors for color codes
        nvim-colorizer = {
          enable = true;
          fileTypes = [{ language = "typescriptreact"; tailwind = "both"; }];
        };
        # Status column
        statuscol = {
          enable = true;
          settings = {
            relculright = true;
            ft_ignore = [ "NeogitStatus" "neo-tree" "aerial" ];
            segments = [
              {
                hl = "FoldColumn";
                text = [{ __raw = "require('statuscol.builtin').foldfunc"; }];
                click = "v:lua.ScFa";
              }
              {
                text = null;
                sign = {
                  name = [ ".*" ];
                  namespace = [ ".*" ];
                  text = [ ".*" ];
                  maxwidth = 1;
                  colwidth = 1;
                  auto = false;
                };
                click = "v:lua.ScSa";
              }
              { text = [ " %l %=%r " ]; click = "v:lua.ScLa"; }
              {
                text = null;
                sign = {
                  name = [ ".*" ];
                  maxwidth = 1;
                  colwidth = 1;
                  auto = true;
                  wrap = true;
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
            float_opts = { height = 45; width = 170; };
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
              "]m" = "@function.outer";
              "]v" = "@assignment.outer";
              "]c" = "@call.outer";
              "]b" = "@block.outer";
              "]s" = "@statement.outer";
              "]i" = "@conditional.outer";
            };
            gotoNextEnd = {
              "]M" = "@function.outer";
              "][" = "@class.outer";
              "]V" = "@assignment.outer";
              "]C" = "@call.outer";
              "]B" = "@block.outer";
              "]S" = "@statement.outer";
              "]I" = "@conditional.outer";
            };
            gotoPreviousStart = {
              "[[" = "@class.outer";
              "[m" = "@function.outer";
              "[v" = "@assignment.outer";
              "[c" = "@call.outer";
              "[b" = "@block.outer";
              "[s" = "@statement.outer";
              "[i" = "@conditional.outer";
            };
            gotoPreviousEnd = {
              "[M" = "@function.outer";
              "[]" = "@class.outer";
              "[V" = "@assignment.outer";
              "[C" = "@call.outer";
              "[B" = "@block.outer";
              "[S" = "@statement.outer";
              "[I" = "@conditional.outer";
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
        typescript-tools = { enable = true; settings.exposeAsCodeAction = "all"; };
        # File / AST breadcrumbs
        barbecue.enable = true;
        # nvim-cmp LSP signature help source
        # cmp-nvim-lsp-signature-help.enable = true;
        # Treesitter completion source for CMP
        cmp-treesitter.enable = true;
        # Code commenting
        comment.enable = true;
        # GitHub Copilot coding assistant
        copilot-vim.enable = true;
        # Debug Adapter Protocol
        dap.enable = true;
        # Diff view
        diffview.enable = true;
        # UI improvements
        dressing.enable = true;
        # LSP & notification UI
        fidget.enable = true;
        # Shareable file permalinks
        gitlinker.enable = true;
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
        # Symbol navigation popup
        navbuddy = { enable = true; lsp.autoAttach = true; };
        # Neovim git interface
        neogit = { enable = true; settings.integrations.diffview = true; };
        # Enable Nix language support
        nix.enable = true;
        # Automatically manage character pairs
        nvim-autopairs.enable = true;
        # File explorer
        oil.enable = true;
        # Schemastore
        schemastore.enable = true;
        # Better split management
        smart-splits.enable = true;
        # Enable working with TODO: code comments
        todo-comments.enable = true;
        # Code context via Treesitter
        # treesitter-context.enable = true;
        # Diagnostics, etc. 
        trouble.enable = true;
        # Icons
        web-devicons.enable = true;
        # Keybinding hint viewer
        which-key.enable = true;
      };
      extraPlugins = with pkgs.vimPlugins; [
        aerial-nvim
        bufdelete-nvim
        bufresize-nvim
        codesnap-nvim
        firenvim
        git-conflict-nvim
        gitlab-nvim
        lsp-signature-nvim
        nui-nvim
        nvim-dbee
        nvim-surround
        nvim-treeclimber
        nvim-treesitter-textsubjects
        overseer-nvim
        render-markdown-nvim
        vim-bazel
        vim-bundle-mako
        vim-jinja
      ];
      extraConfigLua =
        let
          helpers = inputs.nixvim.lib.${hostPlatform}.helpers;
          extraPluginsConfig = {
            # TODO: enable once figured out
            # bufresize = { };
            codesnap = {
              code_font_family = "Hack Nerd Font Mono";
              has_breadcrumbs = true;
              save_path = "~/Pictures";
              watermark = "";
            };
            git-conflict = { };
            gitlab = { };
            nvim-surround = { };
            nvim-treeclimber = { };
            overseer = { };
            render-markdown = { };
            aerial = {
              autojump = true;
              filter_kind = false;
              open_automatic = true;
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
          };
        in
        (concatStringsSep "\n"
          ((mapAttrsToList (n: v: "require(\"${n}\").setup(${helpers.toLuaObject v})") extraPluginsConfig)
            ++ [
            "if vim.g.neovide then vim.g.neovide_scale_factor = 0.7 end"
            ''
              lspconfig = require('lspconfig')
              lspconfig.postgres_lsp.setup({
                root_dir = lspconfig.util.root_pattern 'flake.nix'
              })
            ''
          ]));
      keymaps = [
        { key = ";"; action = ":"; }
        { key = "<A-S-(>"; action = ":BufferLineMovePrev<CR>"; }
        { key = "<A-S-)>"; action = ":BufferLineMoveNext<CR>"; }
        { key = "<A-W>"; action = ":wall<CR>"; }
        { key = "<A-w>"; action = ":write<CR>"; }
        { key = "<A-w>"; action = ":write<CR>"; }
        { key = "<A-x>"; action = ":Bdelete<CR>"; }
        { key = "<A-S-{>"; action = ":BufferLineCyclePrev<CR>"; }
        { key = "<A-S-}>"; action = ":BufferLineCycleNext<CR>"; }
        { key = "<C-l>"; action = ":set invlist<CR>"; }
        { key = "<C-l>i"; action = ":LspInfo<CR>"; }
        { key = "<C-l>r"; action = ":LspRestart<CR>"; }
        { key = "<S-f>"; action = ":ToggleTerm direction=float<CR>"; } # TODO: figure out how to resize
        { key = "<S-s>"; action = ":sort<CR>"; }
        { key = "<S-t>"; action = ":ToggleTerm<CR>"; }
        { key = "<leader>D"; action = ":DiffviewClose<CR>"; }
        { key = "<leader>F"; action = ":Telescope find_files hidden=true<CR>"; }
        { key = "<leader>H"; action = ":wincmd 2h<CR>"; }
        { key = "<leader>J"; action = ":wincmd 2j<CR>"; }
        { key = "<leader>K"; action = ":wincmd 2k<CR>"; }
        { key = "<leader>L"; action = ":wincmd 2l<CR>"; }
        { key = "<leader>a"; action = ":AerialToggle<CR>"; }
        { key = "<leader>b"; action = ":Neotree toggle buffers<CR>"; }
        { key = "<leader>c"; action = ":nohlsearch<CR>"; }
        { key = "<leader>d"; action = ":DiffviewOpen<CR>"; }
        { key = "<leader>de"; action = ":TodoTelescope<CR>"; }
        { key = "<leader>dl"; action = ":TodoLocList<CR>"; }
        { key = "<leader>dr"; action = ":TodoTrouble<CR>"; }
        { key = "<leader>f"; action = ":Telescope find_files<CR>"; }
        { key = "<leader>g"; action = ":Telescope live_grep<CR>"; options.nowait = true; }
        { key = "<leader>h"; action = ":wincmd h<CR>"; }
        { key = "<leader>j"; action = ":wincmd j<CR>"; }
        { key = "<leader>k"; action = ":wincmd k<CR>"; }
        { key = "<leader>l"; action = ":wincmd l<CR>"; }
        { key = "<leader>m"; action = ":Telescope keymaps<CR>"; }
        { key = "<leader>n"; action = ":Neotree focus<CR>"; }
        { key = "<leader>p"; action = ":Trouble diagnostics<CR>"; }
        { key = "<leader>r"; action = ":Neotree reveal<CR>"; }
        { key = "<leader>rn"; action = ":IncRename "; }
        { key = "<leader>s"; action = ":Navbuddy<CR>"; }
        { key = "<leader>t"; action = ":Neotree toggle filesystem<CR>"; }
        { key = "<leader>v"; action = ":Neotree toggle git_status<CR>"; }
        { key = "<leader>x"; action = ":Neogit<CR>"; }
        { key = "<leader>z"; action = ":Neogit branch<CR>"; }
      ];
    };
  };
}
