package backend

import (
	"bufio"
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/gkze/ghawfr/workflow"
)

// Local executes workflow jobs directly on the local host using generic worker
// semantics plus an injected action-handler registry.
type Local struct {
	RunnerLabels   []string
	ActionHandlers map[string]ActionHandler
}

// Capabilities reports the worker capability set advertised by the local executor.
func (l Local) Capabilities() CapabilitySet {
	labels := append([]string(nil), l.RunnerLabels...)
	if len(labels) == 0 {
		labels = []string{"ubuntu-latest", "ubuntu-24.04", "ubuntu-24.04-arm", "macos-latest", "macos-15", "local"}
	}
	return CapabilitySet{RunnerLabels: labels}
}

// RunJob executes one materialized workflow job locally.
func (l Local) RunJob(ctx context.Context, job *workflow.Job, options RunOptions) (*JobResult, error) {
	if job == nil {
		return nil, fmt.Errorf("job is nil")
	}
	if err := l.Capabilities().SupportsRunner(job.RunsOn); err != nil {
		return nil, fmt.Errorf("job %q runner requirements: %w", job.ID, err)
	}
	workspace, err := resolveWorkingDirectory(options.WorkingDirectory)
	if err != nil {
		return nil, err
	}
	filesystem, err := prepareRunnerFilesystem(workspace, workspace, false)
	if err != nil {
		return nil, err
	}
	options.Expressions.Runner = applyRunnerFilesystem(runnerContextForCurrentHost(), filesystem, false)
	return executeJob(ctx, job, options, l.ActionHandlers, runLocalStep)
}

func runLocalStep(ctx context.Context, job *workflow.Job, step workflow.Step, workspace string, expr workflow.ExpressionContext) (StepResult, error) {
	command, err := workflow.InterpolateString(job, step.Run.Command, expr)
	if err != nil {
		return StepResult{ID: step.ID}, fmt.Errorf("interpolate run command: %w", err)
	}
	workingDirectory, err := workflow.InterpolateString(job, step.Run.WorkingDirectory, expr)
	if err != nil {
		return StepResult{ID: step.ID}, fmt.Errorf("interpolate working directory: %w", err)
	}
	files, err := createFileCommandFiles()
	if err != nil {
		return StepResult{ID: step.ID}, err
	}
	defer files.cleanup()

	binary, args, err := shellCommand(step.Run.Shell, command)
	if err != nil {
		return StepResult{ID: step.ID}, err
	}
	cmd := exec.CommandContext(ctx, binary, args...)
	cmd.Dir = resolveStepDirectory(workspace, workingDirectory)
	cmd.Env = buildCommandEnvironment(expr.Env, workspace, files)
	var combined bytes.Buffer
	cmd.Stdout = &combined
	cmd.Stderr = &combined
	runErr := cmd.Run()
	outputs, err := readKeyValueFile(files.Output, "GITHUB_OUTPUT")
	if err != nil {
		return StepResult{ID: step.ID}, err
	}
	environmentValues, err := readKeyValueFile(files.Env, "GITHUB_ENV")
	if err != nil {
		return StepResult{ID: step.ID}, err
	}
	pathEntries, err := readPathFile(files.Path)
	if err != nil {
		return StepResult{ID: step.ID}, err
	}
	summary, err := readTextFile(files.Summary)
	if err != nil {
		return StepResult{ID: step.ID}, err
	}
	result := StepResult{ID: step.ID, Outputs: outputs, Environment: workflow.EnvironmentMap(environmentValues), PathEntries: pathEntries, Summary: summary}
	if runErr != nil {
		return result, fmt.Errorf("run step %q in job %q: %w\n%s", step.ID, job.ID, runErr, strings.TrimSpace(combined.String()))
	}
	return result, nil
}

func resolveEnvironment(job *workflow.Job, values workflow.EnvironmentMap, expr workflow.ExpressionContext) (workflow.EnvironmentMap, error) {
	if len(values) == 0 {
		return nil, nil
	}
	return workflow.InterpolateEnvironment(job, values, expr)
}

func mergeEnvironment(base workflow.EnvironmentMap, overlay workflow.EnvironmentMap) workflow.EnvironmentMap {
	if len(base) == 0 && len(overlay) == 0 {
		return nil
	}
	merged := base.Clone()
	if merged == nil {
		merged = make(workflow.EnvironmentMap, len(overlay))
	}
	for key, value := range overlay {
		merged[key] = value
	}
	return merged
}

func buildCommandEnvironmentValues(values workflow.EnvironmentMap, workspace string, files fileCommandFiles) workflow.EnvironmentMap {
	env := values.Clone()
	if env == nil {
		env = make(workflow.EnvironmentMap)
	}
	env["CI"] = "true"
	env["GITHUB_WORKSPACE"] = workspace
	env["GITHUB_OUTPUT"] = files.Output
	env["GITHUB_ENV"] = files.Env
	env["GITHUB_PATH"] = files.Path
	env["GITHUB_STATE"] = files.State
	env["GITHUB_STEP_SUMMARY"] = files.Summary
	return env
}

func buildCommandEnvironment(values workflow.EnvironmentMap, workspace string, files fileCommandFiles) []string {
	env := append(os.Environ(),
		"CI=true",
		"GITHUB_WORKSPACE="+workspace,
		"GITHUB_OUTPUT="+files.Output,
		"GITHUB_ENV="+files.Env,
		"GITHUB_PATH="+files.Path,
		"GITHUB_STATE="+files.State,
		"GITHUB_STEP_SUMMARY="+files.Summary,
	)
	for key, value := range values {
		env = append(env, key+"="+value)
	}
	return env
}

func applyPathEntries(values workflow.EnvironmentMap, entries []string) workflow.EnvironmentMap {
	if len(entries) == 0 {
		return values
	}
	updated := values.Clone()
	if updated == nil {
		updated = make(workflow.EnvironmentMap)
	}
	existing := updated["PATH"]
	if existing == "" {
		existing = os.Getenv("PATH")
	}
	parts := make([]string, 0, len(entries)+1)
	for _, entry := range entries {
		if entry == "" {
			continue
		}
		parts = append(parts, entry)
	}
	if existing != "" {
		parts = append(parts, existing)
	}
	updated["PATH"] = strings.Join(parts, string(os.PathListSeparator))
	return updated
}

func actionSlug(uses string) string {
	slug, _, _ := strings.Cut(strings.TrimSpace(uses), "@")
	return strings.ToLower(slug)
}

type fileCommandFiles struct {
	Output  string
	Env     string
	Path    string
	State   string
	Summary string
	cleanup func()
}

func createFileCommandFiles() (fileCommandFiles, error) {
	output, cleanupOutput, err := createTempCommandFile("ghawfr-github-output-")
	if err != nil {
		return fileCommandFiles{}, err
	}
	env, cleanupEnv, err := createTempCommandFile("ghawfr-github-env-")
	if err != nil {
		cleanupOutput()
		return fileCommandFiles{}, err
	}
	path, cleanupPath, err := createTempCommandFile("ghawfr-github-path-")
	if err != nil {
		cleanupOutput()
		cleanupEnv()
		return fileCommandFiles{}, err
	}
	state, cleanupState, err := createTempCommandFile("ghawfr-github-state-")
	if err != nil {
		cleanupOutput()
		cleanupEnv()
		cleanupPath()
		return fileCommandFiles{}, err
	}
	summary, cleanupSummary, err := createTempCommandFile("ghawfr-github-summary-")
	if err != nil {
		cleanupOutput()
		cleanupEnv()
		cleanupPath()
		cleanupState()
		return fileCommandFiles{}, err
	}
	return fileCommandFiles{
		Output:  output,
		Env:     env,
		Path:    path,
		State:   state,
		Summary: summary,
		cleanup: func() {
			cleanupOutput()
			cleanupEnv()
			cleanupPath()
			cleanupState()
			cleanupSummary()
		},
	}, nil
}

func createTempCommandFile(prefix string) (string, func(), error) {
	file, err := os.CreateTemp("", prefix)
	if err != nil {
		return "", nil, fmt.Errorf("create file %q: %w", prefix, err)
	}
	path := file.Name()
	if err := file.Close(); err != nil {
		return "", nil, fmt.Errorf("close file %q: %w", path, err)
	}
	return path, func() { _ = os.Remove(path) }, nil
}

func shellCommand(shell string, command string) (string, []string, error) {
	switch strings.TrimSpace(shell) {
	case "", "bash":
		return "bash", []string{"-eo", "pipefail", "-c", command}, nil
	case "sh":
		return "sh", []string{"-e", "-c", command}, nil
	default:
		return "", nil, fmt.Errorf("unsupported shell %q", shell)
	}
}

func resolveStepDirectory(workspace string, workingDirectory string) string {
	if strings.TrimSpace(workingDirectory) == "" {
		return workspace
	}
	if filepath.IsAbs(workingDirectory) {
		return workingDirectory
	}
	return filepath.Join(workspace, workingDirectory)
}

func readKeyValueFile(path string, name string) (workflow.OutputMap, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read %s file %q: %w", name, path, err)
	}
	if len(data) == 0 {
		return nil, nil
	}
	values := make(workflow.OutputMap)
	scanner := bufio.NewScanner(bytes.NewReader(data))
	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			continue
		}
		if key, delimiter, ok := strings.Cut(line, "<<"); ok {
			var payload []string
			for scanner.Scan() {
				item := scanner.Text()
				if item == delimiter {
					break
				}
				payload = append(payload, item)
			}
			values[strings.TrimSpace(key)] = strings.Join(payload, "\n")
			continue
		}
		key, value, ok := strings.Cut(line, "=")
		if !ok {
			return nil, fmt.Errorf("parse %s line %q", name, line)
		}
		values[strings.TrimSpace(key)] = value
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("scan %s file %q: %w", name, path, err)
	}
	if len(values) == 0 {
		return nil, nil
	}
	return values, nil
}

func readPathFile(path string) ([]string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read GITHUB_PATH file %q: %w", path, err)
	}
	if len(data) == 0 {
		return nil, nil
	}
	entries := make([]string, 0)
	scanner := bufio.NewScanner(bytes.NewReader(data))
	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			continue
		}
		entries = append(entries, line)
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("scan GITHUB_PATH file %q: %w", path, err)
	}
	if len(entries) == 0 {
		return nil, nil
	}
	return entries, nil
}

func readTextFile(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", fmt.Errorf("read text file %q: %w", path, err)
	}
	return string(data), nil
}
