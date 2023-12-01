{ ... }: {
  programs.alacritty.settings = {
    font.size = 9.0;
    window = {
      dimensions = { columns = 200; lines = 60; };
      position = { x = 10; y = 10; };
    };
  };
}
