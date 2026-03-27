package qemu

import (
	"fmt"
	"os"
)

// DiskArtifacts are the generated helper scripts used to stage the base image
// and create the runtime overlay disk.
type DiskArtifacts struct {
	FetchBaseImagePath string
	PrepareRuntimePath string
}

func materializeDiskArtifacts(spec LaunchSpec) (DiskArtifacts, error) {
	artifacts := DiskArtifacts{
		FetchBaseImagePath: spec.FetchBaseImagePath,
		PrepareRuntimePath: spec.PrepareRuntimePath,
	}
	fetchScript := fmt.Sprintf(`#!/usr/bin/env bash
set -euo pipefail
mkdir -p "$(dirname %q)"
if [ -s %q ]; then
  echo "base image already present: %q"
  exit 0
fi
curl -L --fail --output %q.tmp %q
mv %q.tmp %q
`, spec.BaseImagePath, spec.BaseImagePath, spec.BaseImagePath, spec.BaseImagePath, spec.BaseImageURL, spec.BaseImagePath, spec.BaseImagePath)
	if err := os.WriteFile(artifacts.FetchBaseImagePath, []byte(fetchScript), 0o755); err != nil {
		return DiskArtifacts{}, fmt.Errorf("write fetch-base-image script %q: %w", artifacts.FetchBaseImagePath, err)
	}
	prepareScript := fmt.Sprintf(`#!/usr/bin/env bash
set -euo pipefail
if [ ! -s %q ]; then
  echo "missing base image: %q" >&2
  exit 1
fi
if [ -e %q ]; then
  echo "runtime disk already exists: %q"
  exit 0
fi
qemu-img create -f qcow2 -F qcow2 -b %q %q
`, spec.BaseImagePath, spec.BaseImagePath, spec.RuntimeDiskPath, spec.RuntimeDiskPath, spec.BaseImagePath, spec.RuntimeDiskPath)
	if err := os.WriteFile(artifacts.PrepareRuntimePath, []byte(prepareScript), 0o755); err != nil {
		return DiskArtifacts{}, fmt.Errorf("write prepare-runtime script %q: %w", artifacts.PrepareRuntimePath, err)
	}
	return artifacts, nil
}
