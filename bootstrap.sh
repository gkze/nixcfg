#!/usr/bin/env bash
set -euo pipefail
shopt -s extglob

typeset FILENAME
FILENAME="$(basename "${0}")"

typeset XCODE_CLT_INSTALL_FILE
XCODE_CLT_INSTALL_FILE="/tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress"

typeset USER_SUDOERS_ENTRY_FILE
USER_SUDOERS_ENTRY_FILE="/etc/sudoers.d/${USER}"

typeset NIX_INSTALLER_URL
NIX_INSTALLER_URL="https://install.determinate.systems/nix"

# typeset NIXCFG_REPO_URL
# NIXCFG_REPO_URL="git@github.com:gkze/nixcfg"

# typeset NIXCFG_LOCAL_PATH
# NIXCFG_LOCAL_PATH="${HOME}/.config/nixcfg"

typeset HOMEBREW_INSTALL_SCRIPT_URL
HOMEBREW_INSTALL_SCRIPT_URL="https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"

typeset NIX_DARWIN_URL
NIX_DARWIN_URL="https://github.com/LnL7/nix-darwin/archive/master.tar.gz"

log() {
  printf "[%s] [%s] ${1}\n" "$(date -Iseconds)" "${FILENAME}"
}

add_user_sudoers_entry() {
  log "Adding sudoers entry for ${USER} to avoid password prompts"
  echo "${USER} ALL = (ALL) NOPASSWD: ALL" | sudo tee "${USER_SUDOERS_ENTRY_FILE}"
}

macos_install_xcode_command_line_tools() {
  log "Installing Xcode Command Line Tools"

  touch "${XCODE_CLT_INSTALL_FILE}"
  softwareupdate -ia --verbose | while read -r l; do log "[softwareupdate] ${l}"; done

  log "Xcode Command Line Tools installed"
}

install_homebrew() {
  curl -Ls "${HOMEBREW_INSTALL_SCRIPT_URL}" | bash
}

run_nix_installer() {
  log "Running Determinate Systems Nix installer"

  curl -Ls "${NIX_INSTALLER_URL}" | bash -s -- install --no-confirm
}

add_nix_darwin_channel() {
  log "Adding nix-darwin channel"

  nix-build "${NIX_DARWIN_URL}" -A installer
}

install_nix_darwin() {
  log "Installing nix-darwin"

  ./result/bin/darwin-installer
}

cleanup() {
  if [[ -f ${XCODE_CLT_INSTALL_FILE} ]]; then
    rm "${XCODE_CLT_INSTALL_FILE}"
  fi
}

main() {
  trap cleanup INT TERM EXIT

  add_user_sudoers_entry
  run_nix_installer

  case "$(uname -s)" in
  [Dd]arwin)
    log "on macOS"
    macos_install_xcode_command_line_tools
    install_homebrew

    pushd /tmp
    add_nix_darwin_channel
    install_nix_darwin
    popd
    ;;
  [Ll]inux) printf "Linux not yet supported" && exit 1 ;;
  esac
}

if [[ ${BASH_SOURCE[0]} == "${0}" ]]; then main "${@-}"; fi
