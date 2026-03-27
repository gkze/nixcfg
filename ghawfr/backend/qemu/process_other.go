//go:build !unix

package qemu

import (
	"fmt"
	"os/exec"
	"time"
)

func configureLaunchCommand(_ *exec.Cmd) {}

// IsProcessRunning is not implemented on non-unix hosts yet.
func IsProcessRunning(_ ProcessState) (bool, error) {
	return false, fmt.Errorf("process status is not implemented on this platform")
}

// StopProcess is not implemented on non-unix hosts yet.
func StopProcess(_ ProcessState, _ time.Duration) error {
	return fmt.Errorf("process stop is not implemented on this platform")
}
