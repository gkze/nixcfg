#!/usr/bin/env bash
set -euo pipefail

nix build ".#darwinConfigurations.$(hostname).system" &&
  ./result/sw/bin/darwin-rebuild switch --flake .
