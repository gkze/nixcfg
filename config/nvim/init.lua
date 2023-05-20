-- Automatically run PackerCompile on init.lua (this file) save / write
vim.api.nvim_exec(
	[[
  augroup Packer
    autocmd!
    autocmd BufWritePost init.lua PackerCompile
  augroup end
  ]],
	false
)

-- Configure general basics
for k, v in pairs({
	colorcolumn = "80",
	completeopt = "menuone,noinsert,noselect,preview",
	cursorline = true,
	expandtab = true,
	list = true,
	listchars = "space:·,tab:>-,eol:~",
	mouse = "a",
	number = true,
	shiftwidth = 2,
	tabstop = 2,
	termguicolors = true,
}) do
	vim.o[k] = v
end

-- Set up Packer symbols
local packer = require("packer")
local startup, use = packer.startup, packer.use

-- Declare plugins
startup(function()
	-- Necessary for Packer to manage itself
	use("wbthomason/packer.nvim")

	-- Gruvbox8 colorscheme (theme)
	use("lifepillar/vim-gruvbox8")

	-- Editorconfig support
	use("editorconfig/editorconfig-vim")

	-- GFM (GitHub Flavored Markdown) syntax support
	use("rhysd/vim-gfm-syntax")

	-- Status line
	use({
		"nvim-lualine/lualine.nvim",
		requires = { "kyazdani42/nvim-web-devicons", opt = true },
	})

	-- TOML syntax
	use("cespare/vim-toml")

	-- Collection of configurations for built-in LSP client
	use("neovim/nvim-lspconfig")

	-- LSP status
	use("nvim-lua/lsp-status.nvim")

	-- Autocompletion plugin
	use("hrsh7th/nvim-cmp")

	-- LSP signature while typing
	use("ray-x/lsp_signature.nvim")

	-- LSP source for nvim-cmp
	use("hrsh7th/cmp-nvim-lsp")

	-- Snippets source for nvim-cmp
	use("saadparwaiz1/cmp_luasnip")

	-- Snippets plugin
	use("L3MON4D3/LuaSnip")

	-- Tree Sitter (used mostly for rich syntax highlighting)
	use("nvim-treesitter/nvim-treesitter")

	-- Neovim icons
	use({ "kyazdani42/nvim-web-devicons" })

	-- File browser
	use({
		"kyazdani42/nvim-tree.lua",
		requires = { "kyazdani42/nvim-web-devicons" },
		config = function()
			require("nvim-tree").setup({
				hijack_cursor = true,
				update_cwd = true,
				update_focused_file = { update_cwd = true },
			})
		end,
	})

	-- Code symbols outline
	use("simrat39/symbols-outline.nvim")

	-- FZF is a Fuzzy Finder
	use("/opt/homebrew/opt/fzf")

	-- Telescope is a Fuzzy Finder
	use({
		"nvim-telescope/telescope.nvim",
		requires = { "nvim-lua/plenary.nvim" },
	})

	-- VS Code like pictograms for completion items
	use("onsails/lspkind-nvim")

	-- Inline git blame
	use("APZelos/blamer.nvim")

	-- Tab bar
	use({
		"romgrk/barbar.nvim",
		requires = { "kyazdani42/nvim-web-devicons" },
	})

	-- Go development plugin
	use("ray-x/go.nvim")

	-- Rust development plugin
	use("simrat39/rust-tools.nvim")

	-- CSV handling
	use("chrisbra/csv.vim")
end)

-- LSP signature while typing
require("lsp_signature").setup()

-- Load lualine
require("lualine").setup({ theme = "gruvbox_material" })

-- Load Go development plugin
require("go").setup()

-- Set up LSP (Language Server Protocol)
require("lsp")

-- Load Tree Sitter configuration
require("treesitter")

-- Set colorscheme (theme)
vim.cmd("colorscheme gruvbox8")

-- Create shorter reference for setting keymaps
local map = vim.api.nvim_set_keymap

-- Map <leader>f to Telescope
map("n", "<leader>f", ":Telescope find_files<CR>", { noremap = true })

-- Configure nvim-tree.lua
local nvimtree_mapopts = { noremap = true }

map("n", "<C-n>", ":NvimTreeToggle<CR>", nvimtree_mapopts)
map("n", "<leader>r", ":NvimTreeRefresh<CR>", nvimtree_mapopts)
map("n", "<leader>n", ":NvimTreeFindFile<CR>", nvimtree_mapopts)

-- Configure FZF
map("n", "<C-f>", ":FZF<CR>", { noremap = true })

-- Configure symbols-outline.nvim
map("n", "<C-s>", ":SymbolsOutline<CR>", { noremap = true })

-- Enable inline git blame
vim.g.blamer_enabled = 1

-- Set up tabs
require("tabs")

-- Set up pictograms
require("lspkind").init({
	-- enables text annotations
	--

	-- default symbol map
	-- can be either 'default' (requires nerd-fonts font) or
	-- 'codicons' for codicon preset (requires vscode-codicons font)
	--
	-- default: 'default'
	preset = "codicons",

	-- override preset symbols
	--
	-- default: {}
	symbol_map = {
		Text = "",
		Method = "",
		Function = "",
		Constructor = "",
		Field = "ﰠ",
		Variable = "",
		Class = "ﴯ",
		Interface = "",
		Module = "",
		Property = "ﰠ",
		Unit = "塞",
		Value = "",
		Enum = "",
		Keyword = "",
		Snippet = "",
		Color = "",
		File = "",
		Reference = "",
		Folder = "",
		EnumMember = "",
		Constant = "",
		Struct = "פּ",
		Event = "",
		Operator = "",
		TypeParameter = "",
	},
})

-- Autoformat Lua sourtces on save / write
vim.api.nvim_exec(
	[[
  augroup Lua
    autocmd!
    autocmd BufWritePost *.lua !stylua --indent-type Spaces --indent-width 2 <afile> 
    autocmd BufWritePost *.lua edit
    autocmd BufWritePost *.lua redraw!
  augroup end
  ]],
	false
)

-- Autoformat Go sources on save / write
vim.api.nvim_exec(
	[[
  augroup Go
    autocmd FileType go setlocal shiftwidth=4 tabstop=2 softtabstop=2 noexpandtab
    autocmd BufWritePost *.go :silent! lua require('go.format').gofmt()
    autocmd BufWritePost *.go :silent! lua require('go.format').goimport()
  augroup end
  ]],
	false
)
