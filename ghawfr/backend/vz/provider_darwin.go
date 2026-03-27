//go:build darwin

package vz

import (
	"context"
	"fmt"
	"path/filepath"
	"runtime"

	vz "github.com/Code-Hex/vz/v3"

	ghbackend "github.com/gkze/ghawfr/backend"
	"github.com/gkze/ghawfr/workflow"
)

const guestWorkspace = "/workspace"

// Provider is the first Virtualization.framework-backed worker provider scaffold.
//
// It currently resolves worker requirements and image plans and rejects
// unsupported combinations up front. Actual VM boot/guest agent wiring will be
// added on top of this provider shape.
type Provider struct{}

// PlanWorker describes the concrete VZ guest/runtime route for one job.
func (Provider) PlanWorker(job *workflow.Job, options ghbackend.RunOptions) (ghbackend.WorkerPlan, error) {
	if job == nil {
		return ghbackend.WorkerPlan{}, fmt.Errorf("job is nil")
	}
	requirements, err := ghbackend.RequirementsForRunner(job.RunsOn)
	if err != nil {
		return ghbackend.WorkerPlan{}, err
	}
	plan, err := ghbackend.ImagePlanForProvider(requirements, ghbackend.ProviderKindVZ)
	if err != nil {
		return ghbackend.WorkerPlan{}, err
	}
	workingDirectory, err := ghbackend.PlanWorkingDirectory(options)
	if err != nil {
		return ghbackend.WorkerPlan{}, err
	}
	instanceDirectory := ghbackend.PlanInstanceDirectory(workingDirectory, ghbackend.ProviderKindVZ, job)
	workerPlan := ghbackend.WorkerPlan{
		Provider:          ghbackend.ProviderKindVZ,
		Requirements:      requirements,
		Image:             &plan,
		WorkingDirectory:  workingDirectory,
		InstanceDirectory: instanceDirectory,
		Transport: ghbackend.TransportPlan{
			Kind:    ghbackend.TransportKindVSock,
			Address: "cid:3 port:6000",
		},
		HostRequirements: []ghbackend.HostRequirement{
			{Kind: "framework", Name: "Virtualization.framework", Purpose: "boot Apple-native Linux or macOS guests"},
			{Kind: "binary-entitlement", Name: "com.apple.security.virtualization", Purpose: "allow the ghawfr binary to call Virtualization APIs"},
		},
		Notes: []string{
			"Apple Silicon host required for the current VZ worker path",
			"Linux guests are expected to convert canonical qcow2 images into raw runtime disks",
		},
	}
	if requirements.OS == ghbackend.GuestOSLinux {
		workerPlan.GuestWorkspace = guestWorkspace
		workerPlan.Shares = []ghbackend.DirectoryShare{{
			HostPath:  workingDirectory,
			GuestPath: guestWorkspace,
			ReadOnly:  false,
		}}
		workerPlan.HostRequirements = append(workerPlan.HostRequirements,
			ghbackend.HostRequirement{Kind: "capability", Name: "virtiofs", Purpose: "share the workspace into the Linux guest"},
		)
	}
	if requirements.OS == ghbackend.GuestOSDarwin {
		workerPlan.Notes = append(workerPlan.Notes,
			"macOS guest execution will likely require a guest agent path distinct from the Linux virtiofs/vsock path",
		)
	}
	return workerPlan, nil
}

// MaterializeWorker creates the Virtualization.framework machine artifacts for one job.
func (p Provider) MaterializeWorker(job *workflow.Job, options ghbackend.RunOptions) (ghbackend.MaterializedWorker, error) {
	plan, err := p.PlanWorker(job, options)
	if err != nil {
		return ghbackend.MaterializedWorker{}, err
	}
	artifacts, err := MaterializePlan(plan)
	if err != nil {
		return ghbackend.MaterializedWorker{}, err
	}
	return ghbackend.MaterializedWorker{
		Plan: plan,
		Artifacts: []string{
			artifacts.Plan.PlanPath,
			artifacts.Plan.HostChecksPath,
			artifacts.Machine,
		},
	}, nil
}

// AcquireWorker validates the requested job against the VZ-backed image/runtime
// strategy and currently returns a not-yet-implemented error after planning.
func (p Provider) AcquireWorker(_ context.Context, job *workflow.Job, options ghbackend.RunOptions) (ghbackend.WorkerLease, error) {
	materialized, err := p.MaterializeWorker(job, options)
	if err != nil {
		return nil, err
	}
	if runtime.GOARCH != "arm64" {
		return nil, fmt.Errorf("vz provider requires an Apple Silicon host (%s)", filepath.Join(materialized.Plan.InstanceDirectory, "vz-machine.json"))
	}
	_ = vz.ErrUnsupportedOSVersion
	return nil, fmt.Errorf(
		"vz provider materialized %s and planned %s/%s guest from %s (%s -> %s) with transport %s, but VM boot is not wired yet",
		filepath.Join(materialized.Plan.InstanceDirectory, "vz-machine.json"),
		materialized.Plan.Requirements.OS,
		materialized.Plan.Requirements.Arch,
		materialized.Plan.Image.Source,
		materialized.Plan.Image.CanonicalFormat,
		materialized.Plan.Image.RuntimeFormat,
		materialized.Plan.Transport.Kind,
	)
}
