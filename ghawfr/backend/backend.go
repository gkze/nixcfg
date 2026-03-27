package backend

import (
	"context"
	"fmt"
	"sort"
	"strings"

	"github.com/gkze/ghawfr/artifacts"
	ghcache "github.com/gkze/ghawfr/cache"
	"github.com/gkze/ghawfr/workflow"
)

// JobStatus is one backend execution result state.
type JobStatus string

const (
	// JobStatusSuccess means the job completed successfully.
	JobStatusSuccess JobStatus = "success"
	// JobStatusFailure means the job failed.
	JobStatusFailure JobStatus = "failure"
	// JobStatusSkipped means the job was skipped.
	JobStatusSkipped JobStatus = "skipped"
)

// StepResult is one executed workflow step result.
type StepResult struct {
	ID          workflow.StepID
	Outputs     workflow.OutputMap
	Environment workflow.EnvironmentMap
	PathEntries []string
	Summary     string
	Conclusion  string
	Outcome     string
	Post        PostStep
}

// JobResult is one executed workflow job result.
type JobResult struct {
	JobID     workflow.JobID
	LogicalID workflow.JobID
	Outputs   workflow.OutputMap
	Result    JobStatus
	Steps     []StepResult
}

// CapabilitySet describes one worker's advertised execution capabilities.
type CapabilitySet struct {
	RunnerLabels []string
}

// SupportsRunner reports whether the capability set can satisfy the given runs-on request.
func (c CapabilitySet) SupportsRunner(runner workflow.Runner) error {
	if runner.Group != "" {
		return fmt.Errorf("runner groups are not supported yet")
	}
	if runner.LabelsExpression != "" {
		return fmt.Errorf("expression-based runs-on labels are not supported yet")
	}
	if len(runner.Labels) == 0 {
		return nil
	}
	if len(c.RunnerLabels) == 0 {
		return nil
	}
	available := make(map[string]struct{}, len(c.RunnerLabels))
	for _, label := range c.RunnerLabels {
		available[strings.ToLower(strings.TrimSpace(label))] = struct{}{}
	}
	missing := make([]string, 0)
	for _, label := range runner.Labels {
		normalized := strings.ToLower(strings.TrimSpace(label))
		if normalized == "" {
			continue
		}
		if _, ok := available[normalized]; ok {
			continue
		}
		missing = append(missing, normalized)
	}
	if len(missing) == 0 {
		return nil
	}
	sort.Strings(missing)
	return fmt.Errorf("unsupported runner labels: %s", strings.Join(missing, ", "))
}

// RunOptions controls one backend job execution.
type RunOptions struct {
	WorkingDirectory string
	Expressions      workflow.ExpressionContext
	Artifacts        *artifacts.Store
	Cache            *ghcache.Store
	Provider         Provider
}

// CommandResult is one shell-command execution result returned by a guest or
// remote worker transport.
type CommandResult struct {
	Stdout   string
	Stderr   string
	ExitCode int
}

// CommandTransport executes one shell command inside a guest or remote worker
// context.
type CommandTransport interface {
	ExecCommand(ctx context.Context, workingDirectory string, environment workflow.EnvironmentMap, command string) (CommandResult, error)
}

// Executor runs one materialized workflow job.
type Executor interface {
	RunJob(ctx context.Context, job *workflow.Job, options RunOptions) (*JobResult, error)
}

// Worker is an executor that also advertises worker capabilities.
type Worker interface {
	Executor
	Capabilities() CapabilitySet
}

// ActionContext is one materialized action-step execution request.
type ActionContext struct {
	Job              *workflow.Job
	Step             workflow.Step
	WorkingDirectory string
	Expressions      workflow.ExpressionContext
	Artifacts        *artifacts.Store
	Cache            *ghcache.Store
	Inputs           workflow.ActionInputMap
	Env              workflow.EnvironmentMap
}

// ActionHandler executes one materialized action step.
type ActionHandler interface {
	Handle(ctx context.Context, action ActionContext) (StepResult, error)
}

// ActionHandlerFunc adapts a function into an ActionHandler.
type ActionHandlerFunc func(ctx context.Context, action ActionContext) (StepResult, error)

// Handle executes the wrapped action step.
func (f ActionHandlerFunc) Handle(ctx context.Context, action ActionContext) (StepResult, error) {
	return f(ctx, action)
}

// PostStep runs one registered post-step hook after the main job steps finish.
type PostStep interface {
	Run(ctx context.Context, status JobStatus) (StepResult, error)
}

// PostStepFunc adapts a function into a PostStep.
type PostStepFunc func(ctx context.Context, status JobStatus) (StepResult, error)

// Run executes the wrapped post-step hook.
func (f PostStepFunc) Run(ctx context.Context, status JobStatus) (StepResult, error) {
	return f(ctx, status)
}
