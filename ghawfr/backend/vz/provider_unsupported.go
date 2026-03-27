//go:build !darwin

package vz

import (
	"context"
	"fmt"

	ghbackend "github.com/gkze/ghawfr/backend"
	"github.com/gkze/ghawfr/workflow"
)

// Provider is unavailable on non-darwin hosts.
type Provider struct{}

// PlanWorker reports that the VZ provider is only available on darwin hosts.
func (Provider) PlanWorker(_ *workflow.Job, _ ghbackend.RunOptions) (ghbackend.WorkerPlan, error) {
	return ghbackend.WorkerPlan{}, fmt.Errorf("vz provider is only available on darwin hosts")
}

// MaterializeWorker reports that the VZ provider is only available on darwin hosts.
func (Provider) MaterializeWorker(_ *workflow.Job, _ ghbackend.RunOptions) (ghbackend.MaterializedWorker, error) {
	return ghbackend.MaterializedWorker{}, fmt.Errorf("vz provider is only available on darwin hosts")
}

// AcquireWorker reports that the VZ provider is only available on darwin hosts.
func (Provider) AcquireWorker(_ context.Context, _ *workflow.Job, _ ghbackend.RunOptions) (ghbackend.WorkerLease, error) {
	return nil, fmt.Errorf("vz provider is only available on darwin hosts")
}
