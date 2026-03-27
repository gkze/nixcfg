package actionadapter

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/gkze/ghawfr/workflow"
)

func TestResolveRemoteActionPathResolvesWithinGuestWorkspace(t *testing.T) {
	got, err := resolveRemoteActionPath("cache-dir/value.txt", "/host/workspace", "/guest/workspace")
	if err != nil {
		t.Fatalf("resolveRemoteActionPath: %v", err)
	}
	if want := "/guest/workspace/cache-dir/value.txt"; got != want {
		t.Fatalf("resolveRemoteActionPath = %q, want %q", got, want)
	}
}

func TestResolveRemoteActionPathRejectsRelativeEscape(t *testing.T) {
	if _, err := resolveRemoteActionPath("../escape", "/host/workspace", "/guest/workspace"); err == nil {
		t.Fatal("resolveRemoteActionPath error = nil, want guest workspace escape failure")
	}
}

func TestTranslateRemotePathToHostTranslatesWithinWorkspace(t *testing.T) {
	hostWorkspace := t.TempDir()
	got, err := translateRemotePathToHost("cache-dir/value.txt", hostWorkspace, "/guest/workspace")
	if err != nil {
		t.Fatalf("translateRemotePathToHost: %v", err)
	}
	if want := filepath.Join(hostWorkspace, "cache-dir", "value.txt"); got != want {
		t.Fatalf("translateRemotePathToHost = %q, want %q", got, want)
	}
}

func TestTranslateRemotePathToHostRejectsRelativeEscape(t *testing.T) {
	hostWorkspace := t.TempDir()
	if _, err := translateRemotePathToHost("../escape", hostWorkspace, "/guest/workspace"); err == nil {
		t.Fatal("translateRemotePathToHost error = nil, want host workspace escape failure")
	}
}

func TestResolveRemotePathWithBaseExpandsGuestHome(t *testing.T) {
	got, err := resolveRemotePathWithBase("/guest/workspace", "~/.venv", "/host/workspace", "/guest/workspace", "/guest/workspace/.ghawfr/runner/home")
	if err != nil {
		t.Fatalf("resolveRemotePathWithBase: %v", err)
	}
	if want := "/guest/workspace/.ghawfr/runner/home/.venv"; got != want {
		t.Fatalf("resolveRemotePathWithBase = %q, want %q", got, want)
	}
}

func TestResolveRemotePathWithBaseRejectsRelativeEscape(t *testing.T) {
	if _, err := resolveRemotePathWithBase("/guest/workspace/subdir", "../..", "/host/workspace", "/guest/workspace", ""); err == nil {
		t.Fatal("resolveRemotePathWithBase error = nil, want guest root escape failure")
	}
}

func TestLocalLookPathUsesProvidedEnvironmentPathBeforeProcessPath(t *testing.T) {
	processBin := filepath.Join(t.TempDir(), "process-bin")
	preferredBin := filepath.Join(t.TempDir(), "preferred-bin")
	for _, directory := range []string{processBin, preferredBin} {
		if err := os.MkdirAll(directory, 0o755); err != nil {
			t.Fatalf("mkdir %q: %v", directory, err)
		}
	}
	processTool := filepath.Join(processBin, "python3")
	preferredTool := filepath.Join(preferredBin, "python3")
	if err := os.WriteFile(processTool, []byte("#!/usr/bin/env sh\nexit 0\n"), 0o755); err != nil {
		t.Fatalf("write process tool: %v", err)
	}
	if err := os.WriteFile(preferredTool, []byte("#!/usr/bin/env sh\nexit 0\n"), 0o755); err != nil {
		t.Fatalf("write preferred tool: %v", err)
	}
	t.Setenv("PATH", processBin)
	got, err := localLookPath("", workflow.EnvironmentMap{"PATH": preferredBin}, "python3")
	if err != nil {
		t.Fatalf("localLookPath: %v", err)
	}
	if got != preferredTool {
		t.Fatalf("localLookPath = %q, want %q", got, preferredTool)
	}
}

func TestLocalLookPathDoesNotFallbackWhenEnvironmentPathExplicitlyEmpty(t *testing.T) {
	processBin := filepath.Join(t.TempDir(), "process-bin")
	if err := os.MkdirAll(processBin, 0o755); err != nil {
		t.Fatalf("mkdir %q: %v", processBin, err)
	}
	processTool := filepath.Join(processBin, "python3")
	if err := os.WriteFile(processTool, []byte("#!/usr/bin/env sh\nexit 0\n"), 0o755); err != nil {
		t.Fatalf("write process tool: %v", err)
	}
	t.Setenv("PATH", processBin)
	_, err := localLookPath("", workflow.EnvironmentMap{"PATH": ""}, "python3")
	if !errors.Is(err, exec.ErrNotFound) {
		t.Fatalf("localLookPath error = %v, want exec.ErrNotFound", err)
	}
}

func TestLocalLookPathResolvesRelativePathEntriesAgainstWorkingDirectory(t *testing.T) {
	workspace := t.TempDir()
	relativeBin := filepath.Join(workspace, "bin")
	if err := os.MkdirAll(relativeBin, 0o755); err != nil {
		t.Fatalf("mkdir %q: %v", relativeBin, err)
	}
	toolPath := filepath.Join(relativeBin, "python3")
	if err := os.WriteFile(toolPath, []byte("#!/usr/bin/env sh\nexit 0\n"), 0o755); err != nil {
		t.Fatalf("write tool: %v", err)
	}
	got, err := localLookPath(workspace, workflow.EnvironmentMap{"PATH": "bin"}, "python3")
	if err != nil {
		t.Fatalf("localLookPath: %v", err)
	}
	if got != toolPath {
		t.Fatalf("localLookPath = %q, want %q", got, toolPath)
	}
}

func TestLocalLookPathAcceptsExplicitExecutablePath(t *testing.T) {
	toolPath := filepath.Join(t.TempDir(), "python3")
	if err := os.WriteFile(toolPath, []byte("#!/usr/bin/env sh\nexit 0\n"), 0o755); err != nil {
		t.Fatalf("write tool: %v", err)
	}
	got, err := localLookPath("", nil, toolPath)
	if err != nil {
		t.Fatalf("localLookPath: %v", err)
	}
	if got != toolPath {
		t.Fatalf("localLookPath = %q, want %q", got, toolPath)
	}
}

func TestNormalizeRemoteToolPathAcceptsAbsolutePaths(t *testing.T) {
	got, err := normalizeRemoteToolPath("/guest/workspace", "/opt/uv/bin/uv")
	if err != nil {
		t.Fatalf("normalizeRemoteToolPath: %v", err)
	}
	if want := "/opt/uv/bin/uv"; got != want {
		t.Fatalf("normalizeRemoteToolPath = %q, want %q", got, want)
	}
}

func TestNormalizeRemoteToolPathResolvesRelativePathsAgainstWorkingDirectory(t *testing.T) {
	got, err := normalizeRemoteToolPath("/guest/workspace", "./bin/uv")
	if err != nil {
		t.Fatalf("normalizeRemoteToolPath: %v", err)
	}
	if want := "/guest/workspace/bin/uv"; got != want {
		t.Fatalf("normalizeRemoteToolPath = %q, want %q", got, want)
	}
}

func TestNormalizeRemoteToolPathRejectsBareCommandNames(t *testing.T) {
	_, err := normalizeRemoteToolPath("/guest/workspace", "uv")
	if err == nil {
		t.Fatal("normalizeRemoteToolPath error = nil, want non-path result failure")
	}
}

func TestNormalizeRemoteToolPathRejectsAliasLikeOutput(t *testing.T) {
	_, err := normalizeRemoteToolPath("/guest/workspace", "uv is /opt/uv/bin/uv")
	if err == nil {
		t.Fatal("normalizeRemoteToolPath error = nil, want invalid path failure")
	}
}

func TestNormalizeRemoteToolPathRejectsMultilineOutput(t *testing.T) {
	_, err := normalizeRemoteToolPath("/guest/workspace", "/opt/uv/bin/uv\n/opt/uv/bin/uv2")
	if err == nil {
		t.Fatal("normalizeRemoteToolPath error = nil, want multiline path failure")
	}
}
