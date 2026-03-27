package qemu

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	ghbackend "github.com/gkze/ghawfr/backend"
	"github.com/gkze/ghawfr/workflow"
)

func TestProviderAcquireWorkerMaterializesArtifactsBeforeReportingMissingBinary(t *testing.T) {
	root := t.TempDir()
	job := &workflow.Job{ID: "linux", RunsOn: workflow.Runner{Labels: []string{"ubuntu-24.04"}}}
	_, err := (Provider{}).AcquireWorker(context.Background(), job, ghbackend.RunOptions{WorkingDirectory: root})
	if err == nil {
		t.Fatal("AcquireWorker error = nil, want missing-binary error")
	}
	instanceDir := ghbackend.PlanInstanceDirectory(root, ghbackend.ProviderKindQEMU, job)
	for _, path := range []string{
		filepath.Join(instanceDir, "plan.json"),
		filepath.Join(instanceDir, "host-checks.json"),
		filepath.Join(instanceDir, "qemu-launch.json"),
		filepath.Join(instanceDir, "launch.sh"),
		filepath.Join(instanceDir, "fetch-base-image.sh"),
		filepath.Join(instanceDir, "prepare-runtime-disk.sh"),
		filepath.Join(instanceDir, "build-cloud-init.sh"),
		filepath.Join(instanceDir, "build-ghawfr-worker.sh"),
		filepath.Join(instanceDir, "ssh-guest.sh"),
		filepath.Join(instanceDir, "wait-for-ssh.sh"),
		filepath.Join(instanceDir, "cloud-init", "user-data"),
		filepath.Join(instanceDir, "cloud-init", "meta-data"),
		filepath.Join(instanceDir, "cloud-init", "network-config"),
		filepath.Join(instanceDir, "id_ed25519"),
		filepath.Join(instanceDir, "id_ed25519.pub"),
	} {
		if _, statErr := os.Stat(path); statErr != nil {
			t.Fatalf("stat %q: %v", path, statErr)
		}
	}
}

func TestProviderAcquireWorkerStartsRemoteWorkerWithFakeToolchain(t *testing.T) {
	root := t.TempDir()
	binDir := filepath.Join(root, "bin")
	if err := os.MkdirAll(binDir, 0o755); err != nil {
		t.Fatalf("MkdirAll bin: %v", err)
	}
	repoRoot, err := moduleRoot()
	if err != nil {
		t.Fatalf("moduleRoot: %v", err)
	}
	workerBinary := filepath.Join(binDir, "ghawfr-worker-bin")
	build := exec.Command("go", "build", "-o", workerBinary, filepath.Join(repoRoot, "cmd", "ghawfr-worker"))
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build ghawfr-worker: %v\n%s", err, strings.TrimSpace(string(output)))
	}
	for name, content := range map[string]string{
		"ghawfr-worker":      "#!/usr/bin/env bash\nset -euo pipefail\nexec \"" + workerBinary + "\" \"$@\"\n",
		"qemu-system-x86_64": "#!/usr/bin/env bash\nset -euo pipefail\nexec sleep 30\n",
		"qemu-img":           "#!/usr/bin/env bash\nset -euo pipefail\nout=\"${@: -1}\"\nmkdir -p \"$(dirname \"$out\")\"\n: > \"$out\"\n",
		"curl":               "#!/usr/bin/env bash\nset -euo pipefail\nout=\"\"\nwhile [ $# -gt 0 ]; do\n  if [ \"$1\" = \"--output\" ]; then\n    out=\"$2\"\n    shift 2\n    continue\n  fi\n  shift\ndone\nmkdir -p \"$(dirname \"$out\")\"\n: > \"$out\"\n",
		"ssh":                "#!/usr/bin/env bash\nset -euo pipefail\nwhile [ $# -gt 0 ]; do\n  case \"$1\" in\n    -o|-i|-p) shift 2 ;;\n    -*) shift ;;\n    *@*) shift; break ;;\n    *) break ;;\n  esac\ndone\nexec \"$@\"\n",
	} {
		if err := os.WriteFile(filepath.Join(binDir, name), []byte(content), 0o755); err != nil {
			t.Fatalf("WriteFile %s: %v", name, err)
		}
	}
	t.Setenv("PATH", binDir+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("GHAWFR_WORKER_REMOTE_COMMAND", filepath.Join(binDir, "ghawfr-worker"))
	job := &workflow.Job{ID: "linux", LogicalID: "linux", RunsOn: workflow.Runner{Labels: []string{"ubuntu-24.04"}}}
	lease, err := (Provider{}).AcquireWorker(context.Background(), job, ghbackend.RunOptions{WorkingDirectory: root})
	if err != nil {
		t.Fatalf("AcquireWorker: %v", err)
	}
	defer func() {
		_ = lease.Release(context.Background())
	}()
	if lease.Worker() == nil {
		t.Fatal("lease.Worker() = nil, want started remote worker")
	}
}

func TestProviderPlanWorkerBuildsLinuxQEMURoute(t *testing.T) {
	root := t.TempDir()
	plan, err := (Provider{}).PlanWorker(
		&workflow.Job{ID: "linux", RunsOn: workflow.Runner{Labels: []string{"ubuntu-24.04"}}},
		ghbackend.RunOptions{WorkingDirectory: root},
	)
	if err != nil {
		t.Fatalf("PlanWorker: %v", err)
	}
	if plan.Provider != ghbackend.ProviderKindQEMU {
		t.Fatalf("plan.Provider = %q, want %q", plan.Provider, ghbackend.ProviderKindQEMU)
	}
	if plan.Transport.Kind != ghbackend.TransportKindSSH {
		t.Fatalf("plan.Transport.Kind = %q, want %q", plan.Transport.Kind, ghbackend.TransportKindSSH)
	}
	if plan.Image == nil || plan.Image.RuntimeFormat != ghbackend.ImageFormatQCOW2 {
		t.Fatalf("plan.Image = %#v, want qcow2 runtime", plan.Image)
	}
	if got := plan.GuestWorkspace; got != "/workspace" {
		t.Fatalf("plan.GuestWorkspace = %q, want /workspace", got)
	}
	if len(plan.HostRequirements) == 0 || !strings.Contains(plan.HostRequirements[0].Name, "qemu-system") {
		t.Fatalf("plan.HostRequirements = %#v, want qemu host requirement", plan.HostRequirements)
	}
}
