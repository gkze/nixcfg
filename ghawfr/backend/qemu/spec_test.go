package qemu

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	ghbackend "github.com/gkze/ghawfr/backend"
)

func TestBuildLaunchSpecIncludesSSHForwardingAndWorkspaceShare(t *testing.T) {
	root := t.TempDir()
	plan := ghbackend.WorkerPlan{
		Provider:          ghbackend.ProviderKindQEMU,
		Requirements:      ghbackend.WorkerRequirements{OS: ghbackend.GuestOSLinux, Arch: ghbackend.GuestArchX8664},
		WorkingDirectory:  root,
		GuestWorkspace:    "/workspace",
		InstanceDirectory: filepath.Join(root, ".ghawfr", "workers", "qemu", "linux-1234abcd"),
		Image:             &ghbackend.ImagePlan{Source: "https://example.invalid/noble.qcow2"},
		Shares: []ghbackend.DirectoryShare{{
			HostPath:  root,
			GuestPath: "/workspace",
		}},
	}
	spec, err := BuildLaunchSpec(plan)
	if err != nil {
		t.Fatalf("BuildLaunchSpec: %v", err)
	}
	if got, want := spec.Binary, "qemu-system-x86_64"; got != want {
		t.Fatalf("spec.Binary = %q, want %q", got, want)
	}
	joined := strings.Join(spec.Args, " ")
	for _, want := range []string{"hostfwd=tcp:127.0.0.1:", "file=" + filepath.Join(plan.InstanceDirectory, "disk.qcow2"), "mount_tag=workspace"} {
		if !strings.Contains(joined, want) {
			t.Fatalf("joined args = %q, want substring %q", joined, want)
		}
	}
	if !strings.Contains(spec.SSHAddress, ":") || spec.SSHPort == 0 {
		t.Fatalf("ssh address/port = %q/%d, want concrete loopback port", spec.SSHAddress, spec.SSHPort)
	}
	for _, path := range []string{spec.SSHPrivateKeyPath, spec.SSHPublicKeyPath, spec.SSHCommandPath, spec.WaitForSSHPath, spec.CloudInitDir, spec.BaseImagePath, spec.FetchBaseImagePath, spec.PrepareRuntimePath, spec.CloudInitBuildPath, spec.WorkerBinaryPath, spec.WorkerBuildPath, spec.GuestWorkerPath} {
		if path == "" {
			t.Fatalf("launch spec path is empty: %#v", spec)
		}
	}
}

func TestMaterializePlanWritesLaunchArtifacts(t *testing.T) {
	root := t.TempDir()
	plan := ghbackend.WorkerPlan{
		Provider:          ghbackend.ProviderKindQEMU,
		Requirements:      ghbackend.WorkerRequirements{OS: ghbackend.GuestOSLinux, Arch: ghbackend.GuestArchX8664},
		WorkingDirectory:  root,
		GuestWorkspace:    "/workspace",
		InstanceDirectory: filepath.Join(root, ".ghawfr", "workers", "qemu", "linux-1234abcd"),
		Image:             &ghbackend.ImagePlan{Source: "https://example.invalid/noble.qcow2"},
	}
	artifacts, err := MaterializePlan(plan)
	if err != nil {
		t.Fatalf("MaterializePlan: %v", err)
	}
	for _, path := range []string{
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
		artifacts.Spec.SSHPrivateKeyPath,
		artifacts.Spec.SSHPublicKeyPath,
		artifacts.SSH.SSHCommandPath,
		artifacts.SSH.WaitForSSHPath,
		artifacts.Worker.BuildScriptPath,
		artifacts.Worker.BinaryPath,
	} {
		if _, err := os.Stat(path); err != nil {
			t.Fatalf("stat %q: %v", path, err)
		}
	}
	if artifacts.CloudInit.Builder != "" {
		if _, err := os.Stat(artifacts.CloudInit.ISOPath); err != nil {
			t.Fatalf("stat cloud-init iso %q: %v", artifacts.CloudInit.ISOPath, err)
		}
	}
	userData, err := os.ReadFile(artifacts.CloudInit.UserDataPath)
	if err != nil {
		t.Fatalf("ReadFile user-data: %v", err)
	}
	for _, want := range []string{"#cloud-config", "name: ghawfr", "ssh_authorized_keys:"} {
		if !strings.Contains(string(userData), want) {
			t.Fatalf("user-data = %q, want substring %q", string(userData), want)
		}
	}
	launchScript, err := os.ReadFile(artifacts.Command)
	if err != nil {
		t.Fatalf("ReadFile launch.sh: %v", err)
	}
	for _, want := range []string{"fetch-base-image.sh", "build-cloud-init.sh", "prepare-runtime-disk.sh", "qemu-system-x86_64"} {
		if !strings.Contains(string(launchScript), want) {
			t.Fatalf("launch.sh = %q, want substring %q", string(launchScript), want)
		}
	}
	if !artifacts.Worker.Built {
		t.Fatal("artifacts.Worker.Built = false, want built guest worker binary")
	}
	workerBuildScript, err := os.ReadFile(artifacts.Worker.BuildScriptPath)
	if err != nil {
		t.Fatalf("ReadFile build-ghawfr-worker.sh: %v", err)
	}
	for _, want := range []string{"GOOS=\"linux\"", "GOARCH=\"amd64\"", "./cmd/ghawfr-worker"} {
		if !strings.Contains(string(workerBuildScript), want) {
			t.Fatalf("build-ghawfr-worker.sh = %q, want substring %q", string(workerBuildScript), want)
		}
	}
	sshScript, err := os.ReadFile(artifacts.SSH.SSHCommandPath)
	if err != nil {
		t.Fatalf("ReadFile ssh-guest.sh: %v", err)
	}
	for _, want := range []string{"StrictHostKeyChecking=no", "127.0.0.1", "-p"} {
		if !strings.Contains(string(sshScript), want) {
			t.Fatalf("ssh-guest.sh = %q, want substring %q", string(sshScript), want)
		}
	}
}
