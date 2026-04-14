package actionadapter

import (
	"fmt"
	"os"
	"path"
	"path/filepath"
	"strings"

	"github.com/gkze/ghawfr/backend"
	"github.com/gkze/ghawfr/internal/guestpath"
	"github.com/gkze/ghawfr/state"
	"github.com/gkze/ghawfr/workflow"
)

func resolveActionInput(action backend.ActionContext, key string) (string, error) {
	value, ok := action.Inputs[key]
	if !ok {
		return "", nil
	}
	return workflow.InterpolateString(action.Job, value, action.Expressions)
}

func parseBooleanInput(value string, defaultValue bool) (bool, error) {
	trimmed := strings.TrimSpace(value)
	if trimmed == "" {
		return defaultValue, nil
	}
	switch strings.ToLower(trimmed) {
	case "1", "true", "yes", "on":
		return true, nil
	case "0", "false", "no", "off":
		return false, nil
	default:
		return false, fmt.Errorf("invalid boolean value %q", value)
	}
}

func splitMultilineValue(value string) []string {
	trimmed := strings.TrimSpace(value)
	if trimmed == "" {
		return nil
	}
	parts := make([]string, 0)
	for _, item := range strings.Split(trimmed, "\n") {
		item = strings.TrimSpace(item)
		if item == "" {
			continue
		}
		parts = append(parts, item)
	}
	return parts
}

func resolveWorkspacePath(workspace string, rawPath string) string {
	trimmed := strings.TrimSpace(rawPath)
	if trimmed == "" {
		return workspace
	}
	if strings.HasPrefix(trimmed, "~/") || trimmed == "~" {
		home, err := os.UserHomeDir()
		if err == nil {
			if trimmed == "~" {
				return home
			}
			trimmed = filepath.Join(home, strings.TrimPrefix(trimmed, "~/"))
		}
	}
	if filepath.IsAbs(trimmed) {
		return trimmed
	}
	return filepath.Join(workspace, trimmed)
}

func installRootForExecutable(executablePath string) string {
	dir := filepath.Dir(executablePath)
	parent := filepath.Dir(dir)
	if parent == "" || parent == "." || parent == dir {
		return dir
	}
	return parent
}

func cloneOptionalEnvironment(values workflow.EnvironmentMap) workflow.EnvironmentMap {
	if len(values) == 0 {
		return nil
	}
	return values.Clone()
}

func mergeOptionalEnvironment(
	base workflow.EnvironmentMap,
	overlay workflow.EnvironmentMap,
) workflow.EnvironmentMap {
	if len(base) == 0 && len(overlay) == 0 {
		return nil
	}
	merged := base.Clone()
	if merged == nil {
		merged = make(workflow.EnvironmentMap)
	}
	for key, value := range overlay {
		merged[key] = value
	}
	return merged
}

func localToolCacheRoot(action backend.ActionContext) string {
	toolCache := action.Expressions.Runner.ToolCache
	if strings.TrimSpace(toolCache) == "" {
		toolCache = state.RunnerToolCacheDir(action.WorkingDirectory)
	}
	return toolCache
}

func localToolCacheLocation(action backend.ActionContext, family string, version string) string {
	return filepath.Join(
		localToolCacheRoot(action),
		sanitizeToolCacheComponent(family),
		sanitizeToolCacheComponent(defaultToolCacheVersion(version)),
		sanitizeToolCacheComponent(defaultToolCacheArch(action.Expressions.Runner.Arch)),
	)
}

func remoteToolCacheRoots(action backend.ActionContext, guestWorkspace string) (string, string) {
	host := state.RunnerToolCacheDir(action.WorkingDirectory)
	guest := action.Expressions.Runner.ToolCache
	if strings.TrimSpace(guest) == "" {
		guest = state.GuestRunnerToolCacheDir(guestWorkspace)
	}
	return host, guest
}

func remoteToolCacheLocation(
	action backend.ActionContext,
	guestWorkspace string,
	family string,
	version string,
) string {
	_, guest := remoteToolCacheRoots(action, guestWorkspace)
	return path.Join(
		guest,
		sanitizeToolCacheComponent(family),
		sanitizeToolCacheComponent(defaultToolCacheVersion(version)),
		sanitizeToolCacheComponent(defaultToolCacheArch(action.Expressions.Runner.Arch)),
	)
}

func remoteRunnerHomeRoots(
	action backend.ActionContext,
	guestWorkspace string,
) (string, string, error) {
	translatedEnv := translateWorkspaceEnvironment(
		action.Env,
		action.WorkingDirectory,
		guestWorkspace,
	)
	guest := strings.TrimSpace(translatedEnv["HOME"])
	if guest == "" {
		guest = strings.TrimSpace(action.Expressions.Runner.Home)
	}
	if guest == "" {
		guest = state.GuestRunnerHomeDir(guestWorkspace)
	}
	host, err := translateRemotePathToHost(guest, action.WorkingDirectory, guestWorkspace)
	if err != nil {
		return "", "", err
	}
	return host, guest, nil
}

func isRemoteHomeRelativePath(rawPath string) bool {
	trimmed := strings.TrimSpace(rawPath)
	return trimmed == "~" || strings.HasPrefix(trimmed, "~/")
}

func expandRemoteHomePath(rawPath string, guestHome string) (string, bool) {
	trimmed := strings.TrimSpace(rawPath)
	if trimmed == "~" {
		return guestHome, true
	}
	if strings.HasPrefix(trimmed, "~/") {
		return path.Join(guestHome, strings.TrimPrefix(trimmed, "~/")), true
	}
	return "", false
}

func venvBinDirectoryName(runnerOS string) string {
	if strings.EqualFold(strings.TrimSpace(runnerOS), "Windows") {
		return "Scripts"
	}
	return "bin"
}

func resolveRemotePathWithBase(
	baseGuestDirectory string,
	rawPath string,
	hostWorkspace string,
	guestWorkspace string,
	guestHome string,
) (string, error) {
	return guestpath.ResolvePathWithBase(
		baseGuestDirectory,
		rawPath,
		hostWorkspace,
		guestWorkspace,
		guestHome,
	)
}

func translateWorkspaceEnvironment(
	values workflow.EnvironmentMap,
	hostWorkspace string,
	guestWorkspace string,
) workflow.EnvironmentMap {
	return guestpath.TranslateEnvironment(values, hostWorkspace, guestWorkspace)
}

func shellQuote(value string) string {
	return guestpath.ShellQuote(value)
}

func remoteMkdirCommand(
	rawPath string,
	hostWorkspace string,
	guestWorkspace string,
) (string, error) {
	expression, err := remotePathShellExpression(rawPath, hostWorkspace, guestWorkspace)
	if err != nil {
		return "", err
	}
	return "mkdir -p -- " + expression, nil
}

func remotePathShellExpression(
	rawPath string,
	hostWorkspace string,
	guestWorkspace string,
) (string, error) {
	trimmed := strings.TrimSpace(rawPath)
	if trimmed == "" {
		return "", fmt.Errorf("path is empty")
	}
	if trimmed == "~" {
		return "$HOME", nil
	}
	if strings.HasPrefix(trimmed, "~/") {
		suffix := strings.TrimPrefix(trimmed, "~")
		return "$HOME" + shellQuote(suffix), nil
	}
	resolved, err := resolveRemoteActionPath(trimmed, hostWorkspace, guestWorkspace)
	if err != nil {
		return "", err
	}
	return shellQuote(resolved), nil
}

func resolveRemoteActionPath(
	rawPath string,
	hostWorkspace string,
	guestWorkspace string,
) (string, error) {
	return guestpath.ResolveActionPath(rawPath, hostWorkspace, guestWorkspace)
}

func translateRemotePathToHost(
	rawPath string,
	hostWorkspace string,
	guestWorkspace string,
) (string, error) {
	return guestpath.TranslateToHost(rawPath, hostWorkspace, guestWorkspace)
}

func pathWithinWorkspace(workspace string, value string) (string, bool) {
	return guestpath.PathWithinWorkspace(workspace, value)
}

func pathWithinGuestRoot(root string, value string) bool {
	return guestpath.WithinGuestRoot(root, value)
}

func pathWithinAnyGuestRoot(value string, roots ...string) bool {
	return guestpath.WithinAnyGuestRoot(value, roots...)
}
