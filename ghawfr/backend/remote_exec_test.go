package backend_test

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/gkze/ghawfr/actionadapter"
	"github.com/gkze/ghawfr/artifacts"
	"github.com/gkze/ghawfr/backend"
	ghcache "github.com/gkze/ghawfr/cache"
	"github.com/gkze/ghawfr/workflow"
)

type commandTransportFunc func(
	ctx context.Context,
	workingDirectory string,
	environment workflow.EnvironmentMap,
	command string,
) (backend.CommandResult, error)

func (f commandTransportFunc) ExecCommand(
	ctx context.Context,
	workingDirectory string,
	environment workflow.EnvironmentMap,
	command string,
) (backend.CommandResult, error) {
	return f(ctx, workingDirectory, environment, command)
}

func newRemoteTestWorker(
	labels []string,
	guestWorkspace string,
	commands backend.CommandTransport,
) backend.RemoteExecWorker {
	return actionadapter.NewRemoteExec(labels, guestWorkspace, commands)
}

func newRemoteShellTestWorker(
	labels []string,
	guestWorkspace string,
) backend.RemoteExecWorker {
	return newRemoteTestWorker(
		labels,
		guestWorkspace,
		commandTransportFunc(runShellCommandWithEnvironment),
	)
}

func newRemoteShellTestWorkerNoEnv(
	labels []string,
	guestWorkspace string,
) backend.RemoteExecWorker {
	return newRemoteTestWorker(
		labels,
		guestWorkspace,
		commandTransportFunc(runShellCommand),
	)
}

func runShellCommandWithEnvironment(
	ctx context.Context,
	workingDirectory string,
	environment workflow.EnvironmentMap,
	command string,
) (backend.CommandResult, error) {
	cmd := exec.CommandContext(ctx, "sh", "-c", command)
	cmd.Dir = workingDirectory
	env := os.Environ()
	for key, value := range environment {
		env = append(env, key+"="+value)
	}
	cmd.Env = env
	output, err := cmd.CombinedOutput()
	result := backend.CommandResult{Stdout: string(output)}
	if exitError, ok := err.(*exec.ExitError); ok {
		result.ExitCode = exitError.ExitCode()
		return result, nil
	}
	return result, err
}

func runShellCommand(
	ctx context.Context,
	workingDirectory string,
	_ workflow.EnvironmentMap,
	command string,
) (backend.CommandResult, error) {
	cmd := exec.CommandContext(ctx, "sh", "-c", command)
	cmd.Dir = workingDirectory
	cmd.Env = os.Environ()
	output, err := cmd.CombinedOutput()
	result := backend.CommandResult{Stdout: string(output)}
	if exitError, ok := err.(*exec.ExitError); ok {
		result.ExitCode = exitError.ExitCode()
		return result, nil
	}
	return result, err
}

const (
	remoteRunnerFilesystemCheckCommand = "test \"${{ runner.temp }}\" = \"$RUNNER_TEMP\"\n" +
		"test \"${{ runner.tool_cache }}\" = \"$RUNNER_TOOL_CACHE\"\n" +
		"test \"$HOME\" = \"/guest/workspace/.ghawfr/runner/home\""
	remoteRunnerHomeCheckCommand = "test \"$HOME\" = \"$GITHUB_WORKSPACE/.ghawfr/runner/home\"\n" +
		"test -d \"$HOME/.cache/nix\""
	remoteWorkspaceCacheSeedCommand = "mkdir -p \"$GITHUB_WORKSPACE/cache-dir\"\n" +
		"printf one > \"$GITHUB_WORKSPACE/cache-dir/value.txt\""
)

func TestRemoteExecWorkerRunJobUsesTransportAndPreservesFileCommandSemantics(t *testing.T) {
	workspace := t.TempDir()
	worker := newRemoteShellTestWorker(
		[]string{"ubuntu-24.04"},
		workspace,
	)
	job := &workflow.Job{
		ID:        "remote",
		LogicalID: "remote",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		OutputExpressions: workflow.OutputMap{
			"pkg": "${{ steps.setup.outputs.pkg }}",
		},
		OutputKeys: []string{"pkg"},
		Steps: []workflow.Step{
			{
				ID:   "setup",
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: strings.Join([]string{
						"mkdir -p .bin",
						"printf '#!/usr/bin/env sh\\necho tool\\n' > .bin/mytool",
						"chmod +x .bin/mytool",
						"echo 'FOO=bar' >> \"$GITHUB_ENV\"",
						"echo 'pkg=alpha' >> \"$GITHUB_OUTPUT\"",
						"echo \"$GITHUB_WORKSPACE/.bin\" >> \"$GITHUB_PATH\"",
					}, "\n"),
				},
			},
			{
				ID:   "verify",
				Kind: workflow.StepKindRun,
				Run:  &workflow.RunStep{Command: "test \"$FOO\" = bar\ntest \"$(mytool)\" = tool"},
			},
		},
	}
	result, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: workspace},
	)
	if err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	if got, want := result.Result, backend.JobStatusSuccess; got != want {
		t.Fatalf("result.Result = %q, want %q", got, want)
	}
	if got, want := result.Outputs["pkg"], "alpha"; got != want {
		t.Fatalf("job output pkg = %q, want %q", got, want)
	}
	if _, err := os.Stat(filepath.Join(workspace, ".bin", "mytool")); err != nil {
		t.Fatalf("stat shared workspace tool: %v", err)
	}
}

func TestRemoteExecWorkerRunJobHonorsRequestedShell(t *testing.T) {
	workspace := t.TempDir()
	worker := newRemoteShellTestWorkerNoEnv(
		[]string{"ubuntu-24.04"},
		workspace,
	)
	job := &workflow.Job{
		ID:        "shell",
		LogicalID: "shell",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			ID:   "bash",
			Kind: workflow.StepKindRun,
			Run: &workflow.RunStep{
				Shell:   "bash",
				Command: "values=(a b)\ntest \"${values[1]}\" = b",
			},
		}},
	}
	result, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: workspace},
	)
	if err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	if got, want := result.Result, backend.JobStatusSuccess; got != want {
		t.Fatalf("result.Result = %q, want %q", got, want)
	}
}

func TestRemoteExecWorkerRunsToolSetupActionsThroughRemoteTransport(t *testing.T) {
	workspace := t.TempDir()
	var commands []string
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		"/workspace",
		commandTransportFunc(
			func(
				_ context.Context,
				workingDirectory string,
				environment workflow.EnvironmentMap,
				command string,
			) (backend.CommandResult, error) {
				commands = append(commands, command)
				if got, want := workingDirectory, "/workspace"; got != want {
					t.Fatalf("workingDirectory = %q, want %q", got, want)
				}
				switch command {
				case "command -v nix":
					return backend.CommandResult{
						Stdout: "/nix/var/nix/profiles/default/bin/nix\n",
					}, nil
				case "command -v python3":
					return backend.CommandResult{Stdout: "/opt/python/bin/python3\n"}, nil
				case "command -v uv":
					return backend.CommandResult{Stdout: "/opt/uv/bin/uv\n"}, nil
				case "command -v uvx":
					return backend.CommandResult{Stdout: "/opt/uv/bin/uvx\n"}, nil
				case "'/opt/python/bin/python3' '--version'":
					return backend.CommandResult{Stdout: "Python 3.14.0\n"}, nil
				case "'python3' '--version'":
					return backend.CommandResult{Stdout: "Python 3.14.0\n"}, nil
				case "'/opt/uv/bin/uv' '--version'":
					return backend.CommandResult{Stdout: "uv 0.6.0\n"}, nil
				case "'/opt/uv/bin/uv' venv --clear '/workspace/.venv'":
					if err := os.MkdirAll(filepath.Join(workspace, ".venv", "bin"), 0o755); err != nil {
						t.Fatalf("mkdir remote venv: %v", err)
					}
					return backend.CommandResult{}, nil
				default:
					wantPythonLocation := "/workspace/.ghawfr/runner/tool-cache/Python/3.14/x64"
					if got := environment["pythonLocation"]; got != wantPythonLocation {
						t.Fatalf("pythonLocation = %q, want %q", got, wantPythonLocation)
					}
					wantUVCacheDir := "/workspace/.ghawfr/runner/tool-cache/uv-cache"
					if got := environment["UV_CACHE_DIR"]; got != wantUVCacheDir {
						t.Fatalf("UV_CACHE_DIR = %q, want %q", got, wantUVCacheDir)
					}
					if got, want := environment["VIRTUAL_ENV"], "/workspace/.venv"; got != want {
						t.Fatalf("VIRTUAL_ENV = %q, want %q", got, want)
					}
					wantPathPrefix := strings.Join([]string{
						"/workspace/.venv/bin",
						"/workspace/.ghawfr/runner/tool-cache/uv/system/x64/bin",
						"/workspace/.ghawfr/runner/tool-cache/Python/3.14/x64/bin",
						"/workspace/.ghawfr/runner/tool-cache/nix/system/x64/bin",
					}, ":") + ":"
					if !strings.HasPrefix(environment["PATH"], wantPathPrefix) {
						t.Fatalf("PATH = %q, want tool-cache paths prepended", environment["PATH"])
					}
					return backend.CommandResult{}, nil
				}
			},
		),
	)
	pythonPath := "/workspace/.ghawfr/runner/tool-cache/Python/3.14/x64/bin/python3"
	uvPath := "/workspace/.ghawfr/runner/tool-cache/uv/system/x64/bin/uv"
	uvxPath := "/workspace/.ghawfr/runner/tool-cache/uv/system/x64/bin/uvx"
	toolPathPrefix := strings.Join([]string{
		"/workspace/.venv/bin",
		"/workspace/.ghawfr/runner/tool-cache/uv/system/x64/bin",
		"/workspace/.ghawfr/runner/tool-cache/Python/3.14/x64/bin",
		"/workspace/.ghawfr/runner/tool-cache/nix/system/x64/bin",
	}, ":")
	job := &workflow.Job{
		ID:        "setup",
		LogicalID: "setup",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{
			{
				ID:     "nix",
				Kind:   workflow.StepKindAction,
				Action: &workflow.ActionStep{Uses: "DeterminateSystems/determinate-nix-action@v3"},
			},
			{
				ID:   "py",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses:   "actions/setup-python@v6",
					Inputs: workflow.ActionInputMap{"python-version": "3.14"},
				},
			},
			{
				ID:   "uv",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses:   "astral-sh/setup-uv@v6",
					Inputs: workflow.ActionInputMap{"activate-environment": "true"},
				},
			},
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: strings.Join([]string{
						`test "${{ steps.py.outputs.python-version }}" = 3.14`,
						`test "${{ steps.py.outputs.cache-hit }}" = false`,
						"test \"${{ steps.py.outputs.python-path }}\" = " + pythonPath,
						`test "${{ steps.uv.outputs.cache-hit }}" = false`,
						`test "${{ steps.uv.outputs.python-cache-hit }}" = false`,
						`test "${{ steps.uv.outputs.uv-version }}" = 0.6.0`,
						"test \"${{ steps.uv.outputs.uv-path }}\" = " + uvPath,
						"test \"${{ steps.uv.outputs.uvx-path }}\" = " + uvxPath,
						`test "${{ steps.uv.outputs.venv }}" = /workspace/.venv`,
						`test "$pythonLocation" = /workspace/.ghawfr/runner/tool-cache/Python/3.14/x64`,
						`test "$UV_CACHE_DIR" = /workspace/.ghawfr/runner/tool-cache/uv-cache`,
						`test "$VIRTUAL_ENV" = /workspace/.venv`,
						"case \"$PATH\" in " + toolPathPrefix + ":*) true ;; *) false ;; esac",
					}, "\n"),
				},
			},
		},
	}
	result, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: workspace},
	)
	if err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	if got, want := result.Result, backend.JobStatusSuccess; got != want {
		t.Fatalf("result.Result = %q, want %q", got, want)
	}
	for _, want := range []string{
		"command -v nix",
		"command -v python3",
		"command -v uv",
		"command -v uvx",
	} {
		if !containsString(commands, want) {
			t.Fatalf("commands = %#v, want %q", commands, want)
		}
	}
	if _, err := os.Stat(filepath.Join(workspace, ".venv", "bin")); err != nil {
		t.Fatalf("remote venv path missing on host workspace: %v", err)
	}
}

func TestRemoteExecWorkerSetupUVUsesResolvedWorkingDirectoryForVenvCreation(t *testing.T) {
	workspace := t.TempDir()
	var venvWorkingDirectory string
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		"/workspace",
		commandTransportFunc(
			func(
				_ context.Context,
				workingDirectory string,
				environment workflow.EnvironmentMap,
				command string,
			) (backend.CommandResult, error) {
				switch command {
				case "command -v uv":
					return backend.CommandResult{Stdout: "/opt/uv/bin/uv\n"}, nil
				case "command -v uvx":
					return backend.CommandResult{Stdout: "/opt/uv/bin/uvx\n"}, nil
				case "'python3' '--version'":
					return backend.CommandResult{Stdout: "Python 3.14.0\n"}, nil
				case "'/opt/uv/bin/uv' '--version'":
					return backend.CommandResult{Stdout: "uv 0.6.0\n"}, nil
				case "'/opt/uv/bin/uv' venv --clear '/workspace/subdir/.venv'":
					venvWorkingDirectory = workingDirectory
					if err := os.MkdirAll(filepath.Join(workspace, "subdir", ".venv", "bin"), 0o755); err != nil {
						t.Fatalf("mkdir remote venv: %v", err)
					}
					return backend.CommandResult{}, nil
				default:
					return backend.CommandResult{}, nil
				}
			},
		),
	)
	job := &workflow.Job{
		ID:        "uv-working-directory",
		LogicalID: "uv-working-directory",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			ID:   "uv",
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses: "astral-sh/setup-uv@v6",
				Inputs: workflow.ActionInputMap{
					"activate-environment": "true",
					"working-directory":    "subdir",
				},
			},
		}},
	}
	result, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: workspace},
	)
	if err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	if got, want := result.Result, backend.JobStatusSuccess; got != want {
		t.Fatalf("result.Result = %q, want %q", got, want)
	}
	if got, want := venvWorkingDirectory, "/workspace/subdir"; got != want {
		t.Fatalf("uv venv workingDirectory = %q, want %q", got, want)
	}
}

func TestRemoteExecWorkerRunStepSupportsAbsoluteGithubWorkspaceWorkingDirectory(t *testing.T) {
	hostWorkspace := t.TempDir()
	guestWorkspace := "/guest/workspace"
	var seenWorkingDirectory string
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		guestWorkspace,
		commandTransportFunc(
			func(
				_ context.Context,
				workingDirectory string,
				environment workflow.EnvironmentMap,
				command string,
			) (backend.CommandResult, error) {
				seenWorkingDirectory = workingDirectory
				return backend.CommandResult{}, nil
			},
		),
	)
	job := &workflow.Job{
		ID:        "run-absolute-workdir",
		LogicalID: "run-absolute-workdir",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			Kind: workflow.StepKindRun,
			Run: &workflow.RunStep{
				WorkingDirectory: "${{ github.workspace }}/subdir",
				Command:          "true",
			},
		}},
	}
	if _, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: hostWorkspace},
	); err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	if got, want := seenWorkingDirectory, "/guest/workspace/subdir"; got != want {
		t.Fatalf("workingDirectory = %q, want %q", got, want)
	}
}

func TestRemoteExecWorkerRunStepRejectsEscapingAbsoluteGithubWorkspaceWorkingDirectory(
	t *testing.T,
) {
	hostWorkspace := t.TempDir()
	guestWorkspace := "/guest/workspace"
	called := false
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		guestWorkspace,
		commandTransportFunc(
			func(
				_ context.Context,
				workingDirectory string,
				environment workflow.EnvironmentMap,
				command string,
			) (backend.CommandResult, error) {
				called = true
				return backend.CommandResult{}, nil
			},
		),
	)
	job := &workflow.Job{
		ID:        "run-absolute-workdir-escape",
		LogicalID: "run-absolute-workdir-escape",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			Kind: workflow.StepKindRun,
			Run: &workflow.RunStep{
				WorkingDirectory: "${{ github.workspace }}/../escape",
				Command:          "true",
			},
		}},
	}
	_, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: hostWorkspace},
	)
	if err == nil {
		t.Fatal("RunJob error = nil, want guest workspace escape failure")
	}
	if called {
		t.Fatal(
			"remote command transport was called, want guest workspace escape validation before execution",
		)
	}
	if !strings.Contains(err.Error(), "outside guest workspace") {
		t.Fatalf("RunJob error = %v, want guest workspace escape message", err)
	}
}

func TestRemoteExecWorkerRunStepRejectsHostAbsoluteWorkingDirectoryFallback(t *testing.T) {
	hostWorkspace := t.TempDir()
	guestWorkspace := "/guest/workspace"
	called := false
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		guestWorkspace,
		commandTransportFunc(
			func(
				_ context.Context,
				workingDirectory string,
				environment workflow.EnvironmentMap,
				command string,
			) (backend.CommandResult, error) {
				called = true
				return backend.CommandResult{}, nil
			},
		),
	)
	job := &workflow.Job{
		ID:        "run-host-absolute-workdir",
		LogicalID: "run-host-absolute-workdir",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			Kind: workflow.StepKindRun,
			Run: &workflow.RunStep{
				WorkingDirectory: filepath.Join(hostWorkspace, "subdir"),
				Command:          "true",
			},
		}},
	}
	_, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: hostWorkspace},
	)
	if err == nil {
		t.Fatal("RunJob error = nil, want host absolute remote working-directory failure")
	}
	if called {
		t.Fatal(
			"remote command transport was called, want host absolute path rejection before execution",
		)
	}
	if !strings.Contains(err.Error(), "outside guest workspace") {
		t.Fatalf("RunJob error = %v, want outside guest workspace message", err)
	}
}

func TestRemoteExecWorkerSetupUVRejectsEscapingRelativeWorkingDirectory(t *testing.T) {
	workspace := t.TempDir()
	var commands []string
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		"/workspace",
		commandTransportFunc(
			func(
				_ context.Context,
				workingDirectory string,
				environment workflow.EnvironmentMap,
				command string,
			) (backend.CommandResult, error) {
				commands = append(commands, command)
				switch command {
				case "command -v uv":
					return backend.CommandResult{Stdout: "/opt/uv/bin/uv\n"}, nil
				case "command -v uvx":
					return backend.CommandResult{Stdout: "/opt/uv/bin/uvx\n"}, nil
				case "'python3' '--version'":
					return backend.CommandResult{Stdout: "Python 3.14.0\n"}, nil
				case "'/opt/uv/bin/uv' '--version'":
					return backend.CommandResult{Stdout: "uv 0.6.0\n"}, nil
				default:
					return backend.CommandResult{}, nil
				}
			},
		),
	)
	job := &workflow.Job{
		ID:        "uv-escape-workdir",
		LogicalID: "uv-escape-workdir",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses: "astral-sh/setup-uv@v6",
				Inputs: workflow.ActionInputMap{
					"activate-environment": "true",
					"working-directory":    "../escape",
				},
			},
		}},
	}
	_, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: workspace},
	)
	if err == nil {
		t.Fatal("RunJob error = nil, want guest root escape failure")
	}
	for _, command := range commands {
		if strings.Contains(command, " venv --clear ") {
			t.Fatal("uv venv command executed, want fail-fast validation before uv venv execution")
		}
	}
	if !strings.Contains(err.Error(), "escapes guest root") {
		t.Fatalf("RunJob error = %v, want guest root escape message", err)
	}
}

func TestRemoteExecWorkerSetupUVRejectsEscapingAbsoluteGithubWorkspaceWorkingDirectory(
	t *testing.T,
) {
	workspace := t.TempDir()
	var commands []string
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		"/workspace",
		commandTransportFunc(
			func(
				_ context.Context,
				workingDirectory string,
				environment workflow.EnvironmentMap,
				command string,
			) (backend.CommandResult, error) {
				commands = append(commands, command)
				switch command {
				case "command -v uv":
					return backend.CommandResult{Stdout: "/opt/uv/bin/uv\n"}, nil
				case "command -v uvx":
					return backend.CommandResult{Stdout: "/opt/uv/bin/uvx\n"}, nil
				case "'python3' '--version'":
					return backend.CommandResult{Stdout: "Python 3.14.0\n"}, nil
				case "'/opt/uv/bin/uv' '--version'":
					return backend.CommandResult{Stdout: "uv 0.6.0\n"}, nil
				default:
					return backend.CommandResult{}, nil
				}
			},
		),
	)
	job := &workflow.Job{
		ID:        "uv-absolute-escape",
		LogicalID: "uv-absolute-escape",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses: "astral-sh/setup-uv@v6",
				Inputs: workflow.ActionInputMap{
					"activate-environment": "true",
					"working-directory":    "${{ github.workspace }}/../escape",
					"venv-path":            "${{ github.workspace }}/.venv",
				},
			},
		}},
	}
	_, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: workspace},
	)
	if err == nil {
		t.Fatal("RunJob error = nil, want guest working-directory escape failure")
	}
	for _, command := range commands {
		if strings.Contains(command, " venv --clear ") {
			t.Fatal(
				"uv venv command executed, want working-directory escape validation before uv venv execution",
			)
		}
	}
	if !strings.Contains(err.Error(), "outside allowed guest roots") {
		t.Fatalf("RunJob error = %v, want allowed roots escape message", err)
	}
}

func TestRemoteExecWorkerSetupUVRejectsRawAbsoluteWorkingDirectoryOutsideAllowedRoots(
	t *testing.T,
) {
	workspace := t.TempDir()
	var commands []string
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		"/workspace",
		commandTransportFunc(
			func(
				_ context.Context,
				workingDirectory string,
				environment workflow.EnvironmentMap,
				command string,
			) (backend.CommandResult, error) {
				commands = append(commands, command)
				switch command {
				case "command -v uv":
					return backend.CommandResult{Stdout: "/opt/uv/bin/uv\n"}, nil
				case "command -v uvx":
					return backend.CommandResult{Stdout: "/opt/uv/bin/uvx\n"}, nil
				case "'python3' '--version'":
					return backend.CommandResult{Stdout: "Python 3.14.0\n"}, nil
				case "'/opt/uv/bin/uv' '--version'":
					return backend.CommandResult{Stdout: "uv 0.6.0\n"}, nil
				default:
					return backend.CommandResult{}, nil
				}
			},
		),
	)
	job := &workflow.Job{
		ID:        "uv-raw-absolute-escape",
		LogicalID: "uv-raw-absolute-escape",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses: "astral-sh/setup-uv@v6",
				Inputs: workflow.ActionInputMap{
					"activate-environment": "true",
					"working-directory":    "/etc",
					"venv-path":            "${{ github.workspace }}/.venv",
				},
			},
		}},
	}
	_, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: workspace},
	)
	if err == nil {
		t.Fatal("RunJob error = nil, want raw absolute guest working-directory failure")
	}
	for _, command := range commands {
		if strings.Contains(command, " venv --clear ") {
			t.Fatal(
				"uv venv command executed, want raw absolute working-directory validation " +
					"before uv venv execution",
			)
		}
	}
	if !strings.Contains(err.Error(), "outside allowed guest roots") {
		t.Fatalf("RunJob error = %v, want allowed roots escape message", err)
	}
}

func TestRemoteExecWorkerSetupUVRejectsHostAbsoluteWorkingDirectoryFallback(t *testing.T) {
	workspace := t.TempDir()
	var commands []string
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		"/workspace",
		commandTransportFunc(
			func(
				_ context.Context,
				workingDirectory string,
				environment workflow.EnvironmentMap,
				command string,
			) (backend.CommandResult, error) {
				commands = append(commands, command)
				switch command {
				case "command -v uv":
					return backend.CommandResult{Stdout: "/opt/uv/bin/uv\n"}, nil
				case "command -v uvx":
					return backend.CommandResult{Stdout: "/opt/uv/bin/uvx\n"}, nil
				case "'python3' '--version'":
					return backend.CommandResult{Stdout: "Python 3.14.0\n"}, nil
				case "'/opt/uv/bin/uv' '--version'":
					return backend.CommandResult{Stdout: "uv 0.6.0\n"}, nil
				default:
					return backend.CommandResult{}, nil
				}
			},
		),
	)
	job := &workflow.Job{
		ID:        "uv-host-absolute-escape",
		LogicalID: "uv-host-absolute-escape",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses: "astral-sh/setup-uv@v6",
				Inputs: workflow.ActionInputMap{
					"activate-environment": "true",
					"working-directory":    filepath.Join(workspace, "subdir"),
					"venv-path":            "${{ github.workspace }}/.venv",
				},
			},
		}},
	}
	_, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: workspace},
	)
	if err == nil {
		t.Fatal("RunJob error = nil, want host absolute working-directory failure")
	}
	for _, command := range commands {
		if strings.Contains(command, " venv --clear ") {
			t.Fatal(
				"uv venv command executed, want host absolute working-directory validation " +
					"before uv venv execution",
			)
		}
	}
	if !strings.Contains(err.Error(), "outside allowed guest roots") {
		t.Fatalf("RunJob error = %v, want allowed roots escape message", err)
	}
}

func TestRemoteExecWorkerSetupUVActivationIgnoresUnsharedHomeOverrideWhenPathsStayInWorkspace(
	t *testing.T,
) {
	workspace := t.TempDir()
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		"/workspace",
		commandTransportFunc(
			func(
				_ context.Context,
				workingDirectory string,
				environment workflow.EnvironmentMap,
				command string,
			) (backend.CommandResult, error) {
				if got, want := workingDirectory, "/workspace"; got != want {
					t.Fatalf("workingDirectory = %q, want %q", got, want)
				}
				switch command {
				case "command -v uv":
					return backend.CommandResult{Stdout: "/opt/uv/bin/uv\n"}, nil
				case "command -v uvx":
					return backend.CommandResult{Stdout: "/opt/uv/bin/uvx\n"}, nil
				case "'python3' '--version'":
					return backend.CommandResult{Stdout: "Python 3.14.0\n"}, nil
				case "'/opt/uv/bin/uv' '--version'":
					return backend.CommandResult{Stdout: "uv 0.6.0\n"}, nil
				case "'/opt/uv/bin/uv' venv --clear '/workspace/.venv'":
					if err := os.MkdirAll(filepath.Join(workspace, ".venv", "bin"), 0o755); err != nil {
						t.Fatalf("mkdir remote venv: %v", err)
					}
					return backend.CommandResult{}, nil
				default:
					return backend.CommandResult{}, nil
				}
			},
		),
	)
	job := &workflow.Job{
		ID:        "uv-home-override",
		LogicalID: "uv-home-override",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Env:       workflow.EnvironmentMap{"HOME": "/tmp/guest-home"},
		Steps: []workflow.Step{{
			ID:   "uv",
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses:   "astral-sh/setup-uv@v6",
				Inputs: workflow.ActionInputMap{"activate-environment": "true"},
			},
		}},
	}
	result, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: workspace},
	)
	if err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	if got, want := result.Result, backend.JobStatusSuccess; got != want {
		t.Fatalf("result.Result = %q, want %q", got, want)
	}
	if _, err := os.Stat(filepath.Join(workspace, ".venv", "bin")); err != nil {
		t.Fatalf("remote venv path missing on host workspace: %v", err)
	}
}

func TestRemoteExecWorkerSupportsCreatePullRequestAction(t *testing.T) {
	workspace := t.TempDir()
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		"/workspace",
		commandTransportFunc(
			func(
				_ context.Context,
				workingDirectory string,
				environment workflow.EnvironmentMap,
				command string,
			) (backend.CommandResult, error) {
				return backend.CommandResult{}, nil
			},
		),
	)
	job := &workflow.Job{
		ID:        "create-pr",
		LogicalID: "create-pr",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses: "peter-evans/create-pull-request@v8",
				Inputs: workflow.ActionInputMap{
					"sign-commits":   "true",
					"branch":         "update_flake_lock_action",
					"delete-branch":  "true",
					"title":          "chore: update",
					"commit-message": "chore: update",
					"body-path":      "/tmp/pr-body.md",
				},
			},
		}},
	}
	result, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: workspace},
	)
	if err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	if got, want := result.Result, backend.JobStatusSuccess; got != want {
		t.Fatalf("result.Result = %q, want %q", got, want)
	}
}

func TestRemoteExecWorkerSetupPythonRespectsUpdateEnvironmentFalse(t *testing.T) {
	workspace := t.TempDir()
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		"/workspace",
		commandTransportFunc(
			func(
				_ context.Context,
				workingDirectory string,
				environment workflow.EnvironmentMap,
				command string,
			) (backend.CommandResult, error) {
				if got, want := workingDirectory, "/workspace"; got != want {
					t.Fatalf("workingDirectory = %q, want %q", got, want)
				}
				switch command {
				case "command -v python3":
					return backend.CommandResult{Stdout: "/opt/python/bin/python3\n"}, nil
				case "'/opt/python/bin/python3' '--version'":
					return backend.CommandResult{Stdout: "Python 3.14.2\n"}, nil
				default:
					return backend.CommandResult{}, nil
				}
			},
		),
	)
	job := &workflow.Job{
		ID:        "setup-python-no-env",
		LogicalID: "setup-python-no-env",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			ID:   "py",
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses: "actions/setup-python@v6",
				Inputs: workflow.ActionInputMap{
					"python-version":     "3.14",
					"update-environment": "false",
				},
			},
		}},
	}
	result, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: workspace},
	)
	if err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	step := result.Steps[0]
	if len(step.PathEntries) != 0 {
		t.Fatalf("PathEntries = %#v, want none", step.PathEntries)
	}
	if len(step.Environment) != 0 {
		t.Fatalf("Environment = %#v, want none", step.Environment)
	}
	if got, want := step.Outputs["python-version"], "3.14"; got != want {
		t.Fatalf("python-version output = %q, want %q", got, want)
	}
}

func TestRemoteExecWorkerSetupPythonSupportsMultilineVersionFallback(t *testing.T) {
	workspace := t.TempDir()
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		"/workspace",
		commandTransportFunc(
			func(
				_ context.Context,
				workingDirectory string,
				environment workflow.EnvironmentMap,
				command string,
			) (backend.CommandResult, error) {
				switch command {
				case "command -v python3":
					return backend.CommandResult{Stdout: "/opt/python/bin/python3\n"}, nil
				case "'/opt/python/bin/python3' '--version'":
					return backend.CommandResult{Stdout: "Python 3.14.2\n"}, nil
				default:
					return backend.CommandResult{}, nil
				}
			},
		),
	)
	job := &workflow.Job{
		ID:        "setup-python-fallback",
		LogicalID: "setup-python-fallback",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			ID:   "py",
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses:   "actions/setup-python@v6",
				Inputs: workflow.ActionInputMap{"python-version": "3.15\n3.14"},
			},
		}},
	}
	result, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: workspace},
	)
	if err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	if got, want := result.Steps[0].Outputs["python-version"], "3.14"; got != want {
		t.Fatalf("python-version output = %q, want %q", got, want)
	}
}

func TestRemoteExecWorkerSetupPythonFailsOnVersionMismatch(t *testing.T) {
	workspace := t.TempDir()
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		"/workspace",
		commandTransportFunc(
			func(
				_ context.Context,
				workingDirectory string,
				environment workflow.EnvironmentMap,
				command string,
			) (backend.CommandResult, error) {
				switch command {
				case "command -v python3":
					return backend.CommandResult{Stdout: "/opt/python/bin/python3\n"}, nil
				case "'/opt/python/bin/python3' '--version'":
					return backend.CommandResult{Stdout: "Python 3.13.1\n"}, nil
				default:
					return backend.CommandResult{}, nil
				}
			},
		),
	)
	job := &workflow.Job{
		ID:        "setup-python-mismatch",
		LogicalID: "setup-python-mismatch",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses:   "actions/setup-python@v6",
				Inputs: workflow.ActionInputMap{"python-version": "3.14"},
			},
		}},
	}
	if _, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: workspace},
	); err == nil {
		t.Fatal("RunJob error = nil, want python version mismatch failure")
	}
}

func TestRemoteExecWorkerSetupUVRejectsUnsupportedVersionInputs(t *testing.T) {
	workspace := t.TempDir()
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		"/workspace",
		commandTransportFunc(
			func(
				_ context.Context,
				workingDirectory string,
				environment workflow.EnvironmentMap,
				command string,
			) (backend.CommandResult, error) {
				return backend.CommandResult{}, nil
			},
		),
	)
	job := &workflow.Job{
		ID:        "setup-uv-unsupported",
		LogicalID: "setup-uv-unsupported",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses:   "astral-sh/setup-uv@v6",
				Inputs: workflow.ActionInputMap{"version": "0.6.0"},
			},
		}},
	}
	if _, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: workspace},
	); err == nil {
		t.Fatal("RunJob error = nil, want unsupported setup-uv input failure")
	}
}

func TestRemoteExecWorkerUsesGuestRunnerContextForConditions(t *testing.T) {
	workspace := t.TempDir()
	worker := newRemoteShellTestWorker(
		[]string{"macos-15"},
		workspace,
	)
	job := &workflow.Job{
		ID:        "runner-if",
		LogicalID: "runner-if",
		RunsOn:    workflow.Runner{Labels: []string{"macos-15"}},
		OutputExpressions: workflow.OutputMap{
			"ran": "${{ steps.emit.outputs.ran }}",
		},
		OutputKeys: []string{"ran"},
		Steps: []workflow.Step{{
			ID:   "emit",
			If:   "runner.os == 'macOS' && runner.arch == 'ARM64'",
			Kind: workflow.StepKindRun,
			Run:  &workflow.RunStep{Command: "echo 'ran=yes' >> \"$GITHUB_OUTPUT\""},
		}},
	}
	result, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: workspace},
	)
	if err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	if got, want := result.Outputs["ran"], "yes"; got != want {
		t.Fatalf("job output ran = %q, want %q", got, want)
	}
}

func TestRemoteExecWorkerCachixActionChecksGuestToolAndExportsEnv(t *testing.T) {
	workspace := t.TempDir()
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		workspace,
		commandTransportFunc(
			func(
				ctx context.Context,
				workingDirectory string,
				environment workflow.EnvironmentMap,
				command string,
			) (backend.CommandResult, error) {
				if command == "command -v cachix" {
					return backend.CommandResult{Stdout: "/opt/cachix/bin/cachix\n"}, nil
				}
				cmd := exec.CommandContext(ctx, "sh", "-c", command)
				cmd.Dir = workingDirectory
				env := os.Environ()
				for key, value := range environment {
					env = append(env, key+"="+value)
				}
				cmd.Env = env
				output, err := cmd.CombinedOutput()
				result := backend.CommandResult{Stdout: string(output)}
				if exitError, ok := err.(*exec.ExitError); ok {
					result.ExitCode = exitError.ExitCode()
					return result, nil
				}
				return result, err
			},
		),
	)
	job := &workflow.Job{
		ID:        "cachix",
		LogicalID: "cachix",
		Env:       workflow.EnvironmentMap{"CACHIX_NAME": "gkze"},
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{
			{
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses: "cachix/cachix-action@v16",
					Inputs: workflow.ActionInputMap{
						"name":      "${{ env.CACHIX_NAME }}",
						"authToken": "${{ secrets.CACHIX_AUTH_TOKEN }}",
					},
				},
			},
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{Command: "test \"$CACHIX_NAME\" = gkze\n" +
					"test \"$CACHIX_AUTH_TOKEN\" = token\n" +
					"case \":$PATH:\" in *:" +
					workspace +
					"/.ghawfr/runner/tool-cache/cachix/system/x64/bin:*) true ;; *) false ;; esac"},
			},
		},
	}
	result, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{
			WorkingDirectory: workspace,
			Expressions: workflow.ExpressionContext{
				Secrets: workflow.SecretMap{"CACHIX_AUTH_TOKEN": "token"},
			},
		},
	)
	if err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	if got, want := result.Result, backend.JobStatusSuccess; got != want {
		t.Fatalf("result.Result = %q, want %q", got, want)
	}
}

func TestRemoteExecWorkerProvidesGuestRunnerTempToolCacheAndHome(t *testing.T) {
	hostWorkspace := t.TempDir()
	var seenCommand string
	var seenEnv workflow.EnvironmentMap
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		"/guest/workspace",
		commandTransportFunc(
			func(
				_ context.Context,
				workingDirectory string,
				environment workflow.EnvironmentMap,
				command string,
			) (backend.CommandResult, error) {
				if got, want := workingDirectory, "/guest/workspace"; got != want {
					t.Fatalf("workingDirectory = %q, want %q", got, want)
				}
				seenCommand = command
				seenEnv = environment.Clone()
				return backend.CommandResult{}, nil
			},
		),
	)
	job := &workflow.Job{
		ID:        "runner-fs",
		LogicalID: "runner-fs",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			Kind: workflow.StepKindRun,
			Run: &workflow.RunStep{
				Command: remoteRunnerFilesystemCheckCommand,
			},
		}},
	}
	result, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: hostWorkspace},
	)
	if err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	if got, want := result.Result, backend.JobStatusSuccess; got != want {
		t.Fatalf("result.Result = %q, want %q", got, want)
	}
	if got, want := seenEnv["RUNNER_TEMP"], "/guest/workspace/.ghawfr/runner/temp"; got != want {
		t.Fatalf("RUNNER_TEMP = %q, want %q", got, want)
	}
	wantToolCache := "/guest/workspace/.ghawfr/runner/tool-cache"
	if got := seenEnv["RUNNER_TOOL_CACHE"]; got != wantToolCache {
		t.Fatalf("RUNNER_TOOL_CACHE = %q, want %q", got, wantToolCache)
	}
	if got := seenEnv["AGENT_TOOLSDIRECTORY"]; got != wantToolCache {
		t.Fatalf("AGENT_TOOLSDIRECTORY = %q, want %q", got, wantToolCache)
	}
	if got, want := seenEnv["HOME"], "/guest/workspace/.ghawfr/runner/home"; got != want {
		t.Fatalf("HOME = %q, want %q", got, want)
	}
	if !strings.Contains(seenCommand, "/guest/workspace/.ghawfr/runner/temp") {
		t.Fatalf("command = %q, want runner temp guest path", seenCommand)
	}
	if !strings.Contains(seenCommand, "/guest/workspace/.ghawfr/runner/tool-cache") {
		t.Fatalf("command = %q, want runner tool-cache guest path", seenCommand)
	}
	for _, path := range []string{
		filepath.Join(hostWorkspace, ".ghawfr", "runner", "temp"),
		filepath.Join(hostWorkspace, ".ghawfr", "runner", "tool-cache"),
		filepath.Join(hostWorkspace, ".ghawfr", "runner", "home"),
	} {
		if _, err := os.Stat(path); err != nil {
			t.Fatalf("stat %q: %v", path, err)
		}
	}
}

func TestRemoteExecWorkerCacheActionUsesGuestWorkspacePaths(t *testing.T) {
	hostWorkspace := t.TempDir()
	var commands []string
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		"/guest/workspace",
		commandTransportFunc(
			func(
				_ context.Context,
				workingDirectory string,
				environment workflow.EnvironmentMap,
				command string,
			) (backend.CommandResult, error) {
				commands = append(commands, command)
				if got, want := workingDirectory, "/guest/workspace"; got != want {
					t.Fatalf("workingDirectory = %q, want %q", got, want)
				}
				return backend.CommandResult{}, nil
			},
		),
	)
	job := &workflow.Job{
		ID:        "cache",
		LogicalID: "cache",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses:   "actions/cache@v5",
				Inputs: workflow.ActionInputMap{"path": "${{ github.workspace }}/.cache/nix"},
			},
		}},
	}
	_, err := worker.RunJob(context.Background(), job, backend.RunOptions{
		WorkingDirectory: hostWorkspace,
		Expressions: workflow.ExpressionContext{
			GitHub: workflow.GitHubContext{Workspace: hostWorkspace},
		},
	})
	if err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	if !containsString(commands, "mkdir -p -- '/guest/workspace/.cache/nix'") {
		t.Fatalf("commands = %#v, want guest-workspace mkdir", commands)
	}
}

func TestRemoteExecWorkerIgnoresUnsharedHomeOverrideForWorkspaceCachePaths(t *testing.T) {
	hostWorkspace := t.TempDir()
	guestWorkspace := "/guest/workspace"
	called := false
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		guestWorkspace,
		commandTransportFunc(
			func(
				_ context.Context,
				workingDirectory string,
				environment workflow.EnvironmentMap,
				command string,
			) (backend.CommandResult, error) {
				called = true
				if got, want := workingDirectory, guestWorkspace; got != want {
					t.Fatalf("workingDirectory = %q, want %q", got, want)
				}
				return backend.CommandResult{}, nil
			},
		),
	)
	job := &workflow.Job{
		ID:        "cache-home-ignored",
		LogicalID: "cache-home-ignored",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Env:       workflow.EnvironmentMap{"HOME": "/tmp/guest-home"},
		Steps: []workflow.Step{{
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses:   "actions/cache@v5",
				Inputs: workflow.ActionInputMap{"path": "${{ github.workspace }}/cache-dir"},
			},
		}},
	}
	if _, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{
			WorkingDirectory: hostWorkspace,
			Expressions: workflow.ExpressionContext{
				GitHub: workflow.GitHubContext{Workspace: hostWorkspace},
			},
		},
	); err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	if !called {
		t.Fatal("remote command transport was not called, want workspace cache path to proceed")
	}
}

func TestRemoteExecWorkerCacheActionSupportsHomeRelativePaths(t *testing.T) {
	workspace := t.TempDir()
	worker := newRemoteShellTestWorker(
		[]string{"ubuntu-24.04"},
		workspace,
	)
	job := &workflow.Job{
		ID:        "cache-home",
		LogicalID: "cache-home",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{
			{
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses:   "actions/cache@v5",
					Inputs: workflow.ActionInputMap{"path": "~/.cache/nix"},
				},
			},
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: remoteRunnerHomeCheckCommand,
				},
			},
		},
	}
	result, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: workspace},
	)
	if err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	if got, want := result.Result, backend.JobStatusSuccess; got != want {
		t.Fatalf("result.Result = %q, want %q", got, want)
	}
	if _, err := os.Stat(
		filepath.Join(workspace, ".ghawfr", "runner", "home", ".cache", "nix"),
	); err != nil {
		t.Fatalf("home cache path missing: %v", err)
	}
}

func TestRemoteExecWorkerArtifactActionsTranslateGuestWorkspacePaths(t *testing.T) {
	workspace := t.TempDir()
	store := artifacts.NewStore(filepath.Join(t.TempDir(), "artifacts"))
	if err := os.MkdirAll(filepath.Join(workspace, "dist"), 0o755); err != nil {
		t.Fatalf("MkdirAll dist: %v", err)
	}
	if err := os.WriteFile(
		filepath.Join(workspace, "dist", "out.txt"),
		[]byte("hello\n"),
		0o644,
	); err != nil {
		t.Fatalf("WriteFile dist/out.txt: %v", err)
	}
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		"/guest/workspace",
		commandTransportFunc(
			func(
				_ context.Context,
				_ string,
				_ workflow.EnvironmentMap,
				_ string,
			) (backend.CommandResult, error) {
				return backend.CommandResult{}, nil
			},
		),
	)
	expr := workflow.ExpressionContext{GitHub: workflow.GitHubContext{Workspace: workspace}}
	upload := &workflow.Job{
		ID:        "upload",
		LogicalID: "upload",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses: "actions/upload-artifact@v6",
				Inputs: workflow.ActionInputMap{
					"name": "bundle",
					"path": "${{ github.workspace }}/dist/*.txt",
				},
			},
		}},
	}
	if _, err := worker.RunJob(
		context.Background(),
		upload,
		backend.RunOptions{
			WorkingDirectory: workspace,
			Artifacts:        store,
			Expressions:      expr,
		},
	); err != nil {
		t.Fatalf("upload RunJob: %v", err)
	}
	download := &workflow.Job{
		ID:        "download",
		LogicalID: "download",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses: "actions/download-artifact@v7",
				Inputs: workflow.ActionInputMap{
					"name": "bundle",
					"path": "${{ github.workspace }}/restored",
				},
			},
		}},
	}
	if _, err := worker.RunJob(
		context.Background(),
		download,
		backend.RunOptions{
			WorkingDirectory: workspace,
			Artifacts:        store,
			Expressions:      expr,
		},
	); err != nil {
		t.Fatalf("download RunJob: %v", err)
	}
	data, err := os.ReadFile(filepath.Join(workspace, "restored", "dist", "out.txt"))
	if err != nil {
		t.Fatalf("ReadFile restored artifact: %v", err)
	}
	if got, want := string(data), "hello\n"; got != want {
		t.Fatalf("restored artifact = %q, want %q", got, want)
	}
}

func TestRemoteExecWorkerRestoresAndSavesWorkspaceCacheAcrossRuns(t *testing.T) {
	workspace := t.TempDir()
	store := ghcache.NewStore(t.TempDir())
	worker := newRemoteShellTestWorker(
		[]string{"ubuntu-24.04"},
		workspace,
	)
	seed := &workflow.Job{
		ID:        "seed-cache",
		LogicalID: "seed-cache",
		Steps: []workflow.Step{
			{
				ID:   "cache",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses: "actions/cache@v5",
					Inputs: workflow.ActionInputMap{
						"path": "${{ github.workspace }}/cache-dir",
						"key":  "remote-demo-v1",
					},
				},
			},
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: remoteWorkspaceCacheSeedCommand,
				},
			},
		},
	}
	expr := workflow.ExpressionContext{GitHub: workflow.GitHubContext{Workspace: workspace}}
	if _, err := worker.RunJob(
		context.Background(),
		seed,
		backend.RunOptions{
			WorkingDirectory: workspace,
			Cache:            store,
			Expressions:      expr,
		},
	); err != nil {
		t.Fatalf("seed RunJob: %v", err)
	}
	if err := os.RemoveAll(filepath.Join(workspace, "cache-dir")); err != nil {
		t.Fatalf("remove cache-dir: %v", err)
	}
	fallback := &workflow.Job{
		ID:        "fallback-cache",
		LogicalID: "fallback-cache",
		Steps: []workflow.Step{
			{
				ID:   "cache",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses: "actions/cache@v5",
					Inputs: workflow.ActionInputMap{
						"path":         "${{ github.workspace }}/cache-dir",
						"key":          "remote-demo-v2",
						"restore-keys": "remote-demo-",
					},
				},
			},
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: "test \"${{ steps.cache.outputs.cache-hit }}\" = false\n" +
						"test \"$(cat \"$GITHUB_WORKSPACE/cache-dir/value.txt\")\" = one\n" +
						"printf two > \"$GITHUB_WORKSPACE/cache-dir/value.txt\"",
				},
			},
		},
	}
	if _, err := worker.RunJob(
		context.Background(),
		fallback,
		backend.RunOptions{
			WorkingDirectory: workspace,
			Cache:            store,
			Expressions:      expr,
		},
	); err != nil {
		t.Fatalf("fallback RunJob: %v", err)
	}
	if err := os.RemoveAll(filepath.Join(workspace, "cache-dir")); err != nil {
		t.Fatalf("remove cache-dir again: %v", err)
	}
	restore := &workflow.Job{
		ID:        "restore-cache",
		LogicalID: "restore-cache",
		Steps: []workflow.Step{
			{
				ID:   "cache",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses: "actions/cache@v5",
					Inputs: workflow.ActionInputMap{
						"path":         "${{ github.workspace }}/cache-dir",
						"key":          "remote-demo-v2",
						"restore-keys": "remote-demo-",
					},
				},
			},
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: "test \"${{ steps.cache.outputs.cache-hit }}\" = true\n" +
						"test \"$(cat \"$GITHUB_WORKSPACE/cache-dir/value.txt\")\" = two",
				},
			},
		},
	}
	if _, err := worker.RunJob(
		context.Background(),
		restore,
		backend.RunOptions{
			WorkingDirectory: workspace,
			Cache:            store,
			Expressions:      expr,
		},
	); err != nil {
		t.Fatalf("restore RunJob: %v", err)
	}
}

func TestRemoteExecWorkerRestoresAndSavesHomeRelativeCacheAcrossRuns(t *testing.T) {
	workspace := t.TempDir()
	store := ghcache.NewStore(t.TempDir())
	worker := newRemoteShellTestWorker(
		[]string{"ubuntu-24.04"},
		workspace,
	)
	homeCacheRoot := filepath.Join(workspace, ".ghawfr", "runner", "home", ".cache", "demo")
	seed := &workflow.Job{
		ID:        "seed-home-cache",
		LogicalID: "seed-home-cache",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{
			{
				ID:   "cache",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses: "actions/cache@v5",
					Inputs: workflow.ActionInputMap{
						"path": "~/.cache/demo",
						"key":  "remote-home-v1",
					},
				},
			},
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: "mkdir -p \"$HOME/.cache/demo\"\nprintf one > \"$HOME/.cache/demo/value.txt\"",
				},
			},
		},
	}
	if _, err := worker.RunJob(
		context.Background(),
		seed,
		backend.RunOptions{
			WorkingDirectory: workspace,
			Cache:            store,
		},
	); err != nil {
		t.Fatalf("seed RunJob: %v", err)
	}
	if err := os.RemoveAll(homeCacheRoot); err != nil {
		t.Fatalf("remove home cache root: %v", err)
	}
	fallback := &workflow.Job{
		ID:        "fallback-home-cache",
		LogicalID: "fallback-home-cache",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{
			{
				ID:   "cache",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses: "actions/cache@v5",
					Inputs: workflow.ActionInputMap{
						"path":         "~/.cache/demo",
						"key":          "remote-home-v2",
						"restore-keys": "remote-home-",
					},
				},
			},
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: "test \"${{ steps.cache.outputs.cache-hit }}\" = false\n" +
						"test \"$(cat \"$HOME/.cache/demo/value.txt\")\" = one\n" +
						"printf two > \"$HOME/.cache/demo/value.txt\"",
				},
			},
		},
	}
	if _, err := worker.RunJob(
		context.Background(),
		fallback,
		backend.RunOptions{
			WorkingDirectory: workspace,
			Cache:            store,
		},
	); err != nil {
		t.Fatalf("fallback RunJob: %v", err)
	}
	if err := os.RemoveAll(homeCacheRoot); err != nil {
		t.Fatalf("remove home cache root again: %v", err)
	}
	restore := &workflow.Job{
		ID:        "restore-home-cache",
		LogicalID: "restore-home-cache",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{
			{
				ID:   "cache",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses: "actions/cache@v5",
					Inputs: workflow.ActionInputMap{
						"path":         "~/.cache/demo",
						"key":          "remote-home-v2",
						"restore-keys": "remote-home-",
					},
				},
			},
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: "test \"${{ steps.cache.outputs.cache-hit }}\" = true\n" +
						"test \"$(cat \"$HOME/.cache/demo/value.txt\")\" = two",
				},
			},
		},
	}
	if _, err := worker.RunJob(
		context.Background(),
		restore,
		backend.RunOptions{
			WorkingDirectory: workspace,
			Cache:            store,
		},
	); err != nil {
		t.Fatalf("restore RunJob: %v", err)
	}
}

func TestRemoteExecWorkerSupportsCacheLookupOnly(t *testing.T) {
	workspace := t.TempDir()
	store := ghcache.NewStore(t.TempDir())
	worker := newRemoteShellTestWorker(
		[]string{"ubuntu-24.04"},
		workspace,
	)
	expr := workflow.ExpressionContext{GitHub: workflow.GitHubContext{Workspace: workspace}}
	seed := &workflow.Job{
		ID:        "seed-lookup-cache",
		LogicalID: "seed-lookup-cache",
		Steps: []workflow.Step{
			{
				ID:   "cache",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses: "actions/cache@v5",
					Inputs: workflow.ActionInputMap{
						"path": "${{ github.workspace }}/cache-dir",
						"key":  "remote-lookup-v1",
					},
				},
			},
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: remoteWorkspaceCacheSeedCommand,
				},
			},
		},
	}
	if _, err := worker.RunJob(
		context.Background(),
		seed,
		backend.RunOptions{
			WorkingDirectory: workspace,
			Cache:            store,
			Expressions:      expr,
		},
	); err != nil {
		t.Fatalf("seed RunJob: %v", err)
	}
	if err := os.RemoveAll(filepath.Join(workspace, "cache-dir")); err != nil {
		t.Fatalf("remove cache-dir: %v", err)
	}
	lookup := &workflow.Job{
		ID:        "lookup-cache",
		LogicalID: "lookup-cache",
		Steps: []workflow.Step{
			{
				ID:   "cache",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses: "actions/cache@v5",
					Inputs: workflow.ActionInputMap{
						"path":        "${{ github.workspace }}/cache-dir",
						"key":         "remote-lookup-v1",
						"lookup-only": "true",
					},
				},
			},
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: "test \"${{ steps.cache.outputs.cache-hit }}\" = true\n" +
						"test ! -e \"$GITHUB_WORKSPACE/cache-dir/value.txt\"",
				},
			},
		},
	}
	if _, err := worker.RunJob(
		context.Background(),
		lookup,
		backend.RunOptions{
			WorkingDirectory: workspace,
			Cache:            store,
			Expressions:      expr,
		},
	); err != nil {
		t.Fatalf("lookup RunJob: %v", err)
	}
}

func TestRemoteExecWorkerFailsOnCacheMissWhenConfigured(t *testing.T) {
	workspace := t.TempDir()
	store := ghcache.NewStore(t.TempDir())
	worker := newRemoteShellTestWorkerNoEnv(
		[]string{"ubuntu-24.04"},
		workspace,
	)
	expr := workflow.ExpressionContext{GitHub: workflow.GitHubContext{Workspace: workspace}}
	job := &workflow.Job{
		ID:        "cache-miss",
		LogicalID: "cache-miss",
		Steps: []workflow.Step{{
			ID:   "cache",
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{Uses: "actions/cache@v5", Inputs: workflow.ActionInputMap{
				"path":               "${{ github.workspace }}/cache-dir",
				"key":                "remote-missing-key",
				"fail-on-cache-miss": "true",
			}},
		}},
	}
	if _, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{
			WorkingDirectory: workspace,
			Cache:            store,
			Expressions:      expr,
		},
	); err == nil {
		t.Fatal("RunJob error = nil, want cache miss failure")
	}
}

func TestRemoteExecWorkerSupportsHomeOverrideWithinSharedWorkspaceForCachePaths(t *testing.T) {
	hostWorkspace := t.TempDir()
	guestWorkspace := "/guest/workspace"
	var seenEnv workflow.EnvironmentMap
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		guestWorkspace,
		commandTransportFunc(
			func(
				_ context.Context,
				workingDirectory string,
				environment workflow.EnvironmentMap,
				command string,
			) (backend.CommandResult, error) {
				if got, want := workingDirectory, guestWorkspace; got != want {
					t.Fatalf("workingDirectory = %q, want %q", got, want)
				}
				seenEnv = environment.Clone()
				return backend.CommandResult{}, nil
			},
		),
	)
	job := &workflow.Job{
		ID:        "cache-home-override",
		LogicalID: "cache-home-override",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Env:       workflow.EnvironmentMap{"HOME": "${{ github.workspace }}/custom-home"},
		Steps: []workflow.Step{{
			ID:   "cache",
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{Uses: "actions/cache@v5", Inputs: workflow.ActionInputMap{
				"path": "~/.cache/demo",
			}},
		}},
	}
	if _, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{
			WorkingDirectory: hostWorkspace,
			Expressions: workflow.ExpressionContext{
				GitHub: workflow.GitHubContext{Workspace: hostWorkspace},
			},
		},
	); err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	if got, want := seenEnv["HOME"], "/guest/workspace/custom-home"; got != want {
		t.Fatalf("HOME = %q, want %q", got, want)
	}
}

func TestRemoteExecWorkerRejectsHomeOverrideOutsideSharedWorkspaceForCachePaths(t *testing.T) {
	workspace := t.TempDir()
	store := ghcache.NewStore(t.TempDir())
	called := false
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		workspace,
		commandTransportFunc(
			func(
				ctx context.Context,
				workingDirectory string,
				environment workflow.EnvironmentMap,
				command string,
			) (backend.CommandResult, error) {
				called = true
				cmd := exec.CommandContext(ctx, "sh", "-c", command)
				cmd.Dir = workingDirectory
				env := os.Environ()
				for key, value := range environment {
					env = append(env, key+"="+value)
				}
				cmd.Env = env
				output, err := cmd.CombinedOutput()
				result := backend.CommandResult{Stdout: string(output)}
				if exitError, ok := err.(*exec.ExitError); ok {
					result.ExitCode = exitError.ExitCode()
					return result, nil
				}
				return result, err
			},
		),
	)
	job := &workflow.Job{
		ID:        "cache-home",
		LogicalID: "cache-home",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Env:       workflow.EnvironmentMap{"HOME": "/tmp/guest-home"},
		Steps: []workflow.Step{{
			ID:   "cache",
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{Uses: "actions/cache@v5", Inputs: workflow.ActionInputMap{
				"path": "~/.cache/demo",
				"key":  "remote-home-cache",
			}},
		}},
	}
	_, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: workspace, Cache: store},
	)
	if err == nil {
		t.Fatal("RunJob error = nil, want unsupported remote HOME override failure")
	}
	if called {
		t.Fatal(
			"remote command transport was called, want fail-fast validation before guest mutation",
		)
	}
	if !strings.Contains(err.Error(), "resolve remote runner home") ||
		!strings.Contains(err.Error(), "outside guest workspace") {
		t.Fatalf("RunJob error = %v, want remote HOME outside workspace message", err)
	}
}

func TestRemoteExecWorkerRejectsCachePathTraversal(t *testing.T) {
	workspace := t.TempDir()
	store := ghcache.NewStore(t.TempDir())
	called := false
	worker := newRemoteTestWorker(
		[]string{"ubuntu-24.04"},
		workspace,
		commandTransportFunc(
			func(context.Context, string, workflow.EnvironmentMap, string) (backend.CommandResult, error) {
				called = true
				return backend.CommandResult{}, nil
			},
		),
	)
	job := &workflow.Job{
		ID:        "cache-escape",
		LogicalID: "cache-escape",
		Steps: []workflow.Step{{
			ID:   "cache",
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{Uses: "actions/cache@v5", Inputs: workflow.ActionInputMap{
				"path": "../escape",
				"key":  "remote-escape-cache",
			}},
		}},
	}
	_, err := worker.RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: workspace, Cache: store},
	)
	if err == nil {
		t.Fatal("RunJob error = nil, want guest workspace escape failure")
	}
	if called {
		t.Fatal("remote command transport was called, want guest path validation before execution")
	}
	if !strings.Contains(err.Error(), "escapes host workspace") {
		t.Fatalf("RunJob error = %v, want host workspace escape message", err)
	}
}

func TestRemoteExecWorkerSupportsCacheRestoreAndSaveActions(t *testing.T) {
	workspace := t.TempDir()
	store := ghcache.NewStore(t.TempDir())
	worker := newRemoteShellTestWorker(
		[]string{"ubuntu-24.04"},
		workspace,
	)
	expr := workflow.ExpressionContext{GitHub: workflow.GitHubContext{Workspace: workspace}}
	save := &workflow.Job{
		ID:        "save-cache",
		LogicalID: "save-cache",
		Steps: []workflow.Step{
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: remoteWorkspaceCacheSeedCommand,
				},
			},
			{
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses: "actions/cache/save@v4",
					Inputs: workflow.ActionInputMap{
						"path": "${{ github.workspace }}/cache-dir",
						"key":  "remote-split-v1",
					},
				},
			},
		},
	}
	if _, err := worker.RunJob(
		context.Background(),
		save,
		backend.RunOptions{
			WorkingDirectory: workspace,
			Cache:            store,
			Expressions:      expr,
		},
	); err != nil {
		t.Fatalf("save RunJob: %v", err)
	}
	if err := os.RemoveAll(filepath.Join(workspace, "cache-dir")); err != nil {
		t.Fatalf("remove cache-dir: %v", err)
	}

	fallback := &workflow.Job{
		ID:        "restore-save-cache",
		LogicalID: "restore-save-cache",
		Steps: []workflow.Step{
			{
				ID:   "restore",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses: "actions/cache/restore@v4",
					Inputs: workflow.ActionInputMap{
						"path":         "${{ github.workspace }}/cache-dir",
						"key":          "remote-split-v2",
						"restore-keys": "remote-split-",
					},
				},
			},
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: "test \"${{ steps.restore.outputs.cache-primary-key }}\" = remote-split-v2\n" +
						"test \"${{ steps.restore.outputs.cache-matched-key }}\" = remote-split-v1\n" +
						"test \"${{ steps.restore.outputs.cache-hit }}\" = false\n" +
						"test \"$(cat \"$GITHUB_WORKSPACE/cache-dir/value.txt\")\" = one\n" +
						"printf two > \"$GITHUB_WORKSPACE/cache-dir/value.txt\"",
				},
			},
			{
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses: "actions/cache/save@v4",
					Inputs: workflow.ActionInputMap{
						"path": "${{ github.workspace }}/cache-dir",
						"key":  "${{ steps.restore.outputs.cache-primary-key }}",
					},
				},
			},
		},
	}
	if _, err := worker.RunJob(
		context.Background(),
		fallback,
		backend.RunOptions{
			WorkingDirectory: workspace,
			Cache:            store,
			Expressions:      expr,
		},
	); err != nil {
		t.Fatalf("fallback RunJob: %v", err)
	}
	if err := os.RemoveAll(filepath.Join(workspace, "cache-dir")); err != nil {
		t.Fatalf("remove cache-dir again: %v", err)
	}

	restore := &workflow.Job{
		ID:        "restore-cache",
		LogicalID: "restore-cache",
		Steps: []workflow.Step{
			{
				ID:   "restore",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses: "actions/cache/restore@v4",
					Inputs: workflow.ActionInputMap{
						"path":         "${{ github.workspace }}/cache-dir",
						"key":          "remote-split-v2",
						"restore-keys": "remote-split-",
					},
				},
			},
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: "test \"${{ steps.restore.outputs.cache-primary-key }}\" = remote-split-v2\n" +
						"test \"${{ steps.restore.outputs.cache-matched-key }}\" = remote-split-v2\n" +
						"test \"${{ steps.restore.outputs.cache-hit }}\" = true\n" +
						"test \"$(cat \"$GITHUB_WORKSPACE/cache-dir/value.txt\")\" = two",
				},
			},
		},
	}
	if _, err := worker.RunJob(
		context.Background(),
		restore,
		backend.RunOptions{
			WorkingDirectory: workspace,
			Cache:            store,
			Expressions:      expr,
		},
	); err != nil {
		t.Fatalf("restore RunJob: %v", err)
	}
}

func containsString(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}
