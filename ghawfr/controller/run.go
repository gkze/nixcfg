package controller

import (
	"context"
	"errors"
	"fmt"
	"os"

	"github.com/gkze/ghawfr/backend"
	"github.com/gkze/ghawfr/state"
	"github.com/gkze/ghawfr/workflow"
)

// AdvanceResult describes one controller/backend execution step.
type AdvanceResult struct {
	Before   *Snapshot
	After    *Snapshot
	Delta    Delta
	Executed *backend.JobResult
}

// RunResult describes one controller loop execution wave.
type RunResult struct {
	Initial *Snapshot
	Final   *Snapshot
	Jobs    []*backend.JobResult
}

// AdvanceFile rebuilds the current snapshot, runs the next ready job, records the
// result, and rebuilds the snapshot again.
func AdvanceFile(
	ctx context.Context,
	path string,
	run *state.Run,
	executor backend.Executor,
	runOptions backend.RunOptions,
	parseOptions workflow.ParseOptions,
) (*AdvanceResult, error) {
	return AdvanceSelectedFile(ctx, path, nil, run, executor, runOptions, parseOptions)
}

// AdvanceSelectedFile is like AdvanceFile but restricts execution to the given
// materialized job identifiers.
func AdvanceSelectedFile(
	ctx context.Context,
	path string,
	allowed workflow.JobSet,
	run *state.Run,
	executor backend.Executor,
	runOptions backend.RunOptions,
	parseOptions workflow.ParseOptions,
) (*AdvanceResult, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read workflow %q: %w", path, err)
	}
	return AdvanceSelected(ctx, path, data, allowed, run, executor, runOptions, parseOptions)
}

// Advance rebuilds the current snapshot, runs the next ready job, records the
// result, and rebuilds the snapshot again.
func Advance(
	ctx context.Context,
	sourcePath string,
	data []byte,
	run *state.Run,
	executor backend.Executor,
	runOptions backend.RunOptions,
	parseOptions workflow.ParseOptions,
) (*AdvanceResult, error) {
	return AdvanceSelected(ctx, sourcePath, data, nil, run, executor, runOptions, parseOptions)
}

// AdvanceSelected is like Advance but restricts execution to the given
// materialized job identifiers.
func AdvanceSelected(
	ctx context.Context,
	sourcePath string,
	data []byte,
	allowed workflow.JobSet,
	run *state.Run,
	executor backend.Executor,
	runOptions backend.RunOptions,
	parseOptions workflow.ParseOptions,
) (*AdvanceResult, error) {
	if executor == nil {
		return nil, fmt.Errorf("executor is nil")
	}
	if run == nil {
		run = state.NewRun(sourcePath)
	}
	before, err := BuildSnapshot(sourcePath, data, runStateFrom(run), parseOptions)
	if err != nil {
		return nil, err
	}
	readyID, ok := selectReadyJob(before.Ready, allowed)
	if !ok {
		return &AdvanceResult{Before: before, After: before, Delta: DiffSnapshots(before, before)}, nil
	}
	job := before.Workflow.Jobs[readyID]
	if job == nil {
		return nil, fmt.Errorf("ready job %q is missing from workflow snapshot", readyID)
	}
	runOptions.Expressions = mergeExpressionContext(runOptions.Expressions, run)
	result, err := runJobWithProvider(ctx, job, executor, runOptions)
	if err != nil {
		var unsupported backend.UnsupportedActionError
		if result == nil || errors.As(err, &unsupported) {
			return nil, err
		}
	}
	recordResult(run, result)
	after, err := BuildSnapshot(sourcePath, data, runStateFrom(run), parseOptions)
	if err != nil {
		return nil, err
	}
	return &AdvanceResult{Before: before, After: after, Delta: DiffSnapshots(before, after), Executed: result}, nil
}

// RunUntilBlocked keeps executing ready jobs until no more ready jobs remain.
func RunUntilBlocked(
	ctx context.Context,
	sourcePath string,
	data []byte,
	run *state.Run,
	executor backend.Executor,
	runOptions backend.RunOptions,
	parseOptions workflow.ParseOptions,
) (*RunResult, error) {
	return RunUntilBlockedSelected(ctx, sourcePath, data, nil, run, executor, runOptions, parseOptions)
}

// RunUntilBlockedSelected is like RunUntilBlocked but restricts execution to the
// given materialized job identifiers.
func RunUntilBlockedSelected(
	ctx context.Context,
	sourcePath string,
	data []byte,
	allowed workflow.JobSet,
	run *state.Run,
	executor backend.Executor,
	runOptions backend.RunOptions,
	parseOptions workflow.ParseOptions,
) (*RunResult, error) {
	if run == nil {
		run = state.NewRun(sourcePath)
	}
	initial, err := BuildSnapshot(sourcePath, data, runStateFrom(run), parseOptions)
	if err != nil {
		return nil, err
	}
	result := &RunResult{Initial: initial, Final: initial, Jobs: make([]*backend.JobResult, 0)}
	for {
		advance, err := AdvanceSelected(ctx, sourcePath, data, allowed, run, executor, runOptions, parseOptions)
		if err != nil {
			return nil, err
		}
		result.Final = advance.After
		if advance.Executed == nil {
			return result, nil
		}
		result.Jobs = append(result.Jobs, advance.Executed)
	}
}

// RunUntilBlockedFile keeps executing ready jobs from one workflow file until no
// more ready jobs remain.
func RunUntilBlockedFile(
	ctx context.Context,
	path string,
	run *state.Run,
	executor backend.Executor,
	runOptions backend.RunOptions,
	parseOptions workflow.ParseOptions,
) (*RunResult, error) {
	return RunUntilBlockedSelectedFile(ctx, path, nil, run, executor, runOptions, parseOptions)
}

// RunUntilBlockedSelectedFile is like RunUntilBlockedFile but restricts execution
// to the given materialized job identifiers.
func RunUntilBlockedSelectedFile(
	ctx context.Context,
	path string,
	allowed workflow.JobSet,
	run *state.Run,
	executor backend.Executor,
	runOptions backend.RunOptions,
	parseOptions workflow.ParseOptions,
) (*RunResult, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read workflow %q: %w", path, err)
	}
	return RunUntilBlockedSelected(ctx, path, data, allowed, run, executor, runOptions, parseOptions)
}

func runJobWithProvider(ctx context.Context, job *workflow.Job, executor backend.Executor, options backend.RunOptions) (*backend.JobResult, error) {
	if options.Provider == nil {
		return executor.RunJob(ctx, job, options)
	}
	lease, err := options.Provider.AcquireWorker(ctx, job, options)
	if err != nil {
		return nil, err
	}
	defer func() {
		_ = lease.Release(ctx)
	}()
	return lease.Worker().RunJob(ctx, job, options)
}

func selectReadyJob(ready workflow.JobIDs, allowed workflow.JobSet) (workflow.JobID, bool) {
	for _, jobID := range ready {
		if len(allowed) > 0 && !allowed[jobID] {
			continue
		}
		return jobID, true
	}
	return "", false
}

func runStateFrom(run *state.Run) RunState {
	if run == nil {
		return RunState{}
	}
	return RunState{ExecutedJobs: run.ExecutedJobs(), CompletedJobs: run.CompletedJobs(), Needs: run.NeedsContext()}
}

func mergeExpressionContext(base workflow.ExpressionContext, run *state.Run) workflow.ExpressionContext {
	base.Needs = mergeNeeds(base.Needs, runStateFrom(run).Needs)
	return base
}

func recordResult(run *state.Run, result *backend.JobResult) {
	if run == nil || result == nil {
		return
	}
	switch result.Result {
	case backend.JobStatusSuccess:
		run.Record(result.JobID, result.LogicalID, state.JobStatusSuccess, result.Outputs)
	case backend.JobStatusFailure:
		run.Record(result.JobID, result.LogicalID, state.JobStatusFailure, result.Outputs)
	case backend.JobStatusSkipped:
		run.Record(result.JobID, result.LogicalID, state.JobStatusSkipped, result.Outputs)
	}
}
