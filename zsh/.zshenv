export \
  ASSUME_NO_MOVING_GC_UNSAFE_RISK_IT_WITH="go1.20" \
  AWS_SDK_LOAD_CONFIG="1" \
  BUN_INSTALL="${HOME}/.bun" \
  DOCKER_SCAN_SUGGEST="false" \
  EDITOR="nvim" \
  GITHUB_TOKEN="$(envchain main printenv GITHUB_TOKEN)" \
  GPG_TTY="$(tty)" \
  GRAB_HOME="${HOME}/Development" \
  HISTFILE="${HOME}/.zsh/.zsh_history" \
  LESS="--mouse" \
  MANPAGER="sh -c 'col -bx | bat -l man -p'" \
  NIX_PAGER="bat -p" \
  PATH="/opt/homebrew/bin:${PATH}" \
  XDG_CONFIG_DATA="${HOME}/.local/share" \
  XDG_CONFIG_HOME="${HOME}/.config" \
  ZDOTDIR="${HOME}/.zsh"
