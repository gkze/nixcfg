{ prev, ... }:
{
  nixVersions = prev.nixVersions // {
    git = prev.nixVersions.git.appendPatches [ ./skip-flat-app-bundles-in-optimiser.patch ];
  };
}
