package qemu

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"encoding/pem"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"golang.org/x/crypto/ssh"
)

// CloudInitArtifacts are the generated source files used to seed the Linux guest.
type CloudInitArtifacts struct {
	UserDataPath      string
	MetaDataPath      string
	NetworkConfigPath string
	ISOPath           string
	BuildScriptPath   string
	Builder           string
}

func materializeCloudInit(spec LaunchSpec) (CloudInitArtifacts, error) {
	if err := os.MkdirAll(spec.CloudInitDir, 0o755); err != nil {
		return CloudInitArtifacts{}, fmt.Errorf("create cloud-init dir %q: %w", spec.CloudInitDir, err)
	}
	publicKey, err := materializeSSHKeypair(spec)
	if err != nil {
		return CloudInitArtifacts{}, err
	}
	artifacts := CloudInitArtifacts{
		UserDataPath:      filepath.Join(spec.CloudInitDir, "user-data"),
		MetaDataPath:      filepath.Join(spec.CloudInitDir, "meta-data"),
		NetworkConfigPath: filepath.Join(spec.CloudInitDir, "network-config"),
		ISOPath:           spec.CloudInitPath,
		BuildScriptPath:   spec.CloudInitBuildPath,
	}
	if err := os.WriteFile(artifacts.UserDataPath, []byte(renderUserData(spec, publicKey)), 0o644); err != nil {
		return CloudInitArtifacts{}, fmt.Errorf("write user-data %q: %w", artifacts.UserDataPath, err)
	}
	if err := os.WriteFile(artifacts.MetaDataPath, []byte(renderMetaData(spec)), 0o644); err != nil {
		return CloudInitArtifacts{}, fmt.Errorf("write meta-data %q: %w", artifacts.MetaDataPath, err)
	}
	if err := os.WriteFile(artifacts.NetworkConfigPath, []byte(renderNetworkConfig()), 0o644); err != nil {
		return CloudInitArtifacts{}, fmt.Errorf("write network-config %q: %w", artifacts.NetworkConfigPath, err)
	}
	builderName, buildScript, err := renderCloudInitBuildScript(spec)
	if err != nil {
		return CloudInitArtifacts{}, err
	}
	artifacts.Builder = builderName
	if err := os.WriteFile(artifacts.BuildScriptPath, []byte(buildScript), 0o755); err != nil {
		return CloudInitArtifacts{}, fmt.Errorf("write cloud-init build script %q: %w", artifacts.BuildScriptPath, err)
	}
	if builderName != "" {
		if err := runCloudInitBuildScript(artifacts.BuildScriptPath); err != nil {
			return CloudInitArtifacts{}, err
		}
	}
	return artifacts, nil
}

func materializeSSHKeypair(spec LaunchSpec) (string, error) {
	if publicKey, err := os.ReadFile(spec.SSHPublicKeyPath); err == nil && strings.TrimSpace(string(publicKey)) != "" {
		return strings.TrimSpace(string(publicKey)), nil
	}
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return "", fmt.Errorf("generate ed25519 key: %w", err)
	}
	sshPublic, err := ssh.NewPublicKey(publicKey)
	if err != nil {
		return "", fmt.Errorf("encode ssh public key: %w", err)
	}
	publicAuthorizedKey := strings.TrimSpace(string(ssh.MarshalAuthorizedKey(sshPublic)))
	privateDER, err := x509.MarshalPKCS8PrivateKey(privateKey)
	if err != nil {
		return "", fmt.Errorf("marshal private key: %w", err)
	}
	privatePEM := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: privateDER})
	if err := os.WriteFile(spec.SSHPrivateKeyPath, privatePEM, 0o600); err != nil {
		return "", fmt.Errorf("write private key %q: %w", spec.SSHPrivateKeyPath, err)
	}
	if err := os.WriteFile(spec.SSHPublicKeyPath, []byte(publicAuthorizedKey+"\n"), 0o644); err != nil {
		return "", fmt.Errorf("write public key %q: %w", spec.SSHPublicKeyPath, err)
	}
	return publicAuthorizedKey, nil
}

func renderUserData(spec LaunchSpec, publicKey string) string {
	mounts := make([]string, 0, len(spec.Shares))
	for _, share := range spec.Shares {
		if share.GuestPath == "" {
			continue
		}
		mounts = append(mounts, fmt.Sprintf("  - [%s, %s, 9p, \"trans=virtio,version=9p2000.L,msize=104857600\", \"0\", \"0\"]", mountTagForShare(share.GuestPath), share.GuestPath))
	}
	content := "#cloud-config\n" +
		"users:\n" +
		fmt.Sprintf("  - name: %s\n", spec.SSHUser) +
		"    sudo: ALL=(ALL) NOPASSWD:ALL\n" +
		"    shell: /bin/bash\n" +
		"    ssh_authorized_keys:\n" +
		fmt.Sprintf("      - %s\n", publicKey) +
		"ssh_pwauth: false\n" +
		"disable_root: true\n"
	if len(mounts) > 0 {
		content += "mounts:\n" + strings.Join(mounts, "\n") + "\n"
	}
	return content
}

func renderMetaData(spec LaunchSpec) string {
	return fmt.Sprintf("instance-id: %s\nlocal-hostname: ghawfr\n", filepath.Base(filepath.Dir(spec.CloudInitDir)))
}

func renderNetworkConfig() string {
	return "version: 2\nethernets:\n  id0:\n    match:\n      name: en*\n    dhcp4: true\n"
}

func renderCloudInitBuildScript(spec LaunchSpec) (string, string, error) {
	type isoBuilder struct {
		name    string
		command string
	}
	builders := []isoBuilder{
		{
			name:    "hdiutil",
			command: fmt.Sprintf("rm -f %q\nhdiutil makehybrid -o %q %q -iso -joliet -default-volume-name cidata\n", spec.CloudInitPath, spec.CloudInitPath, spec.CloudInitDir),
		},
		{
			name:    "xorriso",
			command: fmt.Sprintf("rm -f %q\nxorriso -as mkisofs -output %q -volid cidata -joliet -rock %q\n", spec.CloudInitPath, spec.CloudInitPath, spec.CloudInitDir),
		},
		{
			name:    "genisoimage",
			command: fmt.Sprintf("rm -f %q\ngenisoimage -output %q -volid cidata -joliet -rock %q\n", spec.CloudInitPath, spec.CloudInitPath, spec.CloudInitDir),
		},
		{
			name:    "mkisofs",
			command: fmt.Sprintf("rm -f %q\nmkisofs -output %q -volid cidata -joliet -rock %q\n", spec.CloudInitPath, spec.CloudInitPath, spec.CloudInitDir),
		},
	}
	for _, builder := range builders {
		if _, err := exec.LookPath(builder.name); err == nil {
			return builder.name, "#!/usr/bin/env bash\nset -euo pipefail\n" + builder.command, nil
		}
	}
	fallback := "#!/usr/bin/env bash\nset -euo pipefail\necho 'no supported ISO builder found (tried: hdiutil, xorriso, genisoimage, mkisofs)' >&2\nexit 1\n"
	return "", fallback, nil
}

func runCloudInitBuildScript(path string) error {
	cmd := exec.Command("bash", path)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("build cloud-init iso with %q: %w\n%s", path, err, strings.TrimSpace(string(output)))
	}
	return nil
}
