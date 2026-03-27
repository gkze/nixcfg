// Package actionadapter provides curated action-step adapters that can be
// registered with generic backend workers.
//
// The backend package owns execution semantics and worker/provider plumbing.
// This package owns higher-level behavior for selected uses: steps such as
// actions/checkout, setup actions, cache restore/save helpers, and artifact
// helpers.
package actionadapter
