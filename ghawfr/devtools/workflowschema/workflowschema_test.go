package workflowschema

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"strings"
	"testing"
)

func TestFetchDocsWritesSnapshots(t *testing.T) {
	root := t.TempDir()
	layout := NewLayout(root)
	client := roundTripFunc(func(request *http.Request) (*http.Response, error) {
		switch request.URL.String() {
		case "https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions":
			return textResponse(http.StatusOK, nil, `<html><body><h1>Workflow Syntax</h1><p>Hello   World</p><script>ignored()</script></body></html>`), nil
		case "https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows":
			return textResponse(http.StatusOK, nil, `<html><body><style>ignored {}</style><p>Events &amp; Triggers</p></body></html>`), nil
		default:
			return nil, fmt.Errorf("unexpected request URL %q", request.URL.String())
		}
	})

	results, err := FetchDocs(context.Background(), layout, &http.Client{Transport: client})
	if err != nil {
		t.Fatalf("FetchDocs: %v", err)
	}
	if len(results) != len(DefaultDocumentSpecs()) {
		t.Fatalf("len(results) = %d, want %d", len(results), len(DefaultDocumentSpecs()))
	}
	for _, result := range results {
		if _, err := os.Stat(result.HTMLPath); err != nil {
			t.Fatalf("Stat(%q): %v", result.HTMLPath, err)
		}
		text, err := os.ReadFile(result.TextPath)
		if err != nil {
			t.Fatalf("ReadFile(%q): %v", result.TextPath, err)
		}
		if !strings.HasSuffix(string(text), "\n") {
			t.Fatalf("cached text %q does not end with newline", result.TextPath)
		}
	}

	syntaxText, err := os.ReadFile(layout.DocTextPath(docWorkflowSyntax))
	if err != nil {
		t.Fatalf("ReadFile(%q): %v", layout.DocTextPath(docWorkflowSyntax), err)
	}
	if got := string(syntaxText); !strings.Contains(got, "workflow syntax hello world") {
		t.Fatalf("workflow syntax text = %q, want normalized content", got)
	}

	eventsText, err := os.ReadFile(layout.DocTextPath(docWorkflowEvents))
	if err != nil {
		t.Fatalf("ReadFile(%q): %v", layout.DocTextPath(docWorkflowEvents), err)
	}
	if got := string(eventsText); !strings.Contains(got, "events & triggers") {
		t.Fatalf("events text = %q, want unescaped content", got)
	}
}

func mustWriteFile(t *testing.T, path string, data []byte) {
	t.Helper()
	if err := writeFile(path, data); err != nil {
		t.Fatalf("writeFile(%q): %v", path, err)
	}
}
