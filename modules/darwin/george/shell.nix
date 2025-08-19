{ primaryUser, pkgs, ... }:
{
  users.users.${primaryUser}.shell = pkgs.zsh;
}
