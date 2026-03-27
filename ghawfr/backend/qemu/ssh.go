package qemu

import (
	"fmt"
	"os"
)

// SSHArtifacts are helper scripts for talking to the booted guest.
type SSHArtifacts struct {
	SSHCommandPath string
	WaitForSSHPath string
}

func materializeSSHArtifacts(spec LaunchSpec) (SSHArtifacts, error) {
	artifacts := SSHArtifacts{SSHCommandPath: spec.SSHCommandPath, WaitForSSHPath: spec.WaitForSSHPath}
	sshScript := fmt.Sprintf(`#!/usr/bin/env bash
set -euo pipefail
exec ssh \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  -o LogLevel=ERROR \
  -i %q \
  -p %d \
  %s@127.0.0.1 "$@"
`, spec.SSHPrivateKeyPath, spec.SSHPort, spec.SSHUser)
	if err := os.WriteFile(artifacts.SSHCommandPath, []byte(sshScript), 0o755); err != nil {
		return SSHArtifacts{}, fmt.Errorf("write ssh command script %q: %w", artifacts.SSHCommandPath, err)
	}
	waitScript := fmt.Sprintf(`#!/usr/bin/env bash
set -euo pipefail
timeout="${1:-300}"
shift || true
for ((i=0; i<timeout; i++)); do
  if %q true >/dev/null 2>&1; then
    exit 0
  fi
  sleep 1
done
echo "timed out waiting for guest SSH" >&2
exit 1
`, artifacts.SSHCommandPath)
	if err := os.WriteFile(artifacts.WaitForSSHPath, []byte(waitScript), 0o755); err != nil {
		return SSHArtifacts{}, fmt.Errorf("write wait-for-ssh script %q: %w", artifacts.WaitForSSHPath, err)
	}
	return artifacts, nil
}
