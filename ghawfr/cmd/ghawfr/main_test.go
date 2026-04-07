package main

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/gkze/ghawfr/backend"
	"github.com/gkze/ghawfr/backend/qemu"
	"github.com/gkze/ghawfr/state"
	"github.com/gkze/ghawfr/workflow"
)

func TestRunStatePathUsesWorkspaceLocalHashedLocation(t *testing.T) {
	workspace := t.TempDir()
	workflowPath := filepath.Join(workspace, ".github", "workflows", "ci.yml")
	path := runStatePath(workspace, workflowPath)
	if !strings.HasPrefix(path, filepath.Join(workspace, ".ghawfr", "runs")+string(filepath.Separator)) {
		t.Fatalf("state path = %q, want under workspace .ghawfr/runs", path)
	}
	if !strings.Contains(filepath.Base(path), "ci-") {
		t.Fatalf("state path basename = %q, want ci-*", filepath.Base(path))
	}
}

func TestLoadRunStateCreatesFreshStateWhenMissing(t *testing.T) {
	workspace := t.TempDir()
	workflowPath := filepath.Join(workspace, "ci.yml")
	statePath := runStatePath(workspace, workflowPath)
	run, err := loadRunState(statePath, workflowPath)
	if err != nil {
		t.Fatalf("loadRunState: %v", err)
	}
	if run == nil {
		t.Fatal("run = nil, want state")
	}
	if run.SourcePath != workflowPath {
		t.Fatalf("run.SourcePath = %q, want %q", run.SourcePath, workflowPath)
	}
}

func TestLoadRunStateLoadsPersistedState(t *testing.T) {
	workspace := t.TempDir()
	workflowPath := filepath.Join(workspace, "ci.yml")
	statePath := runStatePath(workspace, workflowPath)
	run := state.NewRun(workflowPath)
	if err := run.Save(statePath); err != nil {
		t.Fatalf("Save: %v", err)
	}
	loaded, err := loadRunState(statePath, workflowPath)
	if err != nil {
		t.Fatalf("loadRunState: %v", err)
	}
	if loaded.SourcePath != workflowPath {
		t.Fatalf("loaded.SourcePath = %q, want %q", loaded.SourcePath, workflowPath)
	}
}

func TestWorkflowProviderSupportsExpectedModes(t *testing.T) {
	tests := []struct {
		name string
		mode string
	}{
		{name: "auto", mode: "auto"},
		{name: "local", mode: "local"},
		{name: "smoke-local", mode: "smoke-local"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			t.Setenv("GHAWFR_PROVIDER", test.mode)
			provider, err := workflowProvider()
			if err != nil {
				t.Fatalf("workflowProvider: %v", err)
			}
			if provider == nil {
				t.Fatal("provider = nil, want provider")
			}
		})
	}
}

func TestWorkflowProviderAutoEnablesUnsafeFallbackFromEnv(t *testing.T) {
	t.Setenv("GHAWFR_PROVIDER", "auto")
	t.Setenv("GHAWFR_UNSAFE_LOCAL_FALLBACK", "1")
	provider, err := workflowProvider()
	if err != nil {
		t.Fatalf("workflowProvider: %v", err)
	}
	auto, ok := provider.(backend.AutoProvider)
	if !ok {
		t.Fatalf("provider type = %T, want backend.AutoProvider", provider)
	}
	if auto.UnsafeLocalFallback == nil {
		t.Fatal("auto.UnsafeLocalFallback = nil, want local fallback worker")
	}
}

func TestWorkflowProviderRejectsUnknownMode(t *testing.T) {
	t.Setenv("GHAWFR_PROVIDER", "bogus")
	if _, err := workflowProvider(); err == nil {
		t.Fatal("workflowProvider error = nil, want invalid mode error")
	}
}

func TestDetectWorkspaceRootPrefersWorkflowRepository(t *testing.T) {
	cwd := t.TempDir()
	workflowRepo := t.TempDir()
	workflowPath := filepath.Join(workflowRepo, ".github", "workflows", "ci.yml")
	if err := os.MkdirAll(filepath.Dir(workflowPath), 0o755); err != nil {
		t.Fatalf("MkdirAll workflow dir: %v", err)
	}
	if err := os.WriteFile(workflowPath, []byte("name: test\n"), 0o644); err != nil {
		t.Fatalf("WriteFile workflow: %v", err)
	}
	for _, dir := range []string{cwd, workflowRepo} {
		cmd := exec.Command("git", "init")
		cmd.Dir = dir
		if output, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("git init %q: %v\n%s", dir, err, strings.TrimSpace(string(output)))
		}
	}
	got, err := detectWorkspaceRoot(cwd, workflowPath)
	if err != nil {
		t.Fatalf("detectWorkspaceRoot: %v", err)
	}
	resolvedGot, err := filepath.EvalSymlinks(got)
	if err != nil {
		t.Fatalf("EvalSymlinks(got): %v", err)
	}
	resolvedWant, err := filepath.EvalSymlinks(workflowRepo)
	if err != nil {
		t.Fatalf("EvalSymlinks(want): %v", err)
	}
	if resolvedGot != resolvedWant {
		t.Fatalf("detectWorkspaceRoot = %q (%q), want %q (%q)", got, resolvedGot, workflowRepo, resolvedWant)
	}
}

func TestLocalExpressionContextIncludesRunnerFields(t *testing.T) {
	workspace := t.TempDir()
	context, err := localExpressionContext(workspace)
	if err == nil {
		t.Fatal("localExpressionContext error = nil, want git probe warning")
	}
	if context.Runner.OS == "" {
		t.Fatal("context.Runner.OS = empty, want host runner os")
	}
	if context.Runner.Arch == "" {
		t.Fatal("context.Runner.Arch = empty, want host runner arch")
	}
	if got, want := context.Runner.Temp, filepath.Join(workspace, ".ghawfr", "runner", "temp"); got != want {
		t.Fatalf("context.Runner.Temp = %q, want %q", got, want)
	}
	if got, want := context.Runner.ToolCache, filepath.Join(workspace, ".ghawfr", "runner", "tool-cache"); got != want {
		t.Fatalf("context.Runner.ToolCache = %q, want %q", got, want)
	}
}

func TestFormatOutputMapSortsKeys(t *testing.T) {
	formatted := formatOutputMap(workflow.OutputMap{"z": "last", "a": "first"})
	if got, want := formatted, `a="first", z="last"`; got != want {
		t.Fatalf("formatOutputMap = %q, want %q", got, want)
	}
}

func TestConfiguredDurationSupportsSecondsAndDurations(t *testing.T) {
	t.Setenv("GHAWFR_TEST_TIMEOUT", "15")
	got, err := configuredDuration("GHAWFR_TEST_TIMEOUT", 2)
	if err != nil {
		t.Fatalf("configuredDuration seconds: %v", err)
	}
	if got != 15*time.Second {
		t.Fatalf("configuredDuration seconds = %v, want 15s", got)
	}

	t.Setenv("GHAWFR_TEST_TIMEOUT", "1m30s")
	got, err = configuredDuration("GHAWFR_TEST_TIMEOUT", 2)
	if err != nil {
		t.Fatalf("configuredDuration duration: %v", err)
	}
	if got != 90*time.Second {
		t.Fatalf("configuredDuration duration = %v, want 90s", got)
	}
}

func TestConfiguredDurationRejectsInvalidValues(t *testing.T) {
	t.Setenv("GHAWFR_TEST_TIMEOUT", "0")
	if _, err := configuredDuration("GHAWFR_TEST_TIMEOUT", time.Second); err == nil {
		t.Fatal("configuredDuration zero error = nil, want validation error")
	}

	t.Setenv("GHAWFR_TEST_TIMEOUT", "banana")
	if _, err := configuredDuration("GHAWFR_TEST_TIMEOUT", time.Second); err == nil {
		t.Fatal("configuredDuration parse error = nil, want validation error")
	}
}

func TestRunWorkflowContextSavesPartialStateOnError(t *testing.T) {
	root := t.TempDir()
	workflowPath := filepath.Join(root, "partial.yml")
	if err := os.WriteFile(workflowPath, []byte(`
name: Partial
on: workflow_dispatch
jobs:
  alpha:
    runs-on: macos-latest
    steps:
      - run: touch alpha.done
  beta:
    needs: alpha
    runs-on: macos-latest
    steps:
      - uses: actions/does-not-exist@v1
`), 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}
	t.Chdir(root)
	t.Setenv("GHAWFR_PROVIDER", "smoke-local")

	err := runWorkflowContext(context.Background(), workflowPath, "")
	if err == nil {
		t.Fatal("runWorkflowContext error = nil, want workflow error")
	}
	if !strings.Contains(err.Error(), "not supported by this backend") {
		t.Fatalf("runWorkflowContext error = %v, want unsupported action backend error", err)
	}

	statePath := runStatePath(root, workflowPath)
	runState, err := state.Load(statePath)
	if err != nil {
		t.Fatalf("Load state: %v", err)
	}
	record := runState.Jobs[workflow.JobID("alpha")]
	if record == nil {
		t.Fatal("state record for alpha = nil, want success record")
	}
	if got, want := record.Status, state.JobStatusSuccess; got != want {
		t.Fatalf("record.Status = %q, want %q", got, want)
	}
}

func TestRouteSummaryFormatsAutoProviderSelection(t *testing.T) {
	summary, err := routeSummary(
		backend.AutoProvider{QEMU: qemu.Provider{}},
		&workflow.Job{ID: "linux", RunsOn: workflow.Runner{Labels: []string{"ubuntu-24.04"}}},
		backend.RunOptions{WorkingDirectory: t.TempDir()},
	)
	if err != nil {
		t.Fatalf("routeSummary: %v", err)
	}
	for _, want := range []string{"provider=qemu", "guest=linux/x86_64", "image=qcow2->qcow2", "transport=ssh"} {
		if !strings.Contains(summary, want) {
			t.Fatalf("route summary = %q, want substring %q", summary, want)
		}
	}
}

func TestRouteSummaryFormatsStaticProviderSelection(t *testing.T) {
	summary, err := routeSummary(
		backend.StaticProvider{WorkerImpl: backend.Local{}},
		&workflow.Job{ID: "local"},
		backend.RunOptions{WorkingDirectory: t.TempDir()},
	)
	if err != nil {
		t.Fatalf("routeSummary: %v", err)
	}
	for _, want := range []string{"provider=local", "transport=host"} {
		if !strings.Contains(summary, want) {
			t.Fatalf("route summary = %q, want substring %q", summary, want)
		}
	}
}

func TestQEMULaunchForWorkflowBuildsQEMULaunchArtifacts(t *testing.T) {
	root := t.TempDir()
	workflowPath := filepath.Join(root, "vm.yml")
	if err := os.WriteFile(workflowPath, []byte(`
name: VM
on: push
jobs:
  linux:
    runs-on: ubuntu-24.04
    steps:
      - run: echo hi
`), 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}
	t.Chdir(root)
	t.Setenv("GHAWFR_PROVIDER", "auto")
	definition, job, plan, launch, err := qemuLaunchForWorkflow(workflowPath, "linux")
	if err != nil {
		t.Fatalf("qemuLaunchForWorkflow: %v", err)
	}
	if definition.Name != "VM" || job.ID != "linux" || plan.Provider != backend.ProviderKindQEMU {
		t.Fatalf("definition/job/plan = %#v %#v %#v, want VM/linux/qemu", definition.Name, job.ID, plan.Provider)
	}
	if launch.Command == "" || launch.SSH.SSHCommandPath == "" {
		t.Fatalf("launch = %#v, want command + ssh helper", launch)
	}
}

func TestRunStartAndProbeUseMaterializedQEMUHelpers(t *testing.T) {
	root := t.TempDir()
	workflowPath := filepath.Join(root, "vm.yml")
	if err := os.WriteFile(workflowPath, []byte(`
name: VM
on: push
jobs:
  linux:
    runs-on: ubuntu-24.04
    steps:
      - run: echo hi
`), 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}
	binDir := filepath.Join(root, "bin")
	if err := os.MkdirAll(binDir, 0o755); err != nil {
		t.Fatalf("MkdirAll bin: %v", err)
	}
	cwd, err := os.Getwd()
	if err != nil {
		t.Fatalf("Getwd: %v", err)
	}
	repoRoot := filepath.Clean(filepath.Join(cwd, "..", ".."))
	workerBinary := filepath.Join(binDir, "ghawfr-worker-bin")
	build := exec.Command("go", "build", "-o", workerBinary, filepath.Join(repoRoot, "cmd", "ghawfr-worker"))
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build ghawfr-worker: %v\n%s", err, strings.TrimSpace(string(output)))
	}
	for name, content := range map[string]string{
		"qemu-system-x86_64": "#!/usr/bin/env bash\nset -euo pipefail\nexec sleep 30\n",
		"qemu-img":           "#!/usr/bin/env bash\nset -euo pipefail\nout=\"${@: -1}\"\nmkdir -p \"$(dirname \"$out\")\"\n: > \"$out\"\n",
		"curl":               "#!/usr/bin/env bash\nset -euo pipefail\nout=\"\"\nwhile [ $# -gt 0 ]; do\n  if [ \"$1\" = \"--output\" ]; then\n    out=\"$2\"\n    shift 2\n    continue\n  fi\n  shift\ndone\nmkdir -p \"$(dirname \"$out\")\"\n: > \"$out\"\n",
		"ssh":                "#!/usr/bin/env bash\nset -euo pipefail\nwhile [ $# -gt 0 ]; do\n  case \"$1\" in\n    -o|-i|-p) shift 2 ;;\n    -*) shift ;;\n    *@*) shift; break ;;\n    *) break ;;\n  esac\ndone\nexec \"$@\"\n",
		"ghawfr-worker":      "#!/usr/bin/env bash\nset -euo pipefail\nexec \"" + workerBinary + "\" \"$@\"\n",
	} {
		path := filepath.Join(binDir, name)
		if err := os.WriteFile(path, []byte(content), 0o755); err != nil {
			t.Fatalf("WriteFile %s: %v", name, err)
		}
	}
	t.Chdir(root)
	t.Setenv("PATH", binDir+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("GHAWFR_PROVIDER", "auto")
	t.Setenv("GHAWFR_WORKER_REMOTE_COMMAND", filepath.Join(binDir, "ghawfr-worker"))
	if err := runStart(workflowPath, "linux"); err != nil {
		t.Fatalf("runStart: %v", err)
	}
	if err := runProbe(workflowPath, "linux", "uname -a"); err != nil {
		t.Fatalf("runProbe: %v", err)
	}
	instanceDir := backend.PlanInstanceDirectory(root, backend.ProviderKindQEMU, &workflow.Job{ID: "linux", LogicalID: "linux"})
	statePath := filepath.Join(instanceDir, "qemu-process.json")
	data, err := os.ReadFile(statePath)
	if err != nil {
		t.Fatalf("ReadFile qemu-process.json: %v", err)
	}
	var state qemu.ProcessState
	if err := json.Unmarshal(data, &state); err != nil {
		t.Fatalf("Unmarshal qemu-process.json: %v", err)
	}
	running, err := qemu.IsProcessRunning(state)
	if err != nil {
		t.Fatalf("IsProcessRunning: %v", err)
	}
	if !running {
		t.Fatal("running = false, want started process")
	}
	if err := runStop(workflowPath, "linux"); err != nil {
		t.Fatalf("runStop: %v", err)
	}
}

func TestRunPrepareMaterializesQEMUArtifacts(t *testing.T) {
	root := t.TempDir()
	workflowPath := filepath.Join(root, "vm.yml")
	if err := os.WriteFile(workflowPath, []byte(`
name: VM
on: push
jobs:
  linux:
    runs-on: ubuntu-24.04
    steps:
      - run: echo hi
`), 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}
	t.Chdir(root)
	t.Setenv("GHAWFR_PROVIDER", "auto")
	if err := runPrepare(workflowPath, "linux"); err != nil {
		t.Fatalf("runPrepare: %v", err)
	}
	instanceDir := backend.PlanInstanceDirectory(root, backend.ProviderKindQEMU, &workflow.Job{ID: "linux", LogicalID: "linux"})
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
		if _, err := os.Stat(path); err != nil {
			t.Fatalf("stat %q: %v", path, err)
		}
	}
}

func TestRunWorkflowExecutesSelectedLinuxJobThroughQEMUProvider(t *testing.T) {
	root := t.TempDir()
	workflowPath := filepath.Join(root, "vm.yml")
	if err := os.WriteFile(workflowPath, []byte(`
name: VM
on: push
jobs:
  linux:
    runs-on: ubuntu-24.04
    outputs:
      pkg: ${{ steps.setup.outputs.pkg }}
    steps:
      - uses: actions/cache@v5
        with:
          path: .cache/example
          key: demo
      - id: setup
        run: |
          test -d .cache/example
          mkdir -p .bin
          printf '#!/usr/bin/env sh\necho tool\n' > .bin/mytool
          chmod +x .bin/mytool
          echo 'FOO=bar' >> "$GITHUB_ENV"
          echo 'pkg=alpha' >> "$GITHUB_OUTPUT"
          echo "$GITHUB_WORKSPACE/.bin" >> "$GITHUB_PATH"
      - run: |
          test "$FOO" = bar
          test "$(mytool)" = tool
`), 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}
	binDir := filepath.Join(root, "bin")
	if err := os.MkdirAll(binDir, 0o755); err != nil {
		t.Fatalf("MkdirAll bin: %v", err)
	}
	cwd, err := os.Getwd()
	if err != nil {
		t.Fatalf("Getwd: %v", err)
	}
	repoRoot := filepath.Clean(filepath.Join(cwd, "..", ".."))
	workerBinary := filepath.Join(binDir, "ghawfr-worker-bin")
	build := exec.Command("go", "build", "-o", workerBinary, filepath.Join(repoRoot, "cmd", "ghawfr-worker"))
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build ghawfr-worker: %v\n%s", err, strings.TrimSpace(string(output)))
	}
	workerScript := filepath.Join(binDir, "ghawfr-worker-host")
	if err := os.WriteFile(workerScript, []byte("#!/usr/bin/env bash\nset -euo pipefail\nexec \""+workerBinary+"\" \"$@\"\n"), 0o755); err != nil {
		t.Fatalf("WriteFile ghawfr-worker-host: %v", err)
	}
	for name, content := range map[string]string{
		"qemu-system-x86_64": "#!/usr/bin/env bash\nset -euo pipefail\nexec sleep 30\n",
		"qemu-img":           "#!/usr/bin/env bash\nset -euo pipefail\nout=\"${@: -1}\"\nmkdir -p \"$(dirname \"$out\")\"\n: > \"$out\"\n",
		"curl":               "#!/usr/bin/env bash\nset -euo pipefail\nout=\"\"\nwhile [ $# -gt 0 ]; do\n  if [ \"$1\" = \"--output\" ]; then\n    out=\"$2\"\n    shift 2\n    continue\n  fi\n  shift\ndone\nmkdir -p \"$(dirname \"$out\")\"\n: > \"$out\"\n",
		"ssh":                "#!/usr/bin/env bash\nset -euo pipefail\nwhile [ $# -gt 0 ]; do\n  case \"$1\" in\n    -o|-i|-p) shift 2 ;;\n    -*) shift ;;\n    *@*) shift; break ;;\n    *) break ;;\n  esac\ndone\nexec \"$@\"\n",
	} {
		path := filepath.Join(binDir, name)
		if err := os.WriteFile(path, []byte(content), 0o755); err != nil {
			t.Fatalf("WriteFile %s: %v", name, err)
		}
	}
	t.Chdir(root)
	t.Setenv("PATH", binDir+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("GHAWFR_PROVIDER", "auto")
	t.Setenv("GHAWFR_QEMU_GUEST_WORKSPACE", root)
	t.Setenv("GHAWFR_WORKER_REMOTE_COMMAND", workerScript)
	if err := runWorkflow(workflowPath, "linux"); err != nil {
		t.Fatalf("runWorkflow: %v", err)
	}
	statePath := runStatePath(root, workflowPath)
	runState, err := state.Load(statePath)
	if err != nil {
		t.Fatalf("Load state: %v", err)
	}
	record := runState.Jobs[workflow.JobID("linux")]
	if record == nil {
		t.Fatal("state record for linux = nil, want success record")
	}
	if got, want := record.Status, state.JobStatusSuccess; got != want {
		t.Fatalf("record.Status = %q, want %q", got, want)
	}
	if got, want := record.Outputs["pkg"], "alpha"; got != want {
		t.Fatalf("record output pkg = %q, want %q", got, want)
	}
	if _, err := os.Stat(filepath.Join(root, ".bin", "mytool")); err != nil {
		t.Fatalf("stat remote-created tool: %v", err)
	}
}
