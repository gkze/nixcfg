package workflowschema

import (
	"fmt"
	"os"
	"path/filepath"
)

// Layout describes the on-disk artifact layout for workflow schema tooling.
type Layout struct {
	ModuleRoot         string
	SchemaDir          string
	CacheDir           string
	DocsCacheDir       string
	OfficialDir        string
	ManifestPath       string
	OfficialSchemaPath string
}

// DiscoverLayout resolves the ghawfr module root from the current directory.
func DiscoverLayout() (Layout, error) {
	cwd, err := os.Getwd()
	if err != nil {
		return Layout{}, fmt.Errorf("get working directory: %w", err)
	}
	return DiscoverLayoutFrom(cwd)
}

// DiscoverLayoutFrom resolves the ghawfr module root from startDir.
func DiscoverLayoutFrom(startDir string) (Layout, error) {
	current, err := filepath.Abs(startDir)
	if err != nil {
		return Layout{}, fmt.Errorf("resolve start directory %q: %w", startDir, err)
	}

	for {
		goMod := filepath.Join(current, "go.mod")
		info, statErr := os.Stat(goMod)
		if statErr == nil && !info.IsDir() {
			return NewLayout(current), nil
		}

		parent := filepath.Dir(current)
		if parent == current {
			return Layout{}, fmt.Errorf("could not find go.mod above %q", startDir)
		}
		current = parent
	}
}

// NewLayout constructs a schema artifact layout rooted at moduleRoot.
func NewLayout(moduleRoot string) Layout {
	schemaDir := filepath.Join(moduleRoot, "workflow", "schema")
	cacheDir := filepath.Join(schemaDir, ".cache")
	return Layout{
		ModuleRoot:         moduleRoot,
		SchemaDir:          schemaDir,
		CacheDir:           cacheDir,
		DocsCacheDir:       filepath.Join(cacheDir, "docs"),
		OfficialDir:        filepath.Join(schemaDir, "official"),
		ManifestPath:       filepath.Join(schemaDir, "manifest.json"),
		OfficialSchemaPath: filepath.Join(schemaDir, "official", "workflow-v1.0.json"),
	}
}

// DocHTMLPath returns the cached HTML path for one docs page snapshot.
func (l Layout) DocHTMLPath(name string) string {
	return filepath.Join(l.DocsCacheDir, name+".html")
}

// DocTextPath returns the cached normalized text path for one docs page snapshot.
func (l Layout) DocTextPath(name string) string {
	return filepath.Join(l.DocsCacheDir, name+".txt")
}

func (l Layout) relative(path string) string {
	rel, err := filepath.Rel(l.ModuleRoot, path)
	if err != nil {
		return path
	}
	return filepath.ToSlash(rel)
}
