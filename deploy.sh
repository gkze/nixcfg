#!/usr/bin/env bash
set -euo pipefail

nix build ".#darwinConfigurations.$(hostname).system" &&
  nix run .#rebuild -- --flake "." switch
