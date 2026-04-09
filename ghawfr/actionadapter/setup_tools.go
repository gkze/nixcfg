package actionadapter

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/gkze/ghawfr/backend"
	"github.com/gkze/ghawfr/workflow"
)

var versionPattern = regexp.MustCompile(`\d+(?:\.\d+)+`)

type localToolInstallation struct {
	ExecutablePath string
	Alias          toolCacheAlias
}

type remoteToolInstallation struct {
	ExecutablePath string
	Alias          toolCacheAlias
}

func localToolInstallationInfo(
	action backend.ActionContext,
	tool string,
	family string,
	version string,
) (localToolInstallation, error) {
	executablePath, err := localLookPath(action.WorkingDirectory, action.Env, tool)
	if err != nil {
		return localToolInstallation{}, err
	}
	root := installRootForExecutable(executablePath)
	alias, err := localToolCacheAlias(action, family, version, root)
	if err != nil {
		return localToolInstallation{}, err
	}
	return localToolInstallation{ExecutablePath: executablePath, Alias: alias}, nil
}

func remoteToolInstallationInfo(
	ctx context.Context,
	handlers remoteHandlers,
	action backend.ActionContext,
	tool string,
	family string,
	version string,
) (remoteToolInstallation, error) {
	executablePath, err := handlers.lookupRemoteTool(ctx, action, tool)
	if err != nil {
		return remoteToolInstallation{}, err
	}
	root := installRootForExecutable(executablePath)
	alias, err := handlers.remoteToolCacheAlias(action, family, version, root)
	if err != nil {
		return remoteToolInstallation{}, err
	}
	return remoteToolInstallation{ExecutablePath: executablePath, Alias: alias}, nil
}

func localToolSetupResult(
	action backend.ActionContext,
	tool string,
	family string,
	version string,
	environment workflow.EnvironmentMap,
) (backend.StepResult, error) {
	installation, err := localToolInstallationInfo(action, tool, family, version)
	if err != nil {
		return backend.StepResult{Environment: cloneOptionalEnvironment(environment)}, err
	}
	result := backend.StepResult{Environment: cloneOptionalEnvironment(environment)}
	result.PathEntries = []string{installation.Alias.HostBin}
	return result, nil
}

func (r remoteHandlers) remoteToolSetupResult(
	ctx context.Context,
	action backend.ActionContext,
	tool string,
	family string,
	version string,
	environment workflow.EnvironmentMap,
) (backend.StepResult, error) {
	installation, err := remoteToolInstallationInfo(ctx, r, action, tool, family, version)
	if err != nil {
		return backend.StepResult{Environment: cloneOptionalEnvironment(environment)}, err
	}
	result := backend.StepResult{Environment: cloneOptionalEnvironment(environment)}
	result.PathEntries = []string{installation.Alias.GuestBin}
	return result, nil
}

func detectedVersionOutput(requested string, detected string) string {
	requested = strings.TrimSpace(requested)
	if requested != "" {
		return requested
	}
	return detected
}

func resolveRequestedVersions(value string) []string {
	values := splitMultilineValue(value)
	if len(values) != 0 {
		return values
	}
	trimmed := strings.TrimSpace(value)
	if trimmed == "" {
		return nil
	}
	return []string{trimmed}
}

func matchingRequestedVersion(requested []string, detected string) (string, error) {
	detected = strings.TrimSpace(detected)
	if len(requested) == 0 {
		return "", nil
	}
	if detected == "" {
		return "", fmt.Errorf("could not detect an installed version")
	}
	for _, candidate := range requested {
		candidate = strings.TrimSpace(candidate)
		if candidate == "" {
			continue
		}
		if detected == candidate || strings.HasPrefix(detected, candidate+".") {
			return candidate, nil
		}
	}
	return "", fmt.Errorf("found %q", detected)
}

func rejectUnsupportedInputs(
	action backend.ActionContext,
	actionName string,
	supportedKeys ...string,
) error {
	allowed := make(map[string]struct{}, len(supportedKeys))
	for _, key := range supportedKeys {
		allowed[key] = struct{}{}
	}
	for key, value := range action.Inputs {
		if _, ok := allowed[key]; ok {
			continue
		}
		if strings.TrimSpace(value) == "" {
			continue
		}
		return fmt.Errorf("%s input %q is not supported yet", actionName, key)
	}
	return nil
}

func commandEnvironmentSlice(values workflow.EnvironmentMap) []string {
	env := append([]string(nil), os.Environ()...)
	for key, value := range values {
		env = append(env, key+"="+value)
	}
	return env
}

func localToolVersion(
	environment workflow.EnvironmentMap,
	executable string,
	args ...string,
) string {
	cmd := exec.Command(executable, args...)
	cmd.Env = commandEnvironmentSlice(environment)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return ""
	}
	return extractVersionString(output)
}

func (r remoteHandlers) remoteToolVersion(
	ctx context.Context,
	action backend.ActionContext,
	executable string,
	args ...string,
) string {
	if strings.TrimSpace(executable) == "" {
		return ""
	}
	parts := make([]string, 0, 1+len(args))
	parts = append(parts, shellQuote(executable))
	for _, arg := range args {
		parts = append(parts, shellQuote(arg))
	}
	output, err := r.execRemoteActionOutput(
		ctx,
		r.remoteActionExpressions(action).Env,
		strings.Join(parts, " "),
		filepath.Base(executable)+" version",
	)
	if err != nil {
		return ""
	}
	return extractVersionString([]byte(output))
}

func extractVersionString(output []byte) string {
	match := versionPattern.Find(bytes.TrimSpace(output))
	if len(match) == 0 {
		return ""
	}
	return string(match)
}

func localToolExecutableOutputPath(alias toolCacheAlias, executablePath string) string {
	name := filepath.Base(strings.TrimSpace(executablePath))
	if name == "" || name == "." || name == string(filepath.Separator) {
		return alias.HostBin
	}
	return filepath.Join(alias.HostBin, name)
}

func remoteToolExecutableOutputPath(alias toolCacheAlias, executablePath string) string {
	name := path.Base(strings.TrimSpace(executablePath))
	if name == "" || name == "." || name == "/" {
		return alias.GuestBin
	}
	return path.Join(alias.GuestBin, name)
}

func optionalSiblingExecutablePath(
	workingDirectory string,
	environment workflow.EnvironmentMap,
	baseExecutable string,
	siblingName string,
) string {
	baseExecutable = strings.TrimSpace(baseExecutable)
	siblingName = strings.TrimSpace(siblingName)
	if baseExecutable == "" || siblingName == "" {
		return ""
	}
	directory := filepath.Dir(baseExecutable)
	candidate := filepath.Join(directory, siblingName)
	if isExecutableFile(candidate) {
		return candidate
	}
	if resolved, err := localLookPath(workingDirectory, environment, siblingName); err == nil {
		return resolved
	}
	return ""
}

func localLookPath(
	workingDirectory string,
	environment workflow.EnvironmentMap,
	file string,
) (string, error) {
	file = strings.TrimSpace(file)
	if file == "" {
		return "", exec.ErrNotFound
	}
	resolveCandidate := func(candidate string) (string, error) {
		candidate = strings.TrimSpace(candidate)
		if candidate == "" {
			return "", exec.ErrNotFound
		}
		if !filepath.IsAbs(candidate) {
			base := strings.TrimSpace(workingDirectory)
			if base != "" {
				candidate = filepath.Join(base, candidate)
			}
		}
		absolute, err := filepath.Abs(candidate)
		if err != nil {
			return "", err
		}
		if isExecutableFile(absolute) {
			return absolute, nil
		}
		return "", exec.ErrNotFound
	}
	if strings.Contains(file, string(filepath.Separator)) {
		return resolveCandidate(file)
	}
	pathValue, ok := environment["PATH"]
	if !ok {
		pathValue = os.Getenv("PATH")
	}
	for _, directory := range filepath.SplitList(pathValue) {
		if directory == "" {
			continue
		}
		candidate, err := resolveCandidate(filepath.Join(directory, file))
		if err == nil {
			return candidate, nil
		}
	}
	return "", exec.ErrNotFound
}

func isExecutableFile(path string) bool {
	info, err := os.Stat(path)
	if err != nil || info.IsDir() {
		return false
	}
	return info.Mode().Perm()&0o111 != 0
}
