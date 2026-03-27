package qemu

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"

	ghbackend "github.com/gkze/ghawfr/backend"
)

// GuestWorkerArtifacts are the materialized artifacts used to bootstrap the
// guest-side ghawfr-worker binary.
type GuestWorkerArtifacts struct {
	BinaryPath      string
	BuildScriptPath string
	TargetOS        string
	TargetArch      string
	Built           bool
}

func materializeGuestWorker(spec LaunchSpec, plan ghbackend.WorkerPlan) (GuestWorkerArtifacts, error) {
	artifacts := GuestWorkerArtifacts{
		BinaryPath:      spec.WorkerBinaryPath,
		BuildScriptPath: spec.WorkerBuildPath,
	}
	if artifacts.BinaryPath == "" || artifacts.BuildScriptPath == "" {
		return GuestWorkerArtifacts{}, fmt.Errorf("guest worker paths are empty")
	}
	moduleRoot, err := detectGHAWFRModuleRoot(plan.WorkingDirectory)
	if err != nil {
		return GuestWorkerArtifacts{}, err
	}
	goos, goarch, err := goTargetForRequirements(plan.Requirements)
	if err != nil {
		return GuestWorkerArtifacts{}, err
	}
	artifacts.TargetOS = goos
	artifacts.TargetArch = goarch
	script := fmt.Sprintf(`#!/usr/bin/env bash
set -euo pipefail
mkdir -p %q
cd %q
CGO_ENABLED=0 GOOS=%q GOARCH=%q go build -o %q ./cmd/ghawfr-worker
`, filepath.Dir(artifacts.BinaryPath), moduleRoot, goos, goarch, artifacts.BinaryPath)
	if err := os.WriteFile(artifacts.BuildScriptPath, []byte(script), 0o755); err != nil {
		return GuestWorkerArtifacts{}, fmt.Errorf("write guest worker build script %q: %w", artifacts.BuildScriptPath, err)
	}
	if _, err := exec.LookPath("go"); err != nil {
		return artifacts, nil
	}
	cmd := exec.Command("bash", artifacts.BuildScriptPath)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return GuestWorkerArtifacts{}, fmt.Errorf("build guest worker with %q: %w\n%s", artifacts.BuildScriptPath, err, strings.TrimSpace(string(output)))
	}
	artifacts.Built = true
	return artifacts, nil
}

func detectGHAWFRModuleRoot(workingDirectory string) (string, error) {
	candidates := []string{
		filepath.Join(workingDirectory, "ghawfr"),
		workingDirectory,
	}
	if _, file, _, ok := runtime.Caller(0); ok {
		candidates = append(candidates, filepath.Clean(filepath.Join(filepath.Dir(file), "..", "..")))
	}
	for _, candidate := range candidates {
		if strings.TrimSpace(candidate) == "" {
			continue
		}
		info, err := os.Stat(filepath.Join(candidate, "go.mod"))
		if err == nil && !info.IsDir() {
			return candidate, nil
		}
	}
	return "", fmt.Errorf("could not find ghawfr module root from working directory %q", workingDirectory)
}

func goTargetForRequirements(requirements ghbackend.WorkerRequirements) (string, string, error) {
	var goos string
	switch requirements.OS {
	case ghbackend.GuestOSLinux:
		goos = "linux"
	case ghbackend.GuestOSDarwin:
		goos = "darwin"
	default:
		return "", "", fmt.Errorf("unsupported guest OS %q for worker bootstrap", requirements.OS)
	}
	var goarch string
	switch requirements.Arch {
	case ghbackend.GuestArchX8664:
		goarch = "amd64"
	case ghbackend.GuestArchAArch64:
		goarch = "arm64"
	default:
		return "", "", fmt.Errorf("unsupported guest arch %q for worker bootstrap", requirements.Arch)
	}
	return goos, goarch, nil
}
