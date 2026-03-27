package qemu

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	ghbackend "github.com/gkze/ghawfr/backend"
)

// MaterializedLaunch records the concrete launch artifacts for one QEMU worker.
type MaterializedLaunch struct {
	Plan      ghbackend.MaterializedPlan
	Launch    string
	Command   string
	CloudInit CloudInitArtifacts
	Disk      DiskArtifacts
	SSH       SSHArtifacts
	Worker    GuestWorkerArtifacts
	Spec      LaunchSpec
}

// MaterializePlan creates the QEMU instance directory and writes launcher
// artifacts derived from the worker plan.
func MaterializePlan(plan ghbackend.WorkerPlan) (MaterializedLaunch, error) {
	materialized, err := ghbackend.MaterializeWorkerPlan(plan)
	if err != nil {
		return MaterializedLaunch{}, err
	}
	spec, err := BuildLaunchSpec(plan)
	if err != nil {
		return MaterializedLaunch{}, err
	}
	sshArtifacts, err := materializeSSHArtifacts(spec)
	if err != nil {
		return MaterializedLaunch{}, err
	}
	workerArtifacts, err := materializeGuestWorker(spec, plan)
	if err != nil {
		return MaterializedLaunch{}, err
	}
	disk, err := materializeDiskArtifacts(spec)
	if err != nil {
		return MaterializedLaunch{}, err
	}
	cloudInit, err := materializeCloudInit(spec)
	if err != nil {
		return MaterializedLaunch{}, err
	}
	launchPath := filepath.Join(plan.InstanceDirectory, "qemu-launch.json")
	commandPath := filepath.Join(plan.InstanceDirectory, "launch.sh")
	if err := ghbackend.WriteJSONFile(launchPath, spec); err != nil {
		return MaterializedLaunch{}, err
	}
	if err := os.WriteFile(commandPath, []byte(renderCommand(spec)), 0o755); err != nil {
		return MaterializedLaunch{}, fmt.Errorf("write command %q: %w", commandPath, err)
	}
	return MaterializedLaunch{Plan: materialized, Launch: launchPath, Command: commandPath, CloudInit: cloudInit, Disk: disk, SSH: sshArtifacts, Worker: workerArtifacts, Spec: spec}, nil
}

func renderCommand(spec LaunchSpec) string {
	parts := make([]string, 0, 1+len(spec.Args))
	parts = append(parts, shellQuote(spec.Binary))
	for _, arg := range spec.Args {
		parts = append(parts, shellQuote(arg))
	}
	return "#!/usr/bin/env bash\nset -euo pipefail\n" +
		"if [ ! -s " + shellQuote(spec.BaseImagePath) + " ]; then\n  " + shellQuote(spec.FetchBaseImagePath) + "\nfi\n" +
		"if [ ! -s " + shellQuote(spec.CloudInitPath) + " ]; then\n  " + shellQuote(spec.CloudInitBuildPath) + "\nfi\n" +
		"if [ ! -e " + shellQuote(spec.RuntimeDiskPath) + " ]; then\n  " + shellQuote(spec.PrepareRuntimePath) + "\nfi\n" +
		"exec " + strings.Join(parts, " ") + "\n"
}

func shellQuote(value string) string {
	return fmt.Sprintf("%q", value)
}
