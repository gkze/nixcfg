{
  inputs,
  final,
  prev,
  slib,
  system,
  ...
}:
{
  # Pin element-desktop to 1.12.8 - version 1.12.9 requires Xcode 26 actool
  # which isn't available in nixpkgs yet (nixpkgs#485589)
  # TODO: Remove this override once nixpkgs PR #486275 is merged
  element-desktop = prev.element-desktop.overrideAttrs (_: rec {
    version = "1.12.8";
    src = prev.fetchFromGitHub {
      owner = "element-hq";
      repo = "element-desktop";
      rev = "v1.12.8";
      hash = slib.sourceHash "element-desktop" "srcHash";
    };
    offlineCache = prev.fetchYarnDeps {
      yarnLock = src + "/yarn.lock";
      hash = slib.sourceHash "element-desktop" "sha256";
    };
  });

  # nixpkgs#487201 added wayland unconditionally for czkawka, which breaks
  # Darwin evaluation because wayland is marked badPlatforms on Darwin.
  # Upstream Czkawka ships macOS GUI builds, so keep it available on Darwin.
  czkawka =
    if prev.stdenv.hostPlatform.isDarwin then
      prev.czkawka.overrideAttrs (old: {
        buildInputs = builtins.filter (dep: (dep.pname or "") != "wayland") (old.buildInputs or [ ]);
        dontWrapGApps = false;
        postFixup = "";
      })
    else
      prev.czkawka;

  # nushell: Skip sandbox-incompatible test on Darwin
  nushell = prev.nushell.overrideAttrs (old: {
    checkPhase =
      let
        extraSkip = "--skip=shell::environment::env::path_is_a_list_in_repl";
      in
      prev.lib.replaceStrings
        [ "--skip=repl::test_config_path::test_default_config_path" ]
        [ "${extraSkip} --skip=repl::test_config_path::test_default_config_path" ]
        old.checkPhase;
  });

  # mdformat: Update to 1.0.0 for markdown-it-py 4.x compatibility
  mdformat = prev.mdformat.override {
    python3 = prev.python3.override {
      packageOverrides = _: pyPrev: {
        mdformat = pyPrev.mdformat.overridePythonAttrs (_: {
          version = slib.getFlakeVersion "mdformat";
          src = inputs.mdformat;
        });
      };
    };
  };

  # Pin Swift to a nixpkgs rev where it builds (clang-21.1.8 broke it)
  inherit (import inputs.nixpkgs-swift { inherit system; })
    swiftPackages
    swift
    ;

  mountpoint-s3 = prev.mountpoint-s3.overrideAttrs (old: {
    buildInputs =
      prev.lib.optionals prev.stdenv.hostPlatform.isDarwin [ prev.macfuse-stubs ]
      ++ prev.lib.optionals prev.stdenv.hostPlatform.isLinux [ prev.fuse3 ];
    doCheck = !prev.stdenv.hostPlatform.isDarwin;
    meta = old.meta // {
      platforms = prev.lib.platforms.unix;
    };
  });

  # sequoia-wot: upstream clique tests are currently failing on Darwin
  # (panic in backward_propagation::tests::cliques).
  sequoia-wot = prev.sequoia-wot.overrideAttrs (_: {
    doCheck = !prev.stdenv.hostPlatform.isDarwin;
  });

  # Extend vimPlugins with fixes and custom plugins
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
        src = inputs.treesitter-textobjects;
      };

      vim-bundle-mako = prev.vimUtils.buildVimPlugin {
        pname = slib.normalizeName slib.flakeLock.vim-bundle-mako.original.repo;
        version = inputs.vim-bundle-mako.rev;
        src = inputs.vim-bundle-mako;
      };

      # Override opencode-nvim to use our patched opencode
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
