package controller

import (
	"testing"

	"github.com/gkze/ghawfr/workflow"
)

func TestBuildSnapshotDefersLateBoundJobsThenMaterializesThem(t *testing.T) {
	source := []byte(`
name: Late Bound
on: workflow_dispatch
jobs:
  prepare:
    runs-on: ubuntu-latest
    steps:
      - run: echo prepare
  test:
    needs: prepare
    strategy:
      matrix: ${{ fromJSON(needs.prepare.outputs.matrix) }}
    runs-on: ubuntu-latest
    steps:
      - run: echo test
  package:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - run: echo package
`)

	initial, err := BuildSnapshot("late.yml", source, RunState{}, workflow.ParseOptions{})
	if err != nil {
		t.Fatalf("BuildSnapshot(initial): %v", err)
	}
	if got, want := initial.Workflow.JobOrder.Join(","), "prepare"; got != want {
		t.Fatalf("initial job order = %q, want %q", got, want)
	}
	if got, want := initial.Workflow.DeferredOrder.Join(","), "test,package"; got != want {
		t.Fatalf("initial deferred order = %q, want %q", got, want)
	}
	if got, want := initial.Ready.Join(","), "prepare"; got != want {
		t.Fatalf("initial ready = %q, want %q", got, want)
	}

	next, err := BuildSnapshot("late.yml", source, RunState{
		ExecutedJobs:  workflow.JobSet{"prepare": true},
		CompletedJobs: workflow.JobSet{"prepare": true},
		Needs: workflow.NeedContextMap{
			"prepare": {
				Outputs: workflow.OutputMap{"matrix": `{"package":["a","b"]}`},
				Result:  "success",
			},
		},
	}, workflow.ParseOptions{})
	if err != nil {
		t.Fatalf("BuildSnapshot(next): %v", err)
	}
	if got, want := next.Workflow.JobOrder.Join(","), "prepare,test[package=a],test[package=b],package"; got != want {
		t.Fatalf("next job order = %q, want %q", got, want)
	}
	if got, want := next.Ready.Join(","), "test[package=a],test[package=b]"; got != want {
		t.Fatalf("next ready = %q, want %q", got, want)
	}
	if got, want := next.Workflow.Jobs["package"].Needs.Join(","), "test[package=a],test[package=b]"; got != want {
		t.Fatalf("package needs = %q, want %q", got, want)
	}

	delta := DiffSnapshots(initial, next)
	if got, want := delta.AddedJobs.Join(","), "package,test[package=a],test[package=b]"; got != want {
		t.Fatalf("delta.AddedJobs = %q, want %q", got, want)
	}
	if got, want := delta.NewlyReadyJobs.Join(","), "test[package=a],test[package=b]"; got != want {
		t.Fatalf("delta.NewlyReadyJobs = %q, want %q", got, want)
	}
	if got, want := delta.ResolvedDeferredJobs.Join(","), "package,test"; got != want {
		t.Fatalf("delta.ResolvedDeferredJobs = %q, want %q", got, want)
	}
}
