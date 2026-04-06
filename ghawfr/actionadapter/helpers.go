package actionadapter

import (
	"fmt"
	"os"
	"path"
	"path/filepath"
	"strings"

	"github.com/gkze/ghawfr/backend"
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

func mergeOptionalEnvironment(base workflow.EnvironmentMap, overlay workflow.EnvironmentMap) workflow.EnvironmentMap {
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

func remoteToolCacheLocation(action backend.ActionContext, guestWorkspace string, family string, version string) string {
	_, guest := remoteToolCacheRoots(action, guestWorkspace)
	return path.Join(
		guest,
		sanitizeToolCacheComponent(family),
		sanitizeToolCacheComponent(defaultToolCacheVersion(version)),
		sanitizeToolCacheComponent(defaultToolCacheArch(action.Expressions.Runner.Arch)),
	)
}

func remoteRunnerHomeRoots(action backend.ActionContext, guestWorkspace string) (string, string, error) {
	translatedEnv := translateWorkspaceEnvironment(action.Env, action.WorkingDirectory, guestWorkspace)
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

func resolveRemotePathWithBase(baseGuestDirectory string, rawPath string, hostWorkspace string, guestWorkspace string, guestHome string) (string, error) {
	trimmed := strings.TrimSpace(rawPath)
	if trimmed == "" || trimmed == "." {
		return baseGuestDirectory, nil
	}
	if expanded, ok := expandRemoteHomePath(trimmed, guestHome); ok {
		if strings.TrimSpace(guestHome) == "" {
			return "", fmt.Errorf("guest home is empty")
		}
		return expanded, nil
	}
	if filepath.IsAbs(trimmed) || strings.HasPrefix(trimmed, guestWorkspace+"/") || trimmed == guestWorkspace {
		return resolveRemoteActionPath(trimmed, hostWorkspace, guestWorkspace)
	}
	joined := path.Join(baseGuestDirectory, filepath.ToSlash(trimmed))
	allowedRoot := strings.TrimSpace(guestWorkspace)
	if guestHome != "" && (baseGuestDirectory == guestHome || strings.HasPrefix(baseGuestDirectory, guestHome+"/")) {
		allowedRoot = guestHome
	}
	if allowedRoot == "" {
		return joined, nil
	}
	if joined == allowedRoot || strings.HasPrefix(joined, allowedRoot+"/") {
		return joined, nil
	}
	return "", fmt.Errorf("path %q escapes guest root %q", rawPath, allowedRoot)
}

func translateWorkspaceEnvironment(values workflow.EnvironmentMap, hostWorkspace string, guestWorkspace string) workflow.EnvironmentMap {
	if len(values) == 0 || hostWorkspace == guestWorkspace {
		return values.Clone()
	}
	translated := values.Clone()
	for key, value := range translated {
		translated[key] = strings.ReplaceAll(value, hostWorkspace, guestWorkspace)
	}
	return translated
}

func shellQuote(value string) string {
	if value == "" {
		return "''"
	}
	return "'" + strings.ReplaceAll(value, "'", "'\"'\"'") + "'"
}

func remoteMkdirCommand(rawPath string, hostWorkspace string, guestWorkspace string) (string, error) {
	expression, err := remotePathShellExpression(rawPath, hostWorkspace, guestWorkspace)
	if err != nil {
		return "", err
	}
	return "mkdir -p -- " + expression, nil
}

func remotePathShellExpression(rawPath string, hostWorkspace string, guestWorkspace string) (string, error) {
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

func resolveRemoteActionPath(rawPath string, hostWorkspace string, guestWorkspace string) (string, error) {
	trimmed := strings.TrimSpace(rawPath)
	if trimmed == "" {
		return "", fmt.Errorf("path is empty")
	}
	if filepath.IsAbs(trimmed) {
		translated, err := tryTranslateAbsoluteWorkspacePath(trimmed, hostWorkspace, guestWorkspace)
		if err != nil {
			return "", err
		}
		return translated, nil
	}
	if strings.HasPrefix(trimmed, guestWorkspace+"/") || trimmed == guestWorkspace {
		return trimmed, nil
	}
	return joinWithinGuestWorkspace(guestWorkspace, filepath.ToSlash(trimmed))
}

func translateRemotePathToHost(rawPath string, hostWorkspace string, guestWorkspace string) (string, error) {
	trimmed := strings.TrimSpace(rawPath)
	if trimmed == "" || trimmed == "." {
		return hostWorkspace, nil
	}
	if trimmed == "~" || strings.HasPrefix(trimmed, "~/") {
		return "", fmt.Errorf("home-relative guest paths are not supported for host-managed artifact or checkout actions")
	}
	if filepath.IsAbs(trimmed) {
		if trimmed == guestWorkspace {
			return hostWorkspace, nil
		}
		prefix := guestWorkspace + "/"
		if strings.HasPrefix(trimmed, prefix) {
			rel := strings.TrimPrefix(trimmed, prefix)
			return joinWithinHostWorkspace(hostWorkspace, filepath.FromSlash(rel))
		}
		return "", fmt.Errorf("guest absolute path %q is outside guest workspace %q", trimmed, guestWorkspace)
	}
	return joinWithinHostWorkspace(hostWorkspace, filepath.FromSlash(trimmed))
}

func joinWithinGuestWorkspace(guestWorkspace string, value string) (string, error) {
	root := path.Clean(guestWorkspace)
	if root == "." || root == "" {
		root = "/"
	}
	joined := path.Join(root, value)
	if joined == root {
		return joined, nil
	}
	if strings.HasPrefix(joined, root+"/") {
		return joined, nil
	}
	return "", fmt.Errorf("path %q escapes guest workspace %q", value, guestWorkspace)
}

func joinWithinHostWorkspace(hostWorkspace string, value string) (string, error) {
	root, err := filepath.Abs(hostWorkspace)
	if err != nil {
		return "", fmt.Errorf("resolve host workspace %q: %w", hostWorkspace, err)
	}
	joined := filepath.Join(root, value)
	rel, err := filepath.Rel(root, joined)
	if err != nil {
		return "", fmt.Errorf("rel path from %q to %q: %w", root, joined, err)
	}
	if rel == "." {
		return root, nil
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("path %q escapes host workspace %q", value, root)
	}
	return joined, nil
}

func tryTranslateAbsoluteWorkspacePath(value string, hostWorkspace string, guestWorkspace string) (string, error) {
	workspace, err := filepath.Abs(hostWorkspace)
	if err != nil {
		return "", fmt.Errorf("resolve host workspace %q: %w", hostWorkspace, err)
	}
	absolute, err := filepath.Abs(value)
	if err != nil {
		return "", fmt.Errorf("resolve absolute path %q: %w", value, err)
	}
	rel, err := filepath.Rel(workspace, absolute)
	if err != nil {
		return "", fmt.Errorf("rel path from %q to %q: %w", workspace, absolute, err)
	}
	if rel == "." {
		return guestWorkspace, nil
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return absolute, nil
	}
	return path.Join(guestWorkspace, filepath.ToSlash(rel)), nil
}

func pathWithinWorkspace(workspace string, value string) (string, bool) {
	root, err := filepath.Abs(workspace)
	if err != nil {
		return "", false
	}
	absolute, err := filepath.Abs(value)
	if err != nil {
		return "", false
	}
	rel, err := filepath.Rel(root, absolute)
	if err != nil {
		return "", false
	}
	if rel == "." {
		return ".", true
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", false
	}
	return rel, true
}

func pathWithinGuestRoot(root string, value string) bool {
	root = path.Clean(strings.TrimSpace(root))
	value = path.Clean(strings.TrimSpace(value))
	if root == "" || value == "" {
		return false
	}
	return value == root || strings.HasPrefix(value, root+"/")
}

func pathWithinAnyGuestRoot(value string, roots ...string) bool {
	for _, root := range roots {
		if pathWithinGuestRoot(root, value) {
			return true
		}
	}
	return false
}
