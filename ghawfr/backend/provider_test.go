package backend

import (
	"context"
	"testing"
)

func TestStaticProviderReturnsConfiguredWorker(t *testing.T) {
	provider := StaticProvider{WorkerImpl: Local{}}
	plan, err := provider.PlanWorker(nil, RunOptions{WorkingDirectory: t.TempDir()})
	if err != nil {
		t.Fatalf("PlanWorker: %v", err)
	}
	if plan.Provider != ProviderKindLocal || plan.Transport.Kind != TransportKindHost {
		t.Fatalf("plan = %#v, want local/host", plan)
	}
	lease, err := provider.AcquireWorker(context.Background(), nil, RunOptions{WorkingDirectory: t.TempDir()})
	if err != nil {
		t.Fatalf("AcquireWorker: %v", err)
	}
	if lease == nil {
		t.Fatal("lease = nil, want worker lease")
	}
	if lease.Worker() == nil {
		t.Fatal("lease.Worker() = nil, want worker")
	}
	if err := lease.Release(context.Background()); err != nil {
		t.Fatalf("Release: %v", err)
	}
}
