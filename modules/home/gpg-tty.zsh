if GPG_TTY="$(tty 2>/dev/null)"; then
  export GPG_TTY
else
  unset GPG_TTY
fi
