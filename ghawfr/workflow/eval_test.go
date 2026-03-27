package workflow

import "testing"

func TestInterpolateStringAndEvaluateConditionUseTypedContextValues(t *testing.T) {
	job := &Job{
		ID:        "test",
		LogicalID: "test",
		MatrixValues: map[string]Value{
			"os": {Kind: ValueKindScalar, Scalar: "ubuntu-latest"},
		},
		MatrixKeys: []string{"os"},
	}
	context := ExpressionContext{
		Inputs:  InputMap{"count": NumberData(3)},
		Secrets: SecretMap{"TOKEN": "abc123"},
		GitHub: GitHubContext{Event: GitHubEventMap{
			"pull_request": ObjectData(map[string]Data{
				"number": NumberData(42),
				"draft":  BooleanData(false),
			}),
		}},
	}

	text, err := InterpolateString(job, "${{ matrix.os }} #${{ github.event.pull_request.number }} x${{ inputs.count }} ${{ secrets.TOKEN }}", context)
	if err != nil {
		t.Fatalf("InterpolateString: %v", err)
	}
	if got, want := text, "ubuntu-latest #42 x3 abc123"; got != want {
		t.Fatalf("interpolated text = %q, want %q", got, want)
	}

	ok, err := EvaluateCondition(job, "github.event.pull_request.draft == false && inputs.count == 3", context)
	if err != nil {
		t.Fatalf("EvaluateCondition: %v", err)
	}
	if !ok {
		t.Fatal("condition = false, want true")
	}
}

func TestResolveJobOutputsUsesStepOutputs(t *testing.T) {
	job := &Job{
		ID:        "prepare",
		LogicalID: "prepare",
		OutputExpressions: OutputMap{
			"matrix": "${{ steps.emit.outputs.matrix }}",
		},
		OutputKeys: []string{"matrix"},
	}
	outputs, err := ResolveJobOutputs(job, ExpressionContext{Steps: StepContextMap{
		"emit": {Outputs: OutputMap{"matrix": `{"package":["a","b"]}`}},
	}})
	if err != nil {
		t.Fatalf("ResolveJobOutputs: %v", err)
	}
	if got, want := outputs["matrix"], `{"package":["a","b"]}`; got != want {
		t.Fatalf("matrix output = %q, want %q", got, want)
	}
}

func TestInterpolateStringAndEvaluateConditionUseRunnerContext(t *testing.T) {
	job := &Job{ID: "runner", LogicalID: "runner"}
	context := ExpressionContext{Runner: RunnerContext{OS: "macOS", Arch: "ARM64", Temp: "/tmp/runner", ToolCache: "/tmp/tools"}}
	text, err := InterpolateString(job, "${{ runner.os }} ${{ runner.arch }} ${{ runner.temp }} ${{ runner.tool_cache }}", context)
	if err != nil {
		t.Fatalf("InterpolateString: %v", err)
	}
	if got, want := text, "macOS ARM64 /tmp/runner /tmp/tools"; got != want {
		t.Fatalf("interpolated text = %q, want %q", got, want)
	}
	ok, err := EvaluateCondition(job, "runner.os == 'macOS' && runner.arch == 'ARM64' && runner.temp == '/tmp/runner' && runner.tool_cache == '/tmp/tools'", context)
	if err != nil {
		t.Fatalf("EvaluateCondition: %v", err)
	}
	if !ok {
		t.Fatal("condition = false, want true")
	}
}
