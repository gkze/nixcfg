package backend_test

import (
	"context"
	"testing"

	"github.com/gkze/ghawfr/backend"
	"github.com/gkze/ghawfr/workflow"
)

type backendRunJobFunc func(context.Context, *workflow.Job, backend.RunOptions) (*backend.JobResult, error)

// Shared mirrored local/remote conformance scenarios only.
// Keep backend-specific transport, path, and home coverage in the backend-specific test files.
func runSetupPythonUpdateEnvironmentFalseConformance(
	t *testing.T,
	runJob backendRunJobFunc,
	options backend.RunOptions,
) {
	t.Helper()
	result, err := runJob(context.Background(), newSetupPythonUpdateEnvironmentFalseJob(), options)
	if err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	assertSetupPythonUpdateEnvironmentFalseResult(t, result)
}

func runSetupPythonMultilineFallbackConformance(
	t *testing.T,
	runJob backendRunJobFunc,
	options backend.RunOptions,
) {
	t.Helper()
	result, err := runJob(context.Background(), newSetupPythonMultilineFallbackJob(), options)
	if err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	assertSetupPythonMultilineFallbackResult(t, result)
}

func runSetupPythonVersionMismatchConformance(
	t *testing.T,
	runJob backendRunJobFunc,
	options backend.RunOptions,
) {
	t.Helper()
	if _, err := runJob(context.Background(), newSetupPythonVersionMismatchJob(), options); err == nil {
		t.Fatal("RunJob error = nil, want python version mismatch failure")
	}
}

func runSetupUVUnsupportedVersionInputConformance(
	t *testing.T,
	runJob backendRunJobFunc,
	options backend.RunOptions,
) {
	t.Helper()
	if _, err := runJob(context.Background(), newSetupUVUnsupportedVersionInputJob(), options); err == nil {
		t.Fatal("RunJob error = nil, want unsupported setup-uv input failure")
	}
}

func runCreatePullRequestSuccessConformance(
	t *testing.T,
	runJob backendRunJobFunc,
	options backend.RunOptions,
) {
	t.Helper()
	result, err := runJob(context.Background(), newCreatePullRequestSuccessJob(), options)
	if err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	assertCreatePullRequestSuccessResult(t, result)
}

func newSetupPythonUpdateEnvironmentFalseJob() *workflow.Job {
	return &workflow.Job{
		ID:        "setup-python-no-env",
		LogicalID: "setup-python-no-env",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			ID:   "py",
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses: "actions/setup-python@v6",
				Inputs: workflow.ActionInputMap{
					"python-version":     "3.14",
					"update-environment": "false",
				},
			},
		}},
	}
}

func newSetupPythonMultilineFallbackJob() *workflow.Job {
	return &workflow.Job{
		ID:        "setup-python-fallback",
		LogicalID: "setup-python-fallback",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			ID:   "py",
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses:   "actions/setup-python@v6",
				Inputs: workflow.ActionInputMap{"python-version": "3.15\n3.14"},
			},
		}},
	}
}

func newSetupPythonVersionMismatchJob() *workflow.Job {
	return &workflow.Job{
		ID:        "setup-python-mismatch",
		LogicalID: "setup-python-mismatch",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses:   "actions/setup-python@v6",
				Inputs: workflow.ActionInputMap{"python-version": "3.14"},
			},
		}},
	}
}

func newSetupUVUnsupportedVersionInputJob() *workflow.Job {
	return &workflow.Job{
		ID:        "setup-uv-unsupported",
		LogicalID: "setup-uv-unsupported",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses:   "astral-sh/setup-uv@v6",
				Inputs: workflow.ActionInputMap{"version": "0.6.0"},
			},
		}},
	}
}

func newCreatePullRequestSuccessJob() *workflow.Job {
	return &workflow.Job{
		ID:        "create-pr",
		LogicalID: "create-pr",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses: "peter-evans/create-pull-request@v8",
				Inputs: workflow.ActionInputMap{
					"sign-commits":   "true",
					"branch":         "update_flake_lock_action",
					"delete-branch":  "true",
					"title":          "chore: update",
					"commit-message": "chore: update",
					"body-path":      "/tmp/pr-body.md",
				},
			},
		}},
	}
}

func assertSetupPythonUpdateEnvironmentFalseResult(t *testing.T, result *backend.JobResult) {
	t.Helper()
	step := requireSingleStepResult(t, result)
	if len(step.PathEntries) != 0 {
		t.Fatalf("PathEntries = %#v, want none", step.PathEntries)
	}
	if len(step.Environment) != 0 {
		t.Fatalf("Environment = %#v, want none", step.Environment)
	}
	if got, want := step.Outputs["python-version"], "3.14"; got != want {
		t.Fatalf("python-version output = %q, want %q", got, want)
	}
}

func assertSetupPythonMultilineFallbackResult(t *testing.T, result *backend.JobResult) {
	t.Helper()
	step := requireSingleStepResult(t, result)
	if got, want := step.Outputs["python-version"], "3.14"; got != want {
		t.Fatalf("python-version output = %q, want %q", got, want)
	}
}

func assertCreatePullRequestSuccessResult(t *testing.T, result *backend.JobResult) {
	t.Helper()
	if got, want := result.Result, backend.JobStatusSuccess; got != want {
		t.Fatalf("result.Result = %q, want %q", got, want)
	}
}

func requireSingleStepResult(t *testing.T, result *backend.JobResult) backend.StepResult {
	t.Helper()
	if got, want := len(result.Steps), 1; got != want {
		t.Fatalf("len(result.Steps) = %d, want %d", got, want)
	}
	return result.Steps[0]
}
