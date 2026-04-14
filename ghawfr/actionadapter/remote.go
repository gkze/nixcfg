package actionadapter

import (
	"context"
	"fmt"
	"os"
	"path"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/gkze/ghawfr/backend"
	"github.com/gkze/ghawfr/workflow"
)

type remoteHandlers struct {
	guestWorkspace string
	commands       backend.CommandTransport
}

// NewRemoteExec returns a generic remote-exec worker wired with the curated
// action adapters from this package.
func NewRemoteExec(
	runnerLabels []string,
	guestWorkspace string,
	commands backend.CommandTransport,
) backend.RemoteExecWorker {
	return backend.RemoteExecWorker{
		RunnerLabels:   append([]string(nil), runnerLabels...),
		GuestWorkspace: guestWorkspace,
		Commands:       commands,
		ActionHandlers: RemoteHandlers(guestWorkspace, commands),
	}
}

// RemoteHandlers returns the curated remote action-handler registry.
func RemoteHandlers(
	guestWorkspace string,
	commands backend.CommandTransport,
) map[string]backend.ActionHandler {
	adapter := remoteHandlers{guestWorkspace: guestWorkspace, commands: commands}
	return buildCuratedActionHandlers(curatedActionHandlerSet{
		checkout:       backend.ActionHandlerFunc(adapter.handleCheckoutAction),
		determinateNix: backend.ActionHandlerFunc(adapter.handleDeterminateNixAction),
		cachix:         backend.ActionHandlerFunc(adapter.handleCachixAction),
		cache:          backend.ActionHandlerFunc(adapter.handleCacheAction),
		cacheRestore:   backend.ActionHandlerFunc(adapter.handleCacheRestoreAction),
		cacheSave:      backend.ActionHandlerFunc(adapter.handleCacheSaveAction),
		uploadArtifact: backend.ActionHandlerFunc(adapter.handleUploadArtifactAction),
		downloadArtifact: backend.ActionHandlerFunc(
			adapter.handleDownloadArtifactAction,
		),
		setupPython: backend.ActionHandlerFunc(adapter.handleSetupPythonAction),
		setupUV:     backend.ActionHandlerFunc(adapter.handleSetupUVAction),
		createPullRequest: backend.ActionHandlerFunc(
			handleAcceptedCreatePullRequestAction,
		),
	})
}

func (r remoteHandlers) handleCheckoutAction(
	_ context.Context,
	action backend.ActionContext,
) (backend.StepResult, error) {
	pathValue, err := r.resolveRemoteActionInput(action, "path")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve checkout path: %w", err)
	}
	workspace := action.WorkingDirectory
	if strings.TrimSpace(pathValue) != "" {
		workspace, err = translateRemotePathToHost(
			pathValue,
			action.WorkingDirectory,
			r.guestWorkspace,
		)
		if err != nil {
			return backend.StepResult{
					ID: action.Step.ID,
				}, fmt.Errorf(
					"translate checkout path %q: %w",
					pathValue,
					err,
				)
		}
	}
	if _, err := os.Stat(filepath.Join(workspace, ".git")); err != nil {
		return backend.StepResult{
				ID: action.Step.ID,
			}, fmt.Errorf(
				"checkout workspace %q is not a git repository: %w",
				workspace,
				err,
			)
	}
	return backend.StepResult{ID: action.Step.ID}, nil
}

func (r remoteHandlers) handleDeterminateNixAction(
	ctx context.Context,
	action backend.ActionContext,
) (backend.StepResult, error) {
	result, err := r.remoteToolSetupResult(ctx, action, "nix", "nix", "system", nil)
	result.ID = action.Step.ID
	if err != nil {
		return result, fmt.Errorf("determinate-nix-action requires nix in guest PATH: %w", err)
	}
	return result, nil
}

func (r remoteHandlers) handleCachixAction(
	ctx context.Context,
	action backend.ActionContext,
) (backend.StepResult, error) {
	name, err := r.resolveRemoteActionInput(action, "name")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve cachix name: %w", err)
	}
	authToken, err := r.resolveRemoteActionInput(action, "authToken")
	if err != nil {
		return backend.StepResult{
				ID: action.Step.ID,
			}, fmt.Errorf(
				"resolve cachix auth token: %w",
				err,
			)
	}
	environment := workflow.EnvironmentMap{}
	if strings.TrimSpace(name) != "" {
		environment["CACHIX_NAME"] = name
	}
	if strings.TrimSpace(authToken) != "" {
		environment["CACHIX_AUTH_TOKEN"] = authToken
	}
	result, err := r.remoteToolSetupResult(ctx, action, "cachix", "cachix", "system", environment)
	result.ID = action.Step.ID
	if err != nil {
		return result, fmt.Errorf("cachix-action requires cachix in guest PATH: %w", err)
	}
	return result, nil
}

func (r remoteHandlers) handleCacheAction(
	ctx context.Context,
	action backend.ActionContext,
) (backend.StepResult, error) {
	_, hostSpecs, err := r.resolveRemoteCachePaths(ctx, action)
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	key, restoreKeys, lookupOnly, failOnMiss, err := r.resolveRemoteCacheRestoreInputs(action)
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
		result.Outputs = workflow.OutputMap{
			"cache-hit": strconv.FormatBool(
				strings.TrimSpace(matchedKey) == strings.TrimSpace(key),
			),
		}
		if !lookupOnly {
			if _, err := action.Cache.Restore(key, restoreKeys); err != nil {
				return result, fmt.Errorf("restore cache %q: %w", key, err)
			}
		}
	}
	cacheKey := key
	cachePaths := append([]string(nil), hostSpecs...)
	result.Post = backend.PostStepFunc(
		func(ctx context.Context, status backend.JobStatus) (backend.StepResult, error) {
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
		},
	)
	return result, nil
}

func (r remoteHandlers) handleCacheRestoreAction(
	ctx context.Context,
	action backend.ActionContext,
) (backend.StepResult, error) {
	if _, _, err := r.resolveRemoteCachePaths(ctx, action); err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	key, restoreKeys, lookupOnly, failOnMiss, err := r.resolveRemoteCacheRestoreInputs(action)
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
	result.Outputs["cache-hit"] = strconv.FormatBool(
		strings.TrimSpace(matchedKey) == strings.TrimSpace(key),
	)
	result.Outputs["cache-matched-key"] = matchedKey
	if lookupOnly {
		return result, nil
	}
	if _, err := action.Cache.Restore(key, restoreKeys); err != nil {
		return result, fmt.Errorf("restore cache %q: %w", key, err)
	}
	return result, nil
}

func (r remoteHandlers) handleCacheSaveAction(
	ctx context.Context,
	action backend.ActionContext,
) (backend.StepResult, error) {
	_, hostSpecs, err := r.resolveRemoteCachePaths(ctx, action)
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	key, err := r.resolveRemoteCacheKey(action)
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	result := backend.StepResult{ID: action.Step.ID}
	if action.Cache == nil || strings.TrimSpace(key) == "" {
		return result, nil
	}
	if err := action.Cache.Save(key, action.WorkingDirectory, hostSpecs); err != nil {
		return result, fmt.Errorf("save cache %q: %w", key, err)
	}
	return result, nil
}

func (r remoteHandlers) resolveRemoteCachePaths(
	ctx context.Context,
	action backend.ActionContext,
) ([]string, []string, error) {
	pathValue, err := r.resolveRemoteActionInput(action, "path")
	if err != nil {
		return nil, nil, fmt.Errorf("resolve cache path: %w", err)
	}
	paths := splitMultilineValue(pathValue)
	hostSpecs, err := r.remoteCacheHostPathSpecs(action, paths)
	if err != nil {
		return nil, nil, err
	}
	remoteExpr := r.remoteActionExpressions(action)
	for _, rawPath := range paths {
		if strings.HasPrefix(strings.TrimSpace(rawPath), "!") {
			continue
		}
		command, err := remoteMkdirCommand(rawPath, action.WorkingDirectory, r.guestWorkspace)
		if err != nil {
			return nil, nil, fmt.Errorf("resolve remote cache path %q: %w", rawPath, err)
		}
		if err := r.execRemoteActionCommand(ctx, remoteExpr.Env, command, "cache path"); err != nil {
			return nil, nil, err
		}
	}
	return paths, hostSpecs, nil
}

func (r remoteHandlers) resolveRemoteCacheKey(action backend.ActionContext) (string, error) {
	key, err := r.resolveRemoteActionInput(action, "key")
	if err != nil {
		return "", fmt.Errorf("resolve cache key: %w", err)
	}
	return key, nil
}

func (r remoteHandlers) resolveRemoteCacheRestoreInputs(
	action backend.ActionContext,
) (string, []string, bool, bool, error) {
	key, err := r.resolveRemoteCacheKey(action)
	if err != nil {
		return "", nil, false, false, err
	}
	restoreKeysValue, err := r.resolveRemoteActionInput(action, "restore-keys")
	if err != nil {
		return "", nil, false, false, fmt.Errorf("resolve restore-keys: %w", err)
	}
	lookupOnlyValue, err := r.resolveRemoteActionInput(action, "lookup-only")
	if err != nil {
		return "", nil, false, false, fmt.Errorf("resolve lookup-only: %w", err)
	}
	lookupOnly, err := parseBooleanInput(lookupOnlyValue, false)
	if err != nil {
		return "", nil, false, false, fmt.Errorf("parse lookup-only: %w", err)
	}
	failOnMissValue, err := r.resolveRemoteActionInput(action, "fail-on-cache-miss")
	if err != nil {
		return "", nil, false, false, fmt.Errorf("resolve fail-on-cache-miss: %w", err)
	}
	failOnMiss, err := parseBooleanInput(failOnMissValue, false)
	if err != nil {
		return "", nil, false, false, fmt.Errorf("parse fail-on-cache-miss: %w", err)
	}
	return key, splitMultilineValue(restoreKeysValue), lookupOnly, failOnMiss, nil
}

func (r remoteHandlers) handleUploadArtifactAction(
	_ context.Context,
	action backend.ActionContext,
) (backend.StepResult, error) {
	if action.Artifacts == nil {
		return backend.StepResult{
				ID: action.Step.ID,
			}, fmt.Errorf(
				"upload-artifact requires an artifact store",
			)
	}
	name, err := r.resolveRemoteActionInput(action, "name")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve artifact name: %w", err)
	}
	pathsValue, err := r.resolveRemoteActionInput(action, "path")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve artifact path: %w", err)
	}
	ifNoFilesFound, err := r.resolveRemoteActionInput(action, "if-no-files-found")
	if err != nil {
		return backend.StepResult{
				ID: action.Step.ID,
			}, fmt.Errorf(
				"resolve if-no-files-found: %w",
				err,
			)
	}
	translated := make([]string, 0)
	for _, rawPath := range splitMultilineValue(pathsValue) {
		value, err := translateRemotePathToHost(rawPath, action.WorkingDirectory, r.guestWorkspace)
		if err != nil {
			return backend.StepResult{
					ID: action.Step.ID,
				}, fmt.Errorf(
					"translate artifact path %q: %w",
					rawPath,
					err,
				)
		}
		if relative, ok := pathWithinWorkspace(action.WorkingDirectory, value); ok {
			value = relative
		}
		translated = append(translated, value)
	}
	if err := action.Artifacts.Save(
		name,
		action.WorkingDirectory,
		translated,
		ifNoFilesFound,
	); err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	return backend.StepResult{ID: action.Step.ID}, nil
}

func (r remoteHandlers) handleDownloadArtifactAction(
	_ context.Context,
	action backend.ActionContext,
) (backend.StepResult, error) {
	if action.Artifacts == nil {
		return backend.StepResult{
				ID: action.Step.ID,
			}, fmt.Errorf(
				"download-artifact requires an artifact store",
			)
	}
	name, err := r.resolveRemoteActionInput(action, "name")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve artifact name: %w", err)
	}
	pathValue, err := r.resolveRemoteActionInput(action, "path")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve artifact path: %w", err)
	}
	destination := action.WorkingDirectory
	if strings.TrimSpace(pathValue) != "" {
		destination, err = translateRemotePathToHost(
			pathValue,
			action.WorkingDirectory,
			r.guestWorkspace,
		)
		if err != nil {
			return backend.StepResult{
					ID: action.Step.ID,
				}, fmt.Errorf(
					"translate artifact destination %q: %w",
					pathValue,
					err,
				)
		}
	}
	if err := os.MkdirAll(destination, 0o755); err != nil {
		return backend.StepResult{
				ID: action.Step.ID,
			}, fmt.Errorf(
				"create artifact destination %q: %w",
				destination,
				err,
			)
	}
	if err := action.Artifacts.Restore(name, destination); err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	return backend.StepResult{ID: action.Step.ID}, nil
}

func (r remoteHandlers) handleSetupPythonAction(
	ctx context.Context,
	action backend.ActionContext,
) (backend.StepResult, error) {
	if err := rejectUnsupportedInputs(
		action,
		"setup-python",
		"python-version",
		"update-environment",
	); err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	versionValue, err := r.resolveRemoteActionInput(action, "python-version")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve python version: %w", err)
	}
	requestedVersions := resolveRequestedVersions(versionValue)
	updateEnvironmentValue, err := r.resolveRemoteActionInput(action, "update-environment")
	if err != nil {
		return backend.StepResult{
				ID: action.Step.ID,
			}, fmt.Errorf(
				"resolve update-environment: %w",
				err,
			)
	}
	updateEnvironment, err := parseBooleanInput(updateEnvironmentValue, true)
	if err != nil {
		return backend.StepResult{
				ID: action.Step.ID,
			}, fmt.Errorf(
				"parse update-environment: %w",
				err,
			)
	}
	executablePath, err := r.lookupRemoteTool(ctx, action, "python3")
	if err != nil {
		return backend.StepResult{
				ID: action.Step.ID,
			}, fmt.Errorf(
				"setup-python requires python3 in guest PATH: %w",
				err,
			)
	}
	detected := r.remoteToolVersion(ctx, action, executablePath, "--version")
	matchedVersion, err := matchingRequestedVersion(requestedVersions, detected)
	if err != nil {
		return backend.StepResult{
				ID: action.Step.ID,
			}, fmt.Errorf(
				"setup-python requested one of [%s]: %w",
				strings.Join(requestedVersions, ", "),
				err,
			)
	}
	aliasVersion := matchedVersion
	if aliasVersion == "" {
		aliasVersion = detected
	}
	alias, err := r.remoteToolCacheAlias(
		action,
		"Python",
		aliasVersion,
		installRootForExecutable(executablePath),
	)
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	result := backend.StepResult{ID: action.Step.ID}
	if updateEnvironment {
		result.PathEntries = []string{alias.GuestBin}
		result.Environment = workflow.EnvironmentMap{
			"pythonLocation": remoteToolCacheLocation(
				action,
				r.guestWorkspace,
				"Python",
				aliasVersion,
			),
		}
	}
	result.Outputs = workflow.OutputMap{
		"cache-hit":      "false",
		"python-version": detectedVersionOutput(matchedVersion, detected),
		"python-path":    remoteToolExecutableOutputPath(alias, executablePath),
	}
	return result, nil
}

func (r remoteHandlers) handleSetupUVAction(
	ctx context.Context,
	action backend.ActionContext,
) (backend.StepResult, error) {
	if err := rejectUnsupportedInputs(
		action,
		"setup-uv",
		"activate-environment",
		"working-directory",
		"venv-path",
	); err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	installation, err := remoteToolInstallationInfo(ctx, r, action, "uv", "uv", "system")
	if err != nil {
		return backend.StepResult{
				ID: action.Step.ID,
			}, fmt.Errorf(
				"setup-uv requires uv in guest PATH: %w",
				err,
			)
	}
	_, guestCache, cacheErr := r.remoteToolCacheDirectory(action, "uv-cache")
	if cacheErr != nil {
		return backend.StepResult{ID: action.Step.ID}, cacheErr
	}
	result := backend.StepResult{
		ID:          action.Step.ID,
		PathEntries: []string{installation.Alias.GuestBin},
	}
	result.Environment = workflow.EnvironmentMap{
		"UV_CACHE_DIR": guestCache,
	}
	result.Outputs = workflow.OutputMap{
		"cache-hit":        "false",
		"python-cache-hit": "false",
		"python-version":   r.remoteToolVersion(ctx, action, "python3", "--version"),
		"uv-path": remoteToolExecutableOutputPath(
			installation.Alias,
			installation.ExecutablePath,
		),
		"uv-version": r.remoteToolVersion(
			ctx,
			action,
			installation.ExecutablePath,
			"--version",
		),
	}
	if uvxPath, err := r.lookupRemoteTool(ctx, action, "uvx"); err == nil {
		result.Outputs["uvx-path"] = remoteToolExecutableOutputPath(installation.Alias, uvxPath)
	}
	activateValue, err := r.resolveRemoteActionInput(action, "activate-environment")
	if err != nil {
		return backend.StepResult{
				ID: action.Step.ID,
			}, fmt.Errorf(
				"resolve activate-environment: %w",
				err,
			)
	}
	activate, err := parseBooleanInput(activateValue, false)
	if err != nil {
		return backend.StepResult{
				ID: action.Step.ID,
			}, fmt.Errorf(
				"parse activate-environment: %w",
				err,
			)
	}
	if !activate {
		return result, nil
	}
	guestHome := ""
	ensureGuestHome := func() error {
		if strings.TrimSpace(guestHome) != "" {
			return nil
		}
		_, resolvedGuestHome, err := remoteRunnerHomeRoots(action, r.guestWorkspace)
		if err != nil {
			return err
		}
		guestHome = resolvedGuestHome
		return nil
	}
	allowedRoots := func() []string {
		roots := []string{r.guestWorkspace}
		if strings.TrimSpace(guestHome) != "" {
			roots = append(roots, guestHome)
		}
		return roots
	}
	ensureGuestAbsolutePathAllowed := func(name string, value string) error {
		trimmed := strings.TrimSpace(value)
		if !path.IsAbs(trimmed) {
			return nil
		}
		cleaned := path.Clean(trimmed)
		if pathWithinGuestRoot(r.guestWorkspace, cleaned) {
			return nil
		}
		if strings.TrimSpace(guestHome) == "" {
			if err := ensureGuestHome(); err != nil &&
				!pathWithinGuestRoot(r.guestWorkspace, cleaned) {
				return fmt.Errorf("resolve guest home: %w", err)
			}
		}
		if pathWithinAnyGuestRoot(cleaned, allowedRoots()...) {
			return nil
		}
		return fmt.Errorf("%s %q is outside allowed guest roots %v", name, value, allowedRoots())
	}
	workingDirectoryValue, err := r.resolveRemoteActionInput(action, "working-directory")
	if err != nil {
		return backend.StepResult{
				ID: action.Step.ID,
			}, fmt.Errorf(
				"resolve working-directory: %w",
				err,
			)
	}
	if isRemoteHomeRelativePath(workingDirectoryValue) {
		if err := ensureGuestHome(); err != nil {
			return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve guest home: %w", err)
		}
	}
	if err := ensureGuestAbsolutePathAllowed("working-directory", workingDirectoryValue); err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	guestWorkingDirectory, err := resolveRemotePathWithBase(
		r.guestWorkspace,
		workingDirectoryValue,
		action.WorkingDirectory,
		r.guestWorkspace,
		guestHome,
	)
	if err != nil {
		return backend.StepResult{
				ID: action.Step.ID,
			}, fmt.Errorf(
				"resolve guest working-directory: %w",
				err,
			)
	}
	if !pathWithinAnyGuestRoot(guestWorkingDirectory, allowedRoots()...) {
		return backend.StepResult{
				ID: action.Step.ID,
			}, fmt.Errorf(
				"guest working-directory %q escapes allowed roots %v",
				guestWorkingDirectory,
				allowedRoots(),
			)
	}
	venvValue, err := r.resolveRemoteActionInput(action, "venv-path")
	if err != nil {
		return backend.StepResult{ID: action.Step.ID}, fmt.Errorf("resolve venv-path: %w", err)
	}
	guestVenvPath := path.Join(guestWorkingDirectory, ".venv")
	if strings.TrimSpace(venvValue) != "" {
		if isRemoteHomeRelativePath(venvValue) && strings.TrimSpace(guestHome) == "" {
			if err := ensureGuestHome(); err != nil {
				return backend.StepResult{
						ID: action.Step.ID,
					}, fmt.Errorf(
						"resolve guest home: %w",
						err,
					)
			}
		}
		if err := ensureGuestAbsolutePathAllowed("venv-path", venvValue); err != nil {
			return backend.StepResult{ID: action.Step.ID}, err
		}
		guestVenvPath, err = resolveRemotePathWithBase(
			guestWorkingDirectory,
			venvValue,
			action.WorkingDirectory,
			r.guestWorkspace,
			guestHome,
		)
		if err != nil {
			return backend.StepResult{
					ID: action.Step.ID,
				}, fmt.Errorf(
					"resolve guest venv-path: %w",
					err,
				)
		}
	}
	hostVenvPath, err := translateRemotePathToHost(
		guestVenvPath,
		action.WorkingDirectory,
		r.guestWorkspace,
	)
	if err != nil {
		return backend.StepResult{
				ID: action.Step.ID,
			}, fmt.Errorf(
				"translate guest venv-path %q to host workspace: %w",
				guestVenvPath,
				err,
			)
	}
	command := strings.Join(
		[]string{
			shellQuote(installation.ExecutablePath),
			"venv",
			"--clear",
			shellQuote(guestVenvPath),
		},
		" ",
	)
	if err := r.execRemoteActionCommandInDir(
		ctx,
		guestWorkingDirectory,
		mergeOptionalEnvironment(
			r.remoteActionExpressions(action).Env,
			result.Environment,
		),
		command,
		"create uv environment",
	); err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	venvBin := venvBinDirectoryName(action.Expressions.Runner.OS)
	if _, err := os.Stat(filepath.Join(hostVenvPath, venvBin)); err != nil {
		return backend.StepResult{
				ID: action.Step.ID,
			}, fmt.Errorf(
				"uv venv did not create %q: %w",
				filepath.Join(hostVenvPath, venvBin),
				err,
			)
	}
	result.PathEntries = append([]string{path.Join(guestVenvPath, venvBin)}, result.PathEntries...)
	result.Environment["VIRTUAL_ENV"] = guestVenvPath
	result.Outputs["venv"] = guestVenvPath
	return result, nil
}

func (r remoteHandlers) remoteCacheHostPathSpecs(
	action backend.ActionContext,
	paths []string,
) ([]string, error) {
	guestHome := ""
	specs := make([]string, 0, len(paths))
	for _, rawPath := range paths {
		trimmed := strings.TrimSpace(rawPath)
		if trimmed == "" {
			continue
		}
		exclude := strings.HasPrefix(trimmed, "!")
		value := strings.TrimSpace(strings.TrimPrefix(trimmed, "!"))
		translated := ""
		if isRemoteHomeRelativePath(value) {
			if strings.TrimSpace(guestHome) == "" {
				var err error
				if _, guestHome, err = remoteRunnerHomeRoots(action, r.guestWorkspace); err != nil {
					return nil, fmt.Errorf("resolve remote runner home: %w", err)
				}
			}
			expanded, _ := expandRemoteHomePath(value, guestHome)
			var err error
			translated, err = translateRemotePathToHost(
				expanded,
				action.WorkingDirectory,
				r.guestWorkspace,
			)
			if err != nil {
				return nil, fmt.Errorf(
					"remote cache path %q is not persistable via HOME %q: %w",
					value,
					guestHome,
					err,
				)
			}
		} else {
			var err error
			translated, err = translateRemotePathToHost(value, action.WorkingDirectory, r.guestWorkspace)
			if err != nil {
				return nil, fmt.Errorf("remote cache path %q is not persistable: %w", value, err)
			}
		}
		if relative, ok := pathWithinWorkspace(action.WorkingDirectory, translated); ok {
			translated = relative
		}
		if exclude {
			translated = "!" + translated
		}
		specs = append(specs, translated)
	}
	return specs, nil
}

func (r remoteHandlers) remoteActionExpressions(
	action backend.ActionContext,
) workflow.ExpressionContext {
	remoteExpr := action.Expressions
	remoteExpr.GitHub.Workspace = r.guestWorkspace
	remoteExpr.Env = translateWorkspaceEnvironment(
		action.Env,
		action.WorkingDirectory,
		r.guestWorkspace,
	)
	return remoteExpr
}

func (r remoteHandlers) resolveRemoteActionInput(
	action backend.ActionContext,
	key string,
) (string, error) {
	value, ok := action.Inputs[key]
	if !ok {
		return "", nil
	}
	return workflow.InterpolateString(action.Job, value, r.remoteActionExpressions(action))
}

func (r remoteHandlers) execRemoteActionCommand(
	ctx context.Context,
	environment workflow.EnvironmentMap,
	command string,
	description string,
) error {
	return r.execRemoteActionCommandInDir(ctx, r.guestWorkspace, environment, command, description)
}

func (r remoteHandlers) execRemoteActionCommandInDir(
	ctx context.Context,
	workingDirectory string,
	environment workflow.EnvironmentMap,
	command string,
	description string,
) error {
	_, err := r.execRemoteActionOutputInDir(
		ctx,
		workingDirectory,
		environment,
		command,
		description,
	)
	return err
}

func (r remoteHandlers) execRemoteActionOutput(
	ctx context.Context,
	environment workflow.EnvironmentMap,
	command string,
	description string,
) (string, error) {
	return r.execRemoteActionOutputInDir(ctx, r.guestWorkspace, environment, command, description)
}

func (r remoteHandlers) execRemoteActionOutputInDir(
	ctx context.Context,
	workingDirectory string,
	environment workflow.EnvironmentMap,
	command string,
	description string,
) (string, error) {
	result, err := r.commands.ExecCommand(ctx, workingDirectory, environment, command)
	if err != nil {
		return "", fmt.Errorf("%s: %w", description, err)
	}
	if result.ExitCode != 0 {
		combined := strings.TrimSpace(result.Stdout + result.Stderr)
		if combined != "" {
			return "", fmt.Errorf("%s: exit code %d\n%s", description, result.ExitCode, combined)
		}
		return "", fmt.Errorf("%s: exit code %d", description, result.ExitCode)
	}
	return strings.TrimSpace(result.Stdout), nil
}

func normalizeRemoteToolPath(workingDirectory string, rawPath string) (string, error) {
	trimmed := strings.TrimSpace(rawPath)
	if trimmed == "" {
		return "", fmt.Errorf("tool lookup returned empty path")
	}
	if strings.ContainsAny(trimmed, "\r\n\t ") {
		return "", fmt.Errorf("tool lookup returned invalid path %q", rawPath)
	}
	if path.IsAbs(trimmed) {
		return path.Clean(trimmed), nil
	}
	if !strings.Contains(trimmed, "/") {
		return "", fmt.Errorf("tool lookup returned non-path result %q", rawPath)
	}
	return path.Clean(path.Join(workingDirectory, trimmed)), nil
}

func (r remoteHandlers) lookupRemoteTool(
	ctx context.Context,
	action backend.ActionContext,
	tool string,
) (string, error) {
	value, err := r.execRemoteActionOutput(
		ctx,
		r.remoteActionExpressions(action).Env,
		"command -v "+tool,
		tool+" lookup",
	)
	if err != nil {
		return "", err
	}
	return normalizeRemoteToolPath(r.guestWorkspace, value)
}

func (r remoteHandlers) remoteToolCacheAlias(
	action backend.ActionContext,
	family string,
	version string,
	targetRoot string,
) (toolCacheAlias, error) {
	hostRoot, guestRoot := remoteToolCacheRoots(action, r.guestWorkspace)
	return materializeToolCacheAlias(
		hostRoot,
		guestRoot,
		family,
		version,
		action.Expressions.Runner.Arch,
		targetRoot,
	)
}

func (r remoteHandlers) remoteToolCacheDirectory(
	action backend.ActionContext,
	components ...string,
) (string, string, error) {
	hostRoot, guestRoot := remoteToolCacheRoots(action, r.guestWorkspace)
	return materializeToolCacheDirectory(hostRoot, guestRoot, components...)
}
