package planner

import (
	"errors"
	"strconv"
	"strings"
	"testing"

	"github.com/gkze/ghawfr/workflow"
)

func TestBuildOrdersJobsIntoStages(t *testing.T) {
	definition := &workflow.Workflow{
		Jobs: workflow.JobMap{
			"lint":    {ID: "lint"},
			"build":   {ID: "build"},
			"test":    {ID: "test", Needs: workflow.JobIDs{"lint", "build"}},
			"package": {ID: "package", Needs: workflow.JobIDs{"test"}},
		},
		JobOrder: workflow.JobIDs{"lint", "build", "test", "package"},
	}

	plan, err := Build(definition)
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	if got, want := plan.Order.Join(","), "lint,build,test,package"; got != want {
		t.Fatalf("plan.Order = %q, want %q", got, want)
	}
	if got, want := stageSummary(plan.Stages), "0:lint|build;1:test;2:package"; got != want {
		t.Fatalf("plan.Stages = %q, want %q", got, want)
	}
}

func TestBuildPlansExpandedMatrixJobs(t *testing.T) {
	definition, err := workflow.Parse("ci.yml", []byte(`
name: CI
on: workflow_dispatch
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: make build
  test:
    needs: build
    strategy:
      matrix:
        os: [ubuntu-latest, macos-15]
        go: ['1.24', '1.25']
    runs-on: ubuntu-latest
    steps:
      - run: make test
  package:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - run: make package
`))
	if err != nil {
		t.Fatalf("workflow.Parse: %v", err)
	}

	plan, err := Build(definition)
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	if got, want := stageSummary(plan.Stages), "0:build;1:test[go=1.24,os=ubuntu-latest]|test[go=1.24,os=macos-15]|test[go=1.25,os=ubuntu-latest]|test[go=1.25,os=macos-15];2:package"; got != want {
		t.Fatalf("plan.Stages = %q, want %q", got, want)
	}
}

func TestReadyFromPlanReturnsJobsWhoseDependenciesAreSatisfied(t *testing.T) {
	definition := &workflow.Workflow{
		Jobs: workflow.JobMap{
			"lint":  {ID: "lint"},
			"build": {ID: "build"},
			"test":  {ID: "test", Needs: workflow.JobIDs{"lint", "build"}},
		},
		JobOrder: workflow.JobIDs{"lint", "build", "test"},
	}

	plan, err := Build(definition)
	if err != nil {
		t.Fatalf("Build: %v", err)
	}

	ready, err := ReadyFromPlan(definition, plan, workflow.JobSet{}, workflow.JobSet{})
	if err != nil {
		t.Fatalf("ReadyFromPlan(empty): %v", err)
	}
	if got, want := ready.Join(","), "lint,build"; got != want {
		t.Fatalf("ReadyFromPlan(empty) = %q, want %q", got, want)
	}

	ready, err = ReadyFromPlan(definition, plan, workflow.JobSet{"lint": true}, workflow.JobSet{"lint": true})
	if err != nil {
		t.Fatalf("ReadyFromPlan(lint): %v", err)
	}
	if got, want := ready.Join(","), "build"; got != want {
		t.Fatalf("ReadyFromPlan(lint) = %q, want %q", got, want)
	}

	ready, err = ReadyFromPlan(definition, plan, workflow.JobSet{"lint": true, "build": true}, workflow.JobSet{"lint": true, "build": true})
	if err != nil {
		t.Fatalf("ReadyFromPlan(lint,build): %v", err)
	}
	if got, want := ready.Join(","), "test"; got != want {
		t.Fatalf("ReadyFromPlan(lint,build) = %q, want %q", got, want)
	}
}

func TestReadyBuildsPlanAndDelegatesToReadyFromPlan(t *testing.T) {
	definition := &workflow.Workflow{
		Jobs: workflow.JobMap{
			"lint":  {ID: "lint"},
			"build": {ID: "build", Needs: workflow.JobIDs{"lint"}},
		},
		JobOrder: workflow.JobIDs{"lint", "build"},
	}

	ready, err := Ready(definition, workflow.JobSet{"lint": true}, workflow.JobSet{"lint": true})
	if err != nil {
		t.Fatalf("Ready: %v", err)
	}
	if got, want := ready.Join(","), "build"; got != want {
		t.Fatalf("Ready = %q, want %q", got, want)
	}
}

func TestBuildReportsMissingDependencies(t *testing.T) {
	definition := &workflow.Workflow{
		Jobs: workflow.JobMap{
			"test": {ID: "test", Needs: workflow.JobIDs{"build"}},
		},
		JobOrder: workflow.JobIDs{"test"},
	}

	_, err := Build(definition)
	if err == nil {
		t.Fatal("Build error = nil, want error")
	}
	var validationErr *ValidationError
	if !errors.As(err, &validationErr) {
		t.Fatalf("error type = %T, want *ValidationError", err)
	}
	if len(validationErr.MissingDependencies) != 1 {
		t.Fatalf("len(validationErr.MissingDependencies) = %d, want 1", len(validationErr.MissingDependencies))
	}
	if validationErr.MissingDependencies[0].Dependency != "build" {
		t.Fatalf("missing dependency = %#v, want build", validationErr.MissingDependencies[0])
	}
}

func TestBuildReportsCycles(t *testing.T) {
	definition := &workflow.Workflow{
		Jobs: workflow.JobMap{
			"build": {ID: "build", Needs: workflow.JobIDs{"test"}},
			"test":  {ID: "test", Needs: workflow.JobIDs{"build"}},
		},
		JobOrder: workflow.JobIDs{"build", "test"},
	}

	_, err := Build(definition)
	if err == nil {
		t.Fatal("Build error = nil, want error")
	}
	var validationErr *ValidationError
	if !errors.As(err, &validationErr) {
		t.Fatalf("error type = %T, want *ValidationError", err)
	}
	if len(validationErr.Cycles) == 0 {
		t.Fatal("len(validationErr.Cycles) = 0, want > 0")
	}
	if got, want := validationErr.Cycles[0].Join(","), "build,test"; got != want {
		t.Fatalf("cycle = %q, want %q", got, want)
	}
}

func stageSummary(stages []Stage) string {
	parts := make([]string, 0, len(stages))
	for _, stage := range stages {
		parts = append(parts, stageString(stage))
	}
	return strings.Join(parts, ";")
}

func stageString(stage Stage) string {
	return strings.Join([]string{itoa(stage.Index), stage.Jobs.Join("|")}, ":")
}

func itoa(value int) string {
	return strconv.Itoa(value)
}
