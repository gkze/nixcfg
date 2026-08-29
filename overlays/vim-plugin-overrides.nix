{
  final,
  inputs,
  prev,
  slib,
  sources,
  ...
}:
{
  vimPlugins = prev.vimPlugins.extend (
    _: vprev: {
      codesnap-nvim = vprev.codesnap-nvim.overrideAttrs (old: {
        patches = (old.patches or [ ]) ++ [ ./codesnap-nvim.patch ];
      });

      nvim-treesitter-textobjects = vprev.nvim-treesitter-textobjects.overrideAttrs {
        src = prev.fetchFromGitHub {
          owner = "gkze";
          repo = "nvim-treesitter-textobjects";
          inherit (sources.treesitter-textobjects) rev;
          hash = slib.sourceHash "treesitter-textobjects" "srcHash";
        };
      };

      vim-bundle-mako = prev.vimUtils.buildVimPlugin {
        pname = slib.normalizeName slib.flakeLock.vim-bundle-mako.original.repo;
        version = inputs.vim-bundle-mako.rev;
        src = inputs.vim-bundle-mako;
      };

      opencode-nvim = vprev.opencode-nvim.overrideAttrs (old: {
        dependencies = map (dep: if dep.pname or "" == "opencode" then final.opencode else dep) (
          old.dependencies or [ ]
        );
        propagatedBuildInputs = map (dep: if dep.pname or "" == "opencode" then final.opencode else dep) (
          old.propagatedBuildInputs or [ ]
        );
      });
    }
  );
}
