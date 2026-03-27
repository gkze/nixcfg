//go:build darwin

package vz

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	ghbackend "github.com/gkze/ghawfr/backend"
	"github.com/gkze/ghawfr/workflow"
)

func TestProviderAcquireWorkerMaterializesArtifacts(t *testing.T) {
	root := t.TempDir()
	job := &workflow.Job{ID: "darwin", RunsOn: workflow.Runner{Labels: []string{"macos-15"}}}
	_, err := (Provider{}).AcquireWorker(context.Background(), job, ghbackend.RunOptions{WorkingDirectory: root})
	if err == nil {
		t.Fatal("AcquireWorker error = nil, want not-yet-implemented error")
	}
	instanceDir := ghbackend.PlanInstanceDirectory(root, ghbackend.ProviderKindVZ, job)
	for _, path := range []string{
		filepath.Join(instanceDir, "plan.json"),
		filepath.Join(instanceDir, "host-checks.json"),
		filepath.Join(instanceDir, "vz-machine.json"),
	} {
		if _, statErr := os.Stat(path); statErr != nil {
			t.Fatalf("stat %q: %v", path, statErr)
		}
	}
}

func TestProviderPlanWorkerBuildsDarwinVZRoute(t *testing.T) {
	root := t.TempDir()
	plan, err := (Provider{}).PlanWorker(
		&workflow.Job{ID: "darwin", RunsOn: workflow.Runner{Labels: []string{"macos-15"}}},
		ghbackend.RunOptions{WorkingDirectory: root},
	)
	if err != nil {
		t.Fatalf("PlanWorker: %v", err)
	}
	if plan.Provider != ghbackend.ProviderKindVZ {
		t.Fatalf("plan.Provider = %q, want %q", plan.Provider, ghbackend.ProviderKindVZ)
	}
	if plan.Transport.Kind != ghbackend.TransportKindVSock {
		t.Fatalf("plan.Transport.Kind = %q, want %q", plan.Transport.Kind, ghbackend.TransportKindVSock)
	}
	if plan.Image == nil || plan.Image.RuntimeFormat != ghbackend.ImageFormatTart {
		t.Fatalf("plan.Image = %#v, want tart runtime", plan.Image)
	}
	if got := len(plan.Shares); got != 0 {
		t.Fatalf("len(plan.Shares) = %d, want 0 for current macOS guest plan", got)
	}
}

func TestProviderPlanWorkerBuildsLinuxArmVZRoute(t *testing.T) {
	root := t.TempDir()
	plan, err := (Provider{}).PlanWorker(
		&workflow.Job{ID: "linux-arm", RunsOn: workflow.Runner{Labels: []string{"ubuntu-24.04-arm"}}},
		ghbackend.RunOptions{WorkingDirectory: root},
	)
	if err != nil {
		t.Fatalf("PlanWorker: %v", err)
	}
	if plan.Image == nil || plan.Image.RuntimeFormat != ghbackend.ImageFormatRaw {
		t.Fatalf("plan.Image = %#v, want raw runtime", plan.Image)
	}
	if got := plan.GuestWorkspace; got != "/workspace" {
		t.Fatalf("plan.GuestWorkspace = %q, want /workspace", got)
	}
	if len(plan.Shares) != 1 || plan.Shares[0].GuestPath != "/workspace" {
		t.Fatalf("plan.Shares = %#v, want workspace share", plan.Shares)
	}
}
