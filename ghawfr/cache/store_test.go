package cache

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestStoreSaveAndRestoreExactKey(t *testing.T) {
	workspace := t.TempDir()
	if err := os.MkdirAll(filepath.Join(workspace, "nested"), 0o755); err != nil {
		t.Fatalf("mkdir nested: %v", err)
	}
	if err := os.WriteFile(filepath.Join(workspace, "nested", "child.txt"), []byte("child"), 0o644); err != nil {
		t.Fatalf("write child: %v", err)
	}
	store := NewStore(t.TempDir())
	if err := store.Save("cache-a", workspace, []string{"nested"}); err != nil {
		t.Fatalf("Save: %v", err)
	}
	if err := os.Remove(filepath.Join(workspace, "nested", "child.txt")); err != nil {
		t.Fatalf("remove child: %v", err)
	}
	matched, err := store.Restore("cache-a", nil)
	if err != nil {
		t.Fatalf("Restore: %v", err)
	}
	if got, want := matched, "cache-a"; got != want {
		t.Fatalf("matched = %q, want %q", got, want)
	}
	if got, want := mustReadFile(t, filepath.Join(workspace, "nested", "child.txt")), "child"; got != want {
		t.Fatalf("restored child = %q, want %q", got, want)
	}
}

func TestStoreRestoreFallsBackToRestoreKeyPrefix(t *testing.T) {
	workspace := t.TempDir()
	path := filepath.Join(workspace, "value.txt")
	storeRoot := t.TempDir()
	store := NewStore(storeRoot)
	if err := os.WriteFile(path, []byte("one"), 0o644); err != nil {
		t.Fatalf("write one: %v", err)
	}
	if err := store.Save("prefix-a", workspace, []string{"value.txt"}); err != nil {
		t.Fatalf("Save prefix-a: %v", err)
	}
	time.Sleep(10 * time.Millisecond)
	if err := os.WriteFile(path, []byte("two"), 0o644); err != nil {
		t.Fatalf("write two: %v", err)
	}
	if err := store.Save("prefix-b", workspace, []string{"value.txt"}); err != nil {
		t.Fatalf("Save prefix-b: %v", err)
	}
	if err := os.WriteFile(path, []byte("missing"), 0o644); err != nil {
		t.Fatalf("write missing: %v", err)
	}
	matched, err := store.Restore("missing-key", []string{"prefix-"})
	if err != nil {
		t.Fatalf("Restore fallback: %v", err)
	}
	if got, want := matched, "prefix-b"; got != want {
		t.Fatalf("matched = %q, want %q", got, want)
	}
	if got, want := mustReadFile(t, path), "two"; got != want {
		t.Fatalf("restored value = %q, want %q", got, want)
	}
}

func TestStoreSaveHonorsExcludes(t *testing.T) {
	workspace := t.TempDir()
	keep := filepath.Join(workspace, "cache", "keep.txt")
	drop := filepath.Join(workspace, "cache", "drop.sqlite")
	if err := os.MkdirAll(filepath.Dir(keep), 0o755); err != nil {
		t.Fatalf("mkdir cache: %v", err)
	}
	if err := os.WriteFile(keep, []byte("keep"), 0o644); err != nil {
		t.Fatalf("write keep: %v", err)
	}
	if err := os.WriteFile(drop, []byte("drop"), 0o644); err != nil {
		t.Fatalf("write drop: %v", err)
	}
	store := NewStore(t.TempDir())
	if err := store.Save("cache", workspace, []string{"cache", "!cache/*.sqlite"}); err != nil {
		t.Fatalf("Save: %v", err)
	}
	if err := os.Remove(keep); err != nil {
		t.Fatalf("remove keep: %v", err)
	}
	if err := os.Remove(drop); err != nil {
		t.Fatalf("remove drop: %v", err)
	}
	matched, err := store.Restore("cache", nil)
	if err != nil {
		t.Fatalf("Restore: %v", err)
	}
	if got, want := matched, "cache"; got != want {
		t.Fatalf("matched = %q, want %q", got, want)
	}
	if got, want := mustReadFile(t, keep), "keep"; got != want {
		t.Fatalf("restored keep = %q, want %q", got, want)
	}
	if _, err := os.Stat(drop); !os.IsNotExist(err) {
		t.Fatalf("drop.sqlite stat err = %v, want not exist", err)
	}
}

func TestStoreSaveDoesNotOverwriteExistingKey(t *testing.T) {
	workspace := t.TempDir()
	path := filepath.Join(workspace, "value.txt")
	store := NewStore(t.TempDir())
	if err := os.WriteFile(path, []byte("one"), 0o644); err != nil {
		t.Fatalf("write one: %v", err)
	}
	if err := store.Save("cache", workspace, []string{"value.txt"}); err != nil {
		t.Fatalf("Save first: %v", err)
	}
	if err := os.WriteFile(path, []byte("two"), 0o644); err != nil {
		t.Fatalf("write two: %v", err)
	}
	if err := store.Save("cache", workspace, []string{"value.txt"}); err != nil {
		t.Fatalf("Save second: %v", err)
	}
	if err := os.WriteFile(path, []byte("missing"), 0o644); err != nil {
		t.Fatalf("write missing: %v", err)
	}
	matched, err := store.Restore("cache", nil)
	if err != nil {
		t.Fatalf("Restore: %v", err)
	}
	if got, want := matched, "cache"; got != want {
		t.Fatalf("matched = %q, want %q", got, want)
	}
	if got, want := mustReadFile(t, path), "one"; got != want {
		t.Fatalf("restored value = %q, want %q", got, want)
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
