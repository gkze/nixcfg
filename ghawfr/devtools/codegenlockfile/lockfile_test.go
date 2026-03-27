package codegenlockfile

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/gkze/ghawfr/devtools/codegenlock"
)

func TestWriteMatchesSharedGoldenFixture(t *testing.T) {
	root := repoRoot(t)
	layout := NewLayout(root)
	fixtureRoot := filepath.Join(root, "schemas", "codegen", "testdata", "lockfile-golden")
	workingRoot := filepath.Join(t.TempDir(), "fixture")
	mustCopyDir(t, fixtureRoot, workingRoot)

	result, err := Write(
		context.Background(),
		layout,
		filepath.Join(workingRoot, "codegen.yaml"),
		"",
		BuildOptions{},
	)
	if err != nil {
		t.Fatalf("Write: %v", err)
	}
	got, err := os.ReadFile(result.Path)
	if err != nil {
		t.Fatalf("ReadFile(%q): %v", result.Path, err)
	}
	want, err := os.ReadFile(filepath.Join(fixtureRoot, "expected.codegen.lock.json"))
	if err != nil {
		t.Fatalf("ReadFile(expected): %v", err)
	}
	if !bytes.Equal(got, want) {
		t.Fatalf("lockfile bytes mismatch\n--- got ---\n%s\n--- want ---\n%s", got, want)
	}
}

func TestBuildURLSourceOmitAndIncludeMetadata(t *testing.T) {
	root := repoRoot(t)
	layout := NewLayout(root)
	manifestPath := filepath.Join(t.TempDir(), "codegen.yaml")
	mustWriteManifest(t, manifestPath, `  source:
    kind: url
    uri: https://example.com/schema.json
    format: json
`, "https://example.com/schema.json")

	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.String() != "https://example.com/schema.json" {
			return nil, fmt.Errorf("unexpected request %q", request.URL.String())
		}
		return textResponse(http.StatusOK, map[string]string{
			"ETag":          `"abc"`,
			"Last-Modified": "Wed, 04 Mar 2026 00:25:01 GMT",
		}, `{"title":"Remote"}
`), nil
	})}

	lockfile, err := Build(context.Background(), layout, manifestPath, "", BuildOptions{HTTPClient: client})
	if err != nil {
		t.Fatalf("Build without metadata: %v", err)
	}
	source, ok := lockfile.Sources["source"].(codegenlock.LockedUrlSource)
	if !ok {
		t.Fatalf("lockfile.Sources[source] type = %T, want codegenlock.LockedUrlSource", lockfile.Sources["source"])
	}
	if source.FetchedAt != nil || source.Etag != nil || source.LastModified != nil {
		t.Fatalf("source metadata = %#v, want omitted", source)
	}

	fixedNow := time.Date(2026, 3, 21, 12, 34, 56, 0, time.UTC)
	withMetadata, err := Build(context.Background(), layout, manifestPath, "", BuildOptions{
		HTTPClient:      client,
		IncludeMetadata: true,
		Now:             func() time.Time { return fixedNow },
	})
	if err != nil {
		t.Fatalf("Build with metadata: %v", err)
	}
	annotated, ok := withMetadata.Sources["source"].(codegenlock.LockedUrlSource)
	if !ok {
		t.Fatalf("withMetadata.Sources[source] type = %T, want codegenlock.LockedUrlSource", withMetadata.Sources["source"])
	}
	if annotated.FetchedAt == nil || !annotated.FetchedAt.Equal(fixedNow) {
		t.Fatalf("annotated.FetchedAt = %#v, want %s", annotated.FetchedAt, fixedNow)
	}
	if annotated.Etag == nil || string(*annotated.Etag) != `"abc"` {
		t.Fatalf("annotated.Etag = %#v, want %q", annotated.Etag, `"abc"`)
	}
	if annotated.LastModified == nil || string(*annotated.LastModified) != "Wed, 04 Mar 2026 00:25:01 GMT" {
		t.Fatalf("annotated.LastModified = %#v, want header", annotated.LastModified)
	}
}

func TestBuildGitHubRawSourceOmitsProvenanceByDefault(t *testing.T) {
	root := repoRoot(t)
	layout := NewLayout(root)
	manifestPath := filepath.Join(t.TempDir(), "codegen.yaml")
	mustWriteManifest(t, manifestPath, `  source:
    kind: github-raw
    owner: actions
    repo: languageservices
    ref: release-v0.3.49
    path: workflow-parser/src/workflow-v1.0.json
    format: json
    metadata:
      tag: release-v0.3.49
      package: "@actions/workflow-parser"
      package_version: 0.3.49
`, "./workflow-v1.0.json")

	resolvedSHA := "83de320ba99ee2bdbb14a2869462a8033714cd96"
	expectedRawURL := "https://raw.githubusercontent.com/actions/languageservices/" + resolvedSHA + "/workflow-parser/src/workflow-v1.0.json"
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		switch request.URL.String() {
		case "https://api.github.com/repos/actions/languageservices/commits/release-v0.3.49":
			return textResponse(http.StatusOK, nil, `{"sha":"`+resolvedSHA+`"}`), nil
		case expectedRawURL:
			return textResponse(http.StatusOK, nil, "{}\n"), nil
		default:
			return nil, fmt.Errorf("unexpected request %q", request.URL.String())
		}
	})}

	lockfile, err := Build(context.Background(), layout, manifestPath, "", BuildOptions{HTTPClient: client})
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	source, ok := lockfile.Sources["source"].(codegenlock.LockedGitHubRawSource)
	if !ok {
		t.Fatalf("lockfile.Sources[source] type = %T, want codegenlock.LockedGitHubRawSource", lockfile.Sources["source"])
	}
	if string(source.Ref) != resolvedSHA {
		t.Fatalf("source.Ref = %q, want %q", source.Ref, resolvedSHA)
	}
	if string(source.Uri) != expectedRawURL {
		t.Fatalf("source.Uri = %q, want %q", source.Uri, expectedRawURL)
	}
	if source.FetchedAt != nil || source.Tag != nil || source.Package != nil || source.PackageVersion != nil {
		t.Fatalf("source metadata = %#v, want omitted in reproducible mode", source)
	}
}

func TestBuildGitHubRawSourceIncludesProvenanceInAnnotatedMode(t *testing.T) {
	root := repoRoot(t)
	layout := NewLayout(root)
	manifestPath := filepath.Join(t.TempDir(), "codegen.yaml")
	mustWriteManifest(t, manifestPath, `  source:
    kind: github-raw
    owner: actions
    repo: languageservices
    ref: release-v0.3.49
    path: workflow-parser/src/workflow-v1.0.json
    format: json
    metadata:
      tag: release-v0.3.49
      package: "@actions/workflow-parser"
      package_version: 0.3.49
`, "./workflow-v1.0.json")

	resolvedSHA := "83de320ba99ee2bdbb14a2869462a8033714cd96"
	expectedRawURL := "https://raw.githubusercontent.com/actions/languageservices/" + resolvedSHA + "/workflow-parser/src/workflow-v1.0.json"
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		switch request.URL.String() {
		case "https://api.github.com/repos/actions/languageservices/commits/release-v0.3.49":
			return textResponse(http.StatusOK, nil, `{"sha":"`+resolvedSHA+`"}`), nil
		case expectedRawURL:
			return textResponse(http.StatusOK, nil, "{}\n"), nil
		default:
			return nil, fmt.Errorf("unexpected request %q", request.URL.String())
		}
	})}
	fixedNow := time.Date(2026, 3, 21, 12, 34, 56, 0, time.UTC)

	lockfile, err := Build(context.Background(), layout, manifestPath, "", BuildOptions{
		HTTPClient:      client,
		IncludeMetadata: true,
		Now:             func() time.Time { return fixedNow },
	})
	if err != nil {
		t.Fatalf("Build with metadata: %v", err)
	}
	source, ok := lockfile.Sources["source"].(codegenlock.LockedGitHubRawSource)
	if !ok {
		t.Fatalf("lockfile.Sources[source] type = %T, want codegenlock.LockedGitHubRawSource", lockfile.Sources["source"])
	}
	if source.FetchedAt == nil || !source.FetchedAt.Equal(fixedNow) {
		t.Fatalf("source.FetchedAt = %#v, want %s", source.FetchedAt, fixedNow)
	}
	if source.Tag == nil || string(*source.Tag) != "release-v0.3.49" {
		t.Fatalf("source.Tag = %#v, want provenance tag", source.Tag)
	}
	if source.Package == nil || string(*source.Package) != "@actions/workflow-parser" {
		t.Fatalf("source.Package = %#v, want provenance package", source.Package)
	}
	if source.PackageVersion == nil || string(*source.PackageVersion) != "0.3.49" {
		t.Fatalf("source.PackageVersion = %#v, want provenance version", source.PackageVersion)
	}
}

func repoRoot(t *testing.T) string {
	t.Helper()
	_, filename, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(filename), "..", "..", ".."))
}

func mustCopyDir(t *testing.T, src, dst string) {
	t.Helper()
	entries, err := os.ReadDir(src)
	if err != nil {
		t.Fatalf("ReadDir(%q): %v", src, err)
	}
	if err := os.MkdirAll(dst, 0o755); err != nil {
		t.Fatalf("MkdirAll(%q): %v", dst, err)
	}
	for _, entry := range entries {
		srcPath := filepath.Join(src, entry.Name())
		dstPath := filepath.Join(dst, entry.Name())
		if entry.IsDir() {
			mustCopyDir(t, srcPath, dstPath)
			continue
		}
		data, err := os.ReadFile(srcPath)
		if err != nil {
			t.Fatalf("ReadFile(%q): %v", srcPath, err)
		}
		if err := os.WriteFile(dstPath, data, 0o644); err != nil {
			t.Fatalf("WriteFile(%q): %v", dstPath, err)
		}
	}
}

func mustWriteManifest(t *testing.T, path string, sourceBlock string, entrypoint string) {
	t.Helper()
	manifest := fmt.Sprintf(`version: 1
sources:
%sinputs:
  primary:
    kind: jsonschema
    sources:
      - source
    entrypoints:
      - %s
generators:
  python:
    language: python
    tool: datamodel-code-generator
products:
  models:
    inputs:
      - primary
    generators:
      - python
    output_template: generated.py
`, sourceBlock, entrypoint)
	if err := os.WriteFile(path, []byte(manifest), 0o644); err != nil {
		t.Fatalf("WriteFile(%q): %v", path, err)
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return f(request)
}

func textResponse(status int, headers map[string]string, body string) *http.Response {
	response := &http.Response{
		StatusCode: status,
		Status:     fmt.Sprintf("%d %s", status, http.StatusText(status)),
		Header:     make(http.Header),
		Body:       io.NopCloser(strings.NewReader(body)),
	}
	for key, value := range headers {
		response.Header.Set(key, value)
	}
	return response
}
