package backend

import (
	"context"
	"fmt"

	"github.com/gkze/ghawfr/workflow"
)

// Provider acquires one worker for a materialized workflow job.
type Provider interface {
	AcquireWorker(ctx context.Context, job *workflow.Job, options RunOptions) (WorkerLease, error)
}

// WorkerLease owns one acquired worker until Release is called.
type WorkerLease interface {
	Worker() Worker
	Release(ctx context.Context) error
}

// StaticProvider always returns the same worker implementation.
type StaticProvider struct {
	WorkerImpl Worker
}

// PlanWorker describes the direct host-local execution route.
func (p StaticProvider) PlanWorker(_ *workflow.Job, options RunOptions) (WorkerPlan, error) {
	if p.WorkerImpl == nil {
		return WorkerPlan{}, fmt.Errorf("static provider worker is nil")
	}
	workingDirectory, err := PlanWorkingDirectory(options)
	if err != nil {
		return WorkerPlan{}, err
	}
	return WorkerPlan{
		Provider:         ProviderKindLocal,
		WorkingDirectory: workingDirectory,
		Transport:        TransportPlan{Kind: TransportKindHost},
		Notes:            []string{"direct host execution"},
	}, nil
}

// MaterializeWorker reports the direct host-local execution route. It does not
// create provider artifacts because local execution does not require an
// instance directory.
func (p StaticProvider) MaterializeWorker(job *workflow.Job, options RunOptions) (MaterializedWorker, error) {
	plan, err := p.PlanWorker(job, options)
	if err != nil {
		return MaterializedWorker{}, err
	}
	return MaterializedWorker{Plan: plan}, nil
}

// AcquireWorker returns a lease for the configured worker.
func (p StaticProvider) AcquireWorker(_ context.Context, job *workflow.Job, options RunOptions) (WorkerLease, error) {
	if _, err := p.MaterializeWorker(job, options); err != nil {
		return nil, err
	}
	return staticLease{worker: p.WorkerImpl}, nil
}

type staticLease struct {
	worker Worker
}

func (l staticLease) Worker() Worker {
	return l.worker
}

func (l staticLease) Release(_ context.Context) error {
	return nil
}
