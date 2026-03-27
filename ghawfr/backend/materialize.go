package backend

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

// MaterializedPlan records the on-disk artifacts created for one worker plan.
type MaterializedPlan struct {
	PlanPath       string
	HostChecksPath string
}

// MaterializeWorkerPlan creates the provider instance directory and writes the
// generic worker-plan metadata used by provider-specific launchers.
func MaterializeWorkerPlan(plan WorkerPlan) (MaterializedPlan, error) {
	if plan.InstanceDirectory == "" {
		return MaterializedPlan{}, fmt.Errorf("worker plan instance directory is empty")
	}
	if err := os.MkdirAll(plan.InstanceDirectory, 0o755); err != nil {
		return MaterializedPlan{}, fmt.Errorf("create instance directory %q: %w", plan.InstanceDirectory, err)
	}
	artifacts := MaterializedPlan{
		PlanPath:       filepath.Join(plan.InstanceDirectory, "plan.json"),
		HostChecksPath: filepath.Join(plan.InstanceDirectory, "host-checks.json"),
	}
	if err := WriteJSONFile(artifacts.PlanPath, plan); err != nil {
		return MaterializedPlan{}, err
	}
	if err := WriteJSONFile(artifacts.HostChecksPath, ValidateHostRequirements(plan)); err != nil {
		return MaterializedPlan{}, err
	}
	return artifacts, nil
}

// WriteJSONFile writes one indented JSON artifact with a trailing newline.
func WriteJSONFile(path string, value any) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal json %q: %w", path, err)
	}
	data = append(data, '\n')
	if err := os.WriteFile(path, data, 0o644); err != nil {
		return fmt.Errorf("write json %q: %w", path, err)
	}
	return nil
}
