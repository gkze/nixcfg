package workflowschema

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDecodeAndSummarizeOfficialSchemaFixture(t *testing.T) {
	schema, err := decodeOfficialSchema([]byte(fixtureOfficialWorkflowSchema))
	if err != nil {
		t.Fatalf("decodeOfficialSchema: %v", err)
	}
	summary, err := SummarizeOfficialSchema(schema)
	if err != nil {
		t.Fatalf("SummarizeOfficialSchema: %v", err)
	}
	if summary.Version != "workflow-v1.0" {
		t.Fatalf("summary.Version = %q, want workflow-v1.0", summary.Version)
	}
	if summary.DefinitionCount != 9 {
		t.Fatalf("summary.DefinitionCount = %d, want 9", summary.DefinitionCount)
	}
	if got, want := strings.Join(summary.StrictRootProperties, ","), "jobs,on,run-name"; got != want {
		t.Fatalf("StrictRootProperties = %q, want %q", got, want)
	}
	if got, want := strings.Join(summary.StrictEventNames, ","), "image_version,pull_request,pull_request_target,schedule,workflow_dispatch"; got != want {
		t.Fatalf("StrictEventNames = %q, want %q", got, want)
	}
	if !summary.HasImageVersionEvent {
		t.Fatalf("HasImageVersionEvent = false, want true")
	}
	if !summary.HasScheduleTimezone {
		t.Fatalf("HasScheduleTimezone = false, want true")
	}
	if !summary.HasWorkflowDispatchInputs {
		t.Fatalf("HasWorkflowDispatchInputs = false, want true")
	}
	if summary.PullRequestSupportsTags {
		t.Fatalf("PullRequestSupportsTags = true, want false")
	}
	if summary.PullRequestTargetSupportsTags {
		t.Fatalf("PullRequestTargetSupportsTags = true, want false")
	}
}

func TestFetchWritesSnapshotAndManifest(t *testing.T) {
	root := t.TempDir()
	layout := NewLayout(root)
	client := roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.String() != defaultWorkflowSchemaSourceURL {
			return nil, fmt.Errorf("unexpected request URL %q", request.URL.String())
		}
		return textResponse(http.StatusOK, nil, fixtureOfficialWorkflowSchema), nil
	})

	result, err := Fetch(context.Background(), layout, &http.Client{Transport: client}, FetchOptions{})
	if err != nil {
		t.Fatalf("Fetch: %v", err)
	}
	if result.Path != layout.OfficialSchemaPath {
		t.Fatalf("result.Path = %q, want %q", result.Path, layout.OfficialSchemaPath)
	}
	if result.Version != "workflow-v1.0" {
		t.Fatalf("result.Version = %q, want workflow-v1.0", result.Version)
	}
	if result.DefinitionCount != 9 {
		t.Fatalf("result.DefinitionCount = %d, want 9", result.DefinitionCount)
	}
	if _, err := os.Stat(layout.OfficialSchemaPath); err != nil {
		t.Fatalf("Stat(%q): %v", layout.OfficialSchemaPath, err)
	}
	manifest, err := loadManifest(layout.ManifestPath)
	if err != nil {
		t.Fatalf("loadManifest: %v", err)
	}
	if manifest.SourceURL != defaultWorkflowSchemaSourceURL {
		t.Fatalf("manifest.SourceURL = %q, want default", manifest.SourceURL)
	}
	if manifest.Path != filepath.ToSlash(filepath.Join("workflow", "schema", "official", "workflow-v1.0.json")) {
		t.Fatalf("manifest.Path = %q, want official snapshot path", manifest.Path)
	}
	if manifest.SourceCommit != defaultWorkflowSchemaSourceCommit {
		t.Fatalf("manifest.SourceCommit = %q, want %q", manifest.SourceCommit, defaultWorkflowSchemaSourceCommit)
	}
	if manifest.SourceTag != defaultWorkflowSchemaSourceTag {
		t.Fatalf("manifest.SourceTag = %q, want %q", manifest.SourceTag, defaultWorkflowSchemaSourceTag)
	}
	if manifest.PackageName != defaultWorkflowSchemaPackageName {
		t.Fatalf("manifest.PackageName = %q, want %q", manifest.PackageName, defaultWorkflowSchemaPackageName)
	}
	if manifest.PackageVersion != defaultWorkflowSchemaPackageVersion {
		t.Fatalf("manifest.PackageVersion = %q, want %q", manifest.PackageVersion, defaultWorkflowSchemaPackageVersion)
	}
}

func TestInspectReadsSnapshotFromLayout(t *testing.T) {
	root := t.TempDir()
	layout := NewLayout(root)
	mustWriteFile(t, layout.OfficialSchemaPath, []byte(fixtureOfficialWorkflowSchema))

	summary, err := Inspect(layout)
	if err != nil {
		t.Fatalf("Inspect: %v", err)
	}
	if !summary.HasScheduleTimezone {
		t.Fatalf("HasScheduleTimezone = false, want true")
	}
	if summary.PullRequestSupportsTags {
		t.Fatalf("PullRequestSupportsTags = true, want false")
	}
}

func TestFetchWithDocsWritesSnapshotManifestAndDocs(t *testing.T) {
	root := t.TempDir()
	layout := NewLayout(root)
	client := roundTripFunc(func(request *http.Request) (*http.Response, error) {
		switch request.URL.String() {
		case defaultWorkflowSchemaSourceURL:
			return textResponse(http.StatusOK, nil, fixtureOfficialWorkflowSchema), nil
		case "https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions":
			return textResponse(http.StatusOK, nil, `<html><body><p>Workflow Syntax</p></body></html>`), nil
		case "https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows":
			return textResponse(http.StatusOK, nil, `<html><body><p>Events</p></body></html>`), nil
		default:
			return nil, fmt.Errorf("unexpected request URL %q", request.URL.String())
		}
	})

	result, err := Fetch(context.Background(), layout, &http.Client{Transport: client}, FetchOptions{IncludeDocs: true})
	if err != nil {
		t.Fatalf("Fetch: %v", err)
	}
	if len(result.Documents) != len(DefaultDocumentSpecs()) {
		t.Fatalf("len(result.Documents) = %d, want %d", len(result.Documents), len(DefaultDocumentSpecs()))
	}
	for _, document := range result.Documents {
		if _, err := os.Stat(document.HTMLPath); err != nil {
			t.Fatalf("Stat(%q): %v", document.HTMLPath, err)
		}
		if _, err := os.Stat(document.TextPath); err != nil {
			t.Fatalf("Stat(%q): %v", document.TextPath, err)
		}
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

const fixtureOfficialWorkflowSchema = `
{
  "version": "workflow-v1.0",
  "definitions": {
    "workflow-root": {
      "mapping": {
        "properties": {
          "on": "on",
          "jobs": {
            "type": "jobs",
            "required": true
          }
        }
      }
    },
    "workflow-root-strict": {
      "mapping": {
        "properties": {
          "on": {
            "type": "on-strict",
            "required": true
          },
          "run-name": "run-name",
          "jobs": {
            "type": "jobs",
            "required": true
          }
        }
      }
    },
    "on-mapping-strict": {
      "mapping": {
        "properties": {
          "image_version": "image-version",
          "pull_request": "pull-request",
          "pull_request_target": "pull-request-target",
          "schedule": "schedule",
          "workflow_dispatch": "workflow-dispatch"
        }
      }
    },
    "cron-mapping": {
      "mapping": {
        "properties": {
          "cron": {
            "type": "cron-pattern",
            "required": true
          },
          "timezone": "timezone-string"
        }
      }
    },
    "workflow-dispatch-mapping": {
      "mapping": {
        "properties": {
          "inputs": "workflow-dispatch-inputs"
        }
      }
    },
    "pull-request-mapping": {
      "mapping": {
        "properties": {
          "branches": "event-branches",
          "paths": "event-paths"
        }
      }
    },
    "pull-request-target-mapping": {
      "mapping": {
        "properties": {
          "branches": "event-branches",
          "paths": "event-paths"
        }
      }
    },
    "run-name": {
      "context": ["github", "inputs", "vars"],
      "string": {}
    },
    "workflow-dispatch": {
      "one-of": ["null", "workflow-dispatch-mapping"]
    }
  }
}
`
