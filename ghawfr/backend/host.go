package backend

import "runtime"

// RunnerLabelsForHost returns the default local runner labels advertised for one
// host OS/architecture pair.
func RunnerLabelsForHost(goos string, goarch string) []string {
	switch goos {
	case "darwin":
		return normalizeLabels([]string{"macos-15", "macos-latest", "local"})
	case "linux":
		switch goarch {
		case "arm64":
			return normalizeLabels([]string{"ubuntu-24.04-arm", "ubuntu-latest", "local"})
		default:
			return normalizeLabels([]string{"ubuntu-24.04", "ubuntu-latest", "local"})
		}
	default:
		return []string{"local"}
	}
}

// HostLocalWorker returns a generic local worker scoped to the current host
// platform. Callers that want curated uses: behavior should inject action
// handlers or use the actionadapter package.
func HostLocalWorker() Local {
	return Local{RunnerLabels: RunnerLabelsForHost(runtime.GOOS, runtime.GOARCH)}
}
