package artifacts

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/bmatcuk/doublestar/v4"
)

// Store manages file-backed workflow artifacts.
type Store struct {
	Root string
}

// NewStore constructs a file-backed artifact store rooted at the given path.
func NewStore(root string) *Store {
	return &Store{Root: root}
}

// Save stores one named artifact from the given workspace paths.
func (s *Store) Save(name string, workspace string, paths []string, ifNoFilesFound string) error {
	if s == nil {
		return fmt.Errorf("artifact store is nil")
	}
	matches, err := collectMatches(workspace, paths)
	if err != nil {
		return err
	}
	if len(matches) == 0 {
		switch strings.ToLower(strings.TrimSpace(ifNoFilesFound)) {
		case "", "warn", "ignore":
			return nil
		case "error":
			return fmt.Errorf("artifact %q matched no files", name)
		default:
			return fmt.Errorf("artifact %q matched no files (if-no-files-found=%q)", name, ifNoFilesFound)
		}
	}
	artifactRoot := s.artifactRoot(name)
	if err := os.RemoveAll(artifactRoot); err != nil {
		return fmt.Errorf("clear artifact %q: %w", name, err)
	}
	for _, match := range matches {
		destination := filepath.Join(artifactRoot, match.Relative)
		if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
			return fmt.Errorf("create artifact dir for %q: %w", destination, err)
		}
		if err := copyFile(match.Absolute, destination); err != nil {
			return fmt.Errorf("copy %q into artifact %q: %w", match.Relative, name, err)
		}
	}
	return nil
}

// Restore copies one named artifact into the destination directory.
func (s *Store) Restore(name string, destination string) error {
	if s == nil {
		return fmt.Errorf("artifact store is nil")
	}
	artifactRoot := s.artifactRoot(name)
	if _, err := os.Stat(artifactRoot); err != nil {
		return fmt.Errorf("artifact %q is not available: %w", name, err)
	}
	return filepath.WalkDir(artifactRoot, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() {
			return nil
		}
		relative, err := filepath.Rel(artifactRoot, path)
		if err != nil {
			return err
		}
		target := filepath.Join(destination, relative)
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		return copyFile(path, target)
	})
}

func (s *Store) artifactRoot(name string) string {
	return filepath.Join(s.Root, artifactDirName(name))
}

type match struct {
	Absolute string
	Relative string
}

func collectMatches(workspace string, paths []string) ([]match, error) {
	seen := make(map[string]match)
	for _, raw := range paths {
		for _, item := range splitArtifactPaths(raw) {
			resolved := filepath.Join(workspace, item)
			matches, err := expandArtifactPath(workspace, resolved)
			if err != nil {
				return nil, err
			}
			for _, value := range matches {
				seen[value.Relative] = value
			}
		}
	}
	result := make([]match, 0, len(seen))
	for _, value := range seen {
		result = append(result, value)
	}
	sort.Slice(result, func(i, j int) bool {
		return result[i].Relative < result[j].Relative
	})
	return result, nil
}

func expandArtifactPath(workspace string, pattern string) ([]match, error) {
	if hasGlob(pattern) {
		paths, err := doublestar.FilepathGlob(pattern)
		if err != nil {
			return nil, fmt.Errorf("glob artifact path %q: %w", pattern, err)
		}
		return fileMatches(workspace, paths)
	}
	info, err := os.Stat(pattern)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	if !info.IsDir() {
		return fileMatches(workspace, []string{pattern})
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
	return fileMatches(workspace, paths)
}

func fileMatches(workspace string, paths []string) ([]match, error) {
	matches := make([]match, 0, len(paths))
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
		relative, err := filepath.Rel(workspace, path)
		if err != nil {
			return nil, err
		}
		matches = append(matches, match{Absolute: path, Relative: relative})
	}
	return matches, nil
}

func splitArtifactPaths(input string) []string {
	values := make([]string, 0)
	for _, line := range strings.Split(input, "\n") {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" {
			continue
		}
		values = append(values, trimmed)
	}
	return values
}

func hasGlob(path string) bool {
	return strings.ContainsAny(path, "*?[")
}

func artifactDirName(name string) string {
	sum := sha256.Sum256([]byte(name))
	slug := strings.ToLower(strings.TrimSpace(name))
	slug = strings.NewReplacer("/", "-", " ", "-", string(filepath.Separator), "-").Replace(slug)
	if slug == "" {
		slug = "artifact"
	}
	if len(slug) > 40 {
		slug = slug[:40]
	}
	return slug + "-" + hex.EncodeToString(sum[:8])
}

func copyFile(source string, destination string) error {
	input, err := os.Open(source)
	if err != nil {
		return err
	}
	defer input.Close()
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
