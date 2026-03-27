package state

import (
	"path/filepath"
	"testing"

	"github.com/gkze/ghawfr/workflow"
)

func TestRunRecordDerivesCompletedJobsAndNeedsContext(t *testing.T) {
	run := NewRun("workflow.yml")
	run.Record("prepare", "prepare", JobStatusSuccess, workflow.OutputMap{"matrix": `{"package":["a","b"]}`})
	run.Record("test[package=a]", "test", JobStatusSuccess, nil)
	run.Record("test[package=b]", "test", JobStatusFailure, nil)

	executed := run.ExecutedJobs()
	wantExecuted := workflow.JobIDs{"prepare", "test[package=a]", "test[package=b]"}.Join(",")
	if got := keys(executed).Join(","); got != wantExecuted {
		t.Fatalf("executed = %q, want %q", got, wantExecuted)
	}

	completed := run.CompletedJobs()
	wantCompleted := workflow.JobIDs{"prepare", "test[package=a]"}.Join(",")
	if got := keys(completed).Join(","); got != wantCompleted {
		t.Fatalf("completed = %q, want %q", got, wantCompleted)
	}

	needs := run.NeedsContext()
	if got, want := needs["prepare"].Outputs["matrix"], `{"package":["a","b"]}`; got != want {
		t.Fatalf("prepare output = %q, want %q", got, want)
	}
	if got, want := needs["test"].Result, string(JobStatusFailure); got != want {
		t.Fatalf("test result = %q, want %q", got, want)
	}
}

func TestRunSaveAndLoadRoundTrips(t *testing.T) {
	tempDir := t.TempDir()
	path := filepath.Join(tempDir, "run.json")

	run := NewRun("workflow.yml")
	run.Record("prepare", "prepare", JobStatusSuccess, workflow.OutputMap{"matrix": `{"package":["a"]}`})
	if err := run.Save(path); err != nil {
		t.Fatalf("Save: %v", err)
	}

	loaded, err := Load(path)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if loaded.SourcePath != "workflow.yml" {
		t.Fatalf("loaded.SourcePath = %q, want workflow.yml", loaded.SourcePath)
	}
	if got, want := loaded.Jobs["prepare"].Outputs["matrix"], `{"package":["a"]}`; got != want {
		t.Fatalf("loaded output = %q, want %q", got, want)
	}
}

func keys(values workflow.JobSet) workflow.JobIDs {
	result := make(workflow.JobIDs, 0, len(values))
	for key := range values {
		result = append(result, key)
	}
	for i := 0; i < len(result); i++ {
		for j := i + 1; j < len(result); j++ {
			if result[j] < result[i] {
				result[i], result[j] = result[j], result[i]
			}
		}
	}
	return result
}
