package actionadapter

import (
	"sort"
	"testing"

	"github.com/gkze/ghawfr/backend"
)

func TestCuratedActionHandlerRegistriesMatch(t *testing.T) {
	gotLocal := sortedActionHandlerKeys(LocalHandlers())
	gotRemote := sortedActionHandlerKeys(RemoteHandlers("/guest/workspace", nil))
	want := []string{
		"actions/cache",
		"actions/cache/restore",
		"actions/cache/save",
		"actions/checkout",
		"actions/download-artifact",
		"actions/setup-python",
		"actions/upload-artifact",
		"astral-sh/setup-uv",
		"cachix/cachix-action",
		"determinatesystems/determinate-nix-action",
		"peter-evans/create-pull-request",
	}
	if len(gotLocal) != len(want) {
		t.Fatalf("len(LocalHandlers()) = %d, want %d", len(gotLocal), len(want))
	}
	if len(gotRemote) != len(want) {
		t.Fatalf("len(RemoteHandlers()) = %d, want %d", len(gotRemote), len(want))
	}
	localHandlers := LocalHandlers()
	remoteHandlers := RemoteHandlers("/guest/workspace", nil)
	for i, key := range want {
		if gotLocal[i] != key {
			t.Fatalf("LocalHandlers()[%d] = %q, want %q", i, gotLocal[i], key)
		}
		if gotRemote[i] != key {
			t.Fatalf("RemoteHandlers()[%d] = %q, want %q", i, gotRemote[i], key)
		}
		if localHandlers[key] == nil {
			t.Fatalf("LocalHandlers()[%q] = nil, want registered handler", key)
		}
		if remoteHandlers[key] == nil {
			t.Fatalf("RemoteHandlers()[%q] = nil, want registered handler", key)
		}
	}
}

func sortedActionHandlerKeys(handlers map[string]backend.ActionHandler) []string {
	keys := make([]string, 0, len(handlers))
	for key := range handlers {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}
