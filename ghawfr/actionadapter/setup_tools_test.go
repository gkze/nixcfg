package actionadapter

import (
	"os"
	"path"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/gkze/ghawfr/backend"
	"github.com/gkze/ghawfr/workflow"
)

func TestDetectedVersionOutput(t *testing.T) {
	if got, want := detectedVersionOutput("3.14", "3.14.7"), "3.14"; got != want {
		t.Fatalf("detectedVersionOutput(requested) = %q, want %q", got, want)
	}
	if got, want := detectedVersionOutput("  ", "3.14.7"), "3.14.7"; got != want {
		t.Fatalf("detectedVersionOutput(detected) = %q, want %q", got, want)
	}
}

func TestResolveRequestedVersions(t *testing.T) {
	if got := resolveRequestedVersions("  \n  "); got != nil {
		t.Fatalf("resolveRequestedVersions(empty) = %#v, want nil", got)
	}
	if got, want := resolveRequestedVersions("3.13"), []string{"3.13"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("resolveRequestedVersions(single) = %#v, want %#v", got, want)
	}
	if got, want := resolveRequestedVersions("3.14\n\n 3.15 \n"), []string{"3.14", "3.15"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("resolveRequestedVersions(multiline) = %#v, want %#v", got, want)
	}
}

func TestMatchingRequestedVersion(t *testing.T) {
	if got, err := matchingRequestedVersion(nil, "3.14.7"); err != nil || got != "" {
		t.Fatalf("matchingRequestedVersion(nil) = (%q, %v), want (\"\", nil)", got, err)
	}
	if got, err := matchingRequestedVersion([]string{"3.14"}, "3.14.7"); err != nil || got != "3.14" {
		t.Fatalf("matchingRequestedVersion(prefix) = (%q, %v), want (\"3.14\", nil)", got, err)
	}
	if _, err := matchingRequestedVersion([]string{"3.1"}, "3.10.2"); err == nil || !strings.Contains(err.Error(), `found "3.10.2"`) {
		t.Fatalf("matchingRequestedVersion(false prefix) error = %v, want found mismatch", err)
	}
	if _, err := matchingRequestedVersion([]string{"3.14"}, ""); err == nil || !strings.Contains(err.Error(), "could not detect an installed version") {
		t.Fatalf("matchingRequestedVersion(empty detected) error = %v, want detection failure", err)
	}
}

func TestRejectUnsupportedInputs(t *testing.T) {
	action := backend.ActionContext{Inputs: workflow.ActionInputMap{
		"python-version": "3.14",
		"unknown":        "value",
	}}
	if err := rejectUnsupportedInputs(action, "setup-python", "python-version"); err == nil || !strings.Contains(err.Error(), `setup-python input "unknown" is not supported yet`) {
		t.Fatalf("rejectUnsupportedInputs error = %v, want unsupported input failure", err)
	}
	action.Inputs["unknown"] = "  "
	if err := rejectUnsupportedInputs(action, "setup-python", "python-version"); err != nil {
		t.Fatalf("rejectUnsupportedInputs(empty unsupported): %v", err)
	}
}

func TestExtractVersionString(t *testing.T) {
	if got, want := extractVersionString([]byte("Python 3.14.7\n")), "3.14.7"; got != want {
		t.Fatalf("extractVersionString(python) = %q, want %q", got, want)
	}
	if got, want := extractVersionString([]byte("uv 0.6.5 (abc123 2026-01-01)")), "0.6.5"; got != want {
		t.Fatalf("extractVersionString(uv) = %q, want %q", got, want)
	}
	if got := extractVersionString([]byte("Python 3")); got != "" {
		t.Fatalf("extractVersionString(no dotted version) = %q, want empty", got)
	}
}

func TestToolExecutableOutputPaths(t *testing.T) {
	alias := toolCacheAlias{HostBin: "/host/tools/bin", GuestBin: "/guest/tools/bin"}
	if got, want := localToolExecutableOutputPath(alias, "/opt/python/bin/python3"), filepath.Join(alias.HostBin, "python3"); got != want {
		t.Fatalf("localToolExecutableOutputPath = %q, want %q", got, want)
	}
	if got, want := localToolExecutableOutputPath(alias, " "), alias.HostBin; got != want {
		t.Fatalf("localToolExecutableOutputPath(empty) = %q, want %q", got, want)
	}
	if got, want := remoteToolExecutableOutputPath(alias, "/opt/uv/bin/uv"), path.Join(alias.GuestBin, "uv"); got != want {
		t.Fatalf("remoteToolExecutableOutputPath = %q, want %q", got, want)
	}
	if got, want := remoteToolExecutableOutputPath(alias, "/"), alias.GuestBin; got != want {
		t.Fatalf("remoteToolExecutableOutputPath(root) = %q, want %q", got, want)
	}
}

func TestOptionalSiblingExecutablePath(t *testing.T) {
	workspace := t.TempDir()
	toolDir := filepath.Join(workspace, "tools")
	if err := os.MkdirAll(toolDir, 0o755); err != nil {
		t.Fatalf("mkdir toolDir: %v", err)
	}
	baseExecutable := filepath.Join(toolDir, "uv")
	if err := os.WriteFile(baseExecutable, []byte("#!/usr/bin/env sh\nexit 0\n"), 0o755); err != nil {
		t.Fatalf("write uv: %v", err)
	}
	sibling := filepath.Join(toolDir, "uvx")
	if err := os.WriteFile(sibling, []byte("#!/usr/bin/env sh\nexit 0\n"), 0o755); err != nil {
		t.Fatalf("write uvx: %v", err)
	}
	if got := optionalSiblingExecutablePath(workspace, nil, baseExecutable, "uvx"); got != sibling {
		t.Fatalf("optionalSiblingExecutablePath(sibling) = %q, want %q", got, sibling)
	}

	if err := os.Remove(sibling); err != nil {
		t.Fatalf("remove sibling: %v", err)
	}
	fallbackBin := filepath.Join(workspace, "bin")
	if err := os.MkdirAll(fallbackBin, 0o755); err != nil {
		t.Fatalf("mkdir fallbackBin: %v", err)
	}
	fallback := filepath.Join(fallbackBin, "uvx")
	if err := os.WriteFile(fallback, []byte("#!/usr/bin/env sh\nexit 0\n"), 0o755); err != nil {
		t.Fatalf("write fallback uvx: %v", err)
	}
	if got := optionalSiblingExecutablePath(workspace, workflow.EnvironmentMap{"PATH": "bin"}, baseExecutable, "uvx"); got != fallback {
		t.Fatalf("optionalSiblingExecutablePath(fallback) = %q, want %q", got, fallback)
	}
	if got := optionalSiblingExecutablePath(workspace, nil, " ", "uvx"); got != "" {
		t.Fatalf("optionalSiblingExecutablePath(empty base) = %q, want empty", got)
	}
}
