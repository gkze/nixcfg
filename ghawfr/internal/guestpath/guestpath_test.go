package guestpath_test

import (
	"path/filepath"
	"strings"
	"testing"

	"github.com/gkze/ghawfr/internal/guestpath"
	"github.com/gkze/ghawfr/workflow"
)

func TestTranslateEnvironmentRewritesWorkspaceReferences(t *testing.T) {
	env := workflow.EnvironmentMap{
		"HOME": "/host/workspace/.ghawfr/runner/home",
		"PATH": "/host/workspace/bin:/usr/bin",
	}

	got := guestpath.TranslateEnvironment(env, "/host/workspace", "/guest/workspace")

	if got["HOME"] != "/guest/workspace/.ghawfr/runner/home" {
		t.Fatalf("HOME = %q, want guest workspace rewrite", got["HOME"])
	}
	if got["PATH"] != "/guest/workspace/bin:/usr/bin" {
		t.Fatalf("PATH = %q, want guest workspace rewrite", got["PATH"])
	}
	if env["HOME"] != "/host/workspace/.ghawfr/runner/home" {
		t.Fatalf("TranslateEnvironment mutated input env: %#v", env)
	}
}

func TestTranslateHostPathRejectsPathsOutsideWorkspace(t *testing.T) {
	workspace := t.TempDir()
	other := t.TempDir()

	_, err := guestpath.TranslateHostPath(workspace, "/guest/workspace", filepath.Join(other, "cache"))
	if err == nil {
		t.Fatal("TranslateHostPath error = nil, want outside workspace failure")
	}
}

func TestResolveActionPathPreservesAbsolutePathsOutsideWorkspace(t *testing.T) {
	workspace := t.TempDir()
	outside := filepath.Join(t.TempDir(), "tool-cache")

	got, err := guestpath.ResolveActionPath(outside, workspace, "/guest/workspace")
	if err != nil {
		t.Fatalf("ResolveActionPath: %v", err)
	}
	if got != outside {
		t.Fatalf("ResolveActionPath = %q, want %q", got, outside)
	}
}

func TestTranslateToHostTranslatesAbsoluteGuestPaths(t *testing.T) {
	workspace := t.TempDir()

	got, err := guestpath.TranslateToHost("/guest/workspace/cache/value.txt", workspace, "/guest/workspace")
	if err != nil {
		t.Fatalf("TranslateToHost: %v", err)
	}
	want := filepath.Join(workspace, "cache", "value.txt")
	if got != want {
		t.Fatalf("TranslateToHost = %q, want %q", got, want)
	}
}

func TestTranslateToHostRejectsHomeRelativePaths(t *testing.T) {
	workspace := t.TempDir()

	_, err := guestpath.TranslateToHost("~/.cache/nix", workspace, "/guest/workspace")
	if err == nil {
		t.Fatal("TranslateToHost error = nil, want home-relative guest path failure")
	}
	if !strings.Contains(err.Error(), "home-relative guest paths") {
		t.Fatalf("TranslateToHost error = %v, want home-relative guest path message", err)
	}
}

func TestTranslateToHostRejectsAbsolutePathsOutsideGuestWorkspace(t *testing.T) {
	workspace := t.TempDir()

	_, err := guestpath.TranslateToHost("/tmp/cache", workspace, "/guest/workspace")
	if err == nil {
		t.Fatal("TranslateToHost error = nil, want outside guest workspace failure")
	}
	if !strings.Contains(err.Error(), "outside guest workspace") {
		t.Fatalf("TranslateToHost error = %v, want outside guest workspace message", err)
	}
}

func TestResolvePathWithBaseExpandsGuestHome(t *testing.T) {
	got, err := guestpath.ResolvePathWithBase(
		"/guest/workspace",
		"~/.venv",
		"/host/workspace",
		"/guest/workspace",
		"/guest/workspace/.ghawfr/runner/home",
	)
	if err != nil {
		t.Fatalf("ResolvePathWithBase: %v", err)
	}
	if want := "/guest/workspace/.ghawfr/runner/home/.venv"; got != want {
		t.Fatalf("ResolvePathWithBase = %q, want %q", got, want)
	}
}

func TestResolvePathWithBaseRejectsRelativeEscape(t *testing.T) {
	_, err := guestpath.ResolvePathWithBase(
		"/guest/workspace/subdir",
		"../..",
		"/host/workspace",
		"/guest/workspace",
		"",
	)
	if err == nil {
		t.Fatal("ResolvePathWithBase error = nil, want guest root escape failure")
	}
}

func TestResolveStepDirectoryTranslatesRelativeHostPathIntoGuestWorkspace(t *testing.T) {
	workspace := t.TempDir()

	got, err := guestpath.ResolveStepDirectory(workspace, "/guest/workspace", "subdir")
	if err != nil {
		t.Fatalf("ResolveStepDirectory: %v", err)
	}
	if want := "/guest/workspace/subdir"; got != want {
		t.Fatalf("ResolveStepDirectory = %q, want %q", got, want)
	}
}

func TestResolveStepDirectoryRejectsAbsolutePathOutsideGuestWorkspace(t *testing.T) {
	workspace := t.TempDir()

	_, err := guestpath.ResolveStepDirectory(workspace, "/guest/workspace", "/tmp/escape")
	if err == nil {
		t.Fatal("ResolveStepDirectory error = nil, want outside guest workspace failure")
	}
	if !strings.Contains(err.Error(), "outside guest workspace") {
		t.Fatalf("ResolveStepDirectory error = %v, want outside guest workspace message", err)
	}
}

func TestPathWithinWorkspaceReturnsRelativePathForDescendant(t *testing.T) {
	workspace := t.TempDir()
	got, ok := guestpath.PathWithinWorkspace(workspace, filepath.Join(workspace, "cache", "value.txt"))
	if !ok {
		t.Fatal("PathWithinWorkspace ok = false, want true")
	}
	if want := filepath.Join("cache", "value.txt"); got != want {
		t.Fatalf("PathWithinWorkspace = %q, want %q", got, want)
	}
}

func TestShellQuoteEscapesSingleQuotes(t *testing.T) {
	got := guestpath.ShellQuote("can't stop")
	want := `'can'"'"'t stop'`
	if got != want {
		t.Fatalf("ShellQuote = %q, want %q", got, want)
	}
}
