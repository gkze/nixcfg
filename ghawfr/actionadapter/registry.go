package actionadapter

import (
	"context"

	"github.com/gkze/ghawfr/backend"
)

type curatedActionHandlerSet struct {
	checkout          backend.ActionHandler
	determinateNix    backend.ActionHandler
	cachix            backend.ActionHandler
	cache             backend.ActionHandler
	cacheRestore      backend.ActionHandler
	cacheSave         backend.ActionHandler
	uploadArtifact    backend.ActionHandler
	downloadArtifact  backend.ActionHandler
	setupPython       backend.ActionHandler
	setupUV           backend.ActionHandler
	createPullRequest backend.ActionHandler
}

func buildCuratedActionHandlers(set curatedActionHandlerSet) map[string]backend.ActionHandler {
	return map[string]backend.ActionHandler{
		"actions/checkout":                          set.checkout,
		"determinatesystems/determinate-nix-action": set.determinateNix,
		"cachix/cachix-action":                      set.cachix,
		"actions/cache":                             set.cache,
		"actions/cache/restore":                     set.cacheRestore,
		"actions/cache/save":                        set.cacheSave,
		"actions/upload-artifact":                   set.uploadArtifact,
		"actions/download-artifact":                 set.downloadArtifact,
		"actions/setup-python":                      set.setupPython,
		"astral-sh/setup-uv":                        set.setupUV,
		"peter-evans/create-pull-request":           set.createPullRequest,
	}
}

var createPullRequestSupportedInputs = []string{
	"sign-commits",
	"branch",
	"delete-branch",
	"title",
	"commit-message",
	"body",
	"body-path",
	"base",
	"token",
}

func handleAcceptedCreatePullRequestAction(
	_ context.Context,
	action backend.ActionContext,
) (backend.StepResult, error) {
	if err := rejectUnsupportedInputs(
		action,
		"create-pull-request",
		createPullRequestSupportedInputs...,
	); err != nil {
		return backend.StepResult{ID: action.Step.ID}, err
	}
	return backend.StepResult{ID: action.Step.ID}, nil
}
