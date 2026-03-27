package backend

import (
	"testing"

	"github.com/gkze/ghawfr/workflow"
)

func TestRequirementsForRunnerMapsKnownGitHubHostedLabels(t *testing.T) {
	tests := []struct {
		name   string
		runner workflow.Runner
		os     GuestOS
		arch   GuestArch
	}{
		{name: "ubuntu x64", runner: workflow.Runner{Labels: []string{"ubuntu-24.04"}}, os: GuestOSLinux, arch: GuestArchX8664},
		{name: "ubuntu arm", runner: workflow.Runner{Labels: []string{"ubuntu-24.04-arm"}}, os: GuestOSLinux, arch: GuestArchAArch64},
		{name: "macos", runner: workflow.Runner{Labels: []string{"macos-15"}}, os: GuestOSDarwin, arch: GuestArchAArch64},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			requirements, err := RequirementsForRunner(test.runner)
			if err != nil {
				t.Fatalf("RequirementsForRunner: %v", err)
			}
			if requirements.OS != test.os || requirements.Arch != test.arch {
				t.Fatalf("requirements = %#v, want %s/%s", requirements, test.os, test.arch)
			}
		})
	}
}

func TestImagePlanForProviderReturnsPortablePlans(t *testing.T) {
	linuxArm := WorkerRequirements{OS: GuestOSLinux, Arch: GuestArchAArch64}
	plan, err := ImagePlanForProvider(linuxArm, ProviderKindVZ)
	if err != nil {
		t.Fatalf("ImagePlanForProvider linuxArm/vz: %v", err)
	}
	if plan.CanonicalFormat != ImageFormatQCOW2 || plan.RuntimeFormat != ImageFormatRaw {
		t.Fatalf("linuxArm/vz plan = %#v, want qcow2 -> raw", plan)
	}

	linuxX64 := WorkerRequirements{OS: GuestOSLinux, Arch: GuestArchX8664}
	plan, err = ImagePlanForProvider(linuxX64, ProviderKindQEMU)
	if err != nil {
		t.Fatalf("ImagePlanForProvider linuxX64/qemu: %v", err)
	}
	if plan.CanonicalFormat != ImageFormatQCOW2 || plan.RuntimeFormat != ImageFormatQCOW2 {
		t.Fatalf("linuxX64/qemu plan = %#v, want qcow2 -> qcow2", plan)
	}

	macOS := WorkerRequirements{OS: GuestOSDarwin, Arch: GuestArchAArch64}
	plan, err = ImagePlanForProvider(macOS, ProviderKindVZ)
	if err != nil {
		t.Fatalf("ImagePlanForProvider macOS/vz: %v", err)
	}
	if plan.CanonicalFormat != ImageFormatTart || plan.RuntimeFormat != ImageFormatTart {
		t.Fatalf("macOS/vz plan = %#v, want tart", plan)
	}
}
