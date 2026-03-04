{
  prev,
  slib,
  sources,
  ...
}:
{
  goose-cli = prev.goose-cli.overrideAttrs (
    old:
    let
      version = slib.stripVersionPrefix sources.goose-cli.version;
      upstreamSrc = prev.fetchFromGitHub {
        owner = "block";
        repo = "goose";
        tag = "v${version}";
        hash = slib.sourceHash "goose-cli" "srcHash";
      };
      v8Source = sources.goose-v8;
      v8HashEntry = builtins.head (
        builtins.filter (entry: entry.hashType == "srcHash") (v8Source.hashes or [ ])
      );
      rustyV8Src = prev.fetchgit {
        url = "https://github.com/jh-block/rusty_v8.git";
        rev = v8Source.version;
        fetchSubmodules = true;
        inherit (v8HashEntry) hash;
      };
      src = prev.runCommand "goose-cli-${version}-src" { } ''
        cp -r ${upstreamSrc} $out
        chmod -R u+w $out

        mkdir -p $out/vendor
        cp -r ${rustyV8Src} $out/vendor/v8-goose-src
        substituteInPlace $out/vendor/v8/Cargo.toml \
          --replace-fail 'v8-goose = { version = "145.0.2" }' \
          'v8-goose = { path = "../v8-goose-src" }'

        substituteInPlace $out/vendor/v8-goose-src/build.rs \
          --replace-fail '  download_rust_toolchain();' \
          '  // Nix: use Rust toolchain from PATH.\n  // download_rust_toolchain();'
      '';
    in
    {
      inherit version src;

      nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
        prev.cmake
        prev.curl
        prev.gn
        prev.installShellFiles
        prev.ninja
        prev.python3
      ];

      cargoHash = slib.sourceHash "goose-cli" "cargoHash";
      cargoDeps = null;

      env = (old.env or { }) // {
        DISABLE_CLANG = "1";
        GN = "${prev.gn}/bin/gn";
        NINJA = "${prev.ninja}/bin/ninja";
        PYTHON = "${prev.python3}/bin/python3";
        V8_FROM_SOURCE = "1";
      };

      postInstall = (old.postInstall or "") + ''
        installShellCompletion --cmd goose \
          --bash <($out/bin/goose completion bash) \
          --fish <($out/bin/goose completion fish) \
          --zsh <($out/bin/goose completion zsh)
      '';
    }
  );
}
