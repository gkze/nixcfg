package codegenlockfile

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"net/url"
	"os"
	pathpkg "path"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/gkze/ghawfr/devtools/codegenlock"
	"github.com/gkze/ghawfr/devtools/codegenmanifest"
	tekurijsonschema "github.com/santhosh-tekuri/jsonschema/v6"
	"gopkg.in/yaml.v3"
)

const defaultLockfileName = "codegen.lock.json"

// BuildOptions controls lockfile materialization.
type BuildOptions struct {
	HTTPClient      *http.Client
	IncludeMetadata bool
	Now             func() time.Time
}

// WriteResult describes one written lockfile artifact.
type WriteResult struct {
	Path   string
	SHA256 string
}

type manifestEnvelope struct {
	Manifest      codegenmanifest.CodegenManifest
	SourceEntries map[string]json.RawMessage
}

type manifestSourceKind struct {
	Kind string `json:"kind"`
}

type lockedDirectorySource struct {
	Value codegenlock.LockedDirectorySource
}

type lockedURLSource struct {
	Value codegenlock.LockedUrlSource
}

type lockedGitHubRawSource struct {
	Value codegenlock.LockedGitHubRawSource
}

// Build materializes a lockfile from a canonical manifest.
func Build(
	ctx context.Context,
	layout Layout,
	manifestPath string,
	lockfilePath string,
	opts BuildOptions,
) (*codegenlock.CodegenLockfile, error) {
	resolvedManifestPath, resolvedLockfilePath, err := resolvePaths(manifestPath, lockfilePath)
	if err != nil {
		return nil, err
	}
	envelope, err := loadManifest(layout, resolvedManifestPath)
	if err != nil {
		return nil, err
	}
	resolvedOpts := withDefaultOptions(opts)
	manifestDir := filepath.Dir(resolvedManifestPath)
	lockfile := &codegenlock.CodegenLockfile{
		Version: 1,
		Sources: make(codegenlock.LockedSourceMap, len(envelope.SourceEntries)),
	}
	manifestRel, err := normalizeRelativePath(resolvedManifestPath, filepath.Dir(resolvedLockfilePath))
	if err != nil {
		return nil, err
	}
	lockfile.ManifestPath = &manifestRel
	for sourceName, raw := range envelope.SourceEntries {
		lockedSource, err := buildLockedSource(ctx, resolvedOpts, manifestDir, sourceName, raw)
		if err != nil {
			return nil, err
		}
		lockfile.Sources[sourceName] = lockedSource
	}
	if resolvedOpts.IncludeMetadata {
		now := resolvedOpts.Now().UTC()
		lockfile.GeneratedAt = &now
	}
	rendered, err := Render(lockfile)
	if err != nil {
		return nil, err
	}
	if err := validateInstance(layout.LockSchemaPath, rendered); err != nil {
		return nil, err
	}
	return lockfile, nil
}

// Render serializes a lockfile as canonical JSON with a trailing newline.
func Render(lockfile *codegenlock.CodegenLockfile) ([]byte, error) {
	value, err := lockfileCanonicalValue(lockfile)
	if err != nil {
		return nil, err
	}
	var buffer bytes.Buffer
	if err := writeCanonicalJSON(&buffer, value); err != nil {
		return nil, err
	}
	buffer.WriteByte('\n')
	return buffer.Bytes(), nil
}

// Write materializes and writes a lockfile to disk.
func Write(
	ctx context.Context,
	layout Layout,
	manifestPath string,
	lockfilePath string,
	opts BuildOptions,
) (WriteResult, error) {
	resolvedManifestPath, resolvedLockfilePath, err := resolvePaths(manifestPath, lockfilePath)
	if err != nil {
		return WriteResult{}, err
	}
	lockfile, err := Build(ctx, layout, resolvedManifestPath, resolvedLockfilePath, opts)
	if err != nil {
		return WriteResult{}, err
	}
	rendered, err := Render(lockfile)
	if err != nil {
		return WriteResult{}, err
	}
	if err := os.MkdirAll(filepath.Dir(resolvedLockfilePath), 0o755); err != nil {
		return WriteResult{}, fmt.Errorf("create parent directory for %q: %w", resolvedLockfilePath, err)
	}
	if err := os.WriteFile(resolvedLockfilePath, rendered, 0o644); err != nil {
		return WriteResult{}, fmt.Errorf("write lockfile %q: %w", resolvedLockfilePath, err)
	}
	return WriteResult{Path: resolvedLockfilePath, SHA256: sha256Hex(rendered)}, nil
}

func withDefaultOptions(opts BuildOptions) BuildOptions {
	if opts.HTTPClient == nil {
		opts.HTTPClient = http.DefaultClient
	}
	if opts.Now == nil {
		opts.Now = time.Now
	}
	return opts
}

func resolvePaths(manifestPath string, lockfilePath string) (string, string, error) {
	resolvedManifestPath, err := filepath.Abs(manifestPath)
	if err != nil {
		return "", "", fmt.Errorf("resolve manifest path %q: %w", manifestPath, err)
	}
	resolvedLockfilePath := lockfilePath
	if resolvedLockfilePath == "" {
		resolvedLockfilePath = filepath.Join(filepath.Dir(resolvedManifestPath), defaultLockfileName)
	}
	resolvedLockfilePath, err = filepath.Abs(resolvedLockfilePath)
	if err != nil {
		return "", "", fmt.Errorf("resolve lockfile path %q: %w", lockfilePath, err)
	}
	return resolvedManifestPath, resolvedLockfilePath, nil
}

func loadManifest(layout Layout, manifestPath string) (*manifestEnvelope, error) {
	payload, err := loadStructuredDocument(manifestPath)
	if err != nil {
		return nil, err
	}
	jsonData, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("marshal manifest %q for validation: %w", manifestPath, err)
	}
	if err := validateInstance(layout.ManifestSchemaPath, jsonData); err != nil {
		return nil, err
	}
	var manifest codegenmanifest.CodegenManifest
	if err := json.Unmarshal(jsonData, &manifest); err != nil {
		return nil, fmt.Errorf("decode manifest %q: %w", manifestPath, err)
	}
	var sources struct {
		Sources map[string]json.RawMessage `json:"sources"`
	}
	if err := json.Unmarshal(jsonData, &sources); err != nil {
		return nil, fmt.Errorf("decode manifest source index %q: %w", manifestPath, err)
	}
	return &manifestEnvelope{Manifest: manifest, SourceEntries: sources.Sources}, nil
}

func loadStructuredDocument(path string) (map[string]any, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read %q: %w", path, err)
	}
	var raw any
	if err := yaml.Unmarshal(data, &raw); err != nil {
		return nil, fmt.Errorf("decode %q: %w", path, err)
	}
	normalized, err := normalizeYAMLValue(raw)
	if err != nil {
		return nil, err
	}
	mapping, ok := normalized.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("expected object document in %q", path)
	}
	return mapping, nil
}

func normalizeYAMLValue(value any) (any, error) {
	switch typed := value.(type) {
	case nil, string, bool, int, int64, float64:
		return typed, nil
	case []any:
		result := make([]any, 0, len(typed))
		for _, item := range typed {
			normalized, err := normalizeYAMLValue(item)
			if err != nil {
				return nil, err
			}
			result = append(result, normalized)
		}
		return result, nil
	case map[string]any:
		result := make(map[string]any, len(typed))
		for key, item := range typed {
			normalized, err := normalizeYAMLValue(item)
			if err != nil {
				return nil, err
			}
			result[key] = normalized
		}
		return result, nil
	case map[any]any:
		result := make(map[string]any, len(typed))
		for rawKey, item := range typed {
			key, ok := rawKey.(string)
			if !ok {
				return nil, fmt.Errorf("expected string YAML map key, got %T", rawKey)
			}
			normalized, err := normalizeYAMLValue(item)
			if err != nil {
				return nil, err
			}
			result[key] = normalized
		}
		return result, nil
	default:
		return nil, fmt.Errorf("unsupported YAML value type %T", value)
	}
}

func validateInstance(schemaPath string, instanceJSON []byte) error {
	schemaData, err := os.ReadFile(schemaPath)
	if err != nil {
		return fmt.Errorf("read schema %q: %w", schemaPath, err)
	}
	schemaDoc, err := tekurijsonschema.UnmarshalJSON(bytes.NewReader(schemaData))
	if err != nil {
		return fmt.Errorf("parse schema %q: %w", schemaPath, err)
	}
	compiler := tekurijsonschema.NewCompiler()
	if err := compiler.AddResource("schema.json", schemaDoc); err != nil {
		return fmt.Errorf("register schema %q: %w", schemaPath, err)
	}
	compiled, err := compiler.Compile("schema.json")
	if err != nil {
		return fmt.Errorf("compile schema %q: %w", schemaPath, err)
	}
	instanceDoc, err := tekurijsonschema.UnmarshalJSON(bytes.NewReader(instanceJSON))
	if err != nil {
		return fmt.Errorf("parse instance for schema %q: %w", schemaPath, err)
	}
	if err := compiled.Validate(instanceDoc); err != nil {
		return fmt.Errorf("validate instance against %q: %w", schemaPath, err)
	}
	return nil
}

func buildLockedSource(
	ctx context.Context,
	opts BuildOptions,
	manifestDir string,
	sourceName string,
	raw json.RawMessage,
) (codegenlock.LockedSource, error) {
	var kind manifestSourceKind
	if err := json.Unmarshal(raw, &kind); err != nil {
		return nil, fmt.Errorf("decode source %q kind: %w", sourceName, err)
	}
	switch kind.Kind {
	case "directory":
		var source codegenmanifest.DirectorySource
		if err := json.Unmarshal(raw, &source); err != nil {
			return nil, fmt.Errorf("decode directory source %q: %w", sourceName, err)
		}
		locked, err := buildLockedDirectorySource(manifestDir, source, opts)
		if err != nil {
			return nil, fmt.Errorf("lock directory source %q: %w", sourceName, err)
		}
		return locked, nil
	case "url":
		var source codegenmanifest.UrlSource
		if err := json.Unmarshal(raw, &source); err != nil {
			return nil, fmt.Errorf("decode URL source %q: %w", sourceName, err)
		}
		locked, err := buildLockedURLSource(ctx, opts, source)
		if err != nil {
			return nil, fmt.Errorf("lock URL source %q: %w", sourceName, err)
		}
		return locked, nil
	case "github-raw":
		var source codegenmanifest.GitHubRawSource
		if err := json.Unmarshal(raw, &source); err != nil {
			return nil, fmt.Errorf("decode GitHub raw source %q: %w", sourceName, err)
		}
		locked, err := buildLockedGitHubRawSource(ctx, opts, source)
		if err != nil {
			return nil, fmt.Errorf("lock GitHub raw source %q: %w", sourceName, err)
		}
		return locked, nil
	default:
		return nil, fmt.Errorf("unsupported source kind %q", kind.Kind)
	}
}

func buildLockedDirectorySource(
	manifestDir string,
	source codegenmanifest.DirectorySource,
	opts BuildOptions,
) (codegenlock.LockedDirectorySource, error) {
	absolutePath := string(source.Path)
	if !filepath.IsAbs(absolutePath) {
		absolutePath = filepath.Join(manifestDir, absolutePath)
	}
	absolutePath, err := filepath.Abs(absolutePath)
	if err != nil {
		return codegenlock.LockedDirectorySource{}, fmt.Errorf("resolve directory source path %q: %w", source.Path, err)
	}
	normalizedPath, err := normalizeRelativePath(absolutePath, manifestDir)
	if err != nil {
		return codegenlock.LockedDirectorySource{}, err
	}
	patterns := make([]string, 0, len(source.Include))
	for _, pattern := range source.Include {
		patterns = append(patterns, string(pattern))
	}
	digest, err := hashDirectoryMaterialization(absolutePath, patterns)
	if err != nil {
		return codegenlock.LockedDirectorySource{}, err
	}
	locked := codegenlock.LockedDirectorySource{
		Kind:          "directory",
		Path:          codegenlock.PathString(normalizedPath),
		ContentSha256: ptr(codegenlock.Sha256Hex(digest)),
	}
	if opts.IncludeMetadata {
		now := opts.Now().UTC()
		locked.GeneratedAt = &now
	}
	return locked, nil
}

func buildLockedURLSource(
	ctx context.Context,
	opts BuildOptions,
	source codegenmanifest.UrlSource,
) (codegenlock.LockedUrlSource, error) {
	payload, headers, err := fetchHTTPSBytes(ctx, opts.HTTPClient, string(source.Uri))
	if err != nil {
		return codegenlock.LockedUrlSource{}, err
	}
	locked := codegenlock.LockedUrlSource{
		Kind:   "url",
		Uri:    codegenlock.HttpsUrl(source.Uri),
		Sha256: codegenlock.Sha256Hex(sha256Hex(payload)),
	}
	if opts.IncludeMetadata {
		now := opts.Now().UTC()
		locked.FetchedAt = &now
		if etag := headers.Get("ETag"); etag != "" {
			locked.Etag = ptr(codegenlock.NonEmptyString(etag))
		}
		if lastModified := headers.Get("Last-Modified"); lastModified != "" {
			locked.LastModified = ptr(codegenlock.NonEmptyString(lastModified))
		}
	}
	return locked, nil
}

func buildLockedGitHubRawSource(
	ctx context.Context,
	opts BuildOptions,
	source codegenmanifest.GitHubRawSource,
) (codegenlock.LockedGitHubRawSource, error) {
	resolvedRef, err := resolveGitHubCommit(ctx, opts.HTTPClient, string(source.Owner), string(source.Repo), string(source.Ref))
	if err != nil {
		return codegenlock.LockedGitHubRawSource{}, err
	}
	normalizedPath := normalizePosixPath(string(source.Path))
	rawURL := fmt.Sprintf(
		"https://raw.githubusercontent.com/%s/%s/%s/%s",
		string(source.Owner),
		string(source.Repo),
		resolvedRef,
		normalizedPath,
	)
	payload, _, err := fetchHTTPSBytes(ctx, opts.HTTPClient, rawURL)
	if err != nil {
		return codegenlock.LockedGitHubRawSource{}, err
	}
	locked := codegenlock.LockedGitHubRawSource{
		Kind:   "github-raw",
		Owner:  codegenlock.Identifier(source.Owner),
		Repo:   codegenlock.NonEmptyString(source.Repo),
		Ref:    codegenlock.NonEmptyString(resolvedRef),
		Path:   codegenlock.PathString(normalizedPath),
		Uri:    codegenlock.HttpsUrl(rawURL),
		Sha256: codegenlock.Sha256Hex(sha256Hex(payload)),
	}
	if opts.IncludeMetadata {
		now := opts.Now().UTC()
		locked.FetchedAt = &now
		if source.Metadata != nil {
			if source.Metadata.Tag != nil {
				locked.Tag = ptr(codegenlock.NonEmptyString(*source.Metadata.Tag))
			}
			if source.Metadata.Package != nil {
				locked.Package = ptr(codegenlock.NonEmptyString(*source.Metadata.Package))
			}
			if source.Metadata.PackageVersion != nil {
				locked.PackageVersion = ptr(codegenlock.NonEmptyString(*source.Metadata.PackageVersion))
			}
		}
	}
	return locked, nil
}

func resolveGitHubCommit(
	ctx context.Context,
	client *http.Client,
	owner string,
	repo string,
	ref string,
) (string, error) {
	if isGitCommitSHA(ref) {
		return ref, nil
	}
	apiURL := fmt.Sprintf(
		"https://api.github.com/repos/%s/%s/commits/%s",
		owner,
		repo,
		url.PathEscape(ref),
	)
	payload, _, err := fetchHTTPSBytes(ctx, client, apiURL)
	if err != nil {
		return "", err
	}
	var response struct {
		SHA string `json:"sha"`
	}
	if err := json.Unmarshal(payload, &response); err != nil {
		return "", fmt.Errorf("decode GitHub commit response for %s/%s@%s: %w", owner, repo, ref, err)
	}
	if !isGitCommitSHA(response.SHA) {
		return "", fmt.Errorf("GitHub commit lookup for %s/%s@%s returned invalid SHA %q", owner, repo, ref, response.SHA)
	}
	return response.SHA, nil
}

func isGitCommitSHA(value string) bool {
	if len(value) != 40 {
		return false
	}
	for _, r := range value {
		if (r < '0' || r > '9') && (r < 'a' || r > 'f') {
			return false
		}
	}
	return true
}

func fetchHTTPSBytes(
	ctx context.Context,
	client *http.Client,
	requestURL string,
) ([]byte, http.Header, error) {
	parsed, err := url.Parse(requestURL)
	if err != nil {
		return nil, nil, fmt.Errorf("parse URL %q: %w", requestURL, err)
	}
	if parsed.Scheme != "https" || parsed.Host == "" {
		return nil, nil, fmt.Errorf("only absolute HTTPS URLs are supported, got %q", requestURL)
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, requestURL, nil)
	if err != nil {
		return nil, nil, fmt.Errorf("build request for %q: %w", requestURL, err)
	}
	request.Header.Set("User-Agent", "ghawfr-codegen-lockfile")
	if token := os.Getenv("GITHUB_TOKEN"); token != "" && strings.HasPrefix(requestURL, "https://api.github.com/") {
		request.Header.Set("Authorization", "Bearer "+token)
	}
	if token := os.Getenv("GH_TOKEN"); token != "" && request.Header.Get("Authorization") == "" && strings.HasPrefix(requestURL, "https://api.github.com/") {
		request.Header.Set("Authorization", "Bearer "+token)
	}
	response, err := client.Do(request)
	if err != nil {
		return nil, nil, fmt.Errorf("request %q: %w", requestURL, err)
	}
	defer response.Body.Close()
	payload, err := io.ReadAll(response.Body)
	if err != nil {
		return nil, nil, fmt.Errorf("read response body %q: %w", requestURL, err)
	}
	if response.StatusCode >= http.StatusBadRequest {
		return nil, nil, fmt.Errorf("HTTP %d fetching %q", response.StatusCode, requestURL)
	}
	return payload, response.Header.Clone(), nil
}

func hashDirectoryMaterialization(sourceRoot string, includePatterns []string) (string, error) {
	files, err := materializedFiles(sourceRoot, includePatterns)
	if err != nil {
		return "", err
	}
	var buffer bytes.Buffer
	for _, file := range files {
		buffer.WriteString(file.RelativePath)
		buffer.WriteByte(0)
		buffer.WriteString(file.SHA256)
		buffer.WriteByte('\n')
	}
	return sha256Hex(buffer.Bytes()), nil
}

type materializedFile struct {
	RelativePath string
	SHA256       string
}

func materializedFiles(sourceRoot string, includePatterns []string) ([]materializedFile, error) {
	info, err := os.Stat(sourceRoot)
	if err != nil {
		return nil, fmt.Errorf("stat directory source %q: %w", sourceRoot, err)
	}
	if !info.IsDir() {
		return nil, fmt.Errorf("directory source path is not a directory: %q", sourceRoot)
	}
	files := make([]materializedFile, 0)
	err = filepath.WalkDir(sourceRoot, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			return nil
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return fmt.Errorf("directory source includes unsupported symlink: %s", path)
		}
		fileInfo, err := entry.Info()
		if err != nil {
			return err
		}
		if !fileInfo.Mode().IsRegular() {
			return fmt.Errorf("directory source includes unsupported non-regular file: %s", path)
		}
		rel, err := filepath.Rel(sourceRoot, path)
		if err != nil {
			return err
		}
		rel = filepath.ToSlash(rel)
		if !matchesAnyPattern(includePatterns, rel) {
			return nil
		}
		payload, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		files = append(files, materializedFile{RelativePath: rel, SHA256: sha256Hex(payload)})
		return nil
	})
	if err != nil {
		return nil, fmt.Errorf("walk directory source %q: %w", sourceRoot, err)
	}
	sort.Slice(files, func(i, j int) bool {
		return files[i].RelativePath < files[j].RelativePath
	})
	return files, nil
}

func matchesAnyPattern(patterns []string, relativePath string) bool {
	if len(patterns) == 0 {
		return true
	}
	for _, pattern := range patterns {
		if matchPattern(normalizePattern(pattern), relativePath) {
			return true
		}
	}
	return false
}

func normalizePattern(pattern string) string {
	normalized := strings.ReplaceAll(pattern, "\\", "/")
	for strings.HasPrefix(normalized, "./") {
		normalized = strings.TrimPrefix(normalized, "./")
	}
	if normalized == "" {
		return ""
	}
	return pathpkg.Clean(normalized)
}

func matchPattern(pattern string, relativePath string) bool {
	patternParts := splitPattern(pattern)
	pathParts := splitPattern(filepath.ToSlash(relativePath))
	return matchPatternSegments(patternParts, pathParts)
}

func splitPattern(value string) []string {
	if value == "" || value == "." {
		return nil
	}
	return strings.Split(value, "/")
}

func matchPatternSegments(patternParts []string, pathParts []string) bool {
	if len(patternParts) == 0 {
		return len(pathParts) == 0
	}
	if patternParts[0] == "**" {
		for index := 0; index <= len(pathParts); index++ {
			if matchPatternSegments(patternParts[1:], pathParts[index:]) {
				return true
			}
		}
		return false
	}
	if len(pathParts) == 0 {
		return false
	}
	matched, err := pathpkg.Match(patternParts[0], pathParts[0])
	if err != nil || !matched {
		return false
	}
	return matchPatternSegments(patternParts[1:], pathParts[1:])
}

func normalizeRelativePath(target string, start string) (string, error) {
	rel, err := filepath.Rel(start, target)
	if err != nil {
		return "", fmt.Errorf("compute relative path from %q to %q: %w", start, target, err)
	}
	return normalizePosixPath(rel), nil
}

func normalizePosixPath(value string) string {
	normalized := strings.ReplaceAll(value, "\\", "/")
	if normalized == "" {
		return "."
	}
	for strings.HasPrefix(normalized, "./") {
		normalized = strings.TrimPrefix(normalized, "./")
	}
	cleaned := pathpkg.Clean(normalized)
	if cleaned == "" {
		return "."
	}
	if cleaned == "." && normalized != "." {
		return "."
	}
	return cleaned
}

func lockfileCanonicalValue(lockfile *codegenlock.CodegenLockfile) (map[string]any, error) {
	if lockfile == nil {
		return nil, fmt.Errorf("lockfile is nil")
	}
	result := map[string]any{
		"sources": map[string]any{},
		"version": lockfile.Version,
	}
	if lockfile.ManifestPath != nil {
		result["manifest_path"] = *lockfile.ManifestPath
	}
	if lockfile.GeneratedAt != nil {
		result["generated_at"] = lockfile.GeneratedAt.UTC().Format(time.RFC3339)
	}
	sources := result["sources"].(map[string]any)
	for name, source := range lockfile.Sources {
		value, err := lockedSourceCanonicalValue(source)
		if err != nil {
			return nil, fmt.Errorf("encode source %q: %w", name, err)
		}
		sources[name] = value
	}
	return result, nil
}

func lockedSourceCanonicalValue(source codegenlock.LockedSource) (map[string]any, error) {
	switch typed := source.(type) {
	case codegenlock.LockedDirectorySource:
		return lockedDirectoryCanonicalValue(typed), nil
	case *codegenlock.LockedDirectorySource:
		return lockedDirectoryCanonicalValue(*typed), nil
	case codegenlock.LockedUrlSource:
		return lockedURLCanonicalValue(typed), nil
	case *codegenlock.LockedUrlSource:
		return lockedURLCanonicalValue(*typed), nil
	case codegenlock.LockedGitHubRawSource:
		return lockedGitHubRawCanonicalValue(typed), nil
	case *codegenlock.LockedGitHubRawSource:
		return lockedGitHubRawCanonicalValue(*typed), nil
	default:
		return nil, fmt.Errorf("unsupported locked source type %T", source)
	}
}

func lockedDirectoryCanonicalValue(source codegenlock.LockedDirectorySource) map[string]any {
	result := map[string]any{
		"kind": "directory",
		"path": string(source.Path),
	}
	if source.ContentSha256 != nil {
		result["content_sha256"] = string(*source.ContentSha256)
	}
	if source.GeneratedAt != nil {
		result["generated_at"] = source.GeneratedAt.UTC().Format(time.RFC3339)
	}
	return result
}

func lockedURLCanonicalValue(source codegenlock.LockedUrlSource) map[string]any {
	result := map[string]any{
		"kind":   "url",
		"sha256": string(source.Sha256),
		"uri":    string(source.Uri),
	}
	if source.FetchedAt != nil {
		result["fetched_at"] = source.FetchedAt.UTC().Format(time.RFC3339)
	}
	if source.Etag != nil {
		result["etag"] = string(*source.Etag)
	}
	if source.LastModified != nil {
		result["last_modified"] = string(*source.LastModified)
	}
	return result
}

func lockedGitHubRawCanonicalValue(source codegenlock.LockedGitHubRawSource) map[string]any {
	result := map[string]any{
		"kind":   "github-raw",
		"owner":  string(source.Owner),
		"path":   string(source.Path),
		"ref":    string(source.Ref),
		"repo":   string(source.Repo),
		"sha256": string(source.Sha256),
		"uri":    string(source.Uri),
	}
	if source.FetchedAt != nil {
		result["fetched_at"] = source.FetchedAt.UTC().Format(time.RFC3339)
	}
	if source.Tag != nil {
		result["tag"] = string(*source.Tag)
	}
	if source.Package != nil {
		result["package"] = string(*source.Package)
	}
	if source.PackageVersion != nil {
		result["package_version"] = string(*source.PackageVersion)
	}
	return result
}

func writeCanonicalJSON(buffer *bytes.Buffer, value any) error {
	switch typed := value.(type) {
	case nil:
		buffer.WriteString("null")
	case string:
		data, err := json.Marshal(typed)
		if err != nil {
			return err
		}
		buffer.Write(data)
	case bool:
		if typed {
			buffer.WriteString("true")
		} else {
			buffer.WriteString("false")
		}
	case int:
		buffer.WriteString(strconv.Itoa(typed))
	case int64:
		buffer.WriteString(strconv.FormatInt(typed, 10))
	case []any:
		buffer.WriteByte('[')
		for index, item := range typed {
			if index > 0 {
				buffer.WriteByte(',')
			}
			if err := writeCanonicalJSON(buffer, item); err != nil {
				return err
			}
		}
		buffer.WriteByte(']')
	case map[string]any:
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		buffer.WriteByte('{')
		for index, key := range keys {
			if index > 0 {
				buffer.WriteByte(',')
			}
			encodedKey, err := json.Marshal(key)
			if err != nil {
				return err
			}
			buffer.Write(encodedKey)
			buffer.WriteByte(':')
			if err := writeCanonicalJSON(buffer, typed[key]); err != nil {
				return err
			}
		}
		buffer.WriteByte('}')
	case []string:
		items := make([]any, 0, len(typed))
		for _, item := range typed {
			items = append(items, item)
		}
		return writeCanonicalJSON(buffer, items)
	default:
		return fmt.Errorf("unsupported canonical JSON value %T", value)
	}
	return nil
}

func sha256Hex(data []byte) string {
	digest := sha256.Sum256(data)
	return hex.EncodeToString(digest[:])
}

func ptr[T any](value T) *T {
	return &value
}
