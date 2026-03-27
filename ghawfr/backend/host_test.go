package backend

import (
	"reflect"
	"testing"
)

func TestRunnerLabelsForHost(t *testing.T) {
	tests := []struct {
		name   string
		goos   string
		goarch string
		want   []string
	}{
		{name: "darwin arm64", goos: "darwin", goarch: "arm64", want: []string{"local", "macos-15", "macos-latest"}},
		{name: "linux amd64", goos: "linux", goarch: "amd64", want: []string{"local", "ubuntu-24.04", "ubuntu-latest"}},
		{name: "linux arm64", goos: "linux", goarch: "arm64", want: []string{"local", "ubuntu-24.04-arm", "ubuntu-latest"}},
		{name: "other", goos: "freebsd", goarch: "amd64", want: []string{"local"}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got := RunnerLabelsForHost(test.goos, test.goarch)
			if !reflect.DeepEqual(got, test.want) {
				t.Fatalf("RunnerLabelsForHost(%q, %q) = %#v, want %#v", test.goos, test.goarch, got, test.want)
			}
		})
	}
}
