//go:build unix

package qemu

import (
	"fmt"
	"os/exec"
	"syscall"
	"time"
)

func configureLaunchCommand(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
}

// IsProcessRunning reports whether the process recorded in the given state still exists.
func IsProcessRunning(state ProcessState) (bool, error) {
	if state.PID <= 0 {
		return false, fmt.Errorf("process pid is not set")
	}
	err := syscall.Kill(state.PID, syscall.Signal(0))
	switch err {
	case nil:
		return true, nil
	case syscall.ESRCH:
		return false, nil
	case syscall.EPERM:
		return true, nil
	default:
		return false, err
	}
}

// StopProcess terminates the started QEMU launch process, escalating to SIGKILL
// if it does not exit within the given timeout.
func StopProcess(state ProcessState, timeout time.Duration) error {
	if state.PID <= 0 {
		return fmt.Errorf("process pid is not set")
	}
	if err := syscall.Kill(state.PID, syscall.SIGTERM); err != nil && err != syscall.ESRCH {
		if groupErr := syscall.Kill(-state.PID, syscall.SIGTERM); groupErr != nil && groupErr != syscall.ESRCH {
			return err
		}
	}
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		running, err := IsProcessRunning(state)
		if err != nil {
			return err
		}
		if !running {
			return nil
		}
		time.Sleep(100 * time.Millisecond)
	}
	if err := syscall.Kill(state.PID, syscall.SIGKILL); err != nil && err != syscall.ESRCH {
		if groupErr := syscall.Kill(-state.PID, syscall.SIGKILL); groupErr != nil && groupErr != syscall.ESRCH {
			return err
		}
	}
	return nil
}
