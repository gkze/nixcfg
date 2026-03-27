package vz

import (
	"os"
	"path/filepath"
	"testing"

	ghbackend "github.com/gkze/ghawfr/backend"
)

func TestBuildMachineSpecForLinuxArmUsesRawRuntimeDisk(t *testing.T) {
	plan := ghbackend.WorkerPlan{
		Provider:          ghbackend.ProviderKindVZ,
		Requirements:      ghbackend.WorkerRequirements{OS: ghbackend.GuestOSLinux, Arch: ghbackend.GuestArchAArch64},
		InstanceDirectory: filepath.Join(t.TempDir(), ".ghawfr", "workers", "vz", "linux-arm-1234abcd"),
		GuestWorkspace:    "/workspace",
		Shares:            []ghbackend.DirectoryShare{{HostPath: "/repo", GuestPath: "/workspace"}},
		Transport:         ghbackend.TransportPlan{Kind: ghbackend.TransportKindVSock, Address: "cid:3 port:6000"},
		Image: &ghbackend.ImagePlan{
			CanonicalFormat: ghbackend.ImageFormatQCOW2,
			RuntimeFormat:   ghbackend.ImageFormatRaw,
			Source:          "https://example.invalid/linux-arm.qcow2",
		},
	}
	spec, err := BuildMachineSpec(plan)
	if err != nil {
		t.Fatalf("BuildMachineSpec: %v", err)
	}
	if !spec.RequiresImageConvert {
		t.Fatal("spec.RequiresImageConvert = false, want true")
	}
	if got := spec.RuntimeDiskPath; got != filepath.Join(plan.InstanceDirectory, "disk.raw") {
		t.Fatalf("spec.RuntimeDiskPath = %q, want disk.raw under instance dir", got)
	}
}

func TestMaterializePlanWritesMachineArtifacts(t *testing.T) {
	root := t.TempDir()
	plan := ghbackend.WorkerPlan{
		Provider:          ghbackend.ProviderKindVZ,
		Requirements:      ghbackend.WorkerRequirements{OS: ghbackend.GuestOSDarwin, Arch: ghbackend.GuestArchAArch64},
		InstanceDirectory: filepath.Join(root, ".ghawfr", "workers", "vz", "mac-1234abcd"),
		Transport:         ghbackend.TransportPlan{Kind: ghbackend.TransportKindVSock, Address: "cid:3 port:6000"},
		Image: &ghbackend.ImagePlan{
			CanonicalFormat: ghbackend.ImageFormatTart,
			RuntimeFormat:   ghbackend.ImageFormatTart,
			Source:          "ghcr.io/cirruslabs/macos-sequoia-xcode",
		},
	}
	artifacts, err := MaterializePlan(plan)
	if err != nil {
		t.Fatalf("MaterializePlan: %v", err)
	}
	for _, path := range []string{artifacts.Plan.PlanPath, artifacts.Plan.HostChecksPath, artifacts.Machine} {
		if _, err := os.Stat(path); err != nil {
			t.Fatalf("stat %q: %v", path, err)
		}
	}
}
