# Upstream Superset declares exact-version `patchedDependencies` in its root
# package.json. bun2nix's install hook deletes that field from package.json
# and bun.lock because it expects every patch to be applied to the Bun cache
# entry ahead of time, so the cache must carry the patched trees itself.
#
# Bun ignores a declared patch whose exact version is not installed, so a
# patch applies only to packages the generated bun.nix actually provides.
{
  manifest,
  src,
  packageNames,
}:
let
  declared = manifest.patchedDependencies or { };
  installed = builtins.listToAttrs (
    map (name: {
      inherit name;
      value = true;
    }) packageNames
  );
in
builtins.mapAttrs (_: path: src + "/${path}") (
  removeAttrs declared (builtins.filter (name: !(installed ? ${name})) (builtins.attrNames declared))
)
