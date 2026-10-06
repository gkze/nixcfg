# Isolated eval of the Zed crate2nix builder wrapper.
# AST inspection can see __functor / .override, but cannot prove the wrapper
# stays callable after crate2nix's `.override { defaultCrateOverrides = ... }`
# or that Darwin rlib metadata still applies on that path.
{ lib }:
let
  inherit (import ../../packages/zed-editor-nightly/build-rust-crate.nix { inherit lib; })
    wrapBuildRustCrate
    ;

  fakeDrv =
    attrs:
    {
      inherit attrs;
      overrideAttrs =
        f:
        fakeDrv (
          attrs
          // (
            if builtins.isFunction f then
              f attrs
            else
              f
          )
        );
    };

  mkBuilder =
    extras:
    {
      __functor = self: args: fakeDrv (extras // args);
      override =
        f:
        mkBuilder (
          extras
          // (
            if builtins.isFunction f then
              f extras
            else
              f
          )
        );
    };

  checkPlatform =
    {
      isDarwin,
      label,
    }:
    let
      wrapped = wrapBuildRustCrate (mkBuilder { }) isDarwin;
      crate = wrapped { crateName = "rust_command_palette_hooks"; };
      overridden = wrapped.override {
        defaultCrateOverrides = {
          rust_copilot = _: { };
        };
      };
      overriddenCrate = overridden { crateName = "rust_command_palette_hooks"; };
      functionOverride = wrapped.override (_old: {
        defaultCrateOverrides = { };
      });
      darwinMetadata =
        crate.attrs.dontStrip or false == true && crate.attrs.stripExclude or [ ] == [ "*.rlib" ];
    in
    assert lib.assertMsg (builtins.isAttrs wrapped) "${label}: wrapper must be a set, not a bare lambda";
    assert lib.assertMsg (wrapped ? override) "${label}: crate2nix needs .override";
    assert lib.assertMsg (wrapped ? __functor) "${label}: wrapper must stay callable";
    assert lib.assertMsg (overridden ? override && overridden ? __functor)
      "${label}: .override must return another functor set";
    assert lib.assertMsg (functionOverride ? override && functionOverride ? __functor)
      "${label}: function-form .override must return a functor set";
    assert lib.assertMsg (crate.attrs.crateName == "rust_command_palette_hooks")
      "${label}: crate attrs must reach the inner builder";
    assert lib.assertMsg (overriddenCrate.attrs.crateName == "rust_command_palette_hooks")
      "${label}: overridden builder must still receive crate attrs";
    assert lib.assertMsg (if isDarwin then darwinMetadata else !(crate.attrs ? dontStrip))
      "${label}: Darwin rlib metadata policy is wrong";
    assert lib.assertMsg (
      if isDarwin then
        overriddenCrate.attrs.dontStrip or false == true
      else
        !(overriddenCrate.attrs ? dontStrip)
    ) "${label}: .override must keep Darwin rlib metadata";
    true;
in
assert checkPlatform {
  isDarwin = false;
  label = "x86_64-linux";
};
assert checkPlatform {
  isDarwin = true;
  label = "aarch64-darwin";
};
true
