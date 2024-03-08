{ ... }: {
  programs.alacritty.settings = {
    font.size = 9.0;
    window = {
      dimensions = { columns = 200; lines = 60; };
      position = { x = 10; y = 10; };
    };
    # NOTE: this gets mered in with
    # users/george/homx.nix#programs.alacritty.settings.shell.args
    # It comes first
    # shell.args = [ ];
  };
}
