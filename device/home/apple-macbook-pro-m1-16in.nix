{ lib, ... }:
{
  # MacBook Pro specific Alacritty settings via Home Manager
  programs.alacritty.settings.window = {
    # Remap Apple Option key to Alt key. Useful in Neovim for meta / alt
    option_as_alt = "Both";
    # Hand-sized out for MacBook Pro 16" so that the Alacritty window pops
    # up in the center with a bit of space left around the screen
    dimensions = {
      columns = 250;
      lines = 80;
    };
    position = {
      x = 433;
      y = 400;
    };
  };

  # Bump up terminal font size for better readability on the 16" screen
  stylix.fonts.sizes.terminal = lib.mkForce 11;
}
