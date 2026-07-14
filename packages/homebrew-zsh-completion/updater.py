"""Updater for Homebrew zsh completion file."""

from lib.update.updaters import GitHubRawFileUpdater, register_updater


@register_updater
class HomebrewZshCompletionUpdater(GitHubRawFileUpdater):
    """Raw-file updater for the Homebrew zsh completion script."""

    name = "homebrew-zsh-completion"
    owner = "Homebrew"
    repo = "brew"
    path = "completions/zsh/_brew"
