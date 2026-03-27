package workflowschema

import (
	"context"
	"fmt"
	"html"
	"net/http"
	"regexp"
	"strings"
)

var (
	reScript = regexp.MustCompile(`(?is)<script[^>]*>.*?</script>`)
	reStyle  = regexp.MustCompile(`(?is)<style[^>]*>.*?</style>`)
	reTag    = regexp.MustCompile(`(?s)<[^>]+>`)
	reSpace  = regexp.MustCompile(`\s+`)
)

// DocumentSnapshot is one cached GitHub docs page in HTML and normalized text form.
type DocumentSnapshot struct {
	Name     string `json:"name"`
	URL      string `json:"url"`
	HTMLPath string `json:"html_path"`
	TextPath string `json:"text_path"`
}

// FetchDocs downloads and caches the curated GitHub docs pages used by audit.
func FetchDocs(ctx context.Context, layout Layout, client HTTPClient) ([]DocumentSnapshot, error) {
	if client == nil {
		client = http.DefaultClient
	}
	results := make([]DocumentSnapshot, 0, len(DefaultDocumentSpecs()))
	for _, document := range DefaultDocumentSpecs() {
		body, err := fetchURL(ctx, client, document.URL)
		if err != nil {
			return nil, fmt.Errorf("fetch docs page %q: %w", document.URL, err)
		}
		htmlPath := layout.DocHTMLPath(document.Name)
		textPath := layout.DocTextPath(document.Name)
		if err := writeFile(htmlPath, body); err != nil {
			return nil, err
		}
		text := []byte(normalizeDocumentText(string(body)))
		if err := writeFile(textPath, text); err != nil {
			return nil, err
		}
		results = append(results, DocumentSnapshot{
			Name:     document.Name,
			URL:      document.URL,
			HTMLPath: htmlPath,
			TextPath: textPath,
		})
	}
	return results, nil
}

func normalizeDocumentText(input string) string {
	withoutScripts := reScript.ReplaceAllString(input, " ")
	withoutStyles := reStyle.ReplaceAllString(withoutScripts, " ")
	withoutTags := reTag.ReplaceAllString(withoutStyles, " ")
	unescaped := html.UnescapeString(withoutTags)
	normalized := strings.ToLower(unescaped)
	normalized = reSpace.ReplaceAllString(normalized, " ")
	return strings.TrimSpace(normalized) + "\n"
}
