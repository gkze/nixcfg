package workflowschema

import (
	"context"
	"fmt"
	"io"
	"net/http"
)

// HTTPClient is the subset of *http.Client used by the schema devtool.
type HTTPClient interface {
	Do(req *http.Request) (*http.Response, error)
}

func fetchURL(ctx context.Context, client HTTPClient, url string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, fmt.Errorf("build request for %q: %w", url, err)
	}
	req.Header.Set("User-Agent", "ghawfr-dev/workflowschema")

	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("GET %q: %w", url, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= http.StatusBadRequest {
		return nil, fmt.Errorf("GET %q: unexpected status %s", url, resp.Status)
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read %q: %w", url, err)
	}
	return body, nil
}
