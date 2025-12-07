{ primaryUser, pkgs, ... }:
{
  users.users.${primaryUser} = {
    uid = 501;
    shell = pkgs.zsh;
  };
}
