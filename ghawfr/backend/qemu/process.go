package qemu

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	ghbackend "github.com/gkze/ghawfr/backend"
)

// ProcessState records one started QEMU launch process.
type ProcessState struct {
	PID         int       `json:"pid"`
	CommandPath string    `json:"command_path"`
	LogPath     string    `json:"log_path"`
	PIDPath     string    `json:"pid_path"`
	StatePath   string    `json:"state_path"`
	StartedAt   time.Time `json:"started_at"`
}

// ProcessStatePath returns the persisted process-state file path for one
// instance directory.
func ProcessStatePath(instanceDirectory string) string {
	return filepath.Join(instanceDirectory, "qemu-process.json")
}

// LoadProcessState reads one persisted process-state file.
func LoadProcessState(path string) (ProcessState, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return ProcessState{}, fmt.Errorf("read process state %q: %w", path, err)
	}
	var state ProcessState
	if err := json.Unmarshal(data, &state); err != nil {
		return ProcessState{}, fmt.Errorf("unmarshal process state %q: %w", path, err)
	}
	return state, nil
}

// StartMaterializedLaunch starts the prepared QEMU launch script in the
// background and records PID/state artifacts under the instance directory.
func StartMaterializedLaunch(launch MaterializedLaunch) (ProcessState, error) {
	if launch.Command == "" {
		return ProcessState{}, fmt.Errorf("materialized launch command path is empty")
	}
	instanceDirectory := filepath.Dir(launch.Command)
	logPath := filepath.Join(instanceDirectory, "qemu.log")
	pidPath := filepath.Join(instanceDirectory, "qemu.pid")
	statePath := ProcessStatePath(instanceDirectory)
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return ProcessState{}, fmt.Errorf("open log file %q: %w", logPath, err)
	}
	defer func() { _ = logFile.Close() }()
	cmd := exec.Command("bash", launch.Command)
	cmd.Dir = instanceDirectory
	cmd.Stdout = logFile
	cmd.Stderr = logFile
	configureLaunchCommand(cmd)
	if err := cmd.Start(); err != nil {
		return ProcessState{}, fmt.Errorf("start launch command %q: %w", launch.Command, err)
	}
	state := ProcessState{
		PID:         cmd.Process.Pid,
		CommandPath: launch.Command,
		LogPath:     logPath,
		PIDPath:     pidPath,
		StatePath:   statePath,
		StartedAt:   time.Now().UTC(),
	}
	if err := os.WriteFile(pidPath, []byte(fmt.Sprintf("%d\n", state.PID)), 0o644); err != nil {
		return ProcessState{}, fmt.Errorf("write pid file %q: %w", pidPath, err)
	}
	if err := ghbackend.WriteJSONFile(statePath, state); err != nil {
		return ProcessState{}, err
	}
	if err := cmd.Process.Release(); err != nil {
		return ProcessState{}, fmt.Errorf("release process %d: %w", state.PID, err)
	}
	return state, nil
}
