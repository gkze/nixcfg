package workflowschema

import (
	"encoding/json"
	"fmt"
	"os"
	"time"
)

// Manifest records the checked-in workflow schema snapshot metadata.
type Manifest struct {
	SourceURL       string    `json:"source_url,omitempty"`
	Path            string    `json:"path,omitempty"`
	SHA256          string    `json:"sha256,omitempty"`
	FetchedAt       time.Time `json:"fetched_at,omitempty"`
	Version         string    `json:"version,omitempty"`
	DefinitionCount int       `json:"definition_count,omitempty"`
	SourceCommit    string    `json:"source_commit,omitempty"`
	SourceTag       string    `json:"source_tag,omitempty"`
	PackageName     string    `json:"package_name,omitempty"`
	PackageVersion  string    `json:"package_version,omitempty"`
}

func loadManifest(path string) (Manifest, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return Manifest{}, nil
		}
		return Manifest{}, fmt.Errorf("read manifest %q: %w", path, err)
	}

	var manifest Manifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		return Manifest{}, fmt.Errorf("decode manifest %q: %w", path, err)
	}
	return manifest, nil
}

func writeManifest(path string, manifest Manifest) error {
	data, err := marshalJSON(manifest)
	if err != nil {
		return fmt.Errorf("encode manifest %q: %w", path, err)
	}
	if err := writeFile(path, data); err != nil {
		return fmt.Errorf("write manifest %q: %w", path, err)
	}
	return nil
}
