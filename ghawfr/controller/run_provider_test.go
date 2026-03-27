package controller

import (
	"context"
	"testing"

	"github.com/gkze/ghawfr/backend"
	"github.com/gkze/ghawfr/workflow"
)

type stubProvider struct {
	worker   backend.Worker
	acquired int
	released int
}

func (p *stubProvider) AcquireWorker(_ context.Context, _ *workflow.Job, _ backend.RunOptions) (backend.WorkerLease, error) {
	p.acquired++
	return stubLease{provider: p, worker: p.worker}, nil
}

type stubLease struct {
	provider *stubProvider
	worker   backend.Worker
}

func (l stubLease) Worker() backend.Worker { return l.worker }
func (l stubLease) Release(_ context.Context) error {
	l.provider.released++
	return nil
}

func TestRunJobWithProviderUsesLeaseLifecycle(t *testing.T) {
	provider := &stubProvider{worker: backend.Local{}}
	job := &workflow.Job{ID: "job", LogicalID: "job", RunsOn: workflow.Runner{Labels: []string{"ubuntu-24.04"}}}
	_, err := runJobWithProvider(context.Background(), job, backend.Local{}, backend.RunOptions{Provider: provider, WorkingDirectory: t.TempDir()})
	if err != nil {
		t.Fatalf("runJobWithProvider: %v", err)
	}
	if provider.acquired != 1 || provider.released != 1 {
		t.Fatalf("provider lifecycle = acquired %d released %d, want 1/1", provider.acquired, provider.released)
	}
}
