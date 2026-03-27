package backend

import "github.com/gkze/ghawfr/workflow"

// MaterializedWorker is the on-disk prepared state for one planned worker.
type MaterializedWorker struct {
	Plan      WorkerPlan
	Artifacts []string
}

// MaterializingProvider can materialize provider-specific on-disk launch
// artifacts for one worker before the lease is actually acquired.
type MaterializingProvider interface {
	PlannedProvider
	MaterializeWorker(job *workflow.Job, options RunOptions) (MaterializedWorker, error)
}
