package qemu

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"

	ghbackend "github.com/gkze/ghawfr/backend"
	"github.com/gkze/ghawfr/workerproto"
	"github.com/gkze/ghawfr/workflow"
)

// RemoteWorker is one SSH-bootstrapped guest worker session.
type RemoteWorker struct {
	cmd    *exec.Cmd
	stdin  io.WriteCloser
	client *workerproto.Client
	stderr bytes.Buffer
}

// WaitForSSH waits until the guest SSH helper can successfully connect.
func WaitForSSH(launch MaterializedLaunch, timeout time.Duration) error {
	if launch.SSH.WaitForSSHPath == "" {
		return fmt.Errorf("wait-for-ssh script path is empty")
	}
	seconds := int(timeout.Round(time.Second) / time.Second)
	if seconds <= 0 {
		seconds = 1
	}
	cmd := exec.Command("bash", launch.SSH.WaitForSSHPath, strconv.Itoa(seconds))
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("wait for guest ssh via %q: %w\n%s", launch.SSH.WaitForSSHPath, err, strings.TrimSpace(string(output)))
	}
	return nil
}

// StartRemoteWorker starts ghawfr-worker over the SSH helper and negotiates the
// worker protocol over the resulting stdio channel.
func StartRemoteWorker(launch MaterializedLaunch) (*RemoteWorker, error) {
	if launch.SSH.SSHCommandPath == "" {
		return nil, fmt.Errorf("ssh helper script path is empty")
	}
	cmd := exec.Command("bash", launch.SSH.SSHCommandPath, remoteWorkerCommand(launch), "serve", "--stdio")
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, fmt.Errorf("open remote worker stdin: %w", err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, fmt.Errorf("open remote worker stdout: %w", err)
	}
	worker := &RemoteWorker{cmd: cmd, stdin: stdin}
	cmd.Stderr = &worker.stderr
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("start remote worker via %q: %w", launch.SSH.SSHCommandPath, err)
	}
	worker.client = workerproto.NewClient(stdout, stdin, stdin)
	if _, err := worker.client.Hello("ghawfr-controller"); err != nil {
		closeErr := worker.Close()
		stderr := strings.TrimSpace(worker.stderr.String())
		if closeErr != nil && stderr != "" {
			return nil, fmt.Errorf("start remote worker: %w\n%s", err, stderr)
		}
		if closeErr != nil {
			return nil, fmt.Errorf("start remote worker: %w (%v)", err, closeErr)
		}
		if stderr != "" {
			return nil, fmt.Errorf("start remote worker: %w\n%s", err, stderr)
		}
		return nil, err
	}
	return worker, nil
}

// Close ends the remote worker session and waits for the SSH transport process.
func (w *RemoteWorker) Close() error {
	if w == nil {
		return nil
	}
	if w.client != nil {
		_ = w.client.Close()
	}
	if w.cmd == nil {
		return nil
	}
	if err := w.cmd.Wait(); err != nil {
		stderr := strings.TrimSpace(w.stderr.String())
		if stderr != "" {
			return fmt.Errorf("wait for remote worker: %w\n%s", err, stderr)
		}
		return fmt.Errorf("wait for remote worker: %w", err)
	}
	return nil
}

// Exec executes one shell command inside the guest worker session.
func (w *RemoteWorker) Exec(command string, workingDirectory string, environment map[string]string) (workerproto.ExecResponse, error) {
	if w == nil || w.client == nil {
		return workerproto.ExecResponse{}, fmt.Errorf("remote worker client is not initialized")
	}
	return w.client.Exec(command, workingDirectory, environment)
}

// ExecCommand adapts the remote worker session to the backend command transport interface.
func (w *RemoteWorker) ExecCommand(_ context.Context, workingDirectory string, environment workflow.EnvironmentMap, command string) (ghbackend.CommandResult, error) {
	result, err := w.Exec(command, workingDirectory, environment)
	return ghbackend.CommandResult{Stdout: result.Stdout, Stderr: result.Stderr, ExitCode: result.ExitCode}, err
}

// RunGuestCommand runs one shell command through the SSH-bootstrapped guest
// worker protocol.
func RunGuestCommand(launch MaterializedLaunch, command string) (string, error) {
	worker, err := StartRemoteWorker(launch)
	if err != nil {
		return "", err
	}
	defer func() {
		_ = worker.Close()
	}()
	result, err := worker.Exec(command, "", nil)
	if err != nil {
		return result.Stdout + result.Stderr, err
	}
	output := result.Stdout + result.Stderr
	if result.ExitCode != 0 {
		return output, fmt.Errorf("guest command %q exited with code %d", command, result.ExitCode)
	}
	return output, nil
}

func remoteWorkerCommand(launch MaterializedLaunch) string {
	if value := strings.TrimSpace(os.Getenv("GHAWFR_WORKER_REMOTE_COMMAND")); value != "" {
		return value
	}
	if value := strings.TrimSpace(launch.Spec.GuestWorkerPath); value != "" {
		return value
	}
	return "ghawfr-worker"
}
