package cache

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/bmatcuk/doublestar/v4"
)

// Store manages file-backed workflow caches.
type Store struct {
	Root string
}

// NewStore constructs a file-backed cache store rooted at the given path.
func NewStore(root string) *Store {
	return &Store{Root: root}
}

type manifest struct {
	Key     string       `json:"key"`
	Entries []cacheEntry `json:"entries"`
}

type cacheEntry struct {
	Target  string `json:"target"`
	Archive string `json:"archive"`
}

type savedFile struct {
	Absolute string
}

// Save stores one cache entry keyed by the given primary key.
func (s *Store) Save(key string, workspace string, paths []string) error {
	if s == nil {
		return fmt.Errorf("cache store is nil")
	}
	files, err := collectSavedFiles(workspace, paths)
	if err != nil {
		return err
	}
	if len(files) == 0 {
		return nil
	}
	root := s.cacheRoot(key)
	if _, err := os.Stat(filepath.Join(root, "manifest.json")); err == nil {
		return nil
	} else if err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("stat cache %q: %w", key, err)
	}
	if err := os.MkdirAll(filepath.Join(root, "files"), 0o755); err != nil {
		return fmt.Errorf("create cache dir %q: %w", root, err)
	}
	entryManifest := manifest{Key: key, Entries: make([]cacheEntry, 0, len(files))}
	for _, file := range files {
		archive := filepath.Join("files", cacheFileName(file.Absolute))
		destination := filepath.Join(root, archive)
		if err := copyFile(file.Absolute, destination); err != nil {
			return fmt.Errorf("copy %q into cache %q: %w", file.Absolute, key, err)
		}
		entryManifest.Entries = append(entryManifest.Entries, cacheEntry{Target: file.Absolute, Archive: archive})
	}
	data, err := json.MarshalIndent(entryManifest, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal cache manifest for %q: %w", key, err)
	}
	data = append(data, '\n')
	if err := os.WriteFile(filepath.Join(root, "manifest.json"), data, 0o644); err != nil {
		return fmt.Errorf("write cache manifest for %q: %w", key, err)
	}
	return nil
}

// Match returns the best matching cache key without restoring any files.
func (s *Store) Match(key string, restoreKeys []string) (string, error) {
	if s == nil {
		return "", fmt.Errorf("cache store is nil")
	}
	_, matchedKey, err := s.matchingRoot(key, restoreKeys)
	if err != nil {
		return "", err
	}
	return matchedKey, nil
}

// Restore restores the best matching cache entry into its recorded target paths.
// It returns the matched key, or an empty string when no cache matched.
func (s *Store) Restore(key string, restoreKeys []string) (string, error) {
	if s == nil {
		return "", fmt.Errorf("cache store is nil")
	}
	root, matchedKey, err := s.matchingRoot(key, restoreKeys)
	if err != nil {
		return "", err
	}
	if root == "" {
		return "", nil
	}
	entryManifest, err := readManifest(filepath.Join(root, "manifest.json"))
	if err != nil {
		return "", err
	}
	for _, entry := range entryManifest.Entries {
		source := filepath.Join(root, entry.Archive)
		if err := os.MkdirAll(filepath.Dir(entry.Target), 0o755); err != nil {
			return "", fmt.Errorf("create cache target dir for %q: %w", entry.Target, err)
		}
		if err := copyFile(source, entry.Target); err != nil {
			return "", fmt.Errorf("restore cache file %q to %q: %w", source, entry.Target, err)
		}
	}
	return matchedKey, nil
}

func (s *Store) matchingRoot(key string, restoreKeys []string) (string, string, error) {
	exact := s.cacheRoot(key)
	if _, err := os.Stat(filepath.Join(exact, "manifest.json")); err == nil {
		return exact, key, nil
	} else if !os.IsNotExist(err) {
		return "", "", fmt.Errorf("stat cache %q: %w", key, err)
	}
	entries, err := os.ReadDir(s.Root)
	if err != nil {
		if os.IsNotExist(err) {
			return "", "", nil
		}
		return "", "", fmt.Errorf("read cache store root %q: %w", s.Root, err)
	}
	type candidate struct {
		root    string
		key     string
		modTime time.Time
	}
	candidates := make([]candidate, 0)
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		root := filepath.Join(s.Root, entry.Name())
		manifestPath := filepath.Join(root, "manifest.json")
		entryManifest, err := readManifest(manifestPath)
		if err != nil {
			if os.IsNotExist(err) {
				continue
			}
			return "", "", err
		}
		info, err := os.Stat(manifestPath)
		if err != nil {
			return "", "", fmt.Errorf("stat cache manifest %q: %w", manifestPath, err)
		}
		candidates = append(candidates, candidate{root: root, key: entryManifest.Key, modTime: info.ModTime()})
	}
	for _, prefix := range restoreKeys {
		prefix = strings.TrimSpace(prefix)
		if prefix == "" {
			continue
		}
		var best *candidate
		for i := range candidates {
			if !strings.HasPrefix(candidates[i].key, prefix) {
				continue
			}
			if best == nil || candidates[i].modTime.After(best.modTime) {
				best = &candidates[i]
			}
		}
		if best != nil {
			return best.root, best.key, nil
		}
	}
	return "", "", nil
}

func readManifest(path string) (manifest, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return manifest{}, err
	}
	var value manifest
	if err := json.Unmarshal(data, &value); err != nil {
		return manifest{}, fmt.Errorf("parse cache manifest %q: %w", path, err)
	}
	return value, nil
}

func (s *Store) cacheRoot(key string) string {
	return filepath.Join(s.Root, cacheDirName(key))
}

func collectSavedFiles(workspace string, paths []string) ([]savedFile, error) {
	seen := make(map[string]savedFile)
	for _, raw := range paths {
		trimmed := strings.TrimSpace(raw)
		if trimmed == "" {
			continue
		}
		exclude := strings.HasPrefix(trimmed, "!")
		if exclude {
			trimmed = strings.TrimSpace(strings.TrimPrefix(trimmed, "!"))
		}
		resolved := resolveCachePath(workspace, trimmed)
		matches, err := expandCachePath(resolved)
		if err != nil {
			return nil, err
		}
		for _, match := range matches {
			if exclude {
				delete(seen, match.Absolute)
				continue
			}
			seen[match.Absolute] = match
		}
	}
	result := make([]savedFile, 0, len(seen))
	for _, value := range seen {
		result = append(result, value)
	}
	sort.Slice(result, func(i, j int) bool {
		return result[i].Absolute < result[j].Absolute
	})
	return result, nil
}

func resolveCachePath(workspace string, raw string) string {
	trimmed := strings.TrimSpace(raw)
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

func expandCachePath(pattern string) ([]savedFile, error) {
	if hasGlob(pattern) {
		paths, err := doublestar.FilepathGlob(pattern)
		if err != nil {
			return nil, fmt.Errorf("glob cache path %q: %w", pattern, err)
		}
		return fileList(paths)
	}
	info, err := os.Stat(pattern)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	if !info.IsDir() {
		return fileList([]string{pattern})
	}
	paths := make([]string, 0)
	err = filepath.WalkDir(pattern, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() {
			return nil
		}
		paths = append(paths, path)
		return nil
	})
	if err != nil {
		return nil, err
	}
	return fileList(paths)
}

func fileList(paths []string) ([]savedFile, error) {
	result := make([]savedFile, 0, len(paths))
	for _, path := range paths {
		info, err := os.Stat(path)
		if err != nil {
			if os.IsNotExist(err) {
				continue
			}
			return nil, err
		}
		if info.IsDir() {
			continue
		}
		absolute, err := filepath.Abs(path)
		if err != nil {
			return nil, err
		}
		result = append(result, savedFile{Absolute: absolute})
	}
	return result, nil
}

func hasGlob(path string) bool {
	return strings.ContainsAny(path, "*?[")
}

func cacheDirName(key string) string {
	sum := sha256.Sum256([]byte(key))
	slug := strings.ToLower(strings.TrimSpace(key))
	slug = strings.NewReplacer("/", "-", " ", "-", string(filepath.Separator), "-").Replace(slug)
	if slug == "" {
		slug = "cache"
	}
	if len(slug) > 40 {
		slug = slug[:40]
	}
	return slug + "-" + hex.EncodeToString(sum[:8])
}

func cacheFileName(path string) string {
	sum := sha256.Sum256([]byte(path))
	base := filepath.Base(path)
	if base == "" || base == "." || base == string(filepath.Separator) {
		base = "file"
	}
	return hex.EncodeToString(sum[:8]) + "-" + base
}

func copyFile(source string, destination string) error {
	input, err := os.Open(source)
	if err != nil {
		return err
	}
	defer input.Close()
	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return err
	}
	output, err := os.Create(destination)
	if err != nil {
		return err
	}
	defer output.Close()
	if _, err := io.Copy(output, input); err != nil {
		return err
	}
	return output.Close()
}
