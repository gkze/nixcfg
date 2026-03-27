package actionadapter

import (
	"fmt"
	"os"
	"path"
	"path/filepath"
	"strings"
)

type toolCacheAlias struct {
	HostRoot  string
	GuestRoot string
	HostBin   string
	GuestBin  string
}

func materializeToolCacheAlias(hostToolCacheRoot string, guestToolCacheRoot string, family string, version string, arch string, targetRoot string) (toolCacheAlias, error) {
	family = sanitizeToolCacheComponent(family)
	version = sanitizeToolCacheComponent(defaultToolCacheVersion(version))
	arch = sanitizeToolCacheComponent(defaultToolCacheArch(arch))
	rel := filepath.Join(family, version, arch)
	hostRoot := filepath.Join(hostToolCacheRoot, rel)
	guestRoot := path.Join(guestToolCacheRoot, filepath.ToSlash(rel))
	if err := os.MkdirAll(filepath.Dir(hostRoot), 0o755); err != nil {
		return toolCacheAlias{}, fmt.Errorf("create tool cache dir for %q: %w", hostRoot, err)
	}
	if err := os.RemoveAll(hostRoot); err != nil {
		return toolCacheAlias{}, fmt.Errorf("reset tool cache alias %q: %w", hostRoot, err)
	}
	if err := os.Symlink(targetRoot, hostRoot); err != nil {
		return toolCacheAlias{}, fmt.Errorf("symlink tool cache alias %q -> %q: %w", hostRoot, targetRoot, err)
	}
	return toolCacheAlias{
		HostRoot:  hostRoot,
		GuestRoot: guestRoot,
		HostBin:   filepath.Join(hostRoot, "bin"),
		GuestBin:  path.Join(guestRoot, "bin"),
	}, nil
}

func materializeToolCacheDirectory(hostToolCacheRoot string, guestToolCacheRoot string, components ...string) (string, string, error) {
	hostParts := make([]string, 0, len(components)+1)
	guestParts := make([]string, 0, len(components)+1)
	hostParts = append(hostParts, hostToolCacheRoot)
	guestParts = append(guestParts, guestToolCacheRoot)
	for _, component := range components {
		sanitized := sanitizeToolCacheComponent(component)
		hostParts = append(hostParts, sanitized)
		guestParts = append(guestParts, sanitized)
	}
	hostPath := filepath.Join(hostParts...)
	guestPath := path.Join(guestParts...)
	if err := os.MkdirAll(hostPath, 0o755); err != nil {
		return "", "", fmt.Errorf("create tool cache directory %q: %w", hostPath, err)
	}
	return hostPath, guestPath, nil
}

func sanitizeToolCacheComponent(value string) string {
	trimmed := strings.TrimSpace(value)
	if trimmed == "" {
		return "system"
	}
	var builder strings.Builder
	for _, r := range trimmed {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9', r == '.', r == '_', r == '-':
			builder.WriteRune(r)
		default:
			builder.WriteByte('-')
		}
	}
	result := strings.Trim(builder.String(), "-")
	if result == "" {
		return "system"
	}
	return result
}

func defaultToolCacheVersion(value string) string {
	if strings.TrimSpace(value) == "" {
		return "system"
	}
	return value
}

func defaultToolCacheArch(value string) string {
	if strings.TrimSpace(value) == "" {
		return "unknown"
	}
	return strings.ToLower(strings.TrimSpace(value))
}
