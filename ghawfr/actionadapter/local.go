package actionadapter

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"

	"github.com/gkze/ghawfr/backend"
	"github.com/gkze/ghawfr/workflow"
)

// NewLocal returns a generic local worker wired with the curated action
// adapters from this package.
func NewLocal(runnerLabels []string) backend.Local {
	return backend.Local{RunnerLabels: append([]string(nil), runnerLabels...), ActionHandlers: LocalHandlers()}
}

// NewHostLocal returns a host-scoped local worker wired with curated action
// adapters.
func NewHostLocal() backend.Local {
	return NewLocal(backend.RunnerLabelsForHost(runtime.GOOS, runtime.GOARCH))
}

// LocalHandlers returns the curated local action-handler registry.
func LocalHandlers() map[string]backend.ActionHandler {
	return map[string]backend.ActionHandler{
		"actions/checkout":                          backend.ActionHandlerFunc(handleCheckoutAction),
		"determinatesystems/determinate-nix-action": backend.ActionHandlerFunc(handleDeterminateNixAction),
		"cachix/cachix-action":                      backend.ActionHandlerFunc(handleCachixAction),
		"actions/cache":                             backend.ActionHandlerFunc(handleCacheAction),
		"actions/cache/restore":                     backend.ActionHandlerFunc(handleCacheRestoreAction),
		"actions/cache/save":                        backend.ActionHandlerFunc(handleCacheSaveAction),
		"actions/upload-artifact":                   backend.ActionHandlerFunc(handleUploadArtifactAction),
		"actions/download-artifact":                 backend.ActionHandlerFunc(handleDownloadArtifactAction),
		"actions/setup-python":                      backend.ActionHandlerFunc(handleSetupPythonAction),
		"astral-sh/setup-uv":                        backend.ActionHandlerFunc(handleSetupUVAction),
		"peter-evans/create-pull-request":           backend.ActionHandlerFunc(handleCreatePullRequestAction),
	}
}

func handleCheckoutAction(_ context.Context, action backend.ActionContext) (backend.StepResult, error) {
	workspace := action.WorkingDirectory
	pathValue, err := resolveActionInput(action, "path")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve checkout path: %w", err)
	}
	if strings.TrimSpace(pathValue) != "" {
		workspace = resolveWorkspacePath(action.WorkingDirectory, pathValue)
	}
	if _, err := os.Stat(filepath.Join(workspace, ".git")); err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("checkout workspace %q is not a git repository: %w", workspace, err)
	}
	return backend.StepResult{ID: action.Step.ID}, nil
}

func handleDeterminateNixAction(_ context.Context, action backend.ActionContext) (backend.StepResult, error) {
	result, err := localToolSetupResult(action, "nix", "nix", "system", nil)
	result.ID = action.Step.ID
	if err != nil {
		return result, fmt.Errorf("determinate-nix-action requires nix on PATH: %w", err)
	}
	return result, nil
}

func handleCachixAction(_ context.Context, action backend.ActionContext) (backend.StepResult, error) {
	name, err := resolveActionInput(action, "name")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve cachix name: %w", err)
	}
	authToken, err := resolveActionInput(action, "authToken")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve cachix auth token: %w", err)
	}
	environment := workflow.EnvironmentMap{}
	if strings.TrimSpace(name) != "" {
		environment["CACHIX_NAME"] = name
	}
	if strings.TrimSpace(authToken) != "" {
		environment["CACHIX_AUTH_TOKEN"] = authToken
	}
	result, err := localToolSetupResult(action, "cachix", "cachix", "system", environment)
	result.ID = action.Step.ID
	if err != nil {
		return result, fmt.Errorf("cachix-action requires cachix on PATH: %w", err)
	}
	return result, nil
}

func handleCacheAction(_ context.Context, action backend.ActionContext) (backend.StepResult, error) {
	paths, err := resolveLocalCachePaths(action)
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	key, restoreKeys, lookupOnly, failOnMiss, err := resolveLocalCacheRestoreInputs(action)
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	result := backend.StepResult{ID: action.Step.ID}
	if action.Cache == nil || strings.TrimSpace(key) == "" {
		return result, nil
	}
	matchedKey, err := action.Cache.Match(key, restoreKeys)
	if err != nil {
		return result, fmt.Errorf("match cache %q: %w", key, err)
	}
	if matchedKey == "" {
		if failOnMiss {
			return result, fmt.Errorf("cache %q matched no entry", key)
		}
	} else {
		result.Outputs = workflow.OutputMap{"cache-hit": strconv.FormatBool(strings.TrimSpace(matchedKey) == strings.TrimSpace(key))}
		if !lookupOnly {
			if _, err := action.Cache.Restore(key, restoreKeys); err != nil {
				return result, fmt.Errorf("restore cache %q: %w", key, err)
			}
		}
	}
	cacheKey := key
	cachePaths := append([]string(nil), paths...)
	result.Post = backend.PostStepFunc(func(ctx context.Context, status backend.JobStatus) (backend.StepResult, error) {
		post := backend.StepResult{ID: action.Step.ID}
		if status != backend.JobStatusSuccess {
			return post, nil
		}
		if strings.TrimSpace(matchedKey) == strings.TrimSpace(cacheKey) {
			return post, nil
		}
		if err := action.Cache.Save(cacheKey, action.WorkingDirectory, cachePaths); err != nil {
			return post, fmt.Errorf("save cache %q: %w", cacheKey, err)
		}
		return post, nil
	})
	return result, nil
}

func handleCacheRestoreAction(_ context.Context, action backend.ActionContext) (backend.StepResult, error) {
	if _, err := resolveLocalCachePaths(action); err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	key, restoreKeys, lookupOnly, failOnMiss, err := resolveLocalCacheRestoreInputs(action)
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	result := backend.StepResult{ID: action.Step.ID}
	if strings.TrimSpace(key) != "" {
		result.Outputs = workflow.OutputMap{"cache-primary-key": key}
	}
	if action.Cache == nil || strings.TrimSpace(key) == "" {
		return result, nil
	}
	matchedKey, err := action.Cache.Match(key, restoreKeys)
	if err != nil {
		return result, fmt.Errorf("match cache %q: %w", key, err)
	}
	if matchedKey == "" {
		if failOnMiss {
			return result, fmt.Errorf("cache %q matched no entry", key)
		}
		return result, nil
	}
	result.Outputs["cache-hit"] = strconv.FormatBool(strings.TrimSpace(matchedKey) == strings.TrimSpace(key))
	result.Outputs["cache-matched-key"] = matchedKey
	if lookupOnly {
		return result, nil
	}
	if _, err := action.Cache.Restore(key, restoreKeys); err != nil {
		return result, fmt.Errorf("restore cache %q: %w", key, err)
	}
	return result, nil
}

func handleCacheSaveAction(_ context.Context, action backend.ActionContext) (backend.StepResult, error) {
	paths, err := resolveLocalCachePaths(action)
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	key, err := resolveLocalCacheKey(action)
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	result := backend.StepResult{ID: action.Step.ID}
	if action.Cache == nil || strings.TrimSpace(key) == "" {
		return result, nil
	}
	if err := action.Cache.Save(key, action.WorkingDirectory, paths); err != nil {
		return result, fmt.Errorf("save cache %q: %w", key, err)
	}
	return result, nil
}

func resolveLocalCachePaths(action backend.ActionContext) ([]string, error) {
	pathValue, err := resolveActionInput(action, "path")
	if err != nil {
		return nil, fmt.Errorf("resolve cache path: %w", err)
	}
	paths := splitMultilineValue(pathValue)
	for _, rawPath := range paths {
		if strings.HasPrefix(strings.TrimSpace(rawPath), "!") {
			continue
		}
		value := resolveWorkspacePath(action.WorkingDirectory, rawPath)
		if err := os.MkdirAll(value, 0o755); err != nil {
			return nil, fmt.Errorf("create cache path %q: %w", value, err)
		}
	}
	return paths, nil
}

func resolveLocalCacheKey(action backend.ActionContext) (string, error) {
	key, err := resolveActionInput(action, "key")
	if err != nil {
		return "", fmt.Errorf("resolve cache key: %w", err)
	}
	return key, nil
}

func resolveLocalCacheRestoreInputs(action backend.ActionContext) (string, []string, bool, bool, error) {
	key, err := resolveLocalCacheKey(action)
	if err != nil {
		return "", nil, false, false, err
	}
	restoreKeysValue, err := resolveActionInput(action, "restore-keys")
	if err != nil {
		return "", nil, false, false, fmt.Errorf("resolve restore-keys: %w", err)
	}
	lookupOnlyValue, err := resolveActionInput(action, "lookup-only")
	if err != nil {
		return "", nil, false, false, fmt.Errorf("resolve lookup-only: %w", err)
	}
	lookupOnly, err := parseBooleanInput(lookupOnlyValue, false)
	if err != nil {
		return "", nil, false, false, fmt.Errorf("parse lookup-only: %w", err)
	}
	failOnMissValue, err := resolveActionInput(action, "fail-on-cache-miss")
	if err != nil {
		return "", nil, false, false, fmt.Errorf("resolve fail-on-cache-miss: %w", err)
	}
	failOnMiss, err := parseBooleanInput(failOnMissValue, false)
	if err != nil {
		return "", nil, false, false, fmt.Errorf("parse fail-on-cache-miss: %w", err)
	}
	return key, splitMultilineValue(restoreKeysValue), lookupOnly, failOnMiss, nil
}

func handleUploadArtifactAction(_ context.Context, action backend.ActionContext) (backend.StepResult, error) {
	if action.Artifacts == nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("upload-artifact requires an artifact store")
	}
	name, err := resolveActionInput(action, "name")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve artifact name: %w", err)
	}
	pathsValue, err := resolveActionInput(action, "path")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve artifact path: %w", err)
	}
	ifNoFilesFound, err := resolveActionInput(action, "if-no-files-found")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve if-no-files-found: %w", err)
	}
	paths := splitMultilineValue(pathsValue)
	if len(paths) == 0 && strings.TrimSpace(pathsValue) != "" {
		paths = []string{pathsValue}
	}
	if err := action.Artifacts.Save(name, action.WorkingDirectory, paths, ifNoFilesFound); err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	return backend.StepResult{ID: action.Step.ID}, nil
}

func handleDownloadArtifactAction(_ context.Context, action backend.ActionContext) (backend.StepResult, error) {
	if action.Artifacts == nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("download-artifact requires an artifact store")
	}
	name, err := resolveActionInput(action, "name")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve artifact name: %w", err)
	}
	pathValue, err := resolveActionInput(action, "path")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve artifact path: %w", err)
	}
	destination := action.WorkingDirectory
	if strings.TrimSpace(pathValue) != "" {
		destination = resolveWorkspacePath(action.WorkingDirectory, pathValue)
	}
	if err := os.MkdirAll(destination, 0o755); err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("create artifact destination %q: %w", destination, err)
	}
	if err := action.Artifacts.Restore(name, destination); err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	return backend.StepResult{ID: action.Step.ID}, nil
}

func handleSetupPythonAction(_ context.Context, action backend.ActionContext) (backend.StepResult, error) {
	if err := rejectUnsupportedInputs(action, "setup-python", "python-version", "update-environment"); err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	versionValue, err := resolveActionInput(action, "python-version")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve python version: %w", err)
	}
	requestedVersions := resolveRequestedVersions(versionValue)
	updateEnvironmentValue, err := resolveActionInput(action, "update-environment")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve update-environment: %w", err)
	}
	updateEnvironment, err := parseBooleanInput(updateEnvironmentValue, true)
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("parse update-environment: %w", err)
	}
	executablePath, err := localLookPath(action.WorkingDirectory, action.Env, "python3")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("setup-python requires python3 on PATH: %w", err)
	}
	detected := localToolVersion(action.Env, executablePath, "--version")
	matchedVersion, err := matchingRequestedVersion(requestedVersions, detected)
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("setup-python requested one of [%s]: %w", strings.Join(requestedVersions, ", "), err)
	}
	aliasVersion := matchedVersion
	if aliasVersion == "" {
		aliasVersion = detected
	}
	alias, err := localToolCacheAlias(action, "Python", aliasVersion, installRootForExecutable(executablePath))
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	result := backend.StepResult{ID: action.Step.ID}
	if updateEnvironment {
		result.PathEntries = []string{alias.HostBin}
		result.Environment = workflow.EnvironmentMap{
			"pythonLocation": localToolCacheLocation(action, "Python", aliasVersion),
		}
	}
	result.Outputs = workflow.OutputMap{
		"cache-hit":      "false",
		"python-version": detectedVersionOutput(matchedVersion, detected),
		"python-path":    localToolExecutableOutputPath(alias, executablePath),
	}
	return result, nil
}

func handleCreatePullRequestAction(_ context.Context, action backend.ActionContext) (backend.StepResult, error) {
	if err := rejectUnsupportedInputs(action, "create-pull-request",
		"sign-commits",
		"branch",
		"delete-branch",
		"title",
		"commit-message",
		"body",
		"body-path",
		"base",
		"token",
	); err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	return backend.StepResult{ID: action.Step.ID}, nil
}

func handleSetupUVAction(ctx context.Context, action backend.ActionContext) (backend.StepResult, error) {
	if err := rejectUnsupportedInputs(action, "setup-uv", "activate-environment", "working-directory", "venv-path"); err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	installation, err := localToolInstallationInfo(action, "uv", "uv", "system")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("setup-uv requires uv on PATH: %w", err)
	}
	hostCache, _, cacheErr := localToolCacheDirectory(action, "uv-cache")
	if cacheErr != nil {
		return backend.StepResult{ID: action.Step.ID}, cacheErr
	}
	result := backend.StepResult{ID: action.Step.ID, PathEntries: []string{installation.Alias.HostBin}}
	result.Environment = workflow.EnvironmentMap{
		"UV_CACHE_DIR": hostCache,
	}
	result.Outputs = workflow.OutputMap{
		"cache-hit":        "false",
		"python-cache-hit": "false",
		"python-version":   localToolVersion(action.Env, "python3", "--version"),
		"uv-path":          localToolExecutableOutputPath(installation.Alias, installation.ExecutablePath),
		"uv-version":       localToolVersion(action.Env, installation.ExecutablePath, "--version"),
	}
	if uvxPath := optionalSiblingExecutablePath(action.WorkingDirectory, action.Env, installation.ExecutablePath, "uvx"); uvxPath != "" {
		result.Outputs["uvx-path"] = localToolExecutableOutputPath(installation.Alias, uvxPath)
	}
	activateValue, err := resolveActionInput(action, "activate-environment")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve activate-environment: %w", err)
	}
	activate, err := parseBooleanInput(activateValue, false)
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("parse activate-environment: %w", err)
	}
	if !activate {
		return result, nil
	}
	workingDirectoryValue, err := resolveActionInput(action, "working-directory")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve working-directory: %w", err)
	}
	workingDirectory := resolveWorkspacePath(action.WorkingDirectory, workingDirectoryValue)
	venvValue, err := resolveActionInput(action, "venv-path")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve venv-path: %w", err)
	}
	venvPath := filepath.Join(workingDirectory, ".venv")
	if strings.TrimSpace(venvValue) != "" {
		venvPath = resolveWorkspacePath(workingDirectory, venvValue)
	}
	cmd := exec.CommandContext(ctx, installation.ExecutablePath, "venv", "--clear", venvPath)
	cmd.Dir = workingDirectory
	cmd.Env = commandEnvironmentSlice(mergeOptionalEnvironment(action.Env, result.Environment))
	output, err := cmd.CombinedOutput()
	if err != nil {
		message := strings.TrimSpace(string(output))
		if message != "" {
			return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("create uv environment %q: %w\n%s", venvPath, err, message)
		}
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("create uv environment %q: %w", venvPath, err)
	}
	venvBin := filepath.Join(venvPath, venvBinDirectoryName(action.Expressions.Runner.OS))
	if _, err := os.Stat(venvBin); err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("uv venv did not create %q: %w", venvBin, err)
	}
	result.PathEntries = append([]string{venvBin}, result.PathEntries...)
	result.Environment["VIRTUAL_ENV"] = venvPath
	result.Outputs["venv"] = venvPath
	return result, nil
}

func localToolCacheAlias(action backend.ActionContext, family string, version string, targetRoot string) (toolCacheAlias, error) {
	root := localToolCacheRoot(action)
	return materializeToolCacheAlias(root, root, family, version, action.Expressions.Runner.Arch, targetRoot)
}

func localToolCacheDirectory(action backend.ActionContext, components ...string) (string, string, error) {
	root := localToolCacheRoot(action)
	return materializeToolCacheDirectory(root, root, components...)
}
