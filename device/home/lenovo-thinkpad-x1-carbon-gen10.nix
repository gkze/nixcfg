{ ... }: {
  programs.alacritty.settings = {
    font.size = 10.0;
    window = {
      dimensions = { columns = 200; lines = 60; };
      position = { x = 10; y = 10; };
    };
  };
}
