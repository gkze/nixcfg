package backend

import (
	"strings"
	"testing"
)

func TestValidateHostRequirementsChecksBinaryPresence(t *testing.T) {
	checks := ValidateHostRequirements(WorkerPlan{HostRequirements: []HostRequirement{{Kind: "binary", Name: "sh"}}})
	if len(checks) != 1 {
		t.Fatalf("len(checks) = %d, want 1", len(checks))
	}
	if !checks[0].Satisfied {
		t.Fatalf("checks[0] = %#v, want satisfied binary", checks[0])
	}
}

func TestMissingHostRequirementsReturnsMissingBinaries(t *testing.T) {
	missing := MissingHostRequirements(WorkerPlan{HostRequirements: []HostRequirement{{Kind: "binary", Name: "ghawfr-definitely-missing-binary"}}})
	if len(missing) != 1 {
		t.Fatalf("len(missing) = %d, want 1", len(missing))
	}
	if got, want := missing[0].Name, "ghawfr-definitely-missing-binary"; got != want {
		t.Fatalf("missing[0].Name = %q, want %q", got, want)
	}
}

func TestEnsureHostRequirementsReportsMissingBinaries(t *testing.T) {
	err := EnsureHostRequirements(WorkerPlan{HostRequirements: []HostRequirement{{Kind: "binary", Name: "ghawfr-definitely-missing-binary"}}})
	if err == nil {
		t.Fatal("EnsureHostRequirements error = nil, want missing binary error")
	}
	if !strings.Contains(err.Error(), "ghawfr-definitely-missing-binary") {
		t.Fatalf("error = %q, want missing binary name", err)
	}
}
