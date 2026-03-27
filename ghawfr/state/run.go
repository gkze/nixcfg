package state

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"

	"github.com/gkze/ghawfr/workflow"
)

// JobStatus is one persisted job execution status.
type JobStatus string

const (
	// JobStatusRunning means the job has started but not finished.
	JobStatusRunning JobStatus = "running"
	// JobStatusSuccess means the job completed successfully.
	JobStatusSuccess JobStatus = "success"
	// JobStatusFailure means the job failed.
	JobStatusFailure JobStatus = "failure"
	// JobStatusSkipped means the job was skipped by condition evaluation.
	JobStatusSkipped JobStatus = "skipped"
)

// JobRecord is one persisted job execution record.
type JobRecord struct {
	ID        workflow.JobID     `json:"id"`
	LogicalID workflow.JobID     `json:"logical_id"`
	Status    JobStatus          `json:"status"`
	Outputs   workflow.OutputMap `json:"outputs,omitempty"`
}

// Run is the persisted workflow run state used by the controller.
type Run struct {
	SourcePath string                        `json:"source_path"`
	Jobs       map[workflow.JobID]*JobRecord `json:"jobs,omitempty"`
}

// NewRun creates an empty run state for one workflow source.
func NewRun(sourcePath string) *Run {
	return &Run{SourcePath: sourcePath, Jobs: make(map[workflow.JobID]*JobRecord)}
}

// Load reads one run state file from disk.
func Load(path string) (*Run, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read run state %q: %w", path, err)
	}
	var run Run
	if err := json.Unmarshal(data, &run); err != nil {
		return nil, fmt.Errorf("decode run state %q: %w", path, err)
	}
	if run.Jobs == nil {
		run.Jobs = make(map[workflow.JobID]*JobRecord)
	}
	return &run, nil
}

// Save writes one run state file to disk.
func (r *Run) Save(path string) error {
	if r == nil {
		return fmt.Errorf("run state is nil")
	}
	data, err := json.MarshalIndent(r, "", "  ")
	if err != nil {
		return fmt.Errorf("encode run state: %w", err)
	}
	data = append(data, '\n')
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("create state dir for %q: %w", path, err)
	}
	if err := os.WriteFile(path, data, 0o644); err != nil {
		return fmt.Errorf("write run state %q: %w", path, err)
	}
	return nil
}

// Record stores one terminal job result in the run state.
func (r *Run) Record(jobID, logicalID workflow.JobID, status JobStatus, outputs workflow.OutputMap) {
	if r == nil {
		return
	}
	if r.Jobs == nil {
		r.Jobs = make(map[workflow.JobID]*JobRecord)
	}
	if logicalID == "" {
		logicalID = jobID
	}
	r.Jobs[jobID] = &JobRecord{ID: jobID, LogicalID: logicalID, Status: status, Outputs: outputs.Clone()}
}

// ExecutedJobs returns all terminal executable jobs recorded in the run state.
func (r *Run) ExecutedJobs() workflow.JobSet {
	if r == nil || len(r.Jobs) == 0 {
		return nil
	}
	executed := make(workflow.JobSet)
	for jobID, record := range r.Jobs {
		if record == nil {
			continue
		}
		switch record.Status {
		case JobStatusSuccess, JobStatusFailure, JobStatusSkipped:
			executed[jobID] = true
		}
	}
	if len(executed) == 0 {
		return nil
	}
	return executed
}

// CompletedJobs returns the successfully completed executable jobs.
func (r *Run) CompletedJobs() workflow.JobSet {
	if r == nil || len(r.Jobs) == 0 {
		return nil
	}
	completed := make(workflow.JobSet)
	for jobID, record := range r.Jobs {
		if record == nil || record.Status != JobStatusSuccess {
			continue
		}
		completed[jobID] = true
	}
	if len(completed) == 0 {
		return nil
	}
	return completed
}

// NeedsContext returns the logical-job needs context derived from completed job records.
func (r *Run) NeedsContext() workflow.NeedContextMap {
	if r == nil || len(r.Jobs) == 0 {
		return nil
	}
	logicalIDs := make([]workflow.JobID, 0)
	recordsByLogicalID := make(map[workflow.JobID][]*JobRecord)
	for _, record := range r.Jobs {
		if record == nil || record.LogicalID == "" {
			continue
		}
		if _, ok := recordsByLogicalID[record.LogicalID]; !ok {
			logicalIDs = append(logicalIDs, record.LogicalID)
		}
		recordsByLogicalID[record.LogicalID] = append(recordsByLogicalID[record.LogicalID], record)
	}
	if len(recordsByLogicalID) == 0 {
		return nil
	}
	sort.Slice(logicalIDs, func(i, j int) bool {
		return logicalIDs[i] < logicalIDs[j]
	})
	needs := make(workflow.NeedContextMap, len(logicalIDs))
	for _, logicalID := range logicalIDs {
		records := recordsByLogicalID[logicalID]
		sort.Slice(records, func(i, j int) bool {
			return records[i].ID < records[j].ID
		})
		mergedOutputs := make(workflow.OutputMap)
		result := JobStatusSuccess
		for _, record := range records {
			for key, value := range record.Outputs {
				mergedOutputs[key] = value
			}
			result = aggregateStatus(result, record.Status)
		}
		if len(mergedOutputs) == 0 {
			mergedOutputs = nil
		}
		needs[logicalID] = workflow.NeedContext{Outputs: mergedOutputs, Result: string(result)}
	}
	return needs
}

func aggregateStatus(left, right JobStatus) JobStatus {
	if left == JobStatusFailure || right == JobStatusFailure {
		return JobStatusFailure
	}
	if left == JobStatusRunning || right == JobStatusRunning {
		return JobStatusRunning
	}
	if left == JobStatusSkipped || right == JobStatusSkipped {
		return JobStatusSkipped
	}
	return JobStatusSuccess
}
