{ inputs, final, ... }:
let
  mkElectron = final.callPackage (
    inputs.nixpkgs + "/pkgs/development/tools/electron/binary/generic.nix"
  ) { };

  allVersions = [
    "38.7.2"
    "40.1.0"
    "40.7.0"
    "40.8.5"
    "40.9.3"
    "41.2.1"
  ];

  hashes = {
    "38.7.2" = {
      headers = "sha256-lQQPQhKzpjjinmHBlTfm86LKjSsP1KVB2a6Aw3BAI7g=";
      aarch64-darwin = "sha256-uR4S7GaV+WnM95LZXcfqXaNfOZzsK+1NeyXYofVFtd4=";
      aarch64-linux = "sha256-c+h8Qy+lK5AF4S4jofz/z62oU94ZSS+QXFDbtG/XeN8=";
      x86_64-darwin = "sha256-RZ3QXwDCnUNREllvh7xb0K6xZ5bf8HROVCEIZBeHfSQ=";
      x86_64-linux = "sha256-/kKM0hJoDh1t9h+mfvwmC5Ih0InHoUyYY/GLy+rOVig=";
    };
    "40.1.0" = {
      headers = "sha256-9hXTUMbgcX99JHRovCzBeD4q/oXCQdLJkhXi3hgkEDE=";
      aarch64-darwin = "sha256-8oZvO4TgvbejMz9UAfgkSuFLNPXOZ+1F8lmjrDk7Ooc=";
      aarch64-linux = "sha256-Twf/AERacHKJ1G9S3/1vqlBKS2A1Xdsg3zb/japc/HA=";
      x86_64-darwin = "sha256-hCVMai3Bv/hcjE3jVyIYEB1oMUwVwokmk6GZKxk66Gs=";
      x86_64-linux = "sha256-JvdnXMED9soKESLYLzC+11RMGl1VB6aYCZKuB+yHahI=";
    };
    "40.7.0" = {
      headers = "sha256-eNxMR7UthxvYyDurji/AH3PLdxJ95gsUhbiGsrEM+MM=";
      aarch64-darwin = "sha256-zPbPigk/IAwO2viH++f1u4hl6+f6SL7WEWUpP01rdlU=";
      aarch64-linux = "sha256-/dUAOLRDa5d1hdo94KTxGK79h/Ex7jQqZR1h6R6qFQs=";
      x86_64-darwin = "sha256-BfpYmYm1mfrI1voXCVpXktK0vMSqdFpjiRbqWmwK/bk=";
      x86_64-linux = "sha256-D3utkbADhMTStZ6++QRBW+lb8G7b/llfD8tX9R/RR+Q=";
    };
    "40.8.5" = {
      headers = "sha256-O5ZK+FTxrI25WmtG3EQ+jVwhd1/YKngm7f3EhT14LMs=";
      aarch64-darwin = "sha256-ekAMKK66e99pH/A9KmILdsf4x1/frB9FQ6jcE7A5+iQ=";
      aarch64-linux = "sha256-WvAHPFKo3HKeEYNAtfUMSykyvZS6mS4cU4D+FUUzA3M=";
      x86_64-darwin = "sha256-JgDYmy/6RSu13C/hHw93Ga4AEnP29saJiBQJhuX5VQg=";
      x86_64-linux = "sha256-O85u5OTkgffObQvjhPbFOc4W4Lm39GEVrsZRZ3D2wm0=";
    };
    "40.9.3" = {
      headers = "sha256-JCJhlN2Y8nh/ItEtcO5c21PRt5dxZNKHBuTdNJSXYwQ=";
      aarch64-darwin = "sha256-6DqXt8cBfsNunhn1sgQwdp6J5wHCszfaJoXBOFT+cPM=";
      aarch64-linux = "sha256-W3MnMXfpbr1gBbcCD4TAhcSlMbFwHbA8GWOyrCwepVE=";
      x86_64-darwin = "sha256-t1MZR37bo85RYfGMxSg7woCWwjWbxHvIetXTZeoJf9U=";
      x86_64-linux = "sha256-K14zMnvV4YCzwcDsQp+gZ2nulsD5VqNUcblAm1H0Wto=";
    };
    "41.2.1" = {
      headers = "sha256-/VjbK2RkGaBCeWhOJYgraUSlnd1v6+RZREtnWGiO3Lc=";
      aarch64-darwin = "sha256-fiArnv8ADrP2DuB1nZOAhY980rVqp/Fh1ypLH4L/DxM=";
      aarch64-linux = "sha256-wu7fL/Cf1P7fzk8kThYEI1/GMH3Z//6u1L52o+Dc5vM=";
      x86_64-darwin = "sha256-/u/QIDn21AvMfb1vSy2ybj/JXO/6KltMJRLPYa1lNUk=";
      x86_64-linux = "sha256-BMx/1UAMCM2Y4sxcXl9gzrtLcvplAgqK4qiNj0gyhxA=";
    };
  };

  runtimes = builtins.mapAttrs mkElectron hashes;
in
{
  nixcfgElectron = {
    inherit allVersions hashes runtimes;

    runtimeFor =
      version:
      runtimes.${version} or (throw "nixcfgElectron: missing packaged Electron runtime for ${version}");

    versionsForSystem = _system: allVersions;
  };
}
