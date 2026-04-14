package backend

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/gkze/ghawfr/internal/guestpath"
	"github.com/gkze/ghawfr/state"
	"github.com/gkze/ghawfr/workflow"
)

// RemoteExecWorker executes run steps through a guest/remote command transport
// and relies on an injected action-handler registry for uses: steps.
type RemoteExecWorker struct {
	RunnerLabels   []string
	GuestWorkspace string
	Commands       CommandTransport
	ActionHandlers map[string]ActionHandler
}

// Capabilities reports the worker capability set advertised by the remote executor.
func (w RemoteExecWorker) Capabilities() CapabilitySet {
	return CapabilitySet{RunnerLabels: append([]string(nil), w.RunnerLabels...)}
}

// RunJob executes one materialized workflow job with run steps delegated to the
// configured guest/remote transport.
func (w RemoteExecWorker) RunJob(
	ctx context.Context,
	job *workflow.Job,
	options RunOptions,
) (*JobResult, error) {
	if job == nil {
		return nil, fmt.Errorf("job is nil")
	}
	if err := w.Capabilities().SupportsRunner(job.RunsOn); err != nil {
		return nil, fmt.Errorf("job %q runner requirements: %w", job.ID, err)
	}
	if strings.TrimSpace(w.GuestWorkspace) == "" {
		return nil, fmt.Errorf("remote exec worker guest workspace is empty")
	}
	if w.Commands == nil {
		return nil, fmt.Errorf("remote exec worker command transport is nil")
	}
	workspace, err := resolveWorkingDirectory(options.WorkingDirectory)
	if err != nil {
		return nil, err
	}
	filesystem, err := prepareRunnerFilesystem(workspace, w.GuestWorkspace, true)
	if err != nil {
		return nil, err
	}
	runnerContext := options.Expressions.Runner
	if requirements, err := RequirementsForRunner(job.RunsOn); err == nil {
		runnerContext.OS = githubRunnerOS(string(requirements.OS))
		runnerContext.Arch = githubRunnerArch(string(requirements.Arch))
	}
	options.Expressions.Runner = applyRunnerFilesystem(runnerContext, filesystem, true)
	return executeJob(ctx, job, options, w.ActionHandlers, w.runRemoteStep)
}

func (w RemoteExecWorker) runRemoteStep(
	ctx context.Context,
	job *workflow.Job,
	step workflow.Step,
	workspace string,
	expr workflow.ExpressionContext,
) (StepResult, error) {
	remoteExpr := expr
	remoteExpr.GitHub.Workspace = w.GuestWorkspace
	remoteExpr.Env = translateWorkspaceEnvironment(expr.Env, workspace, w.GuestWorkspace)
	command, err := workflow.InterpolateString(job, step.Run.Command, remoteExpr)
	if err != nil {
		return StepResult{ID: step.ID}, fmt.Errorf("interpolate run command: %w", err)
	}
	command, err = wrapRemoteShellCommand(step.Run.Shell, command)
	if err != nil {
		return StepResult{ID: step.ID}, err
	}
	workingDirectory, err := workflow.InterpolateString(job, step.Run.WorkingDirectory, remoteExpr)
	if err != nil {
		return StepResult{ID: step.ID}, fmt.Errorf("interpolate working directory: %w", err)
	}
	hostFiles, guestFiles, err := createWorkspaceFileCommandFiles(workspace, w.GuestWorkspace)
	if err != nil {
		return StepResult{ID: step.ID}, err
	}
	defer hostFiles.cleanup()
	guestStepDirectory, err := resolveRemoteStepDirectory(
		workspace,
		w.GuestWorkspace,
		workingDirectory,
	)
	if err != nil {
		return StepResult{ID: step.ID}, err
	}
	result, err := w.Commands.ExecCommand(
		ctx,
		guestStepDirectory,
		buildCommandEnvironmentValues(remoteExpr.Env, w.GuestWorkspace, guestFiles),
		command,
	)
	outputs, outputErr := readKeyValueFile(hostFiles.Output, "GITHUB_OUTPUT")
	if outputErr != nil {
		return StepResult{ID: step.ID}, outputErr
	}
	environmentValues, envErr := readKeyValueFile(hostFiles.Env, "GITHUB_ENV")
	if envErr != nil {
		return StepResult{ID: step.ID}, envErr
	}
	pathEntries, pathErr := readPathFile(hostFiles.Path)
	if pathErr != nil {
		return StepResult{ID: step.ID}, pathErr
	}
	summary, summaryErr := readTextFile(hostFiles.Summary)
	if summaryErr != nil {
		return StepResult{ID: step.ID}, summaryErr
	}
	stepResult := StepResult{
		ID:          step.ID,
		Outputs:     outputs,
		Environment: workflow.EnvironmentMap(environmentValues),
		PathEntries: pathEntries,
		Summary:     summary,
	}
	if err != nil {
		return stepResult, fmt.Errorf(
			"run step %q in job %q through remote worker: %w",
			step.ID,
			job.ID,
			err,
		)
	}
	if result.ExitCode != 0 {
		combined := strings.TrimSpace(result.Stdout + result.Stderr)
		if combined != "" {
			return stepResult, fmt.Errorf(
				"run step %q in job %q exited with code %d\n%s",
				step.ID,
				job.ID,
				result.ExitCode,
				combined,
			)
		}
		return stepResult, fmt.Errorf(
			"run step %q in job %q exited with code %d",
			step.ID,
			job.ID,
			result.ExitCode,
		)
	}
	return stepResult, nil
}

func resolveRemoteStepDirectory(
	hostWorkspace string,
	guestWorkspace string,
	workingDirectory string,
) (string, error) {
	return guestpath.ResolveStepDirectory(hostWorkspace, guestWorkspace, workingDirectory)
}

func createWorkspaceFileCommandFiles(
	hostWorkspace string,
	guestWorkspace string,
) (fileCommandFiles, fileCommandFiles, error) {
	root := state.FileCommandsDir(hostWorkspace)
	if err := os.MkdirAll(root, 0o755); err != nil {
		return fileCommandFiles{}, fileCommandFiles{}, fmt.Errorf(
			"create file command root %q: %w",
			root,
			err,
		)
	}
	directory, err := os.MkdirTemp(root, "run-")
	if err != nil {
		return fileCommandFiles{}, fileCommandFiles{}, fmt.Errorf(
			"create file command dir under %q: %w",
			root,
			err,
		)
	}
	create := func(name string) (string, error) {
		path := filepath.Join(directory, name)
		if err := os.WriteFile(path, nil, 0o600); err != nil {
			return "", fmt.Errorf("create file command %q: %w", path, err)
		}
		return path, nil
	}
	host := fileCommandFiles{cleanup: func() { _ = os.RemoveAll(directory) }}
	guest := fileCommandFiles{cleanup: func() {}}
	for _, entry := range []struct {
		name  string
		host  *string
		guest *string
	}{
		{name: "GITHUB_OUTPUT", host: &host.Output, guest: &guest.Output},
		{name: "GITHUB_ENV", host: &host.Env, guest: &guest.Env},
		{name: "GITHUB_PATH", host: &host.Path, guest: &guest.Path},
		{name: "GITHUB_STATE", host: &host.State, guest: &guest.State},
		{name: "GITHUB_STEP_SUMMARY", host: &host.Summary, guest: &guest.Summary},
	} {
		hostPath, err := create(entry.name)
		if err != nil {
			host.cleanup()
			return fileCommandFiles{}, fileCommandFiles{}, err
		}
		guestPath, err := translateWorkspacePath(hostWorkspace, guestWorkspace, hostPath)
		if err != nil {
			host.cleanup()
			return fileCommandFiles{}, fileCommandFiles{}, err
		}
		*entry.host = hostPath
		*entry.guest = guestPath
	}
	return host, guest, nil
}

func translateWorkspacePath(
	hostWorkspace string,
	guestWorkspace string,
	hostPath string,
) (string, error) {
	return guestpath.TranslateHostPath(hostWorkspace, guestWorkspace, hostPath)
}

func translateWorkspaceEnvironment(
	values workflow.EnvironmentMap,
	hostWorkspace string,
	guestWorkspace string,
) workflow.EnvironmentMap {
	return guestpath.TranslateEnvironment(values, hostWorkspace, guestWorkspace)
}

func wrapRemoteShellCommand(shell string, command string) (string, error) {
	binary, args, err := shellCommand(shell, command)
	if err != nil {
		return "", err
	}
	parts := make([]string, 0, 1+len(args))
	parts = append(parts, shellQuote(binary))
	for _, arg := range args {
		parts = append(parts, shellQuote(arg))
	}
	return strings.Join(parts, " "), nil
}

func shellQuote(value string) string {
	return guestpath.ShellQuote(value)
}
