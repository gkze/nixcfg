package backend

import (
	"context"
	"fmt"
	"strings"

	"github.com/gkze/ghawfr/workflow"
)

// ProviderSelection describes the worker/provider route selected for one job.
type ProviderSelection struct {
	Kind                ProviderKind
	Requirements        WorkerRequirements
	ImagePlan           *ImagePlan
	UnsafeLocalFallback bool
}

// AutoProvider chooses a provider based on normalized job requirements.
//
// Selection order is:
//  1. explicit host-local jobs (runs-on: local or no labels)
//  2. preferred isolated provider for the requested guest OS/arch
//  3. optional unsafe broad local fallback for smoke execution
//
// This keeps GitHub-hosted labels on the isolated-provider track by default
// instead of silently running them on the host.
type AutoProvider struct {
	Local               Worker
	VZ                  Provider
	QEMU                Provider
	UnsafeLocalFallback Worker
}

// Select returns the planned provider route for one job.
func (p AutoProvider) Select(job *workflow.Job) (ProviderSelection, error) {
	if job == nil {
		return ProviderSelection{}, fmt.Errorf("job is nil")
	}
	if p.Local != nil && shouldUseHostLocal(job.RunsOn) && p.Local.Capabilities().SupportsRunner(job.RunsOn) == nil {
		requirements, _ := RequirementsForRunner(job.RunsOn)
		return ProviderSelection{Kind: ProviderKindLocal, Requirements: requirements}, nil
	}
	if len(job.RunsOn.Labels) == 0 {
		if p.Local != nil {
			return ProviderSelection{Kind: ProviderKindLocal}, nil
		}
		if p.UnsafeLocalFallback != nil {
			return ProviderSelection{Kind: ProviderKindLocal, UnsafeLocalFallback: true}, nil
		}
		return ProviderSelection{}, fmt.Errorf("job %q has no runner labels and no local worker is configured", job.ID)
	}
	requirements, err := RequirementsForRunner(job.RunsOn)
	if err != nil {
		if p.UnsafeLocalFallback != nil && p.UnsafeLocalFallback.Capabilities().SupportsRunner(job.RunsOn) == nil {
			return ProviderSelection{Kind: ProviderKindLocal, UnsafeLocalFallback: true}, nil
		}
		return ProviderSelection{}, err
	}
	if selection, ok := p.isolatedSelection(requirements); ok {
		return selection, nil
	}
	if p.UnsafeLocalFallback != nil && p.UnsafeLocalFallback.Capabilities().SupportsRunner(job.RunsOn) == nil {
		return ProviderSelection{
			Kind:                ProviderKindLocal,
			Requirements:        requirements,
			UnsafeLocalFallback: true,
		}, nil
	}
	return ProviderSelection{}, fmt.Errorf(
		"no provider is configured for job %q with runner labels %s",
		job.ID,
		strings.Join(requirements.Labels, ", "),
	)
}

// PlanWorker describes the concrete provider/runtime route for one job.
func (p AutoProvider) PlanWorker(job *workflow.Job, options RunOptions) (WorkerPlan, error) {
	selection, err := p.Select(job)
	if err != nil {
		return WorkerPlan{}, err
	}
	switch selection.Kind {
	case ProviderKindLocal:
		worker := p.Local
		if selection.UnsafeLocalFallback {
			worker = p.UnsafeLocalFallback
		}
		if worker == nil {
			return WorkerPlan{}, fmt.Errorf("selected local worker is nil")
		}
		workingDirectory, err := PlanWorkingDirectory(options)
		if err != nil {
			return WorkerPlan{}, err
		}
		plan := WorkerPlan{
			Provider:         ProviderKindLocal,
			Requirements:     selection.Requirements,
			WorkingDirectory: workingDirectory,
			Transport:        TransportPlan{Kind: TransportKindHost},
			Notes:            []string{"direct host execution"},
		}
		if selection.UnsafeLocalFallback {
			plan.Notes = append(plan.Notes, "unsafe broad local fallback")
		}
		return plan, nil
	case ProviderKindVZ:
		provider, ok := p.VZ.(PlannedProvider)
		if !ok {
			return WorkerPlan{}, fmt.Errorf("selected vz provider does not support planning")
		}
		return provider.PlanWorker(job, options)
	case ProviderKindQEMU:
		provider, ok := p.QEMU.(PlannedProvider)
		if !ok {
			return WorkerPlan{}, fmt.Errorf("selected qemu provider does not support planning")
		}
		return provider.PlanWorker(job, options)
	default:
		return WorkerPlan{}, fmt.Errorf("unsupported provider selection %q", selection.Kind)
	}
}

// MaterializeWorker materializes provider-specific launch artifacts for one job.
func (p AutoProvider) MaterializeWorker(job *workflow.Job, options RunOptions) (MaterializedWorker, error) {
	selection, err := p.Select(job)
	if err != nil {
		return MaterializedWorker{}, err
	}
	switch selection.Kind {
	case ProviderKindLocal:
		worker := p.Local
		if selection.UnsafeLocalFallback {
			worker = p.UnsafeLocalFallback
		}
		if worker == nil {
			return MaterializedWorker{}, fmt.Errorf("selected local worker is nil")
		}
		workingDirectory, err := PlanWorkingDirectory(options)
		if err != nil {
			return MaterializedWorker{}, err
		}
		plan := WorkerPlan{
			Provider:         ProviderKindLocal,
			Requirements:     selection.Requirements,
			WorkingDirectory: workingDirectory,
			Transport:        TransportPlan{Kind: TransportKindHost},
			Notes:            []string{"direct host execution"},
		}
		if selection.UnsafeLocalFallback {
			plan.Notes = append(plan.Notes, "unsafe broad local fallback")
		}
		return MaterializedWorker{Plan: plan}, nil
	case ProviderKindVZ:
		provider, ok := p.VZ.(MaterializingProvider)
		if !ok {
			return MaterializedWorker{}, fmt.Errorf("selected vz provider does not support materialization")
		}
		return provider.MaterializeWorker(job, options)
	case ProviderKindQEMU:
		provider, ok := p.QEMU.(MaterializingProvider)
		if !ok {
			return MaterializedWorker{}, fmt.Errorf("selected qemu provider does not support materialization")
		}
		return provider.MaterializeWorker(job, options)
	default:
		return MaterializedWorker{}, fmt.Errorf("unsupported provider selection %q", selection.Kind)
	}
}

// AcquireWorker acquires a worker from the selected provider route.
func (p AutoProvider) AcquireWorker(ctx context.Context, job *workflow.Job, options RunOptions) (WorkerLease, error) {
	selection, err := p.Select(job)
	if err != nil {
		return nil, err
	}
	switch selection.Kind {
	case ProviderKindLocal:
		worker := p.Local
		if selection.UnsafeLocalFallback {
			worker = p.UnsafeLocalFallback
		}
		if worker == nil {
			return nil, fmt.Errorf("selected local worker is nil")
		}
		return staticLease{worker: worker}, nil
	case ProviderKindVZ:
		if p.VZ == nil {
			return nil, fmt.Errorf("selected vz provider is nil")
		}
		return p.VZ.AcquireWorker(ctx, job, options)
	case ProviderKindQEMU:
		if p.QEMU == nil {
			return nil, fmt.Errorf("selected qemu provider is nil")
		}
		return p.QEMU.AcquireWorker(ctx, job, options)
	default:
		return nil, fmt.Errorf("unsupported provider selection %q", selection.Kind)
	}
}

func shouldUseHostLocal(runner workflow.Runner) bool {
	if len(runner.Labels) == 0 {
		return true
	}
	for _, label := range runner.Labels {
		if strings.EqualFold(strings.TrimSpace(label), "local") {
			return true
		}
	}
	return false
}

func (p AutoProvider) isolatedSelection(requirements WorkerRequirements) (ProviderSelection, bool) {
	switch requirements.OS {
	case GuestOSDarwin:
		if requirements.Arch == GuestArchAArch64 && p.VZ != nil {
			plan, err := ImagePlanForProvider(requirements, ProviderKindVZ)
			if err != nil {
				return ProviderSelection{}, false
			}
			return ProviderSelection{Kind: ProviderKindVZ, Requirements: requirements, ImagePlan: &plan}, true
		}
	case GuestOSLinux:
		switch requirements.Arch {
		case GuestArchX8664:
			if p.QEMU != nil {
				plan, err := ImagePlanForProvider(requirements, ProviderKindQEMU)
				if err != nil {
					return ProviderSelection{}, false
				}
				return ProviderSelection{Kind: ProviderKindQEMU, Requirements: requirements, ImagePlan: &plan}, true
			}
		case GuestArchAArch64:
			if p.VZ != nil {
				plan, err := ImagePlanForProvider(requirements, ProviderKindVZ)
				if err != nil {
					return ProviderSelection{}, false
				}
				return ProviderSelection{Kind: ProviderKindVZ, Requirements: requirements, ImagePlan: &plan}, true
			}
			if p.QEMU != nil {
				plan, err := ImagePlanForProvider(requirements, ProviderKindQEMU)
				if err != nil {
					return ProviderSelection{}, false
				}
				return ProviderSelection{Kind: ProviderKindQEMU, Requirements: requirements, ImagePlan: &plan}, true
			}
		}
	}
	return ProviderSelection{}, false
}
