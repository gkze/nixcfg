package state

import (
	"crypto/sha256"
	"encoding/hex"
	"path"
	"path/filepath"
	"strings"
)

// RuntimeRoot returns the repository-local ghawfr runtime directory.
func RuntimeRoot(hostWorkspace string) string {
	return filepath.Join(hostWorkspace, ".ghawfr")
}

// ArtifactsDir returns the artifact store root for one workspace.
func ArtifactsDir(hostWorkspace string) string {
	return filepath.Join(RuntimeRoot(hostWorkspace), "artifacts")
}

// CacheDir returns the cache store root for one workspace.
func CacheDir(hostWorkspace string) string {
	return filepath.Join(RuntimeRoot(hostWorkspace), "cache")
}

// RunsDir returns the persisted run-state directory for one workspace.
func RunsDir(hostWorkspace string) string {
	return filepath.Join(RuntimeRoot(hostWorkspace), "runs")
}

// RunStatePath returns the persisted run-state path for one workflow.
func RunStatePath(hostWorkspace string, workflowPath string) string {
	hash := sha256.Sum256([]byte(workflowPath))
	base := strings.TrimSuffix(filepath.Base(workflowPath), filepath.Ext(workflowPath))
	if base == "" {
		base = "workflow"
	}
	suffix := hex.EncodeToString(hash[:8])
	return filepath.Join(RunsDir(hostWorkspace), base+"-"+suffix+".json")
}

// WorkersDir returns the provider-local worker-instance directory root.
func WorkersDir(hostWorkspace string, provider string) string {
	return filepath.Join(RuntimeRoot(hostWorkspace), "workers", provider)
}

// RunnerDir returns the host-side runner filesystem root.
func RunnerDir(hostWorkspace string) string {
	return filepath.Join(RuntimeRoot(hostWorkspace), "runner")
}

// RunnerTempDir returns the host-side runner temp directory.
func RunnerTempDir(hostWorkspace string) string {
	return filepath.Join(RunnerDir(hostWorkspace), "temp")
}

// RunnerToolCacheDir returns the host-side runner tool-cache directory.
func RunnerToolCacheDir(hostWorkspace string) string {
	return filepath.Join(RunnerDir(hostWorkspace), "tool-cache")
}

// RunnerHomeDir returns the host-side runner home directory.
func RunnerHomeDir(hostWorkspace string) string {
	return filepath.Join(RunnerDir(hostWorkspace), "home")
}

// FileCommandsDir returns the host-side file-command directory root.
func FileCommandsDir(hostWorkspace string) string {
	return filepath.Join(RuntimeRoot(hostWorkspace), "file-commands")
}

// GuestRuntimeRoot returns the guest-side ghawfr runtime root.
func GuestRuntimeRoot(guestWorkspace string) string {
	return path.Join(guestWorkspace, ".ghawfr")
}

// GuestRunnerDir returns the guest-side runner filesystem root.
func GuestRunnerDir(guestWorkspace string) string {
	return path.Join(GuestRuntimeRoot(guestWorkspace), "runner")
}

// GuestRunnerTempDir returns the guest-side runner temp directory.
func GuestRunnerTempDir(guestWorkspace string) string {
	return path.Join(GuestRunnerDir(guestWorkspace), "temp")
}

// GuestRunnerToolCacheDir returns the guest-side runner tool-cache directory.
func GuestRunnerToolCacheDir(guestWorkspace string) string {
	return path.Join(GuestRunnerDir(guestWorkspace), "tool-cache")
}

// GuestRunnerHomeDir returns the guest-side runner home directory.
func GuestRunnerHomeDir(guestWorkspace string) string {
	return path.Join(GuestRunnerDir(guestWorkspace), "home")
}
