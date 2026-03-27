package codegenlockfile

import (
	"fmt"
	"path/filepath"
	"runtime"
)

// Layout describes the checked-in schema locations used by the lockfile tools.
type Layout struct {
	RepoRoot           string
	SchemaDir          string
	ManifestSchemaPath string
	LockSchemaPath     string
}

// NewLayout returns the schema layout rooted at the repository root.
func NewLayout(repoRoot string) Layout {
	schemaDir := filepath.Join(repoRoot, "schemas", "codegen")
	return Layout{
		RepoRoot:           repoRoot,
		SchemaDir:          schemaDir,
		ManifestSchemaPath: filepath.Join(schemaDir, "codegen.schema.json"),
		LockSchemaPath:     filepath.Join(schemaDir, "codegen-lock.schema.json"),
	}
}

// DiscoverLayout locates the repository-root-relative schema layout.
func DiscoverLayout() (Layout, error) {
	_, filename, _, ok := runtime.Caller(0)
	if !ok {
		return Layout{}, fmt.Errorf("runtime.Caller failed")
	}
	return NewLayout(filepath.Clean(filepath.Join(filepath.Dir(filename), "..", "..", ".."))), nil
}
