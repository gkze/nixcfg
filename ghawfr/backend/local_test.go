package backend_test

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/gkze/ghawfr/actionadapter"
	"github.com/gkze/ghawfr/artifacts"
	"github.com/gkze/ghawfr/backend"
	ghcache "github.com/gkze/ghawfr/cache"
	"github.com/gkze/ghawfr/workflow"
)

const (
	localUVWithVenvScript = `#!/usr/bin/env sh
if [ "${1:-}" = '--version' ]; then
  echo 'uv 0.6.5'
  exit 0
fi
if [ "${1:-}" = 'venv' ]; then
  mkdir -p "${3}/bin"
  exit 0
fi
exit 0
`
	localUVVersionScript = `#!/usr/bin/env sh
if [ "${1:-}" = '--version' ]; then
  echo 'uv 0.6.5'
  exit 0
fi
exit 0
`
	localEnvPathSetupCommand = `test -z "${FOO:-}"
mkdir -p "$GITHUB_WORKSPACE/.bin"
printf '#!/usr/bin/env bash
echo tool
' > "$GITHUB_WORKSPACE/.bin/mytool"
chmod +x "$GITHUB_WORKSPACE/.bin/mytool"
echo 'FOO=bar' >> "$GITHUB_ENV"
echo "$GITHUB_WORKSPACE/.bin" >> "$GITHUB_PATH"`
)

func TestLocalRunJobUploadsAndDownloadsArtifactsAcrossJobs(t *testing.T) {
	workspace := t.TempDir()
	store := artifacts.NewStore(filepath.Join(workspace, ".ghawfr", "artifacts"))
	if err := os.WriteFile(filepath.Join(workspace, "flake.lock"), []byte("lock"), 0o644); err != nil {
		t.Fatalf("write flake.lock: %v", err)
	}

	upload := &workflow.Job{
		ID:        "upload",
		LogicalID: "upload",
		Steps: []workflow.Step{{
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses: "actions/upload-artifact@v6",
				Inputs: workflow.ActionInputMap{
					"name":              "flake-lock",
					"path":              "flake.lock",
					"if-no-files-found": "error",
				},
			},
		}},
	}
	if _, err := actionadapter.NewLocal(nil).RunJob(
		context.Background(),
		upload,
		backend.RunOptions{WorkingDirectory: workspace, Artifacts: store},
	); err != nil {
		t.Fatalf("upload RunJob: %v", err)
	}
	if err := os.Remove(filepath.Join(workspace, "flake.lock")); err != nil {
		t.Fatalf("remove flake.lock: %v", err)
	}

	download := &workflow.Job{
		ID:        "download",
		LogicalID: "download",
		Steps: []workflow.Step{{
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses:   "actions/download-artifact@v7",
				Inputs: workflow.ActionInputMap{"name": "flake-lock", "path": "."},
			},
		}},
	}
	if _, err := actionadapter.NewLocal(nil).RunJob(
		context.Background(),
		download,
		backend.RunOptions{WorkingDirectory: workspace, Artifacts: store},
	); err != nil {
		t.Fatalf("download RunJob: %v", err)
	}
	if got, want := mustReadFile(t, filepath.Join(workspace, "flake.lock")), "lock"; got != want {
		t.Fatalf("downloaded flake.lock = %q, want %q", got, want)
	}
}

func TestLocalRunJobSupportsMinimalCISetupActions(t *testing.T) {
	workspace := t.TempDir()
	if err := os.Mkdir(filepath.Join(workspace, ".git"), 0o755); err != nil {
		t.Fatalf("mkdir .git: %v", err)
	}
	binDir := filepath.Join(workspace, "bin")
	if err := os.MkdirAll(binDir, 0o755); err != nil {
		t.Fatalf("MkdirAll bin: %v", err)
	}
	if err := os.WriteFile(
		filepath.Join(binDir, "cachix"),
		[]byte("#!/usr/bin/env sh\nexit 0\n"),
		0o755,
	); err != nil {
		t.Fatalf("WriteFile cachix: %v", err)
	}
	if err := os.WriteFile(
		filepath.Join(binDir, "python3"),
		[]byte("#!/usr/bin/env sh\necho 'Python 3.14.2'\n"),
		0o755,
	); err != nil {
		t.Fatalf("WriteFile python3: %v", err)
	}
	if err := os.WriteFile(
		filepath.Join(binDir, "uv"),
		[]byte(localUVWithVenvScript),
		0o755,
	); err != nil {
		t.Fatalf("WriteFile uv: %v", err)
	}
	if err := os.WriteFile(
		filepath.Join(binDir, "uvx"),
		[]byte("#!/usr/bin/env sh\nexit 0\n"),
		0o755,
	); err != nil {
		t.Fatalf("WriteFile uvx: %v", err)
	}
	t.Setenv("PATH", binDir+string(os.PathListSeparator)+os.Getenv("PATH"))
	pythonToolRoot := filepath.Join(
		workspace,
		".ghawfr",
		"runner",
		"tool-cache",
		"Python",
		"3.14",
	)
	uvToolRoot := filepath.Join(workspace, ".ghawfr", "runner", "tool-cache", "uv", "system")
	uvCacheDir := filepath.Join(workspace, ".ghawfr", "runner", "tool-cache", "uv-cache")
	venvPath := filepath.Join(workspace, ".venv")
	venvBin := filepath.Join(venvPath, "bin")
	job := &workflow.Job{
		ID:        "quality",
		LogicalID: "quality",
		Env:       workflow.EnvironmentMap{"CACHIX_NAME": "gkze"},
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{
			{
				Kind:   workflow.StepKindAction,
				Action: &workflow.ActionStep{Uses: "actions/checkout@v6"},
			},
			{
				Kind:   workflow.StepKindAction,
				Action: &workflow.ActionStep{Uses: "DeterminateSystems/determinate-nix-action@v3"},
			},
			{
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses: "cachix/cachix-action@v16",
					Inputs: workflow.ActionInputMap{
						"name":      "${{ env.CACHIX_NAME }}",
						"authToken": "${{ secrets.CACHIX_AUTH_TOKEN }}",
					},
				},
			},
			{
				ID:   "py",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses:   "actions/setup-python@v6",
					Inputs: workflow.ActionInputMap{"python-version": "3.14"},
				},
			},
			{
				ID:   "uv",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses:   "astral-sh/setup-uv@v6",
					Inputs: workflow.ActionInputMap{"activate-environment": "true"},
				},
			},
			{
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses: "actions/cache@v5",
					Inputs: workflow.ActionInputMap{
						"path": ".cache/nix",
						"key":  "nix-${{ env.CACHIX_NAME }}",
					},
				},
			},
			{
				Kind: workflow.StepKindRun,
				Env:  workflow.EnvironmentMap{"QUALITY_COMMAND": "echo ok"},
				Run: &workflow.RunStep{Command: "test \"$QUALITY_COMMAND\" = \"echo ok\"\n" +
					"test \"$CACHIX_AUTH_TOKEN\" = \"token\"\n" +
					"test \"$CACHIX_NAME\" = \"gkze\"\n" +
					"test \"${{ steps.py.outputs.python-version }}\" = \"3.14\"\n" +
					"test \"${{ steps.py.outputs.cache-hit }}\" = \"false\"\n" +
					"case \"${{ steps.py.outputs.python-path }}\" in \"" +
					pythonToolRoot +
					"\"/*/bin/python3) true ;; *) false ;; esac\n" +
					"test \"${{ steps.uv.outputs.cache-hit }}\" = \"false\"\n" +
					"test \"${{ steps.uv.outputs.python-cache-hit }}\" = \"false\"\n" +
					"test -n \"${{ steps.uv.outputs.uv-version }}\"\n" +
					"case \"${{ steps.uv.outputs.uv-path }}\" in \"" +
					uvToolRoot +
					"\"/*/bin/uv) true ;; *) false ;; esac\n" +
					"test \"${{ steps.uv.outputs.venv }}\" = \"" + venvPath + "\"\n" +
					"test \"$UV_CACHE_DIR\" = \"" + uvCacheDir + "\"\n" +
					"test \"$VIRTUAL_ENV\" = \"" + venvPath + "\"\n" +
					"case \"$pythonLocation\" in \"" + pythonToolRoot + "\"/*) true ;; *) false ;; esac\n" +
					"case \"$PATH\" in \"" + venvBin + "\":*) true ;; *) false ;; esac\n" +
					"case \":$PATH:\" in *:\"" + uvToolRoot + "\"/*/bin:*) true ;; *) false ;; esac"},
			},
		},
	}

	result, err := actionadapter.NewLocal(nil).RunJob(context.Background(), job, backend.RunOptions{
		WorkingDirectory: workspace,
		Expressions: workflow.ExpressionContext{
			Secrets: workflow.SecretMap{"CACHIX_AUTH_TOKEN": "token"},
		},
	})
	if err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	if got, want := result.Result, backend.JobStatusSuccess; got != want {
		t.Fatalf("result.Result = %q, want %q", got, want)
	}
	if _, err := os.Stat(filepath.Join(workspace, ".cache", "nix")); err != nil {
		t.Fatalf("cache path missing: %v", err)
	}
	if _, err := os.Stat(filepath.Join(workspace, ".venv", "bin")); err != nil {
		t.Fatalf("venv path missing: %v", err)
	}
}

func TestLocalRunJobSetupPythonRespectsUpdateEnvironmentFalse(t *testing.T) {
	workspace := t.TempDir()
	binDir := filepath.Join(workspace, "bin")
	if err := os.MkdirAll(binDir, 0o755); err != nil {
		t.Fatalf("mkdir bin: %v", err)
	}
	pythonPath := filepath.Join(binDir, "python3")
	if err := os.WriteFile(
		pythonPath,
		[]byte("#!/usr/bin/env sh\necho 'Python 3.14.2'\n"),
		0o755,
	); err != nil {
		t.Fatalf("write python3: %v", err)
	}
	t.Setenv("PATH", binDir+string(os.PathListSeparator)+os.Getenv("PATH"))

	runSetupPythonUpdateEnvironmentFalseConformance(
		t,
		actionadapter.NewLocal(nil).RunJob,
		backend.RunOptions{WorkingDirectory: workspace},
	)
}

func TestLocalRunJobSetupPythonSupportsMultilineVersionFallback(t *testing.T) {
	workspace := t.TempDir()
	binDir := filepath.Join(workspace, "bin")
	if err := os.MkdirAll(binDir, 0o755); err != nil {
		t.Fatalf("mkdir bin: %v", err)
	}
	pythonPath := filepath.Join(binDir, "python3")
	if err := os.WriteFile(
		pythonPath,
		[]byte("#!/usr/bin/env sh\necho 'Python 3.14.2'\n"),
		0o755,
	); err != nil {
		t.Fatalf("write python3: %v", err)
	}
	t.Setenv("PATH", binDir+string(os.PathListSeparator)+os.Getenv("PATH"))

	runSetupPythonMultilineFallbackConformance(
		t,
		actionadapter.NewLocal(nil).RunJob,
		backend.RunOptions{WorkingDirectory: workspace},
	)
}

func TestLocalRunJobSetupPythonFailsWhenWorkflowPathExplicitlyEmpty(t *testing.T) {
	job := &workflow.Job{
		ID:        "setup-python-empty-path",
		LogicalID: "setup-python-empty-path",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Env:       workflow.EnvironmentMap{"PATH": ""},
		Steps: []workflow.Step{{
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{
				Uses:   "actions/setup-python@v6",
				Inputs: workflow.ActionInputMap{"python-version": "3.14"},
			},
		}},
	}
	if _, err := actionadapter.NewLocal(nil).RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: t.TempDir()},
	); err == nil {
		t.Fatal(
			"RunJob error = nil, want setup-python failure when workflow PATH is explicitly empty",
		)
	}
}

func TestLocalRunJobSetupPythonFailsOnVersionMismatch(t *testing.T) {
	workspace := t.TempDir()
	binDir := filepath.Join(workspace, "bin")
	if err := os.MkdirAll(binDir, 0o755); err != nil {
		t.Fatalf("mkdir bin: %v", err)
	}
	pythonPath := filepath.Join(binDir, "python3")
	if err := os.WriteFile(
		pythonPath,
		[]byte("#!/usr/bin/env sh\necho 'Python 3.13.1'\n"),
		0o755,
	); err != nil {
		t.Fatalf("write python3: %v", err)
	}
	t.Setenv("PATH", binDir+string(os.PathListSeparator)+os.Getenv("PATH"))

	runSetupPythonVersionMismatchConformance(
		t,
		actionadapter.NewLocal(nil).RunJob,
		backend.RunOptions{WorkingDirectory: workspace},
	)
}

func TestLocalRunJobSetupUVRejectsUnsupportedVersionInputs(t *testing.T) {
	runSetupUVUnsupportedVersionInputConformance(
		t,
		actionadapter.NewLocal(nil).RunJob,
		backend.RunOptions{WorkingDirectory: t.TempDir()},
	)
}

func TestLocalRunJobSupportsCreatePullRequestAction(t *testing.T) {
	workspace := t.TempDir()

	runCreatePullRequestSuccessConformance(
		t,
		actionadapter.NewLocal(nil).RunJob,
		backend.RunOptions{WorkingDirectory: workspace},
	)
}

func TestLocalRunJobSetupActionsUseWorkflowShapedPath(t *testing.T) {
	workspace := t.TempDir()
	pyRoot := filepath.Join(workspace, "fake-python")
	uvRoot := filepath.Join(workspace, "fake-uv")
	for _, path := range []string{filepath.Join(pyRoot, "bin"), filepath.Join(uvRoot, "bin")} {
		if err := os.MkdirAll(path, 0o755); err != nil {
			t.Fatalf("mkdir %q: %v", path, err)
		}
	}
	if err := os.WriteFile(
		filepath.Join(pyRoot, "bin", "python3"),
		[]byte("#!/usr/bin/env sh\necho 'Python 3.14.7'\n"),
		0o755,
	); err != nil {
		t.Fatalf("write python3: %v", err)
	}
	if err := os.WriteFile(
		filepath.Join(uvRoot, "bin", "uv"),
		[]byte(localUVVersionScript),
		0o755,
	); err != nil {
		t.Fatalf("write uv: %v", err)
	}
	if err := os.WriteFile(
		filepath.Join(uvRoot, "bin", "uvx"),
		[]byte("#!/usr/bin/env sh\nexit 0\n"),
		0o755,
	); err != nil {
		t.Fatalf("write uvx: %v", err)
	}
	job := &workflow.Job{
		ID:        "setup-path-shaped",
		LogicalID: "setup-path-shaped",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{
			{
				ID:   "path",
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: "echo '" + filepath.Join(pyRoot, "bin") + "' >> \"$GITHUB_PATH\"\n" +
						"echo '" + filepath.Join(uvRoot, "bin") + "' >> \"$GITHUB_PATH\"",
				},
			},
			{
				ID:   "py",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses:   "actions/setup-python@v6",
					Inputs: workflow.ActionInputMap{"python-version": "3.14"},
				},
			},
			{
				ID:     "uv",
				Kind:   workflow.StepKindAction,
				Action: &workflow.ActionStep{Uses: "astral-sh/setup-uv@v6"},
			},
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: "test \"${{ steps.py.outputs.python-version }}\" = 3.14\n" +
						"test \"${{ steps.uv.outputs.uv-version }}\" = 0.6.5",
				},
			},
		},
	}
	result, err := actionadapter.NewLocal(nil).
		RunJob(context.Background(), job, backend.RunOptions{WorkingDirectory: workspace})
	if err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	if got, want := result.Result, backend.JobStatusSuccess; got != want {
		t.Fatalf("result.Result = %q, want %q", got, want)
	}
}

func TestLocalRunJobProvidesRunnerTempAndToolCache(t *testing.T) {
	workspace := t.TempDir()
	job := &workflow.Job{
		ID:        "runner-fs",
		LogicalID: "runner-fs",
		RunsOn:    workflow.Runner{Labels: []string{"ubuntu-24.04"}},
		Steps: []workflow.Step{{
			Kind: workflow.StepKindRun,
			Run: &workflow.RunStep{Command: "test -d \"${{ runner.temp }}\"\n" +
				"test -d \"${{ runner.tool_cache }}\"\n" +
				"test \"$RUNNER_TEMP\" = \"${{ runner.temp }}\"\n" +
				"test \"$RUNNER_TOOL_CACHE\" = \"${{ runner.tool_cache }}\"\n" +
				"test \"$AGENT_TOOLSDIRECTORY\" = \"${{ runner.tool_cache }}\""},
		}},
	}
	result, err := actionadapter.NewLocal(nil).
		RunJob(context.Background(), job, backend.RunOptions{WorkingDirectory: workspace})
	if err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	if got, want := result.Result, backend.JobStatusSuccess; got != want {
		t.Fatalf("result.Result = %q, want %q", got, want)
	}
	for _, path := range []string{
		filepath.Join(workspace, ".ghawfr", "runner", "temp"),
		filepath.Join(workspace, ".ghawfr", "runner", "tool-cache"),
	} {
		if _, err := os.Stat(path); err != nil {
			t.Fatalf("stat %q: %v", path, err)
		}
	}
}

func TestLocalRunJobAppliesGithubEnvAndPathToLaterSteps(t *testing.T) {
	workspace := t.TempDir()
	job := &workflow.Job{
		ID:        "env-path",
		LogicalID: "env-path",
		Steps: []workflow.Step{
			{
				ID:   "setup",
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: localEnvPathSetupCommand,
				},
			},
			{
				ID:   "verify",
				Kind: workflow.StepKindRun,
				Run:  &workflow.RunStep{Command: "test \"$FOO\" = bar\ntest \"$(mytool)\" = tool"},
			},
		},
	}

	result, err := actionadapter.NewLocal(nil).
		RunJob(context.Background(), job, backend.RunOptions{WorkingDirectory: workspace})
	if err != nil {
		t.Fatalf("RunJob: %v", err)
	}
	if got, want := result.Result, backend.JobStatusSuccess; got != want {
		t.Fatalf("result.Result = %q, want %q", got, want)
	}
	if got, want := len(result.Steps), 2; got != want {
		t.Fatalf("len(result.Steps) = %d, want %d", got, want)
	}
}

func TestLocalRunJobCapturesStepAndJobOutputs(t *testing.T) {
	workspace := t.TempDir()
	home := filepath.Join(t.TempDir(), "home")
	if err := os.MkdirAll(home, 0o755); err != nil {
		t.Fatalf("mkdir home: %v", err)
	}
	t.Setenv("HOME", home)
	store := ghcache.NewStore(t.TempDir())
	cacheRoot := filepath.Join(home, ".cache", "demo")

	seed := &workflow.Job{
		ID:        "seed-cache",
		LogicalID: "seed-cache",
		Steps: []workflow.Step{
			{
				ID:   "cache",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses:   "actions/cache@v5",
					Inputs: workflow.ActionInputMap{"path": "~/.cache/demo", "key": "demo-v1"},
				},
			},
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: "mkdir -p \"$HOME/.cache/demo\"\nprintf one > \"$HOME/.cache/demo/value.txt\"",
				},
			},
		},
	}
	if _, err := actionadapter.NewLocal(nil).RunJob(
		context.Background(),
		seed,
		backend.RunOptions{WorkingDirectory: workspace, Cache: store},
	); err != nil {
		t.Fatalf("seed RunJob: %v", err)
	}
	if err := os.RemoveAll(cacheRoot); err != nil {
		t.Fatalf("remove cache root: %v", err)
	}

	fallback := &workflow.Job{
		ID:        "fallback-cache",
		LogicalID: "fallback-cache",
		Steps: []workflow.Step{
			{
				ID:   "cache",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses: "actions/cache@v5",
					Inputs: workflow.ActionInputMap{
						"path":         "~/.cache/demo",
						"key":          "demo-v2",
						"restore-keys": "demo-",
					},
				},
			},
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: "test \"${{ steps.cache.outputs.cache-hit }}\" = false\n" +
						"test \"$(cat \"$HOME/.cache/demo/value.txt\")\" = one\n" +
						"printf two > \"$HOME/.cache/demo/value.txt\"",
				},
			},
		},
	}
	if _, err := actionadapter.NewLocal(nil).RunJob(
		context.Background(),
		fallback,
		backend.RunOptions{WorkingDirectory: workspace, Cache: store},
	); err != nil {
		t.Fatalf("fallback RunJob: %v", err)
	}
	if err := os.RemoveAll(cacheRoot); err != nil {
		t.Fatalf("remove cache root again: %v", err)
	}

	restore := &workflow.Job{
		ID:        "restore-cache",
		LogicalID: "restore-cache",
		Steps: []workflow.Step{
			{
				ID:   "cache",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses: "actions/cache@v5",
					Inputs: workflow.ActionInputMap{
						"path":         "~/.cache/demo",
						"key":          "demo-v2",
						"restore-keys": "demo-",
					},
				},
			},
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: "test \"${{ steps.cache.outputs.cache-hit }}\" = true\n" +
						"test \"$(cat \"$HOME/.cache/demo/value.txt\")\" = two",
				},
			},
		},
	}
	if _, err := actionadapter.NewLocal(nil).RunJob(
		context.Background(),
		restore,
		backend.RunOptions{WorkingDirectory: workspace, Cache: store},
	); err != nil {
		t.Fatalf("restore RunJob: %v", err)
	}
}

func TestLocalRunJobSupportsCacheLookupOnly(t *testing.T) {
	workspace := t.TempDir()
	home := filepath.Join(t.TempDir(), "home")
	if err := os.MkdirAll(home, 0o755); err != nil {
		t.Fatalf("mkdir home: %v", err)
	}
	t.Setenv("HOME", home)
	store := ghcache.NewStore(t.TempDir())
	cacheRoot := filepath.Join(home, ".cache", "demo")
	seed := &workflow.Job{
		ID:        "seed-lookup-cache",
		LogicalID: "seed-lookup-cache",
		Steps: []workflow.Step{
			{
				ID:   "cache",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses:   "actions/cache@v5",
					Inputs: workflow.ActionInputMap{"path": "~/.cache/demo", "key": "lookup-v1"},
				},
			},
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: "mkdir -p \"$HOME/.cache/demo\"\nprintf one > \"$HOME/.cache/demo/value.txt\"",
				},
			},
		},
	}
	if _, err := actionadapter.NewLocal(nil).RunJob(
		context.Background(),
		seed,
		backend.RunOptions{WorkingDirectory: workspace, Cache: store},
	); err != nil {
		t.Fatalf("seed RunJob: %v", err)
	}
	if err := os.RemoveAll(cacheRoot); err != nil {
		t.Fatalf("remove cache root: %v", err)
	}
	lookup := &workflow.Job{
		ID:        "lookup-cache",
		LogicalID: "lookup-cache",
		Steps: []workflow.Step{
			{
				ID:   "cache",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses: "actions/cache@v5",
					Inputs: workflow.ActionInputMap{
						"path":        "~/.cache/demo",
						"key":         "lookup-v1",
						"lookup-only": "true",
					},
				},
			},
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: "test \"${{ steps.cache.outputs.cache-hit }}\" = true\n" +
						"test ! -e \"$HOME/.cache/demo/value.txt\"",
				},
			},
		},
	}
	if _, err := actionadapter.NewLocal(nil).RunJob(
		context.Background(),
		lookup,
		backend.RunOptions{WorkingDirectory: workspace, Cache: store},
	); err != nil {
		t.Fatalf("lookup RunJob: %v", err)
	}
}

func TestLocalRunJobFailsOnCacheMissWhenConfigured(t *testing.T) {
	workspace := t.TempDir()
	home := filepath.Join(t.TempDir(), "home")
	if err := os.MkdirAll(home, 0o755); err != nil {
		t.Fatalf("mkdir home: %v", err)
	}
	t.Setenv("HOME", home)
	store := ghcache.NewStore(t.TempDir())
	job := &workflow.Job{
		ID:        "cache-miss",
		LogicalID: "cache-miss",
		Steps: []workflow.Step{{
			ID:   "cache",
			Kind: workflow.StepKindAction,
			Action: &workflow.ActionStep{Uses: "actions/cache@v5", Inputs: workflow.ActionInputMap{
				"path":               "~/.cache/demo",
				"key":                "missing-key",
				"fail-on-cache-miss": "true",
			}},
		}},
	}
	if _, err := actionadapter.NewLocal(nil).RunJob(
		context.Background(),
		job,
		backend.RunOptions{WorkingDirectory: workspace, Cache: store},
	); err == nil {
		t.Fatal("RunJob error = nil, want cache miss failure")
	}
}

func TestLocalRunJobSupportsCacheRestoreAndSaveActions(t *testing.T) {
	workspace := t.TempDir()
	home := filepath.Join(t.TempDir(), "home")
	if err := os.MkdirAll(home, 0o755); err != nil {
		t.Fatalf("mkdir home: %v", err)
	}
	t.Setenv("HOME", home)
	store := ghcache.NewStore(t.TempDir())
	cacheRoot := filepath.Join(home, ".cache", "demo")

	save := &workflow.Job{
		ID:        "save-cache",
		LogicalID: "save-cache",
		Steps: []workflow.Step{
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: "mkdir -p \"$HOME/.cache/demo\"\nprintf one > \"$HOME/.cache/demo/value.txt\"",
				},
			},
			{
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses:   "actions/cache/save@v4",
					Inputs: workflow.ActionInputMap{"path": "~/.cache/demo", "key": "split-v1"},
				},
			},
		},
	}
	if _, err := actionadapter.NewLocal(nil).RunJob(
		context.Background(),
		save,
		backend.RunOptions{WorkingDirectory: workspace, Cache: store},
	); err != nil {
		t.Fatalf("save RunJob: %v", err)
	}
	if err := os.RemoveAll(cacheRoot); err != nil {
		t.Fatalf("remove cache root: %v", err)
	}

	fallback := &workflow.Job{
		ID:        "restore-save-cache",
		LogicalID: "restore-save-cache",
		Steps: []workflow.Step{
			{
				ID:   "restore",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses: "actions/cache/restore@v4",
					Inputs: workflow.ActionInputMap{
						"path":         "~/.cache/demo",
						"key":          "split-v2",
						"restore-keys": "split-",
					},
				},
			},
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: "test \"${{ steps.restore.outputs.cache-primary-key }}\" = split-v2\n" +
						"test \"${{ steps.restore.outputs.cache-matched-key }}\" = split-v1\n" +
						"test \"${{ steps.restore.outputs.cache-hit }}\" = false\n" +
						"test \"$(cat \"$HOME/.cache/demo/value.txt\")\" = one\n" +
						"printf two > \"$HOME/.cache/demo/value.txt\"",
				},
			},
			{
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses: "actions/cache/save@v4",
					Inputs: workflow.ActionInputMap{
						"path": "~/.cache/demo",
						"key":  "${{ steps.restore.outputs.cache-primary-key }}",
					},
				},
			},
		},
	}
	if _, err := actionadapter.NewLocal(nil).RunJob(
		context.Background(),
		fallback,
		backend.RunOptions{WorkingDirectory: workspace, Cache: store},
	); err != nil {
		t.Fatalf("fallback RunJob: %v", err)
	}
	if err := os.RemoveAll(cacheRoot); err != nil {
		t.Fatalf("remove cache root again: %v", err)
	}

	restore := &workflow.Job{
		ID:        "restore-cache",
		LogicalID: "restore-cache",
		Steps: []workflow.Step{
			{
				ID:   "restore",
				Kind: workflow.StepKindAction,
				Action: &workflow.ActionStep{
					Uses: "actions/cache/restore@v4",
					Inputs: workflow.ActionInputMap{
						"path":         "~/.cache/demo",
						"key":          "split-v2",
						"restore-keys": "split-",
					},
				},
			},
			{
				Kind: workflow.StepKindRun,
				Run: &workflow.RunStep{
					Command: "test \"${{ steps.restore.outputs.cache-primary-key }}\" = split-v2\n" +
						"test \"${{ steps.restore.outputs.cache-matched-key }}\" = split-v2\n" +
						"test \"${{ steps.restore.outputs.cache-hit }}\" = true\n" +
						"test \"$(cat \"$HOME/.cache/demo/value.txt\")\" = two",
				},
			},
		},
	}
	if _, err := actionadapter.NewLocal(nil).RunJob(
		context.Background(),
		restore,
		backend.RunOptions{WorkingDirectory: workspace, Cache: store},
	); err != nil {
		t.Fatalf("restore RunJob: %v", err)
	}
}

func mustReadFile(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %q: %v", path, err)
	}
	return string(data)
}
