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
        postPatch =
          let
            libExt = if prev.stdenv.isDarwin then "dylib" else "so";
            moduleLuaOld = ''package.cpath = path_utils.join(";", package.cpath, generator_path)'';
            moduleLuaNew = ''
              local lib_dir = vim.fn.fnamemodify(generator_path, ":h")
              package.cpath = package.cpath .. ";" .. lib_dir .. sep .. "lib?." .. module.get_lib_extension()'';
            fetchLuaOld = "function fetch.ensure_lib()";
            fetchLuaNew = ''
              function fetch.ensure_lib()
                return "${vprev.codesnap-nvim.passthru.codesnap-lib}/lib/libgenerator.${libExt}"
              end
              function fetch._original_ensure_lib()'';
          in
          (old.postPatch or "")
          + ''
            substituteInPlace lua/codesnap/module.lua \
              --replace-fail '${moduleLuaOld}' '${moduleLuaNew}'

            substituteInPlace lua/codesnap/fetch.lua \
              --replace-fail '${fetchLuaOld}' '${fetchLuaNew}'
          '';
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
