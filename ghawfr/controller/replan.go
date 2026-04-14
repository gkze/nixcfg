package controller

import (
	"fmt"
	"os"
	"sort"

	"github.com/gkze/ghawfr/planner"
	"github.com/gkze/ghawfr/workflow"
)

// RunState is the controller-owned late-bound workflow state used for replanning.
type RunState struct {
	ExecutedJobs  workflow.JobSet
	CompletedJobs workflow.JobSet
	Needs         workflow.NeedContextMap
}

// Snapshot is one materialized workflow/planning view for a given run state.
type Snapshot struct {
	Workflow *workflow.Workflow
	Plan     *planner.Plan
	Ready    workflow.JobIDs
}

// Delta summarizes how one snapshot changed relative to a previous snapshot.
type Delta struct {
	AddedJobs            workflow.JobIDs
	RemovedJobs          workflow.JobIDs
	NewlyReadyJobs       workflow.JobIDs
	DeferredJobs         workflow.JobIDs
	ResolvedDeferredJobs workflow.JobIDs
	StillDeferredJobs    workflow.JobIDs
}

// BuildSnapshot reparses, re-expands, and replans one workflow for the given run state.
func BuildSnapshot(sourcePath string, data []byte, state RunState, options workflow.ParseOptions) (*Snapshot, error) {
	options.Expressions.Needs = mergeNeeds(options.Expressions.Needs, state.Needs)
	definition, err := workflow.ParseWithOptions(sourcePath, data, options)
	if err != nil {
		return nil, err
	}
	plan, err := planner.Build(definition)
	if err != nil {
		return nil, err
	}
	ready, err := planner.ReadyFromPlan(definition, plan, state.ExecutedJobs, state.CompletedJobs)
	if err != nil {
		return nil, err
	}
	return &Snapshot{Workflow: definition, Plan: plan, Ready: ready}, nil
}

// BuildSnapshotFile reads, reparses, re-expands, and replans one workflow file for the given run state.
func BuildSnapshotFile(path string, state RunState, options workflow.ParseOptions) (*Snapshot, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read workflow %q: %w", path, err)
	}
	return BuildSnapshot(path, data, state, options)
}

// DiffSnapshots computes a deterministic snapshot delta.
func DiffSnapshots(previous, current *Snapshot) Delta {
	previousJobs := snapshotJobSet(previous)
	currentJobs := snapshotJobSet(current)
	previousReady := jobIDSet(nil)
	currentReady := jobIDSet(nil)
	previousDeferred := deferredJobSet(previous)
	currentDeferred := deferredJobSet(current)
	if previous != nil {
		previousReady = jobIDSet(previous.Ready)
	}
	if current != nil {
		currentReady = jobIDSet(current.Ready)
	}
	return Delta{
		AddedJobs:            sortedDifference(currentJobs, previousJobs),
		RemovedJobs:          sortedDifference(previousJobs, currentJobs),
		NewlyReadyJobs:       sortedDifference(currentReady, previousReady),
		DeferredJobs:         sortedKeys(currentDeferred),
		ResolvedDeferredJobs: sortedDifference(previousDeferred, currentDeferred),
		StillDeferredJobs:    sortedIntersection(previousDeferred, currentDeferred),
	}
}

func mergeNeeds(base, overlay workflow.NeedContextMap) workflow.NeedContextMap {
	if len(base) == 0 && len(overlay) == 0 {
		return nil
	}
	merged := base.Clone()
	if merged == nil {
		merged = make(workflow.NeedContextMap, len(overlay))
	}
	for key, value := range overlay {
		merged[key] = workflow.NeedContext{Outputs: value.Outputs.Clone(), Result: value.Result}
	}
	return merged
}

func snapshotJobSet(snapshot *Snapshot) map[workflow.JobID]struct{} {
	values := make(map[workflow.JobID]struct{})
	if snapshot == nil || snapshot.Workflow == nil {
		return values
	}
	for _, jobID := range snapshot.Workflow.JobOrder {
		values[jobID] = struct{}{}
	}
	return values
}

func deferredJobSet(snapshot *Snapshot) map[workflow.JobID]struct{} {
	values := make(map[workflow.JobID]struct{})
	if snapshot == nil || snapshot.Workflow == nil {
		return values
	}
	for _, logicalID := range snapshot.Workflow.DeferredOrder {
		values[logicalID] = struct{}{}
	}
	return values
}

func jobIDSet(values workflow.JobIDs) map[workflow.JobID]struct{} {
	set := make(map[workflow.JobID]struct{}, len(values))
	for _, value := range values {
		set[value] = struct{}{}
	}
	return set
}

func sortedDifference(left, right map[workflow.JobID]struct{}) workflow.JobIDs {
	values := make(workflow.JobIDs, 0)
	for value := range left {
		if _, ok := right[value]; ok {
			continue
		}
		values = append(values, value)
	}
	sort.Slice(values, func(i, j int) bool {
		return values[i] < values[j]
	})
	return values
}

func sortedIntersection(left, right map[workflow.JobID]struct{}) workflow.JobIDs {
	values := make(workflow.JobIDs, 0)
	for value := range left {
		if _, ok := right[value]; !ok {
			continue
		}
		values = append(values, value)
	}
	sort.Slice(values, func(i, j int) bool {
		return values[i] < values[j]
	})
	return values
}

func sortedKeys(values map[workflow.JobID]struct{}) workflow.JobIDs {
	keys := make(workflow.JobIDs, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		return keys[i] < keys[j]
	})
	return keys
}
