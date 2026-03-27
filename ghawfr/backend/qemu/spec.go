package qemu

import (
	"crypto/sha256"
	"encoding/binary"
	"fmt"
	"path"
	"path/filepath"
	"strings"

	ghbackend "github.com/gkze/ghawfr/backend"
)

// LaunchSpec is the concrete QEMU launch description derived from one worker plan.
type LaunchSpec struct {
	Binary             string                     `json:"binary"`
	Args               []string                   `json:"args"`
	SSHAddress         string                     `json:"ssh_address"`
	SSHPort            int                        `json:"ssh_port"`
	SSHUser            string                     `json:"ssh_user"`
	SSHPrivateKeyPath  string                     `json:"ssh_private_key_path"`
	SSHPublicKeyPath   string                     `json:"ssh_public_key_path"`
	SSHCommandPath     string                     `json:"ssh_command_path"`
	WaitForSSHPath     string                     `json:"wait_for_ssh_path"`
	BaseImageURL       string                     `json:"base_image_url"`
	BaseImagePath      string                     `json:"base_image_path"`
	FetchBaseImagePath string                     `json:"fetch_base_image_path"`
	PrepareRuntimePath string                     `json:"prepare_runtime_path"`
	RuntimeDiskPath    string                     `json:"runtime_disk_path"`
	SerialLogPath      string                     `json:"serial_log_path"`
	CloudInitPath      string                     `json:"cloud_init_path"`
	CloudInitBuildPath string                     `json:"cloud_init_build_path"`
	CloudInitDir       string                     `json:"cloud_init_dir"`
	WorkerBinaryPath   string                     `json:"worker_binary_path"`
	WorkerBuildPath    string                     `json:"worker_build_path"`
	GuestWorkerPath    string                     `json:"guest_worker_path"`
	Shares             []ghbackend.DirectoryShare `json:"shares,omitempty"`
}

// BuildLaunchSpec converts a worker plan into a concrete QEMU command line.
func BuildLaunchSpec(plan ghbackend.WorkerPlan) (LaunchSpec, error) {
	if plan.Provider != ghbackend.ProviderKindQEMU {
		return LaunchSpec{}, fmt.Errorf("worker plan provider = %q, want %q", plan.Provider, ghbackend.ProviderKindQEMU)
	}
	if plan.Image == nil {
		return LaunchSpec{}, fmt.Errorf("worker plan image is nil")
	}
	sshPort := sshPortForPlan(plan)
	baseImagePath := filepath.Join(plan.InstanceDirectory, "base-image.qcow2")
	fetchBaseImagePath := filepath.Join(plan.InstanceDirectory, "fetch-base-image.sh")
	prepareRuntimePath := filepath.Join(plan.InstanceDirectory, "prepare-runtime-disk.sh")
	runtimeDiskPath := filepath.Join(plan.InstanceDirectory, "disk.qcow2")
	cloudInitPath := filepath.Join(plan.InstanceDirectory, "cloud-init.iso")
	cloudInitBuildPath := filepath.Join(plan.InstanceDirectory, "build-cloud-init.sh")
	cloudInitDir := filepath.Join(plan.InstanceDirectory, "cloud-init")
	serialLogPath := filepath.Join(plan.InstanceDirectory, "serial.log")
	sshPrivateKeyPath := filepath.Join(plan.InstanceDirectory, "id_ed25519")
	sshPublicKeyPath := filepath.Join(plan.InstanceDirectory, "id_ed25519.pub")
	sshCommandPath := filepath.Join(plan.InstanceDirectory, "ssh-guest.sh")
	waitForSSHPath := filepath.Join(plan.InstanceDirectory, "wait-for-ssh.sh")
	workerBinaryPath := filepath.Join(plan.InstanceDirectory, "ghawfr-worker")
	workerBuildPath := filepath.Join(plan.InstanceDirectory, "build-ghawfr-worker.sh")
	guestWorkerPath, err := guestPathForPlan(plan, workerBinaryPath)
	if err != nil {
		return LaunchSpec{}, err
	}
	args := []string{
		"-accel", "tcg",
		"-machine", "q35",
		"-cpu", "max",
		"-smp", "4",
		"-m", "8192",
		"-display", "none",
		"-serial", "file:" + serialLogPath,
		"-netdev", fmt.Sprintf("user,id=net0,hostfwd=tcp:127.0.0.1:%d-:22", sshPort),
		"-device", "virtio-net-pci,netdev=net0",
		"-drive", "if=virtio,format=qcow2,file=" + runtimeDiskPath,
		"-drive", "if=virtio,media=cdrom,format=raw,file=" + cloudInitPath,
	}
	for _, share := range plan.Shares {
		if share.HostPath == "" || share.GuestPath == "" {
			continue
		}
		args = append(args,
			"-virtfs",
			fmt.Sprintf("local,path=%s,mount_tag=%s,security_model=none%s", share.HostPath, mountTagForShare(share.GuestPath), readonlySuffix(share.ReadOnly)),
		)
	}
	return LaunchSpec{
		Binary:             "qemu-system-x86_64",
		Args:               args,
		SSHAddress:         fmt.Sprintf("127.0.0.1:%d", sshPort),
		SSHPort:            sshPort,
		SSHUser:            "ghawfr",
		SSHPrivateKeyPath:  sshPrivateKeyPath,
		SSHPublicKeyPath:   sshPublicKeyPath,
		SSHCommandPath:     sshCommandPath,
		WaitForSSHPath:     waitForSSHPath,
		BaseImageURL:       plan.Image.Source,
		BaseImagePath:      baseImagePath,
		FetchBaseImagePath: fetchBaseImagePath,
		PrepareRuntimePath: prepareRuntimePath,
		RuntimeDiskPath:    runtimeDiskPath,
		SerialLogPath:      serialLogPath,
		CloudInitPath:      cloudInitPath,
		CloudInitBuildPath: cloudInitBuildPath,
		CloudInitDir:       cloudInitDir,
		WorkerBinaryPath:   workerBinaryPath,
		WorkerBuildPath:    workerBuildPath,
		GuestWorkerPath:    guestWorkerPath,
		Shares:             append([]ghbackend.DirectoryShare(nil), plan.Shares...),
	}, nil
}

func sshPortForPlan(plan ghbackend.WorkerPlan) int {
	hash := sha256.Sum256([]byte(plan.InstanceDirectory))
	return 22000 + int(binary.BigEndian.Uint16(hash[:2])%2000)
}

func mountTagForShare(path string) string {
	path = strings.Trim(path, "/")
	if path == "" {
		return "share"
	}
	path = strings.ReplaceAll(path, "/", "-")
	return strings.ReplaceAll(path, " ", "-")
}

func readonlySuffix(readOnly bool) string {
	if readOnly {
		return ",readonly=on"
	}
	return ""
}

func guestPathForPlan(plan ghbackend.WorkerPlan, hostPath string) (string, error) {
	if strings.TrimSpace(plan.GuestWorkspace) == "" {
		return "", fmt.Errorf("worker plan guest workspace is empty")
	}
	if strings.TrimSpace(plan.WorkingDirectory) == "" {
		return "", fmt.Errorf("worker plan working directory is empty")
	}
	workingDirectory, err := filepath.Abs(plan.WorkingDirectory)
	if err != nil {
		return "", fmt.Errorf("resolve working directory %q: %w", plan.WorkingDirectory, err)
	}
	value, err := filepath.Abs(hostPath)
	if err != nil {
		return "", fmt.Errorf("resolve host path %q: %w", hostPath, err)
	}
	rel, err := filepath.Rel(workingDirectory, value)
	if err != nil {
		return "", fmt.Errorf("rel path from %q to %q: %w", workingDirectory, value, err)
	}
	if rel == "." {
		return plan.GuestWorkspace, nil
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("host path %q is outside working directory %q", value, workingDirectory)
	}
	return path.Join(plan.GuestWorkspace, filepath.ToSlash(rel)), nil
}
