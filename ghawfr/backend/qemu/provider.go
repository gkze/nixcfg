package qemu

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/gkze/ghawfr/actionadapter"
	ghbackend "github.com/gkze/ghawfr/backend"
	"github.com/gkze/ghawfr/workflow"
)

const defaultGuestWorkspace = "/workspace"

// Provider is the first QEMU-backed worker provider scaffold.
type Provider struct{}

func guestWorkspaceRoot() string {
	if value := strings.TrimSpace(os.Getenv("GHAWFR_QEMU_GUEST_WORKSPACE")); value != "" {
		return value
	}
	return defaultGuestWorkspace
}

// PlanWorker describes the concrete QEMU guest/runtime route for one job.
func (Provider) PlanWorker(job *workflow.Job, options ghbackend.RunOptions) (ghbackend.WorkerPlan, error) {
	if job == nil {
		return ghbackend.WorkerPlan{}, fmt.Errorf("job is nil")
	}
	requirements, err := ghbackend.RequirementsForRunner(job.RunsOn)
	if err != nil {
		return ghbackend.WorkerPlan{}, err
	}
	plan, err := ghbackend.ImagePlanForProvider(requirements, ghbackend.ProviderKindQEMU)
	if err != nil {
		return ghbackend.WorkerPlan{}, err
	}
	workingDirectory, err := ghbackend.PlanWorkingDirectory(options)
	if err != nil {
		return ghbackend.WorkerPlan{}, err
	}
	instanceDirectory := ghbackend.PlanInstanceDirectory(workingDirectory, ghbackend.ProviderKindQEMU, job)
	return ghbackend.WorkerPlan{
		Provider:          ghbackend.ProviderKindQEMU,
		Requirements:      requirements,
		Image:             &plan,
		WorkingDirectory:  workingDirectory,
		InstanceDirectory: instanceDirectory,
		GuestWorkspace:    guestWorkspaceRoot(),
		Transport: ghbackend.TransportPlan{
			Kind:    ghbackend.TransportKindSSH,
			Address: "127.0.0.1:2222",
		},
		Shares: []ghbackend.DirectoryShare{{
			HostPath:  workingDirectory,
			GuestPath: guestWorkspaceRoot(),
			ReadOnly:  false,
		}},
		HostRequirements: []ghbackend.HostRequirement{
			{Kind: "binary", Name: "qemu-system-x86_64", Purpose: "boot x86_64 Linux guests on Apple Silicon or Intel hosts"},
			{Kind: "binary", Name: "qemu-img", Purpose: "create the runtime qcow2 overlay disk"},
			{Kind: "binary", Name: "ssh", Purpose: "control the guest worker over SSH"},
			{Kind: "binary", Name: "go", Purpose: "cross-compile the guest ghawfr-worker bootstrap binary"},
		},
		Notes: []string{
			"canonical Linux image artifact remains qcow2",
			"guest workspace defaults to /workspace and can be overridden with GHAWFR_QEMU_GUEST_WORKSPACE for smoke testing",
			"cloud-init source files are materialized under cloud-init/ and an ISO is built when a supported host tool is available",
			"base image download and runtime overlay preparation scripts are materialized but not executed yet",
			"guest control uses GHAWFR_WORKER_REMOTE_COMMAND when set and otherwise expects ghawfr-worker in the guest PATH",
		},
	}, nil
}

// MaterializeWorker creates the QEMU launch artifacts for one job.
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
			artifacts.Launch,
			artifacts.Command,
			artifacts.Disk.FetchBaseImagePath,
			artifacts.Disk.PrepareRuntimePath,
			artifacts.CloudInit.UserDataPath,
			artifacts.CloudInit.MetaDataPath,
			artifacts.CloudInit.NetworkConfigPath,
			artifacts.CloudInit.BuildScriptPath,
			artifacts.CloudInit.ISOPath,
			artifacts.Spec.SSHPrivateKeyPath,
			artifacts.Spec.SSHPublicKeyPath,
			artifacts.SSH.SSHCommandPath,
			artifacts.SSH.WaitForSSHPath,
			artifacts.Worker.BuildScriptPath,
			artifacts.Worker.BinaryPath,
		},
	}, nil
}

// AcquireWorker materializes and starts one QEMU-backed worker session.
func (p Provider) AcquireWorker(_ context.Context, job *workflow.Job, options ghbackend.RunOptions) (ghbackend.WorkerLease, error) {
	plan, err := p.PlanWorker(job, options)
	if err != nil {
		return nil, err
	}
	launch, err := MaterializePlan(plan)
	if err != nil {
		return nil, err
	}
	if err := ghbackend.EnsureHostRequirements(plan); err != nil {
		return nil, fmt.Errorf("qemu provider prerequisites (%s): %w", filepath.Join(plan.InstanceDirectory, "launch.sh"), err)
	}
	processState, err := StartMaterializedLaunch(launch)
	if err != nil {
		return nil, err
	}
	remoteWorker, err := startRemoteWorkerWithRetry(launch, 5*time.Second)
	if err != nil {
		_ = StopProcess(processState, 2*time.Second)
		return nil, err
	}
	worker := actionadapter.NewRemoteExec(append([]string(nil), plan.Requirements.Labels...), plan.GuestWorkspace, remoteWorker)
	return qemuLease{worker: worker, remote: remoteWorker, process: processState}, nil
}

type qemuLease struct {
	worker  ghbackend.RemoteExecWorker
	remote  *RemoteWorker
	process ProcessState
}

func (l qemuLease) Worker() ghbackend.Worker {
	return l.worker
}

func (l qemuLease) Release(_ context.Context) error {
	if l.remote != nil {
		_ = l.remote.Close()
	}
	return StopProcess(l.process, 5*time.Second)
}

func startRemoteWorkerWithRetry(launch MaterializedLaunch, timeout time.Duration) (*RemoteWorker, error) {
	deadline := time.Now().Add(timeout)
	var lastErr error
	for {
		worker, err := StartRemoteWorker(launch)
		if err == nil {
			return worker, nil
		}
		lastErr = err
		if time.Now().After(deadline) {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	if lastErr == nil {
		lastErr = fmt.Errorf("timed out starting remote worker")
	}
	return nil, lastErr
}
