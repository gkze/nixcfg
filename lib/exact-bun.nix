{
  bun,
  fetchurl,
  lib,
}:
{
  packageManifest,
  packageName,
  source,
  system,
  version,
}:
let
  packageManager =
    if builtins.isAttrs packageManifest then packageManifest.packageManager or null else null;
  expectedPackageManager = "bun@${version}";
  packageManagerCheck =
    if packageManager == expectedPackageManager then
      true
    else
      throw ''
        ${packageName}: persisted Bun version does not match the locked package manifest.
        Expected packageManager ${expectedPackageManager}, got ${builtins.toJSON packageManager}.
        Refresh it with: nixcfg update ${packageName}
      '';
  systemPolicy = builtins.fromJSON (builtins.readFile ./system-policy.json);
  releaseAssets =
    assert systemPolicy.schemaVersion == 1;
    systemPolicy.bunArtifacts;
  asset = releaseAssets.${system} or (throw "${packageName}: Bun has no release asset for ${system}");
  matchingSources = builtins.filter (
    candidate: candidate.hashType == "bunRuntimeHash" && (candidate.platform or null) == system
  ) source.hashes;
  sourceEntry =
    if builtins.length matchingSources == 1 then
      builtins.head matchingSources
    else
      throw "${packageName}: sources.json must contain exactly one bunRuntimeHash for ${system}";
  expectedUrl = "https://github.com/oven-sh/bun/releases/download/bun-v${version}/${asset}";
  sourceUrl = sourceEntry.url or expectedUrl;
  sourceUrlCheck =
    if sourceUrl == expectedUrl then
      true
    else
      throw ''
        ${packageName}: persisted Bun source URL does not match packageManager.
        Expected ${expectedUrl}, got ${sourceUrl}.
      '';
  bunSource = fetchurl {
    url = sourceUrl;
    inherit (sourceEntry) hash;
  };
in
assert packageManagerCheck;
assert sourceUrlCheck;
bun.overrideAttrs (previousAttrs: {
  inherit version;
  src = bunSource;

  doInstallCheck = true;
  postInstallCheck = (previousAttrs.postInstallCheck or "") + ''
    test "$("$out/bin/bun" --version)" = ${lib.escapeShellArg version}
  '';

  passthru = (previousAttrs.passthru or { }) // {
    exactVersion = version;
    source = bunSource;
    sources = {
      "${system}" = bunSource;
    };
  };
})
