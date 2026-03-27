package vz

import (
	"fmt"
	"path/filepath"

	ghbackend "github.com/gkze/ghawfr/backend"
)

// MachineSpec is the concrete Virtualization.framework machine description
// derived from one worker plan.
type MachineSpec struct {
	GuestOS              ghbackend.GuestOS          `json:"guest_os"`
	CPUs                 int                        `json:"cpus"`
	MemoryMiB            int                        `json:"memory_mib"`
	Transport            ghbackend.TransportPlan    `json:"transport"`
	ImageSource          string                     `json:"image_source"`
	RuntimeDiskPath      string                     `json:"runtime_disk_path,omitempty"`
	RuntimeBundlePath    string                     `json:"runtime_bundle_path,omitempty"`
	GuestWorkspace       string                     `json:"guest_workspace,omitempty"`
	DirectoryShares      []ghbackend.DirectoryShare `json:"directory_shares,omitempty"`
	RequiresImageConvert bool                       `json:"requires_image_convert"`
	GuestAgentVSockPort  uint32                     `json:"guest_agent_vsock_port,omitempty"`
}

// BuildMachineSpec converts a worker plan into a concrete VZ machine spec.
func BuildMachineSpec(plan ghbackend.WorkerPlan) (MachineSpec, error) {
	if plan.Provider != ghbackend.ProviderKindVZ {
		return MachineSpec{}, fmt.Errorf("worker plan provider = %q, want %q", plan.Provider, ghbackend.ProviderKindVZ)
	}
	if plan.Image == nil {
		return MachineSpec{}, fmt.Errorf("worker plan image is nil")
	}
	spec := MachineSpec{
		GuestOS:             plan.Requirements.OS,
		CPUs:                4,
		MemoryMiB:           8192,
		Transport:           plan.Transport,
		ImageSource:         plan.Image.Source,
		GuestWorkspace:      plan.GuestWorkspace,
		DirectoryShares:     append([]ghbackend.DirectoryShare(nil), plan.Shares...),
		GuestAgentVSockPort: 6000,
	}
	switch plan.Image.RuntimeFormat {
	case ghbackend.ImageFormatRaw:
		spec.RuntimeDiskPath = filepath.Join(plan.InstanceDirectory, "disk.raw")
		spec.RequiresImageConvert = plan.Image.CanonicalFormat != ghbackend.ImageFormatRaw
	case ghbackend.ImageFormatTart:
		spec.RuntimeBundlePath = filepath.Join(plan.InstanceDirectory, "macos.tart")
	}
	return spec, nil
}
