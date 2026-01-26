_: {
  # TEMPORARILY DISABLED: mac-app-util fails to build with SBCL 2.6.0
  # SBCL 2.6.0 broke fare-quasiquote/cl-interpol readtable handling
  # Tracking issue: https://github.com/hraban/mac-app-util/issues/42
  # TODO: Re-enable once upstream is fixed (see beads issue nixcfg-gl7)
  # imports = [ inputs.mac-app-util.homeManagerModules.default ];
}
