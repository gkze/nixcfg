package guestpath

import (
	"fmt"
	"path"
	"path/filepath"
	"strings"

	"github.com/gkze/ghawfr/workflow"
)

func TranslateEnvironment(
	values workflow.EnvironmentMap,
	hostWorkspace string,
	guestWorkspace string,
) workflow.EnvironmentMap {
	if len(values) == 0 || hostWorkspace == guestWorkspace {
		return values.Clone()
	}
	translated := values.Clone()
	for key, value := range translated {
		translated[key] = strings.ReplaceAll(value, hostWorkspace, guestWorkspace)
	}
	return translated
}

func ShellQuote(value string) string {
	if value == "" {
		return "''"
	}
	return "'" + strings.ReplaceAll(value, "'", "'\"'\"'") + "'"
}

func ResolveActionPath(
	rawPath string,
	hostWorkspace string,
	guestWorkspace string,
) (string, error) {
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

func TranslateToHost(
	rawPath string,
	hostWorkspace string,
	guestWorkspace string,
) (string, error) {
	trimmed := strings.TrimSpace(rawPath)
	if trimmed == "" || trimmed == "." {
		return hostWorkspace, nil
	}
	if trimmed == "~" || strings.HasPrefix(trimmed, "~/") {
		return "", fmt.Errorf(
			"home-relative guest paths are not supported for host-managed artifact or checkout actions",
		)
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
		return "", fmt.Errorf(
			"guest absolute path %q is outside guest workspace %q",
			trimmed,
			guestWorkspace,
		)
	}
	return joinWithinHostWorkspace(hostWorkspace, filepath.FromSlash(trimmed))
}

func TranslateHostPath(
	hostWorkspace string,
	guestWorkspace string,
	hostPath string,
) (string, error) {
	workspace, err := filepath.Abs(hostWorkspace)
	if err != nil {
		return "", fmt.Errorf("resolve host workspace %q: %w", hostWorkspace, err)
	}
	value, err := filepath.Abs(hostPath)
	if err != nil {
		return "", fmt.Errorf("resolve host path %q: %w", hostPath, err)
	}
	rel, err := filepath.Rel(workspace, value)
	if err != nil {
		return "", fmt.Errorf("rel path from %q to %q: %w", workspace, value, err)
	}
	if rel == "." {
		return guestWorkspace, nil
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("host path %q is outside workspace %q", value, workspace)
	}
	return path.Join(guestWorkspace, filepath.ToSlash(rel)), nil
}

func ResolvePathWithBase(
	baseGuestDirectory string,
	rawPath string,
	hostWorkspace string,
	guestWorkspace string,
	guestHome string,
) (string, error) {
	trimmed := strings.TrimSpace(rawPath)
	if trimmed == "" || trimmed == "." {
		return baseGuestDirectory, nil
	}
	if expanded, ok := expandHomePath(trimmed, guestHome); ok {
		if strings.TrimSpace(guestHome) == "" {
			return "", fmt.Errorf("guest home is empty")
		}
		return expanded, nil
	}
	if filepath.IsAbs(trimmed) || strings.HasPrefix(trimmed, guestWorkspace+"/") ||
		trimmed == guestWorkspace {
		return ResolveActionPath(trimmed, hostWorkspace, guestWorkspace)
	}
	joined := path.Join(baseGuestDirectory, filepath.ToSlash(trimmed))
	allowedRoot := strings.TrimSpace(guestWorkspace)
	if guestHome != "" &&
		(baseGuestDirectory == guestHome || strings.HasPrefix(baseGuestDirectory, guestHome+"/")) {
		allowedRoot = guestHome
	}
	if allowedRoot == "" {
		return joined, nil
	}
	if WithinGuestRoot(allowedRoot, joined) {
		return joined, nil
	}
	return "", fmt.Errorf("path %q escapes guest root %q", rawPath, allowedRoot)
}

func PathWithinWorkspace(workspace string, value string) (string, bool) {
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

func WithinGuestRoot(root string, value string) bool {
	root = path.Clean(strings.TrimSpace(root))
	value = path.Clean(strings.TrimSpace(value))
	if root == "" || value == "" {
		return false
	}
	return value == root || strings.HasPrefix(value, root+"/")
}

func WithinAnyGuestRoot(value string, roots ...string) bool {
	for _, root := range roots {
		if WithinGuestRoot(root, value) {
			return true
		}
	}
	return false
}

func ResolveStepDirectory(
	hostWorkspace string,
	guestWorkspace string,
	workingDirectory string,
) (string, error) {
	trimmed := strings.TrimSpace(workingDirectory)
	if trimmed == "" {
		return guestWorkspace, nil
	}
	if filepath.IsAbs(trimmed) {
		cleaned := path.Clean(trimmed)
		if WithinGuestRoot(guestWorkspace, cleaned) {
			return cleaned, nil
		}
		return "", fmt.Errorf(
			"absolute remote working directory %q is outside guest workspace %q",
			trimmed,
			guestWorkspace,
		)
	}
	hostStepDirectory := filepath.Join(hostWorkspace, workingDirectory)
	return TranslateHostPath(hostWorkspace, guestWorkspace, hostStepDirectory)
}

func expandHomePath(rawPath string, guestHome string) (string, bool) {
	trimmed := strings.TrimSpace(rawPath)
	if trimmed == "~" {
		return guestHome, true
	}
	if strings.HasPrefix(trimmed, "~/") {
		return path.Join(guestHome, strings.TrimPrefix(trimmed, "~/")), true
	}
	return "", false
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

func tryTranslateAbsoluteWorkspacePath(
	value string,
	hostWorkspace string,
	guestWorkspace string,
) (string, error) {
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
