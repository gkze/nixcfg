package backend

import (
	"fmt"
	"os"
	"runtime"

	"github.com/gkze/ghawfr/state"
	"github.com/gkze/ghawfr/workflow"
)

type runnerFilesystem struct {
	HostTemp       string
	HostToolCache  string
	HostHome       string
	GuestTemp      string
	GuestToolCache string
	GuestHome      string
}

func runnerContextForCurrentHost() workflow.RunnerContext {
	return runnerContextForHost(runtime.GOOS, runtime.GOARCH)
}

func runnerContextForHost(goos string, goarch string) workflow.RunnerContext {
	return workflow.RunnerContext{OS: githubRunnerOS(goos), Arch: githubRunnerArch(goarch)}
}

func runnerContextForRequirements(requirements WorkerRequirements) workflow.RunnerContext {
	return workflow.RunnerContext{OS: githubRunnerOS(string(requirements.OS)), Arch: githubRunnerArch(string(requirements.Arch))}
}

func runnerEnvironment(context workflow.RunnerContext) workflow.EnvironmentMap {
	env := workflow.EnvironmentMap{}
	if context.Temp != "" {
		env["RUNNER_TEMP"] = context.Temp
	}
	if context.ToolCache != "" {
		env["RUNNER_TOOL_CACHE"] = context.ToolCache
		env["AGENT_TOOLSDIRECTORY"] = context.ToolCache
	}
	if context.Home != "" {
		env["HOME"] = context.Home
	}
	if len(env) == 0 {
		return nil
	}
	return env
}

func prepareRunnerFilesystem(hostWorkspace string, guestWorkspace string, includeHome bool) (runnerFilesystem, error) {
	hostTemp := state.RunnerTempDir(hostWorkspace)
	hostToolCache := state.RunnerToolCacheDir(hostWorkspace)
	directories := []string{hostTemp, hostToolCache}
	hostHome := ""
	guestHome := ""
	if includeHome {
		hostHome = state.RunnerHomeDir(hostWorkspace)
		guestHome = state.GuestRunnerHomeDir(guestWorkspace)
		directories = append(directories, hostHome)
	}
	for _, directory := range directories {
		if err := os.MkdirAll(directory, 0o755); err != nil {
			return runnerFilesystem{}, fmt.Errorf("create runner directory %q: %w", directory, err)
		}
	}
	return runnerFilesystem{
		HostTemp:       hostTemp,
		HostToolCache:  hostToolCache,
		HostHome:       hostHome,
		GuestTemp:      state.GuestRunnerTempDir(guestWorkspace),
		GuestToolCache: state.GuestRunnerToolCacheDir(guestWorkspace),
		GuestHome:      guestHome,
	}, nil
}

func applyRunnerFilesystem(base workflow.RunnerContext, filesystem runnerFilesystem, remote bool) workflow.RunnerContext {
	context := base
	if remote {
		context.Temp = filesystem.GuestTemp
		context.ToolCache = filesystem.GuestToolCache
		context.Home = filesystem.GuestHome
		return context
	}
	context.Temp = filesystem.HostTemp
	context.ToolCache = filesystem.HostToolCache
	return context
}

func githubRunnerOS(value string) string {
	switch value {
	case "darwin":
		return "macOS"
	case "linux":
		return "Linux"
	case "windows":
		return "Windows"
	default:
		return value
	}
}

func githubRunnerArch(value string) string {
	switch value {
	case "amd64", "x86_64":
		return "X64"
	case "arm64", "aarch64":
		return "ARM64"
	default:
		return value
	}
}
