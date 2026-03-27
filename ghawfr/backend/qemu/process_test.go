//go:build unix

package qemu

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestStartMaterializedLaunchWritesProcessArtifactsAndCanBeStopped(t *testing.T) {
	root := t.TempDir()
	commandPath := filepath.Join(root, "launch.sh")
	if err := os.WriteFile(commandPath, []byte("#!/usr/bin/env bash\nset -euo pipefail\nexec sleep 30\n"), 0o755); err != nil {
		t.Fatalf("WriteFile launch.sh: %v", err)
	}
	state, err := StartMaterializedLaunch(MaterializedLaunch{Command: commandPath})
	if err != nil {
		t.Fatalf("StartMaterializedLaunch: %v", err)
	}
	for _, path := range []string{state.LogPath, state.PIDPath, state.StatePath} {
		if _, err := os.Stat(path); err != nil {
			t.Fatalf("stat %q: %v", path, err)
		}
	}
	running, err := IsProcessRunning(state)
	if err != nil {
		t.Fatalf("IsProcessRunning: %v", err)
	}
	if !running {
		t.Fatal("running = false, want process to be alive")
	}
	if err := StopProcess(state, 2*time.Second); err != nil {
		t.Fatalf("StopProcess: %v", err)
	}
}
