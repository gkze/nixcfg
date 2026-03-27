package artifacts

import (
	"os"
	"path/filepath"
	"testing"
)

func TestStoreSaveAndRestoreArtifacts(t *testing.T) {
	workspace := t.TempDir()
	storeRoot := t.TempDir()
	if err := os.MkdirAll(filepath.Join(workspace, "nested"), 0o755); err != nil {
		t.Fatalf("mkdir nested: %v", err)
	}
	if err := os.WriteFile(filepath.Join(workspace, "flake.lock"), []byte("lock"), 0o644); err != nil {
		t.Fatalf("write flake.lock: %v", err)
	}
	if err := os.WriteFile(filepath.Join(workspace, "nested", "child.txt"), []byte("child"), 0o644); err != nil {
		t.Fatalf("write child: %v", err)
	}

	store := NewStore(storeRoot)
	if err := store.Save("merged", workspace, []string{"flake.lock\nnested/**"}, "error"); err != nil {
		t.Fatalf("Save: %v", err)
	}

	destination := t.TempDir()
	if err := store.Restore("merged", destination); err != nil {
		t.Fatalf("Restore: %v", err)
	}
	if got, want := mustReadFile(t, filepath.Join(destination, "flake.lock")), "lock"; got != want {
		t.Fatalf("flake.lock = %q, want %q", got, want)
	}
	if got, want := mustReadFile(t, filepath.Join(destination, "nested", "child.txt")), "child"; got != want {
		t.Fatalf("child.txt = %q, want %q", got, want)
	}
}

func TestStoreSaveErrorsWhenConfiguredAndNoFilesMatch(t *testing.T) {
	store := NewStore(t.TempDir())
	if err := store.Save("empty", t.TempDir(), []string{"missing.txt"}, "error"); err == nil {
		t.Fatal("Save error = nil, want error")
	}
}

func mustReadFile(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %q: %v", path, err)
	}
	return string(data)
}
