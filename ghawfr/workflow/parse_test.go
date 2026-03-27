package workflow

import (
	"errors"
	"strings"
	"testing"
)

func TestParseExpandsStaticMatrixAndRewritesNeeds(t *testing.T) {
	parsed, err := Parse("testdata/ci.yml", []byte(`
name: CI
run-name: Build ${{ github.ref }}
on:
  push:
    branches:
      - main
  workflow_dispatch:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: make build
  test:
    name: ${{ matrix.os }} / ${{ matrix.go }}
    needs:
      - build
    runs-on:
      group: linux
      labels:
        - ubuntu-latest
        - x64
    strategy:
      fail-fast: false
      max-parallel: 2
      matrix:
        os: [ubuntu-latest, macos-15]
        go: ['1.24', '1.25']
    steps:
      - name: Test
        run: make test
  aggregate:
    needs: test
    uses: ./.github/workflows/reuse.yml
`))
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}

	if parsed.Name != "CI" {
		t.Fatalf("parsed.Name = %q, want CI", parsed.Name)
	}
	if parsed.RunName != "Build ${{ github.ref }}" {
		t.Fatalf("parsed.RunName = %q, want run-name", parsed.RunName)
	}
	if got, want := strings.Join(eventNames(parsed.Events), ","), "push,workflow_dispatch"; got != want {
		t.Fatalf("event names = %q, want %q", got, want)
	}
	if got, want := parsed.JobOrder.Join(","), "build,test[go=1.24,os=ubuntu-latest],test[go=1.24,os=macos-15],test[go=1.25,os=ubuntu-latest],test[go=1.25,os=macos-15],aggregate"; got != want {
		t.Fatalf("JobOrder = %q, want %q", got, want)
	}

	build := parsed.Jobs["build"]
	if build == nil {
		t.Fatal("build job missing")
	}
	if build.LogicalID != "build" {
		t.Fatalf("build.LogicalID = %q, want build", build.LogicalID)
	}
	if got, want := strings.Join(build.RunsOn.Labels, ","), "ubuntu-latest"; got != want {
		t.Fatalf("build runs-on labels = %q, want %q", got, want)
	}

	firstTest := parsed.Jobs["test[go=1.24,os=ubuntu-latest]"]
	if firstTest == nil {
		t.Fatal("first expanded test job missing")
	}
	if firstTest.LogicalID != "test" {
		t.Fatalf("firstTest.LogicalID = %q, want test", firstTest.LogicalID)
	}
	if firstTest.Name != "ubuntu-latest / 1.24" {
		t.Fatalf("firstTest.Name = %q, want resolved matrix name", firstTest.Name)
	}
	if got, want := firstTest.Needs.Join(","), "build"; got != want {
		t.Fatalf("firstTest.Needs = %q, want %q", got, want)
	}
	if got, want := strings.Join(firstTest.RunsOn.Labels, ","), "ubuntu-latest,x64"; got != want {
		t.Fatalf("firstTest runs-on labels = %q, want %q", got, want)
	}
	if firstTest.RunsOn.Group != "linux" {
		t.Fatalf("firstTest runs-on group = %q, want linux", firstTest.RunsOn.Group)
	}
	if firstTest.Strategy == nil || firstTest.Strategy.Matrix == nil {
		t.Fatalf("firstTest strategy = %#v, want matrix strategy", firstTest.Strategy)
	}
	if firstTest.Strategy.FailFast == nil || *firstTest.Strategy.FailFast {
		t.Fatalf("firstTest fail-fast = %#v, want false", firstTest.Strategy.FailFast)
	}
	if firstTest.Strategy.MaxParallel == nil || *firstTest.Strategy.MaxParallel != 2 {
		t.Fatalf("firstTest max-parallel = %#v, want 2", firstTest.Strategy.MaxParallel)
	}
	if got, want := strings.Join(matrixPairs(firstTest), ","), "go=1.24,os=ubuntu-latest"; got != want {
		t.Fatalf("firstTest matrix = %q, want %q", got, want)
	}

	aggregate := parsed.Jobs["aggregate"]
	if aggregate == nil {
		t.Fatal("aggregate job missing")
	}
	if aggregate.WorkflowCall == nil || aggregate.WorkflowCall.Uses != "./.github/workflows/reuse.yml" {
		t.Fatalf("aggregate workflow call = %#v, want reusable workflow call", aggregate.WorkflowCall)
	}
	if got, want := aggregate.Needs.Join(","), "test[go=1.24,os=ubuntu-latest],test[go=1.24,os=macos-15],test[go=1.25,os=ubuntu-latest],test[go=1.25,os=macos-15]"; got != want {
		t.Fatalf("aggregate needs = %q, want %q", got, want)
	}
}

func TestParseExpandsIncludeOnlyMatrix(t *testing.T) {
	parsed, err := Parse("testdata/include.yml", []byte(`
name: CI
on: workflow_dispatch
jobs:
  quality:
    name: ${{ matrix.name }}
    strategy:
      matrix:
        include:
          - name: format
            command: nix fmt -- --ci
          - name: pytest
            command: uv run pytest -q
    runs-on: ubuntu-latest
    steps:
      - run: ${{ matrix.command }}
`))
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	if got, want := parsed.JobOrder.Join(","), "quality[name=format],quality[name=pytest]"; got != want {
		t.Fatalf("JobOrder = %q, want %q", got, want)
	}
	format := parsed.Jobs["quality[name=format]"]
	if format == nil {
		t.Fatal("quality[name=format] missing")
	}
	if format.Name != "format" {
		t.Fatalf("format.Name = %q, want format", format.Name)
	}
	if got, want := strings.Join(matrixPairs(format), ","), "command=\"nix fmt -- --ci\",name=format"; got != want {
		t.Fatalf("format matrix = %q, want %q", got, want)
	}
}

func TestParseResolvesMatrixDrivenRunnerLabels(t *testing.T) {
	parsed, err := Parse("runner.yml", []byte(`
name: Runner Matrix
on: workflow_dispatch
jobs:
  test:
    strategy:
      matrix:
        runner: [ubuntu-24.04, macos-15]
    runs-on: ${{ matrix.runner }}
    steps:
      - run: echo hi
`))
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	if got, want := strings.Join(parsed.Jobs["test[runner=ubuntu-24.04]"].RunsOn.Labels, ","), "ubuntu-24.04"; got != want {
		t.Fatalf("ubuntu runner labels = %q, want %q", got, want)
	}
	if got, want := strings.Join(parsed.Jobs["test[runner=macos-15]"].RunsOn.Labels, ","), "macos-15"; got != want {
		t.Fatalf("mac runner labels = %q, want %q", got, want)
	}
}

func TestParseCapturesJobStepEnvAndActionInputs(t *testing.T) {
	parsed, err := Parse("action.yml", []byte(`
name: Action Data
on: workflow_dispatch
jobs:
  quality:
    runs-on: ubuntu-latest
    env:
      cachix_name: gkze
    steps:
      - uses: actions/cache@v5
        env:
          quality_command: uv run pytest -q
        with:
          path: ~/.cache/nix
          key: nix-${{ env.cachix_name }}
      - run: echo hi
`))
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	job := parsed.Jobs["quality"]
	if job == nil {
		t.Fatal("quality job missing")
	}
	if got, want := job.Env["cachix_name"], "gkze"; got != want {
		t.Fatalf("job env = %q, want %q", got, want)
	}
	if len(job.Steps) < 1 || job.Steps[0].Action == nil {
		t.Fatalf("first step = %#v, want action step", job.Steps)
	}
	if got, want := job.Steps[0].Env["quality_command"], "uv run pytest -q"; got != want {
		t.Fatalf("step env = %q, want %q", got, want)
	}
	if got, want := job.Steps[0].Action.Inputs["path"], "~/.cache/nix"; got != want {
		t.Fatalf("action input path = %q, want %q", got, want)
	}
	if got, want := job.Steps[0].Action.Inputs["key"], "nix-${{ env.cachix_name }}"; got != want {
		t.Fatalf("action input key = %q, want %q", got, want)
	}
}

func TestParseEvaluatesWholeMatrixExpression(t *testing.T) {
	parsed, err := Parse("dynamic.yml", []byte(`
name: Dynamic
on: workflow_dispatch
jobs:
  test:
    name: ${{ matrix.os }}
    strategy:
      matrix: ${{ fromJSON('{"os":["ubuntu-latest","macos-15"]}') }}
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
`))
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	if got, want := parsed.JobOrder.Join(","), "test[os=ubuntu-latest],test[os=macos-15]"; got != want {
		t.Fatalf("JobOrder = %q, want %q", got, want)
	}
	if parsed.Jobs["test[os=ubuntu-latest]"].Name != "ubuntu-latest" {
		t.Fatalf("resolved job name = %q, want ubuntu-latest", parsed.Jobs["test[os=ubuntu-latest]"].Name)
	}
}

func TestParseWithOptionsEvaluatesVarsDrivenMatrix(t *testing.T) {
	parsed, err := ParseWithOptions("vars.yml", []byte(`
name: Vars
on: workflow_dispatch
jobs:
  test:
    strategy:
      matrix: ${{ fromJSON(vars.BUILD_MATRIX) }}
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
`), ParseOptions{Expressions: ExpressionContext{Vars: VariableMap{
		"BUILD_MATRIX": `{"os":["ubuntu-latest","macos-15"]}`,
	}}})
	if err != nil {
		t.Fatalf("ParseWithOptions: %v", err)
	}
	if got, want := parsed.JobOrder.Join(","), "test[os=ubuntu-latest],test[os=macos-15]"; got != want {
		t.Fatalf("JobOrder = %q, want %q", got, want)
	}
}

func TestParseDefersNeedsDrivenMatrixWithoutContext(t *testing.T) {
	parsed, err := Parse("needs.yml", []byte(`
name: Needs
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
      - run: echo hi
  package:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - run: echo package
`))
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	if got, want := parsed.JobOrder.Join(","), "prepare"; got != want {
		t.Fatalf("JobOrder = %q, want %q", got, want)
	}
	if got, want := parsed.DeferredOrder.Join(","), "test,package"; got != want {
		t.Fatalf("DeferredOrder = %q, want %q", got, want)
	}
	if parsed.DeferredJobs["test"] == nil {
		t.Fatal("deferred test job missing")
	}
	if got, want := parsed.DeferredJobs["test"].WaitsOn.Join(","), "prepare"; got != want {
		t.Fatalf("test waits-on = %q, want %q", got, want)
	}
}

func TestParseWithOptionsEvaluatesNeedsDrivenMatrix(t *testing.T) {
	parsed, err := ParseWithOptions("needs.yml", []byte(`
name: Needs
on: workflow_dispatch
jobs:
  test:
    strategy:
      matrix: ${{ fromJSON(needs.prepare.outputs.matrix) }}
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
`), ParseOptions{Expressions: ExpressionContext{Needs: NeedContextMap{
		"prepare": {
			Outputs: OutputMap{"matrix": `{"package":["a","b"]}`},
			Result:  "success",
		},
	}}})
	if err != nil {
		t.Fatalf("ParseWithOptions: %v", err)
	}
	if got, want := parsed.JobOrder.Join(","), "test[package=a],test[package=b]"; got != want {
		t.Fatalf("JobOrder = %q, want %q", got, want)
	}
}

func TestParseEvaluatesNestedMatrixExpressions(t *testing.T) {
	parsed, err := Parse("nested.yml", []byte(`
name: Nested
on: workflow_dispatch
jobs:
  test:
    strategy:
      matrix:
        include:
          - env:
              key1: ${{ 'val' }}1
              ${{ insert }}: ${{ fromJSON('{"key2":"val2"}') }}
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
`))
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	job := parsed.Jobs["test[env={key1:val1,key2:val2}]"]
	if job == nil {
		t.Fatalf("expanded job missing; job order = %q", parsed.JobOrder.Join(","))
	}
	envValue := job.MatrixValues["env"]
	if envValue.Kind != ValueKindObject {
		t.Fatalf("env value kind = %s, want object", envValue.Kind)
	}
	if envValue.Object["key1"].Scalar != "val1" {
		t.Fatalf("env.key1 = %q, want val1", envValue.Object["key1"].Scalar)
	}
	if envValue.Object["key2"].Scalar != "val2" {
		t.Fatalf("env.key2 = %q, want val2", envValue.Object["key2"].Scalar)
	}
}

func TestParseRejectsMatrixExcludeUnknownKey(t *testing.T) {
	_, err := Parse("broken.yml", []byte(`
name: CI
on: workflow_dispatch
jobs:
  test:
    strategy:
      matrix:
        os: [ubuntu-latest]
        exclude:
          - arch: arm64
    runs-on: ubuntu-latest
    steps:
      - run: true
`))
	if err == nil {
		t.Fatal("Parse error = nil, want error")
	}
	var parseErr *ParseError
	if !errors.As(err, &parseErr) {
		t.Fatalf("error type = %T, want *ParseError", err)
	}
	if len(parseErr.Diagnostics) == 0 {
		t.Fatal("len(parseErr.Diagnostics) = 0, want > 0")
	}
	if parseErr.Diagnostics[0].Kind != "matrix-expansion" {
		t.Fatalf("diagnostic kind = %q, want matrix-expansion", parseErr.Diagnostics[0].Kind)
	}
}

func TestParseReturnsStructuredDiagnostics(t *testing.T) {
	_, err := Parse("broken.yml", []byte("jobs:\n  build:\n    runs-on: [\n"))
	if err == nil {
		t.Fatal("Parse error = nil, want error")
	}
	var parseErr *ParseError
	if !errors.As(err, &parseErr) {
		t.Fatalf("error type = %T, want *ParseError", err)
	}
	if len(parseErr.Diagnostics) == 0 {
		t.Fatal("len(parseErr.Diagnostics) = 0, want > 0")
	}
	if parseErr.Diagnostics[0].SourcePath != "broken.yml" {
		t.Fatalf("diagnostic source path = %q, want broken.yml", parseErr.Diagnostics[0].SourcePath)
	}
}

func eventNames(events []Event) []string {
	names := make([]string, 0, len(events))
	for _, event := range events {
		names = append(names, event.Name)
	}
	return names
}

func matrixPairs(job *Job) []string {
	pairs := make([]string, 0, len(job.MatrixKeys))
	for _, pair := range job.MatrixPairs() {
		pairs = append(pairs, pair.Key+"="+pair.Value.IdentifierString())
	}
	return pairs
}
