package backend

import (
	"os"
	"path/filepath"
	"testing"
)

func TestMaterializeWorkerPlanWritesPlanAndHostChecks(t *testing.T) {
	root := t.TempDir()
	plan := WorkerPlan{
		Provider:          ProviderKindQEMU,
		InstanceDirectory: filepath.Join(root, ".ghawfr", "workers", "qemu", "job-1234"),
		HostRequirements:  []HostRequirement{{Kind: "binary", Name: "sh"}},
	}
	artifacts, err := MaterializeWorkerPlan(plan)
	if err != nil {
		t.Fatalf("MaterializeWorkerPlan: %v", err)
	}
	for _, path := range []string{artifacts.PlanPath, artifacts.HostChecksPath} {
		if _, err := os.Stat(path); err != nil {
			t.Fatalf("stat %q: %v", path, err)
		}
	}
}
