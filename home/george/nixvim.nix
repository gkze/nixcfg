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
  inherit (lib) intersperse;

  keymapData = import ./nvim-keymaps.nix;
  helpers = config.lib.nixvim;
  oxfmtDefaultConfigText = ''
    {
      // Neovim-wide fallback when a project does not ship Oxfmt config.
      "printWidth": 80,
      "tabWidth": 2,
      "useTabs": false,
      "semi": true,
      "singleQuote": false,
      "jsxSingleQuote": false,
      "trailingComma": "all",
      "quoteProps": "as-needed",
      "arrowParens": "always",
      "bracketSpacing": true,
      "endOfLine": "lf"
    }
  '';
  oxfmtDefaultConfigPath = pkgs.writeText "nixcfg-oxfmt-defaults.jsonc" oxfmtDefaultConfigText;
  tsgolintCmd = lib.getExe pkgs.tsgolint;

  scopeSectionTitles = scope: map (section: section.title) scope.sections;
  itemMode = scope: item: item.mode or (scope.mode or "n");

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
      mode = itemMode scope item;
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
              mode = itemMode scope item;
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
        "- `${item.key}` (`${itemMode scope item}`) → `${itemDisplayAction item}` — ${
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

  pickerScopes = keymapData.scopes;

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
      # Nixvim evaluates plugins through its own nixpkgs instance, which does
      # not inherit the flake-level allowUnfree; mirror it here.
      nixpkgs.config.allowUnfree = true;
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
          flavour = "auto";
          background = lib.mapAttrs (_: appearance: appearance.variant) config.theme.appearances;
          integrations = {
            aerial = true;
            alpha = true;
            dropbar = {
              enabled = true;
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
                oxlintCmd = lib.getExe pkgs.oxlint;
              in
              {
                oxlint = {
                  command = oxlintCmd;
                  args = [
                    "--type-aware"
                    "--fix"
                    "$FILENAME"
                  ];
                  env = {
                    OXLINT_TSGOLINT_PATH = tsgolintCmd;
                  };
                  stdin = false;
                  cwd.__raw = ''
                    function(self, ctx)
                      return ctx.dirname or vim.fn.getcwd()
                    end
                  '';
                };
                oxfmt = {
                  command = lib.getExe pkgs.oxfmt;
                  args.__raw = ''
                    function(self, ctx)
                      local args = { "--stdin-filepath", "$FILENAME" }
                      local dirname = ctx.dirname or vim.fn.getcwd()
                      local config_files = vim.env.VP_VERSION ~= nil
                        and { "vite.config.ts" }
                        or { ".oxfmtrc.json", ".oxfmtrc.jsonc", "oxfmt.config.ts" }
                      if vim.fs.root(dirname, config_files) == nil then
                        vim.list_extend(args, { "--config", "${oxfmtDefaultConfigPath}" })
                      end
                      return args
                    end
                  '';
                  stdin = true;
                  cwd.__raw = ''
                    function(self, ctx)
                      return ctx.dirname or vim.fn.getcwd()
                    end
                  '';
                };
                ruff_fix.command = ruffCmd;
                ruff_format.command = ruffCmd;
                ruff_organize_imports.command = ruffCmd;
                jsonnetfmt.command = lib.getExe' pkgs.jsonnet "jsonnetfmt";
                stylua.command = lib.getExe pkgs.stylua;
                taplo.command = lib.getExe pkgs.taplo;
              };
            formatters_by_ft = {
              jsonnet = [ "jsonnetfmt" ];
              css = [ "oxfmt" ];
              html = [ "oxfmt" ];
              javascript = [
                "oxlint"
                "oxfmt"
              ];
              javascriptreact = [
                "oxlint"
                "oxfmt"
              ];
              json = [ "oxfmt" ];
              jsonc = [ "oxfmt" ];
              lua = [ "stylua" ];
              python = [
                "ruff_fix"
                "ruff_format"
                "ruff_organize_imports"
              ];
              toml = [ "taplo" ];
              typescript = [
                "oxlint"
                "oxfmt"
              ];
              typescriptreact = [
                "oxlint"
                "oxfmt"
              ];
            };
          };
        };
        dropbar = {
          enable = true;
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
              # Use Oxfmt instead to avoid LSP formatting conflicts.
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
            scheme_langserver.enable = !pkgs.stdenv.hostPlatform.isDarwin;
            taplo = {
              enable = true;
              settings.formatting = {
                indent_string = "  ";
                reorder_keys = true;
                reorder_arrays = true;
              };
            };
            tailwindcss.enable = true;
            # TypeScript/JavaScript servers are gated by whether the project
            # provides its own TypeScript (node_modules or a yarn SDK):
            #   - project TS present -> ts_ls drives the project's own tsserver
            #   - otherwise          -> tsgo (TypeScript 7 "Corsa", the Go port)
            # typescript-tools.nvim was removed: it cannot drive tsgo (it bridges
            # the legacy tsserver protocol only) and an explicit tsserver_path
            # would override project-local TypeScript.
            ts_ls = {
              enable = true;
              extraOptions.root_dir.__raw = ''
                function(bufnr, on_dir)
                  local fname = vim.api.nvim_buf_get_name(bufnr)
                  local has_local_ts = vim.fs.find(
                    { "node_modules/typescript/lib/tsserver.js", ".yarn/sdks/typescript/lib/tsserver.js" },
                    { path = fname, upward = true }
                  )[1] ~= nil
                  if has_local_ts then
                    local root = vim.fs.root(bufnr, { "tsconfig.json", "jsconfig.json", "package.json", ".git" })
                    on_dir(root or vim.fs.dirname(fname))
                  end
                  -- No project TypeScript: leave this buffer to tsgo.
                end
              '';
            };
            tsgo = {
              enable = true;
              # nixvim's server package map still points at the removed
              # `typescript-go` alias; nixpkgs renamed it to `typescript`
              # (TS7 "Corsa"). Its binary is now `tsc` (same Go binary), so
              # lspconfig's default `tsgo` cmd would not resolve.
              # LSP mode is `--lsp --stdio`: bare `--stdio` is rejected as an
              # unknown compiler option (TS5023) and the server exits 1.
              package = pkgs.typescript;
              extraOptions.cmd = [
                (lib.getExe' pkgs.typescript "tsc")
                "--lsp"
                "--stdio"
              ];
              extraOptions.root_dir.__raw = ''
                function(bufnr, on_dir)
                  local fname = vim.api.nvim_buf_get_name(bufnr)
                  local has_local_ts = vim.fs.find(
                    { "node_modules/typescript/lib/tsserver.js", ".yarn/sdks/typescript/lib/tsserver.js" },
                    { path = fname, upward = true }
                  )[1] ~= nil
                  if not has_local_ts then
                    local root = vim.fs.root(bufnr, { "tsconfig.json", "jsconfig.json", "package.json", ".git" })
                    on_dir(root or vim.fs.dirname(fname))
                  end
                  -- Project specifies its own TypeScript: tsgo stays out of the way.
                end
              '';
            };
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
          # telescope-backed `vim.ui.select`; replaces the archived dressing.nvim
          extensions.ui-select.enable = true;
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
          highlight.disable = [ "alpha" ];
          nixvimInjections = true;
          settings = {
            highlight = {
              enable = true;
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
        comment.enable = true;
        dap-python.enable = true;
        dap-ui.enable = true;
        dap.enable = true;
        diffview.enable = true;
        fidget.enable = true;
        firenvim.enable = true;
        # Disabled: second git client — neogit is the daily driver, gitsigns
        # covers blame, diffview covers history. Flip back for :Git muscle memory.
        fugitive.enable = false;
        # Disabled: duplicate fuzzy finder — every entry point uses telescope
        # (alpha dashboard, keymaps, vim.ui.select).
        fzf-lua.enable = false;
        git-conflict.enable = true;
        # Disabled: no bindings or config references anywhere.
        git-worktree.enable = false;
        # Disabled: niche binary editing, no references in keymaps or config.
        hex.enable = false;
        illuminate.enable = true;
        inc-rename.enable = true;
        indent-blankline.enable = true;
        # Disabled: kulala and rest.nvim are alternatives; both are currently off.
        # kulala.enable = true;
        lazydev.enable = true;
        # Disabled: conflicts with conform-nvim, which owns formatting
        # (lsp-format's own docs say to use one or the other).
        lsp-format.enable = false;
        lspsaga.enable = true;
        # Disabled: no snippet collections configured; blink-cmp completes fine
        # without it. Re-enable together with friendly-snippets or personal snippets.
        luasnip.enable = false;
        # Disabled: overlaps render-markdown (in-buffer preview); the nixpkgs
        # pin also dates to a 2023 rev.
        markdown-preview.enable = false;
        # Disabled: no references anywhere; upstream dormant since 2025-05.
        marks.enable = false;
        mini.modules.align = { };
        mini.modules.bufremove = { };
        neoconf.enable = true;
        # Disabled: LnL7/vim-nix is legacy regex syntax; the treesitter nix
        # grammar handles highlighting and nil_ls provides the LSP.
        nix.enable = false;
        nui.enable = true;
        nvim-autopairs.enable = true;
        nvim-surround.enable = true;
        octo.enable = true;
        oil.enable = true;
        orgmode.enable = true;
        overseer.enable = true;
        render-markdown.enable = true;
        # Disabled: no real usage found — earlier keymap hits were `:LspRestart`
        # substrings, not rest.nvim references.
        rest.enable = false;
        scope.enable = true;
        smart-splits.enable = true;
        spectre.enable = true;
        todo-comments.enable = true;
        trouble.enable = true;
        undotree.enable = true;
        web-devicons.enable = true;
        which-key.enable = true;
      };
      # rest.nvim optional dependencies; only needed while rest is enabled
      # (without them Neovim warns on startup when rest loads).
      # extraLuaPackages =
      #   luaPkgs: with luaPkgs; [
      #     mimetypes
      #     xml2lua
      #   ];
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
        ++ lib.lists.optionals (!pkgs.stdenv.hostPlatform.isDarwin) [ nvim-dbee ];
      extraConfigLuaPost =
        let
          helpers = config.lib.nixvim;
          extraPluginsConfig = {
            nvim-surround = { };
            overseer = { };
            # nvim-treesitter-textsubjects disabled: incompatible with newer nvim-treesitter API
          }
          // (lib.attrsets.optionalAttrs (!pkgs.stdenv.hostPlatform.isDarwin) { dbee = { }; });
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
      userCommands = {
        Bdelete = {
          command = "lua require('mini.bufremove').delete(0, false)";
          desc = "Delete current buffer, keeping window layout";
        };
        Bwipeout = {
          command = "lua require('mini.bufremove').wipeout(0, false)";
          desc = "Wipe out current buffer, keeping window layout";
        };
      };
      keymaps = globalKeymaps;
    };
  };

  home.file = {
    ".config/nvim/doc/nvim-keymaps.md".text = keymapsDoc;
    ".config/nvim/lua/nvim-keymaps.lua".text = keymapsLua;
  };
}
