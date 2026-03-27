package workflowschema

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"sort"
	"time"
)

const (
	officialRootDefinitionName           = "workflow-root"
	officialStrictRootDefinitionName     = "workflow-root-strict"
	officialStrictEventMappingDefinition = "on-mapping-strict"
	officialScheduleCronDefinitionName   = "cron-mapping"
	officialWorkflowDispatchMappingName  = "workflow-dispatch-mapping"
	officialPullRequestMappingName       = "pull-request-mapping"
	officialPullRequestTargetMappingName = "pull-request-target-mapping"
)

// OfficialSchema is the typed representation of GitHub's workflow-v1.0 schema DSL.
type OfficialSchema struct {
	Version     string                        `json:"version"`
	Definitions map[string]OfficialDefinition `json:"definitions"`
}

// OfficialDefinition is one named schema definition in workflow-v1.0.json.
type OfficialDefinition struct {
	Description   string            `json:"description,omitempty"`
	Context       []string          `json:"context,omitempty"`
	OneOf         []string          `json:"one-of,omitempty"`
	Mapping       *OfficialMapping  `json:"mapping,omitempty"`
	Sequence      *OfficialSequence `json:"sequence,omitempty"`
	String        *OfficialString   `json:"string,omitempty"`
	AllowedValues []string          `json:"allowed-values,omitempty"`
	Null          *OfficialEmpty    `json:"null,omitempty"`
	Boolean       *OfficialEmpty    `json:"boolean,omitempty"`
	Number        *OfficialEmpty    `json:"number,omitempty"`
}

// OfficialEmpty marks presence-only DSL sections like {"null":{}}.
type OfficialEmpty struct{}

// OfficialMapping describes the custom mapping node in the workflow schema DSL.
type OfficialMapping struct {
	Properties     map[string]OfficialTypeRef `json:"properties,omitempty"`
	LooseKeyType   string                     `json:"loose-key-type,omitempty"`
	LooseValueType string                     `json:"loose-value-type,omitempty"`
}

// OfficialSequence describes a custom sequence node in the workflow schema DSL.
type OfficialSequence struct {
	ItemType string `json:"item-type,omitempty"`
}

// OfficialString describes a custom string node in the workflow schema DSL.
type OfficialString struct {
	Constant        string `json:"constant,omitempty"`
	RequireNonEmpty bool   `json:"require-non-empty,omitempty"`
}

// OfficialTypeRef references another named definition, optionally with metadata.
type OfficialTypeRef struct {
	Type        string `json:"type,omitempty"`
	Required    bool   `json:"required,omitempty"`
	Description string `json:"description,omitempty"`
}

// OfficialSchemaSummary is a compact inspection view of the workflow DSL.
type OfficialSchemaSummary struct {
	Version                       string
	DefinitionCount               int
	StrictRootProperties          []string
	StrictEventNames              []string
	HasImageVersionEvent          bool
	HasScheduleTimezone           bool
	HasWorkflowDispatchInputs     bool
	PullRequestSupportsTags       bool
	PullRequestTargetSupportsTags bool
}

// FetchOptions controls schema snapshot refresh behavior.
type FetchOptions struct {
	SourceURL   string
	IncludeDocs bool
}

// FetchResult summarizes one workflow schema refresh.
type FetchResult struct {
	Path            string
	SHA256          string
	SourceURL       string
	Version         string
	DefinitionCount int
	Documents       []DocumentSnapshot
}

func (r *OfficialTypeRef) UnmarshalJSON(data []byte) error {
	var text string
	if err := json.Unmarshal(data, &text); err == nil {
		r.Type = text
		return nil
	}
	var alias struct {
		Type        string `json:"type"`
		Required    bool   `json:"required,omitempty"`
		Description string `json:"description,omitempty"`
	}
	if err := json.Unmarshal(data, &alias); err != nil {
		return fmt.Errorf("decode official type ref: %w", err)
	}
	r.Type = alias.Type
	r.Required = alias.Required
	r.Description = alias.Description
	return nil
}

// Validate checks the minimal structural invariants ghawfr expects from the workflow DSL.
func (s OfficialSchema) Validate() error {
	if s.Version == "" {
		return fmt.Errorf("version must not be empty")
	}
	if len(s.Definitions) == 0 {
		return fmt.Errorf("definitions must not be empty")
	}
	for _, required := range []string{
		officialRootDefinitionName,
		officialStrictRootDefinitionName,
		officialStrictEventMappingDefinition,
	} {
		if _, ok := s.Definitions[required]; !ok {
			return fmt.Errorf("missing definition %q", required)
		}
	}
	return nil
}

// Definition returns one named definition from the workflow schema.
func (s OfficialSchema) Definition(name string) (OfficialDefinition, bool) {
	definition, ok := s.Definitions[name]
	return definition, ok
}

// RootProperties returns the sorted property names from the strict or non-strict workflow root.
func (s OfficialSchema) RootProperties(strict bool) ([]string, error) {
	name := officialRootDefinitionName
	if strict {
		name = officialStrictRootDefinitionName
	}
	return s.MappingPropertyNames(name)
}

// StrictEventNames returns the sorted workflow trigger names from on-mapping-strict.
func (s OfficialSchema) StrictEventNames() ([]string, error) {
	return s.MappingPropertyNames(officialStrictEventMappingDefinition)
}

// MappingPropertyNames returns sorted property names for a mapping definition.
func (s OfficialSchema) MappingPropertyNames(definitionName string) ([]string, error) {
	properties, err := s.MappingProperties(definitionName)
	if err != nil {
		return nil, err
	}
	names := make([]string, 0, len(properties))
	for name := range properties {
		names = append(names, name)
	}
	sort.Strings(names)
	return names, nil
}

// MappingProperties returns the properties for one mapping definition.
func (s OfficialSchema) MappingProperties(definitionName string) (map[string]OfficialTypeRef, error) {
	definition, ok := s.Definition(definitionName)
	if !ok {
		return nil, fmt.Errorf("missing definition %q", definitionName)
	}
	if definition.Mapping == nil {
		return nil, fmt.Errorf("definition %q is not a mapping", definitionName)
	}
	return definition.Mapping.Properties, nil
}

// HasMappingProperty reports whether a mapping definition exposes one property.
func (s OfficialSchema) HasMappingProperty(definitionName string, property string) bool {
	properties, err := s.MappingProperties(definitionName)
	if err != nil {
		return false
	}
	_, ok := properties[property]
	return ok
}

// SummarizeOfficialSchema returns a compact feature summary for the workflow DSL.
func SummarizeOfficialSchema(schema *OfficialSchema) (OfficialSchemaSummary, error) {
	if schema == nil {
		return OfficialSchemaSummary{}, fmt.Errorf("official schema is nil")
	}
	if err := schema.Validate(); err != nil {
		return OfficialSchemaSummary{}, err
	}
	rootProperties, err := schema.RootProperties(true)
	if err != nil {
		return OfficialSchemaSummary{}, err
	}
	strictEvents, err := schema.StrictEventNames()
	if err != nil {
		return OfficialSchemaSummary{}, err
	}
	return OfficialSchemaSummary{
		Version:                       schema.Version,
		DefinitionCount:               len(schema.Definitions),
		StrictRootProperties:          rootProperties,
		StrictEventNames:              strictEvents,
		HasImageVersionEvent:          containsString(strictEvents, "image_version"),
		HasScheduleTimezone:           schema.HasMappingProperty(officialScheduleCronDefinitionName, "timezone"),
		HasWorkflowDispatchInputs:     schema.HasMappingProperty(officialWorkflowDispatchMappingName, "inputs"),
		PullRequestSupportsTags:       schema.HasMappingProperty(officialPullRequestMappingName, "tags"),
		PullRequestTargetSupportsTags: schema.HasMappingProperty(officialPullRequestTargetMappingName, "tags"),
	}, nil
}

// Inspect loads and summarizes the checked-in workflow schema snapshot.
func Inspect(layout Layout) (OfficialSchemaSummary, error) {
	schema, _, err := loadOfficialSchema(layout.OfficialSchemaPath)
	if err != nil {
		return OfficialSchemaSummary{}, err
	}
	return SummarizeOfficialSchema(schema)
}

// Fetch downloads the workflow DSL snapshot, records it in the manifest, and optionally refreshes cached docs.
func Fetch(ctx context.Context, layout Layout, client HTTPClient, options FetchOptions) (FetchResult, error) {
	sourceURL := options.SourceURL
	if sourceURL == "" {
		sourceURL = defaultWorkflowSchemaSourceURL
	}
	if client == nil {
		client = http.DefaultClient
	}
	body, err := fetchURL(ctx, client, sourceURL)
	if err != nil {
		return FetchResult{}, fmt.Errorf("fetch workflow schema: %w", err)
	}
	schema, err := decodeOfficialSchema(body)
	if err != nil {
		return FetchResult{}, fmt.Errorf("verify workflow schema: %w", err)
	}
	if err := writeFile(layout.OfficialSchemaPath, body); err != nil {
		return FetchResult{}, err
	}
	manifest, err := loadManifest(layout.ManifestPath)
	if err != nil {
		return FetchResult{}, err
	}
	manifest.SourceURL = sourceURL
	manifest.Path = layout.relative(layout.OfficialSchemaPath)
	manifest.SHA256 = sha256Hex(body)
	manifest.FetchedAt = time.Now().UTC()
	manifest.Version = schema.Version
	manifest.DefinitionCount = len(schema.Definitions)
	manifest.SourceCommit = ""
	manifest.SourceTag = ""
	manifest.PackageName = ""
	manifest.PackageVersion = ""
	if sourceURL == defaultWorkflowSchemaSourceURL {
		manifest.SourceCommit = defaultWorkflowSchemaSourceCommit
		manifest.SourceTag = defaultWorkflowSchemaSourceTag
		manifest.PackageName = defaultWorkflowSchemaPackageName
		manifest.PackageVersion = defaultWorkflowSchemaPackageVersion
	}
	if err := writeManifest(layout.ManifestPath, manifest); err != nil {
		return FetchResult{}, err
	}

	var documents []DocumentSnapshot
	if options.IncludeDocs {
		documents, err = FetchDocs(ctx, layout, client)
		if err != nil {
			return FetchResult{}, err
		}
	}

	return FetchResult{
		Path:            layout.OfficialSchemaPath,
		SHA256:          manifest.SHA256,
		SourceURL:       sourceURL,
		Version:         schema.Version,
		DefinitionCount: len(schema.Definitions),
		Documents:       documents,
	}, nil
}

func loadOfficialSchema(path string) (*OfficialSchema, []byte, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, nil, fmt.Errorf("read workflow schema %q: %w", path, err)
	}
	schema, err := decodeOfficialSchema(data)
	if err != nil {
		return nil, nil, fmt.Errorf("decode workflow schema %q: %w", path, err)
	}
	return schema, data, nil
}

func decodeOfficialSchema(data []byte) (*OfficialSchema, error) {
	var schema OfficialSchema
	if err := json.Unmarshal(data, &schema); err != nil {
		return nil, fmt.Errorf("unmarshal workflow schema: %w", err)
	}
	if err := schema.Validate(); err != nil {
		return nil, err
	}
	return &schema, nil
}

func containsString(values []string, needle string) bool {
	for _, value := range values {
		if value == needle {
			return true
		}
	}
	return false
}
