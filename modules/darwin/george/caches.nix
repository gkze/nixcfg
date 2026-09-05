{
  nixcfg.common.nix = {
    minFreeStoreBytes = 32 * 1024 * 1024 * 1024;
    maxFreeStoreBytes = 128 * 1024 * 1024 * 1024;
    substituters = [
      "https://gkze.cachix.org"
      "https://zed.cachix.org"
    ];
    trustedPublicKeys = [
      "gkze.cachix.org-1:vO2wq3fAFvRL1TA7R02JnU/R5iKGhoHMLGYbnzPRJjI="
      "zed.cachix.org-1:/pHQ6dpMsAZk2DiP4WCL0p9YDNKWj2Q5FL20bNmw1cU="
    ];
  };
}
