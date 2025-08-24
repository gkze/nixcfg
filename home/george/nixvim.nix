{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (builtins) concatStringsSep;
  inherit (lib.attrsets) mapAttrsToList;
in
{
  programs.nixvim = {
    config = {
      enable = true;
      enableMan = true;
      globals.mapleader = " ";
      opts = {
        colorcolumn = [
          80
          100
        ];
        cursorline = true;
        cursorcolumn = true;
        expandtab = true;
        exrc = true;
        foldlevel = 99;
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
        mouse = "a";
        number = true;
        relativenumber = true;
        list = true;
        listchars = {
          eol = "↵";
          extends = ">";
          nbsp = "°";
          precedes = "<";
          space = "·";
          tab = ">-";
          trail = ".";
        };
        updatetime = 200;
        shiftwidth = 4;
        signcolumn = "yes";
        softtabstop = 4;
        tabstop = 4;
      };
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
      editorconfig.enable = true;
      plugins = {
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
              ghost_text.enabled = true;
              trigger.prefetch_on_insert = true;
              documentation = {
                auto_show = true;
                auto_show_delay_ms = 100;
              };
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
        codesnap = {
          enable = true;
          settings.watermark = "";
        };
        conform-nvim = {
          enable = true;
          settings = {
            formatters =
              let
                ruffCmd = lib.getExe pkgs.ruff;
              in
              {
                prettier.command = lib.getExe pkgs.nodePackages_latest.prettier;
                ruff_fix.command = ruffCmd;
                ruff_format.command = ruffCmd;
                ruff_organize_imports.command = ruffCmd;
              };
            formatters_by_ft = {
              html = [ "prettier" ];
              javascript = [ "prettier" ];
              javascriptreact = [ "prettier" ];
              lua = [ "stylua" ];
              python = [
                "ruff_fix"
                "ruff_format"
                "ruff_organize_imports"
              ];
              typescript = [ "prettier" ];
              typescriptreact = [ "prettier" ];
            };
            format_on_save.lsp_format = "fallback";
          };
        };
        dap.enable = true;
        dap-python.enable = true;
        dap-ui.enable = true;
        fzf-lua.enable = true;
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
        git-worktree.enable = true;
        gitsigns = {
          enable = true;
          settings = {
            current_line_blame = true;
            current_line_blame_opts.delay = 300;
          };
        };
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
              "gD" = "references";
              "gd" = "definition";
              "gi" = "implementation";
              "gt" = "type_definition";
            };
          };
          servers = {
            bashls.enable = true;
            biome.enable = false;
            cssls.enable = true;
            dockerls.enable = true;
            # Testing Taplo
            # efm.enable = true;
            gopls.enable = true;
            html.enable = true;
            # jinja_lsp = {
            #   enable = true;
            #   package = pkgs.jinja-lsp;
            # };
            jsonls = {
              enable = true;
              extraOptions.settings.json = {
                schemas.__raw = "require('schemastore').json.schemas()";
                validate.enable = true;
              };
            };
            lua_ls.enable = true;
            nickel_ls.enable = true;
            nil_ls = {
              enable = true;
              settings.formatting.command = [ (lib.getExe pkgs.nixfmt-rfc-style) ];
            };
            postgres_lsp = {
              enable = true;
              settings = { };
            };
            pyright.enable = true;
            ruff.enable = true;
            rust_analyzer = {
              enable = true;
              installCargo = true;
              installRustc = true;
            };
            scheme_langserver.enable = !pkgs.stdenv.isDarwin;
            taplo = {
              enable = true;
              settings.formatting = {
                reorder_keys = true;
                reorder_arrays = true;
              };
            };
            tailwindcss.enable = true;
            typos_lsp.enable = true;
            yamlls = {
              enable = true;
              # extraOptions.settings.yaml.customTags = [
              #   "!And sequence"
              #   "!Base64 scalar"
              #   "!Cidr scalar"
              #   "!Condition scalar"
              #   "!Equals sequence"
              #   "!FindInMap sequence"
              #   "!GetAZs scalar"
              #   "!GetAtt scalar"
              #   "!GetAtt sequence"
              #   "!If sequence"
              #   "!ImportValue scalar"
              #   "!Join sequence"
              #   "!Not sequence"
              #   "!Or sequence"
              #   "!Ref scalar"
              #   "!Select sequence"
              #   "!Split sequence"
              #   "!Sub scalar"
              #   "!Transform mapping"
              # ];
            };
          };
        };
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
        navbuddy = {
          enable = true;
          lsp.autoAttach = true;
        };
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
          window.mappings.Z = "expand_all_nodes";
        };
        neogit = {
          enable = true;
          settings = {
            process_spinner = false;
            integrations.diffview = true;
          };
        };
        highlight-colors = {
          enable = true;
          settings.enable_tailwind = true;
        };
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
                        if vim.bo.filetype == "alpha" then
                          return ""
                        end

                        return " %{v:lnum} %=%{v:relnum} "
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
        schemastore = {
          enable = true;
          json.enable = false;
          yaml.enable = true;
        };
        telescope = {
          enable = true;
          settings.defaults = {
            layout_config.preview_width = 0.5;
            mappings.i."<CR>".__raw = ''
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
              end
            '';
          };
        };
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
        typescript-tools = {
          enable = true;
          settings.expose_as_code_action = "all";
        };
        # TODO: re-enable
        avante.enable = false;
        barbecue.enable = true;
        bufdelete.enable = true;
        comment.enable = true;
        diffview.enable = true;
        dressing.enable = true;
        fidget.enable = true;
        firenvim.enable = true;
        fugitive.enable = true;
        git-conflict.enable = true;
        hex.enable = true;
        illuminate.enable = true;
        inc-rename.enable = true;
        indent-blankline.enable = true;
        lazydev.enable = true;
        luasnip.enable = true;
        lsp-format.enable = true;
        lspsaga.enable = true;
        markdown-preview.enable = true;
        marks.enable = true;
        mini.modules.align = { };
        nix.enable = true;
        nui.enable = true;
        nvim-autopairs.enable = true;
        nvim-surround.enable = true;
        octo.enable = true;
        oil.enable = true;
        undotree.enable = true;
        render-markdown.enable = true;
        rest.enable = true;
        scope.enable = true;
        spectre.enable = true;
        smart-splits.enable = true;
        todo-comments.enable = true;
        trouble.enable = true;
        which-key.enable = true;
        web-devicons.enable = true;
      };
      extraPlugins =
        with pkgs.vimPlugins;
        [
          aerial-nvim
          kulala-nvim
          neoconf-nvim
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
          helpers = config.lib.nixvim;
          extraPluginsConfig = {
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
          }
          // (lib.attrsets.optionalAttrs (!pkgs.stdenv.isDarwin) { dbee = { }; });
        in
        concatStringsSep "\n" (
          (mapAttrsToList (n: v: ''require("${n}").setup(${helpers.toLuaObject v})'') extraPluginsConfig)
          ++ [
            ''
              if vim.g.neovide then
                -- vim.g.neovide_scale_factor = 0.7
                vim.o.guifont = "Hack Nerd Font Mono:h10"
              end
            ''
          ]
        );
      keymaps = [
        {
          key = ";";
          action = ":";
        }
        {
          key = "<leader>\<";
          action = ":BufferLineMovePrev<CR>";
        }
        {
          key = "<leader>\>";
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
          key = "<leader>,";
          action = ":BufferLineCyclePrev<CR>";
        }
        {
          key = "<leader>.";
          action = ":BufferLineCycleNext<CR>";
        }
        {
          key = "<leader>]";
          action = ":tabnext<CR>";
        }
        {
          key = "<leader>[";
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
