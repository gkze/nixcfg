{ pkgs, lib, ... }: {
  # Homebrew & mas
  homebrew = {
    # Only manage Homebrew when underlying system is macOS
    # (Maybe try on other OSes in the future)
    enable = pkgs.stdenv.isDarwin;

    # Stay updated & clean up things that are not defined below
    onActivation = { autoUpdate = true; cleanup = "zap"; upgrade = true; };

    # Homebrew Taps
    taps = [ "gkze/gkze" "homebrew/cask-fonts" "homebrew/cask-versions" ];

    # Homebrew Formulae
    brews = [ ];

    # Homebrew Casks. They've been split out to what I believe to be
    # cross-platform vs. macOS-only casks (in case we'd like to use Homebrew
    # outside of macOS in the future for whatever reason)
    # TODO: refactor into os / device / profile based installation
    casks = [
      # "discord"
      # "docker"
      # "figma"
      "firefox"
      # "gimp"
      # "github"
      # "google-chrome"
      # "google-drive"
      # "httpie"
      # "low-profile"
      # "messenger"
      # "musicbrainz-picard"
      "nordvpn"
      # "openlens"
      "signal"
      "skype"
      # "slack"
      # "soulseek"
      # "spotify"
      # "telegram"
      # "vagrant"
      # "visual-studio-code-insiders"
      # "vlc"
      # "webtorrent"
      # "whatsapp"
      "zed"
      # "zoom"
    ] ++ (lib.optionals pkgs.stdenv.isDarwin [
      "aerial"
      "appcleaner"
      "beekeeper-studio"
      "brave-browser-beta"
      "codeedit"
      "cog"
      "cyberduck"
      "dash"
      "docker"
      # Installs via Nixpkgs but login doesn't work because probable failure of
      # element:// URI handler install
      "element"
      "hot"
      "maccy"
      "monitorcontrol"
      "netnewswire-beta"
      "rapidapi"
      "rectangle"
      "rekordbox"
      "sloth"
      "swiftdefaultappsprefpane"
      "tableplus"
      "utm"
      "vimr"
      "xcodes"
    ]);
  };
}
