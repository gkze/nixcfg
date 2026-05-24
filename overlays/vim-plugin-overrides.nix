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
            saveCallNew = ''
              local config = config_module.get_config()
                generator.save(save_path, config)
                vim.cmd("delmarks <>")'';
          in
          (old.postPatch or "")
          + ''
            substituteInPlace lua/codesnap/module.lua \
              --replace-fail '${moduleLuaOld}' '${moduleLuaNew}'

            substituteInPlace lua/codesnap/fetch.lua \
              --replace-fail '${fetchLuaOld}' '${fetchLuaNew}'

            if grep -Fq 'string.match(static.config.save_path, "%.(.+)$")' lua/codesnap/init.lua; then
              substituteInPlace lua/codesnap/init.lua \
                --replace-fail 'string.match(static.config.save_path, "%.(.+)$")' 'string.match(save_path, "%.(.+)$")'
            fi

            substituteInPlace lua/codesnap/init.lua \
              --replace-fail 'if matched_extension ~= "png" and matched_extension ~= nil then' 'if matched_extension ~= nil and matched_extension ~= "png" and matched_extension ~= "svg" and matched_extension ~= "html" then' \
              --replace-fail 'error("The extension of save_path should be .png", 0)' 'error("The extension of save_path should be .png, .svg, or .html", 0)'

            if grep -Fq 'require("generator").save_snapshot(config)' lua/codesnap/init.lua; then
              substituteInPlace lua/codesnap/init.lua \
                --replace-fail 'require("generator").save_snapshot(config)' '${saveCallNew}'
            fi

            if grep -Fq 'config.save_path' lua/codesnap/init.lua; then
              substituteInPlace lua/codesnap/init.lua \
                --replace-fail 'config.save_path' 'save_path'
            fi

            substituteInPlace lua/codesnap/utils/table.lua \
              --replace-fail 'if t1[k] == nil and v ~= nil then' 'if t1[k] == nil and v ~= nil and v ~= "none" then'
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
