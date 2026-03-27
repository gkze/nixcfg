package controller

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/gkze/ghawfr/backend"
	"github.com/gkze/ghawfr/state"
	"github.com/gkze/ghawfr/workflow"
)

func TestRunUntilBlockedSelectedExecutesOnlyChosenJob(t *testing.T) {
	tempDir := t.TempDir()
	workflowPath := filepath.Join(tempDir, "select.yml")
	workflowText := []byte(`
name: Select
on: workflow_dispatch
jobs:
  alpha:
    runs-on: ubuntu-latest
    steps:
      - run: echo alpha
  beta:
    runs-on: ubuntu-latest
    steps:
      - run: echo beta
`)
	if err := osWriteFile(workflowPath, workflowText); err != nil {
		t.Fatalf("write workflow: %v", err)
	}

	run := state.NewRun(workflowPath)
	result, err := RunUntilBlockedSelectedFile(
		context.Background(),
		workflowPath,
		workflow.JobSet{"beta": true},
		run,
		backend.Local{},
		backend.RunOptions{WorkingDirectory: tempDir},
		workflow.ParseOptions{},
	)
	if err != nil {
		t.Fatalf("RunUntilBlockedSelectedFile: %v", err)
	}
	if got, want := jobIDs(result.Jobs), "beta"; got != want {
		t.Fatalf("executed jobs = %q, want %q", got, want)
	}
}

func TestRunUntilBlockedContinuesAfterIndependentFailure(t *testing.T) {
	tempDir := t.TempDir()
	workflowPath := filepath.Join(tempDir, "continue.yml")
	workflowText := []byte(`
name: Continue After Failure
on: workflow_dispatch
jobs:
  fail:
    runs-on: ubuntu-latest
    steps:
      - run: exit 1
  pass:
    runs-on: ubuntu-latest
    steps:
      - run: echo pass
`)
	if err := osWriteFile(workflowPath, workflowText); err != nil {
		t.Fatalf("write workflow: %v", err)
	}

	run := state.NewRun(workflowPath)
	result, err := RunUntilBlockedFile(
		context.Background(),
		workflowPath,
		run,
		backend.Local{},
		backend.RunOptions{WorkingDirectory: tempDir},
		workflow.ParseOptions{},
	)
	if err != nil {
		t.Fatalf("RunUntilBlockedFile: %v", err)
	}
	if got, want := jobIDs(result.Jobs), "fail,pass"; got != want {
		t.Fatalf("executed jobs = %q, want %q", got, want)
	}
	if got, want := run.Jobs["fail"].Status, state.JobStatusFailure; got != want {
		t.Fatalf("fail status = %q, want %q", got, want)
	}
	if got, want := run.Jobs["pass"].Status, state.JobStatusSuccess; got != want {
		t.Fatalf("pass status = %q, want %q", got, want)
	}
}

func TestRunUntilBlockedExecutesLateBoundMatrixWorkflow(t *testing.T) {
	tempDir := t.TempDir()
	workflowPath := filepath.Join(tempDir, "late.yml")
	workflowText := []byte(`
name: Late Bound
on: workflow_dispatch
jobs:
  prepare:
    runs-on: ubuntu-latest
    outputs:
      matrix: ${{ steps.emit.outputs.matrix }}
    steps:
      - id: emit
        run: echo 'matrix={"package":["a","b"]}' >> "$GITHUB_OUTPUT"
  test:
    needs: prepare
    strategy:
      matrix: ${{ fromJSON(needs.prepare.outputs.matrix) }}
    runs-on: ubuntu-latest
    steps:
      - id: emit
        run: echo 'pkg=${{ matrix.package }}' >> "$GITHUB_OUTPUT"
  package:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - run: echo package
`)
	if err := osWriteFile(workflowPath, workflowText); err != nil {
		t.Fatalf("write workflow: %v", err)
	}

	run := state.NewRun(workflowPath)
	result, err := RunUntilBlockedFile(
		context.Background(),
		workflowPath,
		run,
		backend.Local{},
		backend.RunOptions{WorkingDirectory: tempDir},
		workflow.ParseOptions{},
	)
	if err != nil {
		t.Fatalf("RunUntilBlockedFile: %v", err)
	}
	if got, want := jobIDs(result.Jobs), "prepare,test[package=a],test[package=b],package"; got != want {
		t.Fatalf("executed jobs = %q, want %q", got, want)
	}
	if len(result.Final.Workflow.DeferredOrder) != 0 {
		t.Fatalf("final deferred order = %q, want empty", result.Final.Workflow.DeferredOrder.Join(","))
	}
	if got, want := run.NeedsContext()["prepare"].Outputs["matrix"], `{"package":["a","b"]}`; got != want {
		t.Fatalf("prepare matrix output = %q, want %q", got, want)
	}
}

func jobIDs(results []*backend.JobResult) string {
	ids := make([]string, 0, len(results))
	for _, result := range results {
		if result == nil {
			continue
		}
		ids = append(ids, result.JobID.String())
	}
	return strings.Join(ids, ",")
}

func osWriteFile(path string, data []byte) error {
	return os.WriteFile(path, data, 0o644)
}
