package backend

import (
	"fmt"
	"os/exec"
	"strings"
)

// HostCheck is the validation result for one host requirement.
type HostCheck struct {
	Requirement HostRequirement
	Satisfied   bool
	Detail      string
}

// ValidateHostRequirements checks provider prerequisites that can be validated
// cheaply on the current host.
func ValidateHostRequirements(plan WorkerPlan) []HostCheck {
	checks := make([]HostCheck, 0, len(plan.HostRequirements))
	for _, requirement := range plan.HostRequirements {
		check := HostCheck{Requirement: requirement}
		switch requirement.Kind {
		case "binary":
			path, err := exec.LookPath(requirement.Name)
			if err == nil {
				check.Satisfied = true
				check.Detail = path
			} else {
				check.Detail = err.Error()
			}
		default:
			check.Detail = "unchecked"
		}
		checks = append(checks, check)
	}
	return checks
}

// MissingHostRequirements returns the subset of requirements that are known to
// be unsatisfied on the current host.
func MissingHostRequirements(plan WorkerPlan) []HostRequirement {
	checks := ValidateHostRequirements(plan)
	missing := make([]HostRequirement, 0)
	for _, check := range checks {
		if check.Requirement.Kind == "binary" && !check.Satisfied {
			missing = append(missing, check.Requirement)
		}
	}
	return missing
}

func formatMissingHostRequirements(missing []HostRequirement) string {
	if len(missing) == 0 {
		return ""
	}
	parts := make([]string, 0, len(missing))
	for _, requirement := range missing {
		parts = append(parts, requirement.Name)
	}
	return strings.Join(parts, ", ")
}

// EnsureHostRequirements returns an error if any cheaply-checkable host
// prerequisite is known to be missing.
func EnsureHostRequirements(plan WorkerPlan) error {
	missing := MissingHostRequirements(plan)
	if len(missing) == 0 {
		return nil
	}
	return fmt.Errorf("missing host requirements: %s", formatMissingHostRequirements(missing))
}
