package qemu

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestWaitForSSHUsesHelperScript(t *testing.T) {
	root := t.TempDir()
	waitPath := filepath.Join(root, "wait-for-ssh.sh")
	marker := filepath.Join(root, "wait-called")
	script := "#!/usr/bin/env bash\nset -euo pipefail\necho \"$1\" > \"" + marker + "\"\n"
	if err := os.WriteFile(waitPath, []byte(script), 0o755); err != nil {
		t.Fatalf("WriteFile wait-for-ssh.sh: %v", err)
	}
	launch := MaterializedLaunch{SSH: SSHArtifacts{WaitForSSHPath: waitPath}}
	if err := WaitForSSH(launch, 5*time.Second); err != nil {
		t.Fatalf("WaitForSSH: %v", err)
	}
	data, err := os.ReadFile(marker)
	if err != nil {
		t.Fatalf("ReadFile marker: %v", err)
	}
	if got, want := strings.TrimSpace(string(data)), "5"; got != want {
		t.Fatalf("timeout arg = %q, want %q", got, want)
	}
}

func TestRunGuestCommandUsesWorkerProtocolOverSSHHelper(t *testing.T) {
	root := t.TempDir()
	binDir := filepath.Join(root, "bin")
	if err := os.MkdirAll(binDir, 0o755); err != nil {
		t.Fatalf("MkdirAll bin: %v", err)
	}
	workerScript := filepath.Join(binDir, "ghawfr-worker")
	repoRoot, err := moduleRoot()
	if err != nil {
		t.Fatalf("moduleRoot: %v", err)
	}
	workerBinary := filepath.Join(binDir, "ghawfr-worker-bin")
	build := exec.Command("go", "build", "-o", workerBinary, filepath.Join(repoRoot, "cmd", "ghawfr-worker"))
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build ghawfr-worker: %v\n%s", err, strings.TrimSpace(string(output)))
	}
	workerContent := "#!/usr/bin/env bash\nset -euo pipefail\nexec \"" + workerBinary + "\" \"$@\"\n"
	if err := os.WriteFile(workerScript, []byte(workerContent), 0o755); err != nil {
		t.Fatalf("WriteFile ghawfr-worker: %v", err)
	}
	sshPath := filepath.Join(root, "ssh-guest.sh")
	sshContent := "#!/usr/bin/env bash\nset -euo pipefail\nwhile [ $# -gt 0 ]; do\n  case \"$1\" in\n    -o|-i|-p) shift 2 ;;\n    -*) shift ;;\n    *@*) shift; break ;;\n    *) break ;;\n  esac\ndone\nexec \"$@\"\n"
	if err := os.WriteFile(sshPath, []byte(sshContent), 0o755); err != nil {
		t.Fatalf("WriteFile ssh-guest.sh: %v", err)
	}
	t.Setenv("PATH", binDir+string(os.PathListSeparator)+os.Getenv("PATH"))
	launch := MaterializedLaunch{SSH: SSHArtifacts{SSHCommandPath: sshPath}}
	output, err := RunGuestCommand(launch, "printf hello")
	if err != nil {
		t.Fatalf("RunGuestCommand: %v", err)
	}
	if got, want := strings.TrimSpace(output), "hello"; got != want {
		t.Fatalf("output = %q, want %q", got, want)
	}
}

func moduleRoot() (string, error) {
	wd, err := os.Getwd()
	if err != nil {
		return "", err
	}
	return filepath.Clean(filepath.Join(wd, "..", "..")), nil
}
