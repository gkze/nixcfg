{
  pkgs,
  src ? ../../..,
}:
let
  failingTty = pkgs.writeShellScriptBin "tty" ''
    exit 1
  '';
  succeedingTty = pkgs.writeShellScriptBin "tty" ''
    printf '%s\n' /dev/ttys-test
  '';
  gpgTtyInit = src + "/modules/home/gpg-tty.zsh";
in
pkgs.runCommand "check-test-zsh-gpg-tty" { } ''
  GPG_TTY=/stale PATH=${failingTty}/bin ${pkgs.zsh}/bin/zsh -f -c '
    source ${gpgTtyInit}
    [[ ! -v GPG_TTY ]]
  '

  PATH=${succeedingTty}/bin ${pkgs.zsh}/bin/zsh -f -c '
    source ${gpgTtyInit}
    [[ "$GPG_TTY" == /dev/ttys-test ]]
    ${pkgs.coreutils}/bin/env | ${pkgs.gnugrep}/bin/grep -Fxq GPG_TTY=/dev/ttys-test
  '

  touch $out
''
