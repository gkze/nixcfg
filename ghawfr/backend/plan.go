package backend

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/gkze/ghawfr/state"
	"github.com/gkze/ghawfr/workflow"
)

// TransportKind identifies the control transport used to talk to one worker.
type TransportKind string

const (
	// TransportKindHost means direct in-process host execution.
	TransportKindHost TransportKind = "host"
	// TransportKindSSH means the worker is controlled over SSH.
	TransportKindSSH TransportKind = "ssh"
	// TransportKindVSock means the worker is controlled over virtio-vsock.
	TransportKindVSock TransportKind = "vsock"
)

// HostRequirement describes one host-side prerequisite for a provider route.
type HostRequirement struct {
	Kind    string
	Name    string
	Purpose string
}

// DirectoryShare describes one host/guest shared directory mapping.
type DirectoryShare struct {
	HostPath  string
	GuestPath string
	ReadOnly  bool
}

// TransportPlan describes the expected worker control transport.
type TransportPlan struct {
	Kind    TransportKind
	Address string
}

// WorkerPlan is the concrete provider/runtime plan for one materialized job.
type WorkerPlan struct {
	Provider          ProviderKind
	Requirements      WorkerRequirements
	Image             *ImagePlan
	WorkingDirectory  string
	InstanceDirectory string
	GuestWorkspace    string
	Transport         TransportPlan
	Shares            []DirectoryShare
	HostRequirements  []HostRequirement
	Notes             []string
}

// PlannedProvider can describe the concrete worker/runtime plan for one job
// before actually acquiring the worker lease.
type PlannedProvider interface {
	Provider
	PlanWorker(job *workflow.Job, options RunOptions) (WorkerPlan, error)
}

// PlanWorkingDirectory resolves the host working directory for one worker plan.
func PlanWorkingDirectory(options RunOptions) (string, error) {
	if strings.TrimSpace(options.WorkingDirectory) != "" {
		return filepath.Abs(options.WorkingDirectory)
	}
	cwd, err := os.Getwd()
	if err != nil {
		return "", fmt.Errorf("resolve working directory: %w", err)
	}
	return cwd, nil
}

// PlanInstanceDirectory returns the provider-local state directory for one job instance.
func PlanInstanceDirectory(root string, provider ProviderKind, job *workflow.Job) string {
	slug := "job"
	if job != nil {
		slug = sanitizePathComponent(job.ID.String())
	}
	suffix := shortJobHash(job)
	return filepath.Join(state.WorkersDir(root, string(provider)), slug+"-"+suffix)
}

func shortJobHash(job *workflow.Job) string {
	identity := "job"
	if job != nil {
		identity = job.ID.String() + "\x00" + job.LogicalID.String() + "\x00" + job.Name
	}
	hash := sha256.Sum256([]byte(identity))
	return hex.EncodeToString(hash[:4])
}

func sanitizePathComponent(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	if value == "" {
		return "job"
	}
	var builder strings.Builder
	lastDash := false
	for _, r := range value {
		switch {
		case r >= 'a' && r <= 'z', r >= '0' && r <= '9':
			builder.WriteRune(r)
			lastDash = false
		default:
			if !lastDash {
				builder.WriteByte('-')
				lastDash = true
			}
		}
	}
	result := strings.Trim(builder.String(), "-")
	if result == "" {
		return "job"
	}
	return result
}
