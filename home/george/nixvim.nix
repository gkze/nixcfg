{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (builtins)
    concatStringsSep
    head
    isAttrs
    isString
    ;
  inherit (lib.attrsets) listToAttrs mapAttrsToList;
  inherit (lib.lists) flatten;

  intersperse =
    sep: list:
    let
      go =
        xs:
        if xs == [ ] then
          [ ]
        else if builtins.length xs == 1 then
          [ (head xs) ]
        else
          [
            (head xs)
            sep
          ]
          ++ go (builtins.tail xs);
    in
    go list;

  keymapData = import ./nvim-keymaps.nix;
  helpers = config.lib.nixvim;

  scopeSectionTitles = scope: map (section: section.title) scope.sections;

  sectionItems =
    scope: sectionNames:
    flatten (
      map (
        section: if builtins.elem section.title sectionNames then section.items else [ ]
      ) scope.sections
    );

  mkKeymapListFromSections =
    scope: sectionNames:
    map (item: {
      inherit (item) key;
      inherit (item) action;
      mode = item.mode or "n";
      options = {
        desc = item.desc or item.summary or "";
      };
    }) (sectionItems scope sectionNames);

  mkKeymapList = scope: mkKeymapListFromSections scope (scopeSectionTitles scope);

  mkAttrsetFromItems =
    items:
    listToAttrs (
      map (item: {
        name = item.key;
        value = item.action;
      }) items
    );

  mkAttrsetFromSections = scope: sectionNames: mkAttrsetFromItems (sectionItems scope sectionNames);

  mkAttrset = scope: mkAttrsetFromSections scope (scopeSectionTitles scope);

  mkNestedAttrset =
    scope:
    listToAttrs (
      map (section: {
        name = section.title;
        value = mkAttrsetFromItems section.items;
      }) scope.sections
    );

  itemDisplayAction =
    item:
    item.displayAction or (
      if isString item.action then
        item.action
      else if isAttrs item.action && item.action ? __raw then
        item.action.__raw
      else
        "<lua>"
    );

  flattenPickerEntries =
    scopes:
    flatten (
      map (
        scope:
        flatten (
          map (
            section:
            map (item: {
              scope = scope.label;
              section = section.title;
              attrPath = concatStringsSep "." scope.attrPath;
              inherit (scope) kind;
              context = scope.context or "";
              inherit (item) key;
              mode = item.mode or "n";
              displayAction = itemDisplayAction item;
              desc = item.desc or item.summary or "";
            }) section.items
          ) scope.sections
        )
      ) scopes
    );

  renderScope =
    scope:
    let
      contextLine =
        if scope ? context && scope.context != "" then "Context: ${scope.context}\n\n" else "";
      renderItem =
        item:
        "- `${item.key}` (`${item.mode or "n"}`) → `${itemDisplayAction item}` — ${
          item.desc or item.summary or ""
        }";
    in
    ''
      ## ${scope.label}

      Attr path: `${concatStringsSep "." scope.attrPath}`

      ${contextLine}${
        concatStringsSep "\n\n" (
          map (section: ''
            ### ${section.title}

            ${concatStringsSep "\n" (map renderItem section.items)}
          '') scope.sections
        )
      }
    '';

  pickerScopes = [
    keymapData.global
    keymapData.lsp
    keymapData.treesitterSelection
    keymapData.treesitterTextobjectsMove
    keymapData.treesitterTextobjectsSelect
    keymapData.blinkCmp
    keymapData.telescope
    keymapData.gitlinker
    keymapData.alpha
  ];

  globalKeymaps = mkKeymapList keymapData.global;
  lspExtraKeymaps = mkKeymapListFromSections keymapData.lsp [ "Docs / diagnostics" ];
  lspBufKeymaps = mkAttrsetFromSections keymapData.lsp [ "Navigation" ];
  treesitterSelectionKeymaps = mkAttrset keymapData.treesitterSelection;
  treesitterTextobjectsMoveMappings = mkNestedAttrset keymapData.treesitterTextobjectsMove;
  treesitterTextobjectsSelectKeymaps = mkAttrset keymapData.treesitterTextobjectsSelect;
  blinkCmpKeymaps = mkAttrset keymapData.blinkCmp;
  telescopeEnterRaw = (head (sectionItems keymapData.telescope [ "Prompt" ])).action.__raw;
  gitlinkerMapping = (head (sectionItems keymapData.gitlinker [ "Linking" ])).action;
  alphaButtons = sectionItems keymapData.alpha [ "Buttons" ];
  alphaLayout =
    let
      button = item: {
        type = "button";
        val = item.label or item.desc or item.key;
        on_press.__raw = "function() vim.cmd[[${item.action}]] end";
        opts = {
          shortcut = item.key;
          align_shortcut = "right";
          keymap = [
            "n"
            item.key
            ":${item.action}<CR>"
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
      buttons = intersperse (padding 1) (map button alphaButtons);
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
        val = buttons;
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
  pickerEntries = flattenPickerEntries pickerScopes;
  keymapsDoc = ''
    # George's Neovim keymap cheat sheet

    Generated from `home/george/nvim-keymaps.nix`.
  ''
  + "\n\n"
  + concatStringsSep "\n\n" (map renderScope pickerScopes);
  keymapsLua = ''
    local entries = ${helpers.toLuaObject pickerEntries}
    local M = {}

    local doc_path = vim.fn.stdpath("config") .. "/doc/nvim-keymaps.md"

    local function open_doc()
      vim.cmd.edit(doc_path)
    end

    function M.open_doc()
      open_doc()
    end

    function M.pick()
      local pickers = require("telescope.pickers")
      local finders = require("telescope.finders")
      local previewers = require("telescope.previewers")
      local conf = require("telescope.config").values
      local actions = require("telescope.actions")
      local action_state = require("telescope.actions.state")

      pickers.new({}, {
        prompt_title = "Neovim keymaps",
        finder = finders.new_table({
          results = entries,
          entry_maker = function(e)
            return {
              value = e,
              ordinal = table.concat({ e.scope or "", e.section or "", e.key or "", e.desc or "" }, " "),
              display = string.format("[%s] %s — %s", e.scope or "?", e.key or "", e.desc or ""),
            }
          end,
        }),
        sorter = conf.generic_sorter({}),
        previewer = previewers.new_buffer_previewer({
          define_preview = function(self, entry)
            local e = entry.value
            local lines = {
              "Scope: " .. (e.scope or ""),
              "Section: " .. (e.section or ""),
              "Attr path: " .. (e.attrPath or ""),
              "Kind: " .. (e.kind or ""),
              "Context: " .. (e.context or ""),
              "Mode: " .. (e.mode or ""),
              "Key: " .. (e.key or ""),
              "Action: " .. (e.displayAction or ""),
              "Description: " .. (e.desc or ""),
            }
            vim.api.nvim_buf_set_lines(self.state.bufnr, 0, -1, false, lines)
          end,
        }),
        attach_mappings = function(prompt_bufnr, map)
          actions.select_default:replace(function()
            local selection = action_state.get_selected_entry()
            actions.close(prompt_bufnr)
            if selection and selection.value then
              open_doc()
            end
          end)
          return true
        end,
      }):find()
    end

    return M
  '';
in
{
  programs.nixvim = {
    config = {
      enable = true;
      enableMan = true;
      files."ftplugin/json.lua".opts.shiftwidth = 2;
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
          flavour = config.theme.variant;
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
          settings.layout = alphaLayout;
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
            keymap = blinkCmpKeymaps;
            signature.enabled = true;
          };
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
        codesnap = {
          enable = true;
          package = pkgs.vimPlugins.codesnap-nvim;
          settings = {
            snapshot_config = {
              watermark = "none";
              code_config = {
                font_family = config.fonts.monospace.name;
                breadcrumbs.font_family = config.fonts.monospace.name;
              };
            };
          };
        };
        conform-nvim = {
          enable = true;
          settings = {
            formatters =
              let
                ruffCmd = lib.getExe pkgs.ruff;
              in
              {
                biome.command = lib.getExe pkgs.biome;
                prettier.command = lib.getExe pkgs.nodePackages_latest.prettier;
                ruff_fix.command = ruffCmd;
                ruff_format.command = ruffCmd;
                ruff_organize_imports.command = ruffCmd;
                jsonnetfmt.command = lib.getExe' pkgs.jsonnet "jsonnetfmt";
                taplo.command = lib.getExe pkgs.taplo;
              };
            formatters_by_ft = {
              jsonnet = [ "jsonnetfmt" ];
              html = [ "biome" ];
              javascript = [ "biome" ];
              javascriptreact = [ "biome" ];
              json = [ "biome" ];
              lua = [ "stylua" ];
              python = [
                "ruff_fix"
                "ruff_format"
                "ruff_organize_imports"
              ];
              toml = [ "taplo" ];
              typescript = [ "prettier" ];
              typescriptreact = [ "prettier" ];
            };
          };
        };
        gitlinker = {
          enable = true;
          settings = {
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
            opts.mappings = gitlinkerMapping;
          };
        };
        gitsigns = {
          enable = true;
          settings = {
            current_line_blame = true;
            current_line_blame_opts.delay = 300;
          };
        };
        highlight-colors = {
          enable = true;
          settings.enable_tailwind = true;
        };
        lsp = {
          enable = true;
          keymaps = {
            extra = lspExtraKeymaps;
            lspBuf = lspBufKeymaps;
          };
          servers = {
            bashls.enable = true;
            biome.enable = true;
            cssls.enable = true;
            dockerls.enable = true;
            # efm.enable = true;
            gopls.enable = true;
            html.enable = true;
            # jinja_lsp = {
            #   enable = true;
            #   package = pkgs.jinja-lsp;
            # };
            jsonnet_ls.enable = true;
            jsonls = {
              enable = true;
              # Use Biome formatter instead to avoid LSP conflicts
              extraOptions.settings.json = {
                format.enable = false;
                schemas.__raw = "require('schemastore').json.schemas()";
                validate.enable = true;
              };
            };
            lua_ls.enable = true;
            nickel_ls.enable = true;
            nil_ls = {
              enable = true;
              settings.formatting.command = [ (lib.getExe pkgs.nixfmt) ];
            };
            # nixd = {
            #   enable = true;
            #   settings.formatting.command = [ (lib.getExe pkgs.nixfmt) ];
            # };
            postgres_lsp = {
              enable = true;
              settings = { };
            };
            ty.enable = true;
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
                indent_string = "  ";
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
          settings = {
            options = {
              component_separators = {
                left = "";
                right = "";
              };
              section_separators = {
                left = "";
                right = "";
              };
            };
            # Avoid lualine's branch component, which creates a fs_event watcher
            # on .git/HEAD and appears to contribute to uv_loop_close hangs.
            sections.lualine_b = [
              "diff"
              "diagnostics"
            ];
          };
        };
        navbuddy = {
          enable = true;
          settings.lsp.auto_attach = true;
        };
        neo-tree = {
          enable = true;
          settings = {
            close_if_last_window = true;
            filesystem = {
              filtered_items = {
                hide_dotfiles = false;
                hide_gitignored = false;
                hide_ignored = false;
                hide_hidden = false;
              };
              follow_current_file = {
                enabled = true;
                leave_dirs_open = true;
              };
              use_libuv_file_watcher = true;
            };
            source_selector.winbar = true;
          };
        };
        neogit = {
          enable = true;
          settings = {
            process_spinner = false;
            integrations.diffview = true;
          };
        };
        schemastore = {
          enable = true;
          json.enable = false;
          yaml.enable = true;
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
        telescope = {
          enable = true;
          settings.defaults = {
            layout_config.preview_width = 0.5;
            mappings.i."<CR>".__raw = telescopeEnterRaw;
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
          folding.enable = true;
          nixvimInjections = true;
          settings = {
            highlight = {
              enable = true;
              disable = [ "alpha" ];
              additional_vim_regex_highlighting = true;
            };
            incremental_selection = {
              enable = true;
              keymaps = treesitterSelectionKeymaps;
            };
          };
        };
        treesitter-textobjects = {
          enable = true;
          settings = {
            lsp_interop.enable = true;
            move = {
              enable = true;
            }
            // treesitterTextobjectsMoveMappings;
            select = {
              enable = true;
              lookahead = true;
              keymaps = treesitterTextobjectsSelectKeymaps;
            };
          };
        };
        typescript-tools = {
          enable = true;
          settings.expose_as_code_action = "all";
        };
        aerial = {
          enable = true;
          settings.filter_kind = false;
        };
        avante.enable = false;
        codecompanion = {
          enable = true;
          settings = {
            strategies = {
              chat.adapter = "anthropic";
              inline.adapter = "anthropic";
              agent.adapter = "anthropic";
            };
          };
        };
        barbecue.enable = true;
        bufdelete.enable = true;
        comment.enable = true;
        dap-python.enable = true;
        dap-ui.enable = true;
        dap.enable = true;
        diffview.enable = true;
        dressing.enable = true;
        fidget.enable = true;
        firenvim.enable = true;
        fugitive.enable = true;
        fzf-lua.enable = true;
        git-conflict.enable = true;
        git-worktree.enable = true;
        hex.enable = true;
        illuminate.enable = true;
        inc-rename.enable = true;
        indent-blankline.enable = true;
        # Disabled: using rest.nvim instead (see rest.enable below)
        # kulala.enable = true;
        lazydev.enable = true;
        lsp-format.enable = true;
        lspsaga.enable = true;
        luasnip.enable = true;
        markdown-preview.enable = true;
        marks.enable = true;
        mini.modules.align = { };
        neoconf.enable = true;
        nix.enable = true;
        nui.enable = true;
        nvim-autopairs.enable = true;
        nvim-surround.enable = true;
        octo.enable = true;
        oil.enable = true;
        orgmode.enable = true;
        overseer.enable = true;
        render-markdown.enable = true;
        rest.enable = true;
        scope.enable = true;
        smart-splits.enable = true;
        spectre.enable = true;
        todo-comments.enable = true;
        trouble.enable = true;
        undotree.enable = true;
        web-devicons.enable = true;
        which-key.enable = true;
      };
      extraLuaPackages =
        luaPkgs: with luaPkgs; [
          # rest.nvim optional dependencies; without these Neovim warns on startup.
          mimetypes
          xml2lua
        ];
      extraPlugins =
        with pkgs.vimPlugins;
        [
          # bufresize-nvim disabled: messes up window sizing with Zellij pane focus changes
          nvim-treesitter-parsers.kdl
          nvim-treesitter-parsers.nickel
          nvim-treesitter.queries.ecma # Required for JS/TS keyword highlighting (inherited queries)
          nvim-treesitter.queries.jsx # Required for JSX/TSX highlighting (inherited queries)
          opencode-nvim
          treewalker-nvim
          vim-bazel
          vim-bundle-mako
          vim-jinja
          vim-nickel
        ]
        ++ lib.lists.optionals (!pkgs.stdenv.isDarwin) [ nvim-dbee ];
      extraConfigLuaPost =
        let
          helpers = config.lib.nixvim;
          extraPluginsConfig = {
            nvim-surround = { };
            overseer = { };
            # nvim-treesitter-textsubjects disabled: incompatible with newer nvim-treesitter API
          }
          // (lib.attrsets.optionalAttrs (!pkgs.stdenv.isDarwin) { dbee = { }; });
        in
        concatStringsSep "\n" (
          (mapAttrsToList (n: v: ''require("${n}").setup(${helpers.toLuaObject v})'') extraPluginsConfig)
          ++ [
            ''
              vim.g.opencode_opts = vim.g.opencode_opts or {}
              vim.o.autoread = true
              vim.api.nvim_create_user_command("NvimKeymaps", function()
                require("nvim-keymaps").pick()
              end, {})
              vim.api.nvim_create_user_command("NvimKeymapsDoc", function()
                require("nvim-keymaps").open_doc()
              end, {})
            ''
            ''
              if vim.g.neovide then
                -- vim.g.neovide_scale_factor = 0.7
                vim.o.guifont = "${config.fonts.monospace.name}:h10"
              end
            ''
          ]
        );
      keymaps = globalKeymaps;
    };
  };

  home.file = {
    ".config/nvim/doc/nvim-keymaps.md".text = keymapsDoc;
    ".config/nvim/lua/nvim-keymaps.lua".text = keymapsLua;
  };
}
