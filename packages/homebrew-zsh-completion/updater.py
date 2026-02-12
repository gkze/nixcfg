"""Updater for Homebrew zsh completion file."""

from lib.update.updaters.github_raw_file import github_raw_file_updater

github_raw_file_updater(
    "homebrew-zsh-completion",
    owner="Homebrew",
    repo="brew",
    path="completions/zsh/_brew",
)
