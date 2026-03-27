package backend

import (
	"context"
	"fmt"
	"os"

	"github.com/gkze/ghawfr/artifacts"
	ghcache "github.com/gkze/ghawfr/cache"
	"github.com/gkze/ghawfr/workflow"
)

// UnsupportedActionError reports that no curated action handler exists for a uses: step.
type UnsupportedActionError struct {
	Uses string
}

func (e UnsupportedActionError) Error() string {
	return fmt.Sprintf("action %q is not supported by this backend", e.Uses)
}

type runStepFunc func(ctx context.Context, job *workflow.Job, step workflow.Step, workspace string, expr workflow.ExpressionContext) (StepResult, error)

func executeJob(
	ctx context.Context,
	job *workflow.Job,
	options RunOptions,
	actionOverrides map[string]ActionHandler,
	runStep runStepFunc,
) (*JobResult, error) {
	if job == nil {
		return nil, fmt.Errorf("job is nil")
	}
	if job.WorkflowCall != nil {
		return nil, fmt.Errorf("reusable workflow jobs are not supported by this backend")
	}
	workspace, err := resolveWorkingDirectory(options.WorkingDirectory)
	if err != nil {
		return nil, err
	}
	jobContext := options.Expressions
	jobContext.Env = mergeEnvironment(jobContext.Env, runnerEnvironment(jobContext.Runner))
	resolvedJobEnv, err := resolveEnvironment(job, job.Env, jobContext)
	if err != nil {
		return nil, fmt.Errorf("resolve job env for %q: %w", job.ID, err)
	}
	jobContext.Env = mergeEnvironment(jobContext.Env, resolvedJobEnv)
	shouldRun, err := workflow.EvaluateCondition(job, job.If, jobContext)
	if err != nil {
		return nil, fmt.Errorf("evaluate job if for %q: %w", job.ID, err)
	}
	if !shouldRun {
		return &JobResult{JobID: job.ID, LogicalID: job.LogicalID, Result: JobStatusSkipped}, nil
	}

	handlers := normalizedActionHandlers(actionOverrides)
	stepContexts := make(workflow.StepContextMap)
	results := make([]StepResult, 0, len(job.Steps))
	posts := make([]PostStep, 0)
	jobResultStatus := JobStatusSuccess
	var jobErr error
	for _, step := range job.Steps {
		stepContext := jobContext
		stepContext.Steps = stepContexts.Clone()
		resolvedStepEnv, err := resolveEnvironment(job, step.Env, stepContext)
		if err != nil {
			return nil, fmt.Errorf("resolve env for step %q in job %q: %w", step.ID, job.ID, err)
		}
		stepContext.Env = mergeEnvironment(stepContext.Env, resolvedStepEnv)
		shouldRun, err := workflow.EvaluateCondition(job, step.If, stepContext)
		if err != nil {
			return nil, fmt.Errorf("evaluate step if for %q in job %q: %w", step.ID, job.ID, err)
		}
		if !shouldRun {
			results = append(results, StepResult{ID: step.ID, Conclusion: string(JobStatusSkipped), Outcome: string(JobStatusSkipped)})
			if step.ID != "" {
				stepContexts[step.ID] = workflow.StepContext{Outputs: nil, Outcome: string(JobStatusSkipped), Conclusion: string(JobStatusSkipped)}
			}
			continue
		}

		var result StepResult
		switch step.Kind {
		case workflow.StepKindRun:
			if step.Run == nil {
				return nil, fmt.Errorf("step %q in job %q is missing run configuration", step.ID, job.ID)
			}
			result, err = runStep(ctx, job, step, workspace, stepContext)
		case workflow.StepKindAction:
			if step.Action == nil {
				return nil, fmt.Errorf("step %q in job %q is missing action configuration", step.ID, job.ID)
			}
			result, err = runActionStep(handlers, ctx, job, step, workspace, stepContext, options.Artifacts, options.Cache)
		default:
			return nil, fmt.Errorf("unsupported step kind %q in job %q", step.Kind, job.ID)
		}
		jobContext.Env = mergeEnvironment(jobContext.Env, result.Environment)
		jobContext.Env = applyPathEntries(jobContext.Env, result.PathEntries)
		if result.Post != nil {
			posts = append(posts, result.Post)
			result.Post = nil
		}
		if err != nil {
			if step.ID != "" {
				stepContexts[step.ID] = workflow.StepContext{Outputs: result.Outputs.Clone(), Outcome: string(JobStatusFailure), Conclusion: string(JobStatusFailure)}
			}
			result.Conclusion = string(JobStatusFailure)
			result.Outcome = string(JobStatusFailure)
			results = append(results, result)
			jobResultStatus = JobStatusFailure
			jobErr = err
			break
		}
		result.Conclusion = string(JobStatusSuccess)
		result.Outcome = string(JobStatusSuccess)
		results = append(results, result)
		if step.ID != "" {
			stepContexts[step.ID] = workflow.StepContext{Outputs: result.Outputs.Clone(), Outcome: result.Outcome, Conclusion: result.Conclusion}
		}
	}
	for i := len(posts) - 1; i >= 0; i-- {
		result, err := posts[i].Run(ctx, jobResultStatus)
		result.Post = nil
		if err != nil {
			result.Conclusion = string(JobStatusFailure)
			result.Outcome = string(JobStatusFailure)
			results = append(results, result)
			if jobErr == nil {
				jobErr = err
			}
			jobResultStatus = JobStatusFailure
			continue
		}
		result.Conclusion = string(JobStatusSuccess)
		result.Outcome = string(JobStatusSuccess)
		results = append(results, result)
	}
	if jobErr != nil {
		return &JobResult{JobID: job.ID, LogicalID: job.LogicalID, Result: JobStatusFailure, Steps: results}, jobErr
	}

	jobContext.Steps = stepContexts
	outputs, err := workflow.ResolveJobOutputs(job, jobContext)
	if err != nil {
		return nil, fmt.Errorf("resolve outputs for %q: %w", job.ID, err)
	}
	return &JobResult{JobID: job.ID, LogicalID: job.LogicalID, Outputs: outputs, Result: JobStatusSuccess, Steps: results}, nil
}

func resolveWorkingDirectory(value string) (string, error) {
	if value != "" {
		return value, nil
	}
	cwd, err := os.Getwd()
	if err != nil {
		return "", fmt.Errorf("resolve working directory: %w", err)
	}
	return cwd, nil
}

func runActionStep(
	actionHandlers map[string]ActionHandler,
	ctx context.Context,
	job *workflow.Job,
	step workflow.Step,
	workspace string,
	expr workflow.ExpressionContext,
	store *artifacts.Store,
	cacheStore *ghcache.Store,
) (StepResult, error) {
	handler, ok := actionHandlers[actionSlug(step.Action.Uses)]
	if !ok {
		return StepResult{ID: step.ID}, UnsupportedActionError{Uses: step.Action.Uses}
	}
	resolvedStep := step
	if step.Action != nil {
		action := *step.Action
		action.Inputs = step.Action.Inputs.Clone()
		resolvedStep.Action = &action
	}
	return handler.Handle(ctx, ActionContext{
		Job:              job,
		Step:             resolvedStep,
		WorkingDirectory: workspace,
		Expressions:      expr,
		Artifacts:        store,
		Cache:            cacheStore,
		Inputs:           step.Action.Inputs.Clone(),
		Env:              expr.Env.Clone(),
	})
}

func normalizedActionHandlers(handlers map[string]ActionHandler) map[string]ActionHandler {
	if len(handlers) == 0 {
		return nil
	}
	normalized := make(map[string]ActionHandler, len(handlers))
	for key, handler := range handlers {
		normalized[actionSlug(key)] = handler
	}
	return normalized
}
