package vz

import (
	"path/filepath"

	ghbackend "github.com/gkze/ghawfr/backend"
)

// MaterializedMachine records the concrete launch artifacts for one VZ worker.
type MaterializedMachine struct {
	Plan    ghbackend.MaterializedPlan
	Machine string
	Spec    MachineSpec
}

// MaterializePlan creates the VZ instance directory and writes machine metadata
// derived from the worker plan.
func MaterializePlan(plan ghbackend.WorkerPlan) (MaterializedMachine, error) {
	materialized, err := ghbackend.MaterializeWorkerPlan(plan)
	if err != nil {
		return MaterializedMachine{}, err
	}
	spec, err := BuildMachineSpec(plan)
	if err != nil {
		return MaterializedMachine{}, err
	}
	machinePath := filepath.Join(plan.InstanceDirectory, "vz-machine.json")
	if err := ghbackend.WriteJSONFile(machinePath, spec); err != nil {
		return MaterializedMachine{}, err
	}
	return MaterializedMachine{Plan: materialized, Machine: machinePath, Spec: spec}, nil
}
