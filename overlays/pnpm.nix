{ prev, ... }:
{
  pnpm_11 = prev.pnpm.overrideAttrs (_old: {
    version = "11.10.0";
    src = prev.fetchurl {
      url = "https://registry.npmjs.org/pnpm/-/pnpm-11.10.0.tgz";
      hash = "sha256-YgtmBepPYvxWptCphzP0eQcdAyHgPkhrUix+mnRhdDE=";
    };
    postInstall = (_old.postInstall or "") + ''
      chmod +x $out/libexec/pnpm/bin/pnpm.cjs $out/libexec/pnpm/bin/pnpx.cjs
      mv $out/bin/pnpm $out/bin/pnpm-unwrapped
      cat > $out/bin/pnpm <<'EOF'
      #!${prev.runtimeShell}
      # ponytail: remove this wrapper once nixpkgs fetchPnpmDeps supports pnpm 11.
      if [ "$1" = "config" ] && [ "$2" = "set" ] && [ "$3" = "manage-package-manager-versions" ]; then
        exit 0
      fi
      exec "$0-unwrapped" "$@"
      EOF
      chmod +x $out/bin/pnpm
    '';
    passthru = {
      majorVersion = "11";
    };
  });
}
