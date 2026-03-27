package backend

import (
	"path/filepath"
	"strings"
	"testing"

	"github.com/gkze/ghawfr/workflow"
)

func TestPlanWorkingDirectoryUsesAbsolutePath(t *testing.T) {
	root := t.TempDir()
	got, err := PlanWorkingDirectory(RunOptions{WorkingDirectory: root})
	if err != nil {
		t.Fatalf("PlanWorkingDirectory: %v", err)
	}
	if got != root {
		t.Fatalf("PlanWorkingDirectory = %q, want %q", got, root)
	}
}

func TestPlanInstanceDirectoryUsesProviderScopedStablePath(t *testing.T) {
	root := t.TempDir()
	job := &workflow.Job{ID: "compute-hashes[platform=x86_64-linux,runner=ubuntu-24.04]", LogicalID: "compute-hashes"}
	path := PlanInstanceDirectory(root, ProviderKindQEMU, job)
	wantPrefix := filepath.Join(root, ".ghawfr", "workers", string(ProviderKindQEMU)) + string(filepath.Separator)
	if !strings.HasPrefix(path, wantPrefix) {
		t.Fatalf("PlanInstanceDirectory = %q, want prefix %q", path, wantPrefix)
	}
	if !strings.Contains(filepath.Base(path), "compute-hashes-platform-x86-64-linux-runner-ubuntu-24-04-") {
		t.Fatalf("instance basename = %q, want sanitized job slug", filepath.Base(path))
	}
}
