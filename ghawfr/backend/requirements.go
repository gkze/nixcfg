package backend

import (
	"fmt"
	"sort"
	"strings"

	"github.com/gkze/ghawfr/workflow"
)

// GuestOS identifies one guest operating system family.
type GuestOS string

const (
	// GuestOSLinux is a Linux guest.
	GuestOSLinux GuestOS = "linux"
	// GuestOSDarwin is a macOS guest.
	GuestOSDarwin GuestOS = "darwin"
)

// GuestArch identifies one guest CPU architecture.
type GuestArch string

const (
	// GuestArchX8664 is the x86_64 guest architecture.
	GuestArchX8664 GuestArch = "x86_64"
	// GuestArchAArch64 is the arm64/aarch64 guest architecture.
	GuestArchAArch64 GuestArch = "aarch64"
)

// ImageFormat identifies one VM image storage format.
type ImageFormat string

const (
	// ImageFormatQCOW2 is the canonical Linux cloud image/container format.
	ImageFormatQCOW2 ImageFormat = "qcow2"
	// ImageFormatRaw is the raw disk format required by Virtualization.framework.
	ImageFormatRaw ImageFormat = "raw"
	// ImageFormatTart is the Tart-distributed macOS image format family.
	ImageFormatTart ImageFormat = "tart"
)

// ProviderKind identifies one worker/provider family.
type ProviderKind string

const (
	// ProviderKindLocal is direct host execution.
	ProviderKindLocal ProviderKind = "local"
	// ProviderKindVZ is Apple Virtualization.framework-based execution.
	ProviderKindVZ ProviderKind = "vz"
	// ProviderKindQEMU is QEMU-based VM execution.
	ProviderKindQEMU ProviderKind = "qemu"
)

// WorkerRequirements is the normalized worker/platform requirement for one job.
type WorkerRequirements struct {
	Labels []string
	OS     GuestOS
	Arch   GuestArch
}

// ImagePlan describes the canonical and runtime image strategy for one provider.
type ImagePlan struct {
	Provider        ProviderKind
	CanonicalFormat ImageFormat
	RuntimeFormat   ImageFormat
	Source          string
}

// RequirementsForRunner derives normalized worker requirements from one runs-on specification.
func RequirementsForRunner(runner workflow.Runner) (WorkerRequirements, error) {
	if runner.Group != "" {
		return WorkerRequirements{}, fmt.Errorf("runner groups are not supported yet")
	}
	if runner.LabelsExpression != "" {
		return WorkerRequirements{}, fmt.Errorf("expression-based runs-on labels are not supported yet")
	}
	labels := normalizeLabels(runner.Labels)
	if len(labels) == 0 {
		return WorkerRequirements{}, fmt.Errorf("runner labels are empty")
	}
	for _, label := range labels {
		switch label {
		case "ubuntu-24.04-arm":
			return WorkerRequirements{Labels: labels, OS: GuestOSLinux, Arch: GuestArchAArch64}, nil
		case "ubuntu-24.04", "ubuntu-latest":
			return WorkerRequirements{Labels: labels, OS: GuestOSLinux, Arch: GuestArchX8664}, nil
		case "macos-15", "macos-latest":
			return WorkerRequirements{Labels: labels, OS: GuestOSDarwin, Arch: GuestArchAArch64}, nil
		}
	}
	return WorkerRequirements{}, fmt.Errorf("unsupported runner labels: %s", strings.Join(labels, ", "))
}

// ImagePlanForProvider returns the preferred image strategy for the given worker requirements and provider.
func ImagePlanForProvider(requirements WorkerRequirements, provider ProviderKind) (ImagePlan, error) {
	switch requirements.OS {
	case GuestOSLinux:
		switch requirements.Arch {
		case GuestArchX8664:
			switch provider {
			case ProviderKindQEMU:
				return ImagePlan{
					Provider:        provider,
					CanonicalFormat: ImageFormatQCOW2,
					RuntimeFormat:   ImageFormatQCOW2,
					Source:          "https://cloud-images.ubuntu.com/releases/noble/release/noble-server-cloudimg-amd64.img",
				}, nil
			case ProviderKindLocal:
				return ImagePlan{Provider: provider}, nil
			}
		case GuestArchAArch64:
			switch provider {
			case ProviderKindVZ:
				return ImagePlan{
					Provider:        provider,
					CanonicalFormat: ImageFormatQCOW2,
					RuntimeFormat:   ImageFormatRaw,
					Source:          "https://cloud-images.ubuntu.com/releases/noble/release/noble-server-cloudimg-arm64.img",
				}, nil
			case ProviderKindQEMU:
				return ImagePlan{
					Provider:        provider,
					CanonicalFormat: ImageFormatQCOW2,
					RuntimeFormat:   ImageFormatQCOW2,
					Source:          "https://cloud-images.ubuntu.com/releases/noble/release/noble-server-cloudimg-arm64.img",
				}, nil
			case ProviderKindLocal:
				return ImagePlan{Provider: provider}, nil
			}
		}
	case GuestOSDarwin:
		if requirements.Arch == GuestArchAArch64 && provider == ProviderKindVZ {
			return ImagePlan{
				Provider:        provider,
				CanonicalFormat: ImageFormatTart,
				RuntimeFormat:   ImageFormatTart,
				Source:          "ghcr.io/cirruslabs/macos-sequoia-xcode",
			}, nil
		}
	}
	return ImagePlan{}, fmt.Errorf("no image plan for %s/%s on provider %s", requirements.OS, requirements.Arch, provider)
}

func normalizeLabels(labels []string) []string {
	values := make([]string, 0, len(labels))
	for _, label := range labels {
		trimmed := strings.ToLower(strings.TrimSpace(label))
		if trimmed == "" {
			continue
		}
		values = append(values, trimmed)
	}
	sort.Strings(values)
	return values
}
