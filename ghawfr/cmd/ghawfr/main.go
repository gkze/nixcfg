package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/gkze/ghawfr/actionadapter"
	"github.com/gkze/ghawfr/artifacts"
	"github.com/gkze/ghawfr/backend"
	"github.com/gkze/ghawfr/backend/qemu"
	ghawfrvz "github.com/gkze/ghawfr/backend/vz"
	ghcache "github.com/gkze/ghawfr/cache"
	"github.com/gkze/ghawfr/controller"
	"github.com/gkze/ghawfr/planner"
	"github.com/gkze/ghawfr/state"
	"github.com/gkze/ghawfr/workflow"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintf(os.Stderr, "ghawfr: %v\n", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) == 0 || isHelp(args[0]) {
		printUsage()
		return nil
	}
	switch args[0] {
	case "inspect":
		if len(args) != 2 {
			return fmt.Errorf("inspect requires a workflow path")
		}
		return runInspect(args[1])
	case "plan":
		if len(args) != 2 {
			return fmt.Errorf("plan requires a workflow path")
		}
		return runPlan(args[1])
	case "route":
		if len(args) < 2 || len(args) > 3 {
			return fmt.Errorf("route requires a workflow path and optional job id")
		}
		target := ""
		if len(args) == 3 {
			target = args[2]
		}
		return runRoute(args[1], target)
	case "prepare":
		if len(args) < 2 || len(args) > 3 {
			return fmt.Errorf("prepare requires a workflow path and optional job id")
		}
		target := ""
		if len(args) == 3 {
			target = args[2]
		}
		return runPrepare(args[1], target)
	case "start":
		if len(args) != 3 {
			return fmt.Errorf("start requires a workflow path and job id")
		}
		return runStart(args[1], args[2])
	case "probe":
		if len(args) < 3 || len(args) > 4 {
			return fmt.Errorf("probe requires a workflow path, job id, and optional shell command")
		}
		command := "true"
		if len(args) == 4 {
			command = args[3]
		}
		return runProbe(args[1], args[2], command)
	case "stop":
		if len(args) != 3 {
			return fmt.Errorf("stop requires a workflow path and job id")
		}
		return runStop(args[1], args[2])
	case "run":
		if len(args) < 2 || len(args) > 3 {
			return fmt.Errorf("run requires a workflow path and optional job id")
		}
		target := ""
		if len(args) == 3 {
			target = args[2]
		}
		return runWorkflow(args[1], target)
	default:
		return fmt.Errorf("unknown command %q", strings.Join(args, " "))
	}
}

func runInspect(path string) error {
	definition, err := workflow.ParseFile(path)
	if err != nil {
		return err
	}
	fmt.Printf("workflow %s\n", titleOrFallback(definition.Name, filepath.Base(path)))
	fmt.Printf("source %s\n", path)
	fmt.Printf("events %s\n", strings.Join(eventNames(definition.Events), ", "))
	for _, jobID := range definition.JobOrder {
		job := definition.Jobs[jobID]
		if job == nil {
			continue
		}
		fmt.Printf("job %s", job.ID)
		if job.LogicalID != "" && job.LogicalID != job.ID {
			fmt.Printf(" logical=%s", job.LogicalID)
		}
		if job.Name != "" {
			fmt.Printf(" name=%q", job.Name)
		}
		if len(job.Needs) > 0 {
			fmt.Printf(" needs=%s", job.Needs.Join(","))
		}
		if len(job.MatrixKeys) > 0 {
			fmt.Printf(" matrix=%s", formatMatrixPairs(job.MatrixPairs()))
		}
		if job.WorkflowCall != nil {
			fmt.Printf(" uses=%s", job.WorkflowCall.Uses)
		} else {
			if len(job.RunsOn.Labels) > 0 {
				fmt.Printf(" runs-on=%s", strings.Join(job.RunsOn.Labels, ","))
			}
			if job.RunsOn.Group != "" {
				fmt.Printf(" runner-group=%s", job.RunsOn.Group)
			}
			fmt.Printf(" steps=%d", len(job.Steps))
		}
		fmt.Println()
	}
	for _, logicalID := range definition.DeferredOrder {
		deferred := definition.DeferredJobs[logicalID]
		if deferred == nil {
			continue
		}
		fmt.Printf("deferred %s", deferred.LogicalID)
		if len(deferred.WaitsOn) > 0 {
			fmt.Printf(" waits-on=%s", deferred.WaitsOn.Join(","))
		}
		if deferred.Reason != "" {
			fmt.Printf(" reason=%q", deferred.Reason)
		}
		fmt.Println()
	}
	return nil
}

func runPlan(path string) error {
	definition, err := workflow.ParseFile(path)
	if err != nil {
		return err
	}
	plan, err := planner.Build(definition)
	if err != nil {
		return err
	}
	fmt.Printf("workflow %s\n", titleOrFallback(definition.Name, filepath.Base(path)))
	for _, stage := range plan.Stages {
		fmt.Printf("stage %d: %s\n", stage.Index, stage.Jobs.Join(", "))
	}
	for _, logicalID := range definition.DeferredOrder {
		deferred := definition.DeferredJobs[logicalID]
		if deferred == nil {
			continue
		}
		fmt.Printf("deferred %s", deferred.LogicalID)
		if len(deferred.WaitsOn) > 0 {
			fmt.Printf(" waits-on=%s", deferred.WaitsOn.Join(","))
		}
		fmt.Println()
	}
	return nil
}

func runRoute(path string, target string) error {
	definition, err := workflow.ParseFile(path)
	if err != nil {
		return err
	}
	workingDirectory, err := os.Getwd()
	if err != nil {
		return fmt.Errorf("resolve working directory: %w", err)
	}
	workingDirectory = detectWorkspaceRoot(workingDirectory, path)
	provider, err := workflowProvider()
	if err != nil {
		return err
	}
	routeOptions := backend.RunOptions{WorkingDirectory: workingDirectory}
	normalizedTarget := strings.ToLower(strings.TrimSpace(target))
	fmt.Printf("workflow %s\n", titleOrFallback(definition.Name, filepath.Base(path)))
	if normalizedTarget != "" {
		fmt.Printf("target %s\n", normalizedTarget)
	}
	matched := false
	for _, jobID := range definition.JobOrder {
		job := definition.Jobs[jobID]
		if job == nil {
			continue
		}
		if normalizedTarget != "" && strings.ToLower(string(job.ID)) != normalizedTarget {
			continue
		}
		matched = true
		route, err := routeSummary(provider, job, routeOptions)
		if err != nil {
			return fmt.Errorf("job %q: %w", job.ID, err)
		}
		fmt.Printf("job %s %s\n", job.ID, route)
	}
	if !matched && normalizedTarget != "" {
		return fmt.Errorf("target job %q not found", normalizedTarget)
	}
	return nil
}

func runPrepare(path string, target string) error {
	definition, err := workflow.ParseFile(path)
	if err != nil {
		return err
	}
	workingDirectory, err := os.Getwd()
	if err != nil {
		return fmt.Errorf("resolve working directory: %w", err)
	}
	workingDirectory = detectWorkspaceRoot(workingDirectory, path)
	provider, err := workflowProvider()
	if err != nil {
		return err
	}
	materializer, ok := provider.(backend.MaterializingProvider)
	if !ok {
		return fmt.Errorf("provider %T does not support prepare", provider)
	}
	runOptions := backend.RunOptions{WorkingDirectory: workingDirectory}
	normalizedTarget := strings.ToLower(strings.TrimSpace(target))
	fmt.Printf("workflow %s\n", titleOrFallback(definition.Name, filepath.Base(path)))
	if normalizedTarget != "" {
		fmt.Printf("target %s\n", normalizedTarget)
	}
	matched := false
	for _, jobID := range definition.JobOrder {
		job := definition.Jobs[jobID]
		if job == nil {
			continue
		}
		if normalizedTarget != "" && strings.ToLower(string(job.ID)) != normalizedTarget {
			continue
		}
		matched = true
		materialized, err := materializer.MaterializeWorker(job, runOptions)
		if err != nil {
			return fmt.Errorf("job %q: %w", job.ID, err)
		}
		fmt.Printf("job %s provider=%s\n", job.ID, materialized.Plan.Provider)
		for _, path := range materialized.Artifacts {
			fmt.Printf("  artifact %s\n", path)
		}
	}
	if !matched && normalizedTarget != "" {
		return fmt.Errorf("target job %q not found", normalizedTarget)
	}
	return nil
}

func runStart(path string, target string) error {
	definition, job, plan, launch, err := qemuLaunchForWorkflow(path, target)
	if err != nil {
		return err
	}
	if err := backend.EnsureHostRequirements(plan); err != nil {
		return err
	}
	state, err := qemu.StartMaterializedLaunch(launch)
	if err != nil {
		return err
	}
	fmt.Printf("workflow %s\n", titleOrFallback(definition.Name, filepath.Base(path)))
	fmt.Printf("target %s\n", job.ID)
	fmt.Printf("provider=qemu\n")
	fmt.Printf("pid %d\n", state.PID)
	fmt.Printf("log %s\n", state.LogPath)
	fmt.Printf("state %s\n", state.StatePath)
	fmt.Printf("ssh %s\n", launch.Spec.SSHAddress)
	return nil
}

func runProbe(path string, target string, command string) error {
	definition, job, _, launch, err := qemuLaunchForWorkflow(path, target)
	if err != nil {
		return err
	}
	sshWaitTimeout, err := configuredDuration("GHAWFR_SSH_WAIT_TIMEOUT", 30*time.Second)
	if err != nil {
		return err
	}
	if err := qemu.WaitForSSH(launch, sshWaitTimeout); err != nil {
		return err
	}
	output, err := qemu.RunGuestCommand(launch, command)
	if err != nil {
		return err
	}
	fmt.Printf("workflow %s\n", titleOrFallback(definition.Name, filepath.Base(path)))
	fmt.Printf("target %s\n", job.ID)
	fmt.Printf("command %q\n", command)
	if strings.TrimSpace(output) != "" {
		fmt.Print(output)
		if !strings.HasSuffix(output, "\n") {
			fmt.Println()
		}
	}
	return nil
}

func runStop(path string, target string) error {
	definition, job, _, launch, err := qemuLaunchForWorkflow(path, target)
	if err != nil {
		return err
	}
	state, err := qemu.LoadProcessState(qemu.ProcessStatePath(filepath.Dir(launch.Command)))
	if err != nil {
		return err
	}
	stopTimeout, err := configuredDuration("GHAWFR_STOP_TIMEOUT", 5*time.Second)
	if err != nil {
		return err
	}
	if err := qemu.StopProcess(state, stopTimeout); err != nil {
		return err
	}
	fmt.Printf("workflow %s\n", titleOrFallback(definition.Name, filepath.Base(path)))
	fmt.Printf("target %s\n", job.ID)
	fmt.Printf("stopped pid %d\n", state.PID)
	return nil
}

func runWorkflow(path string, target string) error {
	runContext, stopSignals := commandContext()
	defer stopSignals()
	return runWorkflowContext(runContext, path, target)
}

func runWorkflowContext(runContext context.Context, path string, target string) (err error) {
	workingDirectory, err := os.Getwd()
	if err != nil {
		return fmt.Errorf("resolve working directory: %w", err)
	}
	workingDirectory = detectWorkspaceRoot(workingDirectory, path)
	workflowPath, err := filepath.Abs(path)
	if err != nil {
		return fmt.Errorf("resolve workflow path %q: %w", path, err)
	}
	statePath := runStatePath(workingDirectory, workflowPath)
	run, err := loadRunState(statePath, workflowPath)
	if err != nil {
		return err
	}
	defer func() {
		if saveErr := run.Save(statePath); saveErr != nil {
			err = errors.Join(err, fmt.Errorf("save run state %q: %w", statePath, saveErr))
			return
		}
		if err == nil {
			fmt.Printf("state %s\n", statePath)
		}
	}()
	expressions := localExpressionContext(workingDirectory)
	artifactStore := artifacts.NewStore(state.ArtifactsDir(workingDirectory))
	cacheStore := ghcache.NewStore(state.CacheDir(workingDirectory))
	normalizedTarget := strings.ToLower(strings.TrimSpace(target))
	allowed := workflow.JobSet(nil)
	if normalizedTarget != "" {
		allowed = workflow.JobSet{workflow.JobID(normalizedTarget): true}
	}
	provider, err := workflowProvider()
	if err != nil {
		return err
	}
	result, err := controller.RunUntilBlockedSelectedFile(
		runContext,
		path,
		allowed,
		run,
		actionadapter.NewHostLocal(),
		backend.RunOptions{
			WorkingDirectory: workingDirectory,
			Expressions:      expressions,
			Artifacts:        artifactStore,
			Cache:            cacheStore,
			Provider:         provider,
		},
		workflow.ParseOptions{Expressions: expressions},
	)
	if err != nil {
		return err
	}
	fmt.Printf("workflow %s\n", titleOrFallback(result.Final.Workflow.Name, filepath.Base(path)))
	if normalizedTarget != "" {
		fmt.Printf("target %s\n", normalizedTarget)
	}
	for _, job := range result.Jobs {
		if job == nil {
			continue
		}
		fmt.Printf("ran %s result=%s\n", job.JobID, job.Result)
		if len(job.Outputs) > 0 {
			fmt.Printf("  outputs: %s\n", formatOutputMap(job.Outputs))
		}
	}
	if normalizedTarget != "" && len(result.Jobs) == 0 {
		if run.CompletedJobs()[workflow.JobID(normalizedTarget)] {
			fmt.Printf("target %s already completed\n", normalizedTarget)
			return nil
		}
		return fmt.Errorf("target job %q did not execute", normalizedTarget)
	}
	if result.Final != nil {
		for _, logicalID := range result.Final.Workflow.DeferredOrder {
			deferred := result.Final.Workflow.DeferredJobs[logicalID]
			if deferred == nil {
				continue
			}
			fmt.Printf("deferred %s", deferred.LogicalID)
			if len(deferred.WaitsOn) > 0 {
				fmt.Printf(" waits-on=%s", deferred.WaitsOn.Join(","))
			}
			fmt.Println()
		}
	}
	return nil
}

func qemuLaunchForWorkflow(path string, target string) (*workflow.Workflow, *workflow.Job, backend.WorkerPlan, qemu.MaterializedLaunch, error) {
	definition, err := workflow.ParseFile(path)
	if err != nil {
		return nil, nil, backend.WorkerPlan{}, qemu.MaterializedLaunch{}, err
	}
	workingDirectory, err := os.Getwd()
	if err != nil {
		return nil, nil, backend.WorkerPlan{}, qemu.MaterializedLaunch{}, fmt.Errorf("resolve working directory: %w", err)
	}
	workingDirectory = detectWorkspaceRoot(workingDirectory, path)
	provider, err := workflowProvider()
	if err != nil {
		return nil, nil, backend.WorkerPlan{}, qemu.MaterializedLaunch{}, err
	}
	planner, ok := provider.(backend.PlannedProvider)
	if !ok {
		return nil, nil, backend.WorkerPlan{}, qemu.MaterializedLaunch{}, fmt.Errorf("provider %T does not support planning", provider)
	}
	normalizedTarget := strings.ToLower(strings.TrimSpace(target))
	for _, jobID := range definition.JobOrder {
		job := definition.Jobs[jobID]
		if job == nil {
			continue
		}
		if strings.ToLower(string(job.ID)) != normalizedTarget {
			continue
		}
		plan, err := planner.PlanWorker(job, backend.RunOptions{WorkingDirectory: workingDirectory})
		if err != nil {
			return nil, nil, backend.WorkerPlan{}, qemu.MaterializedLaunch{}, err
		}
		if plan.Provider != backend.ProviderKindQEMU {
			return nil, nil, backend.WorkerPlan{}, qemu.MaterializedLaunch{}, fmt.Errorf("job %q routes to provider %q, want qemu", job.ID, plan.Provider)
		}
		launch, err := qemu.MaterializePlan(plan)
		if err != nil {
			return nil, nil, backend.WorkerPlan{}, qemu.MaterializedLaunch{}, err
		}
		return definition, job, plan, launch, nil
	}
	return nil, nil, backend.WorkerPlan{}, qemu.MaterializedLaunch{}, fmt.Errorf("target job %q not found", normalizedTarget)
}

func routeSummary(provider backend.Provider, job *workflow.Job, options backend.RunOptions) (string, error) {
	planner, ok := provider.(backend.PlannedProvider)
	if !ok {
		return fmt.Sprintf("provider=%T", provider), nil
	}
	plan, err := planner.PlanWorker(job, options)
	if err != nil {
		return "", err
	}
	parts := []string{fmt.Sprintf("provider=%s", plan.Provider)}
	if plan.Requirements.OS != "" {
		parts = append(parts, fmt.Sprintf("guest=%s/%s", plan.Requirements.OS, plan.Requirements.Arch))
	}
	if plan.Image != nil {
		parts = append(parts, fmt.Sprintf("image=%s->%s", plan.Image.CanonicalFormat, plan.Image.RuntimeFormat))
		if plan.Image.Source != "" {
			parts = append(parts, fmt.Sprintf("source=%s", plan.Image.Source))
		}
	}
	if plan.Transport.Kind != "" {
		parts = append(parts, fmt.Sprintf("transport=%s", plan.Transport.Kind))
	}
	if plan.GuestWorkspace != "" {
		parts = append(parts, fmt.Sprintf("guest-workspace=%s", plan.GuestWorkspace))
	}
	if plan.InstanceDirectory != "" {
		parts = append(parts, fmt.Sprintf("instance=%s", plan.InstanceDirectory))
	}
	if len(plan.HostRequirements) > 0 {
		names := make([]string, 0, len(plan.HostRequirements))
		for _, requirement := range plan.HostRequirements {
			names = append(names, requirement.Name)
		}
		parts = append(parts, fmt.Sprintf("host=%s", strings.Join(names, ",")))
		missing := backend.MissingHostRequirements(plan)
		if len(missing) > 0 {
			missingNames := make([]string, 0, len(missing))
			for _, requirement := range missing {
				missingNames = append(missingNames, requirement.Name)
			}
			parts = append(parts, fmt.Sprintf("missing=%s", strings.Join(missingNames, ",")))
		}
	}
	if len(plan.Notes) > 0 {
		parts = append(parts, fmt.Sprintf("notes=%q", strings.Join(plan.Notes, "; ")))
	}
	return strings.Join(parts, " "), nil
}

func workflowProvider() (backend.Provider, error) {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("GHAWFR_PROVIDER"))) {
	case "", "auto":
		provider := backend.AutoProvider{
			Local: actionadapter.NewHostLocal(),
			VZ:    ghawfrvz.Provider{},
			QEMU:  qemu.Provider{},
		}
		if envEnabled("GHAWFR_UNSAFE_LOCAL_FALLBACK") {
			provider.UnsafeLocalFallback = actionadapter.NewLocal(nil)
		}
		return provider, nil
	case "local":
		return backend.StaticProvider{WorkerImpl: actionadapter.NewHostLocal()}, nil
	case "smoke-local":
		return backend.StaticProvider{WorkerImpl: actionadapter.NewLocal(nil)}, nil
	default:
		return nil, fmt.Errorf("unsupported GHAWFR_PROVIDER %q", os.Getenv("GHAWFR_PROVIDER"))
	}
}

func envEnabled(key string) bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv(key))) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

func loadRunState(path string, sourcePath string) (*state.Run, error) {
	run, err := state.Load(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return state.NewRun(sourcePath), nil
		}
		return nil, fmt.Errorf("load run state %q: %w", path, err)
	}
	if run.SourcePath == "" {
		run.SourcePath = sourcePath
	}
	return run, nil
}

func runStatePath(workingDirectory string, workflowPath string) string {
	return state.RunStatePath(workingDirectory, workflowPath)
}

func detectWorkspaceRoot(workingDirectory string, workflowPath string) string {
	workflowDir := filepath.Dir(workflowPath)
	if filepath.IsAbs(workflowDir) {
		if root := gitOutput(workflowDir, "rev-parse", "--show-toplevel"); root != "" {
			return root
		}
		return workflowDir
	}
	candidate := filepath.Join(workingDirectory, workflowDir)
	if root := gitOutput(candidate, "rev-parse", "--show-toplevel"); root != "" {
		return root
	}
	if root := gitOutput(workingDirectory, "rev-parse", "--show-toplevel"); root != "" {
		return root
	}
	return workingDirectory
}

func commandContext() (context.Context, context.CancelFunc) {
	return signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
}

func configuredDuration(envName string, fallback time.Duration) (time.Duration, error) {
	raw := strings.TrimSpace(os.Getenv(envName))
	if raw == "" {
		return fallback, nil
	}
	if seconds, err := strconv.Atoi(raw); err == nil {
		if seconds <= 0 {
			return 0, fmt.Errorf("%s must be greater than zero", envName)
		}
		return time.Duration(seconds) * time.Second, nil
	}
	duration, err := time.ParseDuration(raw)
	if err != nil {
		return 0, fmt.Errorf("parse %s=%q: %w", envName, raw, err)
	}
	if duration <= 0 {
		return 0, fmt.Errorf("%s must be greater than zero", envName)
	}
	return duration, nil
}

func localExpressionContext(workingDirectory string) workflow.ExpressionContext {
	branch := gitOutput(workingDirectory, "rev-parse", "--abbrev-ref", "HEAD")
	sha := gitOutput(workingDirectory, "rev-parse", "HEAD")
	before := gitOutput(workingDirectory, "rev-parse", "HEAD^")
	defaultBranch := detectDefaultBranch(workingDirectory)
	ref := ""
	if branch != "" && branch != "HEAD" {
		ref = "refs/heads/" + branch
	}
	event := workflow.GitHubEventMap{
		"before": workflow.StringData(before),
		"repository": workflow.ObjectData(map[string]workflow.Data{
			"default_branch": workflow.StringData(defaultBranch),
		}),
	}
	return workflow.ExpressionContext{
		Secrets: localSecrets(),
		Runner: workflow.RunnerContext{
			OS:        localRunnerOS(runtime.GOOS),
			Arch:      localRunnerArch(runtime.GOARCH),
			Temp:      state.RunnerTempDir(workingDirectory),
			ToolCache: state.RunnerToolCacheDir(workingDirectory),
		},
		GitHub: workflow.GitHubContext{
			Event:      event,
			EventName:  "push",
			Ref:        ref,
			RefName:    branch,
			RefType:    "branch",
			Sha:        sha,
			Repository: filepath.Base(workingDirectory),
			Workspace:  workingDirectory,
		},
	}
}

func localSecrets() workflow.SecretMap {
	keys := []string{
		"GITHUB_TOKEN",
		"GH_TOKEN",
		"CACHIX_AUTH_TOKEN",
		"OP_SERVICE_ACCOUNT_TOKEN",
	}
	secrets := make(workflow.SecretMap)
	for _, key := range keys {
		if value := os.Getenv(key); value != "" {
			secrets[key] = value
		}
	}
	if len(secrets) == 0 {
		return nil
	}
	return secrets
}

func detectDefaultBranch(workingDirectory string) string {
	value := gitOutput(workingDirectory, "symbolic-ref", "refs/remotes/origin/HEAD")
	if value != "" {
		parts := strings.Split(value, "/")
		return parts[len(parts)-1]
	}
	if branch := gitOutput(workingDirectory, "branch", "--show-current"); branch != "" {
		return branch
	}
	return "main"
}

func localRunnerOS(goos string) string {
	switch goos {
	case "darwin":
		return "macOS"
	case "linux":
		return "Linux"
	case "windows":
		return "Windows"
	default:
		return goos
	}
}

func localRunnerArch(goarch string) string {
	switch goarch {
	case "amd64":
		return "X64"
	case "arm64":
		return "ARM64"
	default:
		return goarch
	}
}

func gitOutput(workingDirectory string, args ...string) string {
	cmd := exec.Command("git", args...)
	cmd.Dir = workingDirectory
	output, err := cmd.Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(output))
}

func eventNames(events []workflow.Event) []string {
	names := make([]string, 0, len(events))
	for _, event := range events {
		names = append(names, event.Name)
	}
	return names
}

func formatMatrixPairs(pairs []workflow.MatrixPair) string {
	parts := make([]string, 0, len(pairs))
	for _, pair := range pairs {
		parts = append(parts, pair.Key+"="+pair.Value.IdentifierString())
	}
	return strings.Join(parts, ",")
}

func formatOutputMap(values workflow.OutputMap) string {
	parts := make([]string, 0, len(values))
	for key, value := range values {
		parts = append(parts, fmt.Sprintf("%s=%q", key, value))
	}
	return strings.Join(parts, ", ")
}

func titleOrFallback(title string, fallback string) string {
	if title != "" {
		return title
	}
	return fallback
}

func isHelp(arg string) bool {
	return arg == "-h" || arg == "--help" || arg == "help"
}

func printUsage() {
	fmt.Println("ghawfr <command> <workflow>")
	fmt.Println()
	fmt.Println("Commands:")
	fmt.Println("  inspect   Parse, expand, and summarize one workflow file")
	fmt.Println("  plan      Build and print the pure job dependency plan")
	fmt.Println("  route     Show which provider/image route each job would use")
	fmt.Println("            Usage: ghawfr route <workflow> [job-id]")
	fmt.Println("  prepare   Materialize provider launch artifacts without executing jobs")
	fmt.Println("            Usage: ghawfr prepare <workflow> [job-id]")
	fmt.Println("  start     Start one prepared QEMU guest for a job")
	fmt.Println("            Usage: ghawfr start <workflow> <job-id>")
	fmt.Println("  probe     Wait for guest SSH and run one shell command")
	fmt.Println("            Usage: ghawfr probe <workflow> <job-id> [command]")
	fmt.Println("  stop      Stop one started QEMU guest for a job")
	fmt.Println("            Usage: ghawfr stop <workflow> <job-id>")
	fmt.Println("  run       Execute ready jobs until blocked")
	fmt.Println("            Usage: ghawfr run <workflow> [job-id]")
	fmt.Println()
	fmt.Println("Environment:")
	fmt.Println("  GHAWFR_PROVIDER=auto|local|smoke-local")
	fmt.Println("  GHAWFR_UNSAFE_LOCAL_FALLBACK=1   allow broad host execution in auto mode")
}
