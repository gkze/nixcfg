_:
let
  buildVolume = import ../george/build-volume-identity.nix;
in
{
  # App Store tooling (mas, and therefore Homebrew Bundle's masApps entries)
  # discovers installed apps through Spotlight metadata. The previous global
  # disable made every activation reinstall those apps. Keep only Build out of
  # the index, and actively restore the root and Data stores because the shared
  # `spotlight.indexing = true` option deliberately leaves existing state alone.
  darwinDefaults.spotlight.unindexedVolumes = [ buildVolume.name ];

  # Run before Homebrew to restore indexing state before masApps reconciliation.
  # Metadata refresh is asynchronous, so the first migration can still require a
  # follow-up activation. A restoration failure aborts before Homebrew instead of
  # reconciling App Store apps without their metadata. Target the two required
  # stores instead of using -a, which would also re-enable Build.
  system.activationScripts.preActivation.text = ''
    for volumePath in / /System/Volumes/Data; do
      currentStatus="$(/usr/bin/mdutil -s "$volumePath" 2>/dev/null || true)"
      case "$currentStatus" in
        *"Indexing enabled."*) ;;
        *)
          echo "enabling Spotlight indexing for $volumePath..." >&2
          /usr/bin/mdutil -i on "$volumePath" >/dev/null
          ;;
      esac
    done
  '';
}
