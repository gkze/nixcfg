{
  programs.alacritty.settings = {
    window = {
      dimensions = {
        columns = 200;
        lines = 60;
      };
      position = {
        x = 10;
        y = 10;
      };
    };
    # NOTE: this gets mered in with
    # users/george/homx.nix#programs.alacritty.settings.shell.args
    # It comes first
    # shell.args = [ ];
  };
}
