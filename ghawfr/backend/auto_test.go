package backend

import (
	"context"
	"testing"

	"github.com/gkze/ghawfr/workflow"
)

type stubWorker struct {
	labels []string
}

func (w stubWorker) Capabilities() CapabilitySet {
	return CapabilitySet{RunnerLabels: w.labels}
}

func (w stubWorker) RunJob(_ context.Context, job *workflow.Job, _ RunOptions) (*JobResult, error) {
	return &JobResult{JobID: job.ID, LogicalID: job.LogicalID, Result: JobStatusSuccess}, nil
}

type countingProvider struct {
	calls  int
	worker Worker
	plan   WorkerPlan
}

func (p *countingProvider) PlanWorker(_ *workflow.Job, _ RunOptions) (WorkerPlan, error) {
	if p.plan.Provider == "" {
		p.plan = WorkerPlan{Provider: ProviderKindLocal, Transport: TransportPlan{Kind: TransportKindHost}}
	}
	return p.plan, nil
}

func (p *countingProvider) AcquireWorker(_ context.Context, _ *workflow.Job, _ RunOptions) (WorkerLease, error) {
	p.calls++
	return staticLease{worker: p.worker}, nil
}

func TestAutoProviderSelectUsesHostLocalOnlyForExplicitLocalJobs(t *testing.T) {
	provider := AutoProvider{
		Local: stubWorker{labels: []string{"local", "ubuntu-24.04"}},
		QEMU:  &countingProvider{worker: stubWorker{labels: []string{"ubuntu-24.04"}}},
	}
	selection, err := provider.Select(&workflow.Job{ID: "job", RunsOn: workflow.Runner{Labels: []string{"local"}}})
	if err != nil {
		t.Fatalf("Select: %v", err)
	}
	if selection.Kind != ProviderKindLocal {
		t.Fatalf("selection.Kind = %q, want %q", selection.Kind, ProviderKindLocal)
	}
	if selection.UnsafeLocalFallback {
		t.Fatal("selection.UnsafeLocalFallback = true, want false")
	}
}

func TestAutoProviderSelectRoutesLinuxX8664ToQEMU(t *testing.T) {
	provider := AutoProvider{Local: stubWorker{labels: []string{"local", "ubuntu-24.04"}}, QEMU: &countingProvider{worker: stubWorker{labels: []string{"ubuntu-24.04"}}}}
	selection, err := provider.Select(&workflow.Job{ID: "job", RunsOn: workflow.Runner{Labels: []string{"ubuntu-24.04"}}})
	if err != nil {
		t.Fatalf("Select: %v", err)
	}
	if selection.Kind != ProviderKindQEMU {
		t.Fatalf("selection.Kind = %q, want %q", selection.Kind, ProviderKindQEMU)
	}
	if selection.ImagePlan == nil || selection.ImagePlan.RuntimeFormat != ImageFormatQCOW2 {
		t.Fatalf("selection.ImagePlan = %#v, want qcow2 runtime", selection.ImagePlan)
	}
}

func TestAutoProviderSelectRoutesDarwinToVZ(t *testing.T) {
	provider := AutoProvider{Local: stubWorker{labels: []string{"local", "macos-15"}}, VZ: &countingProvider{worker: stubWorker{labels: []string{"macos-15"}}}}
	selection, err := provider.Select(&workflow.Job{ID: "job", RunsOn: workflow.Runner{Labels: []string{"macos-15"}}})
	if err != nil {
		t.Fatalf("Select: %v", err)
	}
	if selection.Kind != ProviderKindVZ {
		t.Fatalf("selection.Kind = %q, want %q", selection.Kind, ProviderKindVZ)
	}
	if selection.ImagePlan == nil || selection.ImagePlan.RuntimeFormat != ImageFormatTart {
		t.Fatalf("selection.ImagePlan = %#v, want tart runtime", selection.ImagePlan)
	}
}

func TestAutoProviderSelectPrefersVZForLinuxArmThenFallsBackToQEMU(t *testing.T) {
	job := &workflow.Job{ID: "job", RunsOn: workflow.Runner{Labels: []string{"ubuntu-24.04-arm"}}}
	withVZ := AutoProvider{
		Local: stubWorker{labels: []string{"macos-15"}},
		VZ:    &countingProvider{worker: stubWorker{labels: []string{"ubuntu-24.04-arm"}}},
		QEMU:  &countingProvider{worker: stubWorker{labels: []string{"ubuntu-24.04-arm"}}},
	}
	selection, err := withVZ.Select(job)
	if err != nil {
		t.Fatalf("Select with VZ: %v", err)
	}
	if selection.Kind != ProviderKindVZ {
		t.Fatalf("selection.Kind = %q, want %q", selection.Kind, ProviderKindVZ)
	}
	withoutVZ := AutoProvider{QEMU: &countingProvider{worker: stubWorker{labels: []string{"ubuntu-24.04-arm"}}}}
	selection, err = withoutVZ.Select(job)
	if err != nil {
		t.Fatalf("Select without VZ: %v", err)
	}
	if selection.Kind != ProviderKindQEMU {
		t.Fatalf("selection.Kind = %q, want %q", selection.Kind, ProviderKindQEMU)
	}
}

func TestAutoProviderSelectUsesUnsafeLocalFallback(t *testing.T) {
	provider := AutoProvider{UnsafeLocalFallback: Local{}}
	selection, err := provider.Select(&workflow.Job{ID: "job", RunsOn: workflow.Runner{Labels: []string{"ubuntu-24.04"}}})
	if err != nil {
		t.Fatalf("Select: %v", err)
	}
	if selection.Kind != ProviderKindLocal || !selection.UnsafeLocalFallback {
		t.Fatalf("selection = %#v, want unsafe local fallback", selection)
	}
}

func TestAutoProviderPlanWorkerDelegatesToSelectedProvider(t *testing.T) {
	qemu := &countingProvider{
		worker: stubWorker{labels: []string{"ubuntu-24.04"}},
		plan:   WorkerPlan{Provider: ProviderKindQEMU, Transport: TransportPlan{Kind: TransportKindSSH}},
	}
	provider := AutoProvider{Local: stubWorker{labels: []string{"local"}}, QEMU: qemu}
	plan, err := provider.PlanWorker(&workflow.Job{ID: "job", RunsOn: workflow.Runner{Labels: []string{"ubuntu-24.04"}}}, RunOptions{WorkingDirectory: t.TempDir()})
	if err != nil {
		t.Fatalf("PlanWorker: %v", err)
	}
	if plan.Provider != ProviderKindQEMU || plan.Transport.Kind != TransportKindSSH {
		t.Fatalf("plan = %#v, want qemu/ssh", plan)
	}
}

func TestAutoProviderAcquireWorkerDelegatesToSelectedProvider(t *testing.T) {
	qemu := &countingProvider{worker: stubWorker{labels: []string{"ubuntu-24.04"}}}
	provider := AutoProvider{Local: stubWorker{labels: []string{"macos-15"}}, QEMU: qemu}
	lease, err := provider.AcquireWorker(context.Background(), &workflow.Job{ID: "job", RunsOn: workflow.Runner{Labels: []string{"ubuntu-24.04"}}}, RunOptions{})
	if err != nil {
		t.Fatalf("AcquireWorker: %v", err)
	}
	if qemu.calls != 1 {
		t.Fatalf("qemu.calls = %d, want 1", qemu.calls)
	}
	if lease == nil || lease.Worker() == nil {
		t.Fatal("lease/worker = nil, want delegated worker")
	}
}
