"""One-liner factory registrations for simple updaters."""

from update.updaters.base import (
    bun_node_modules_updater,
    deno_deps_updater,
    go_vendor_updater,
    npm_deps_updater,
)

go_vendor_updater("axiom-cli")
go_vendor_updater("beads")
go_vendor_updater("crush")
go_vendor_updater("gogcli")
# codex uses crane (not rustPlatform.buildRustPackage), so there is no
# cargoHash to compute — crane derives deps from the lockfile directly.
# The flake input ref is still updated via the refs phase.
npm_deps_updater("gemini-cli")
deno_deps_updater("linear-cli")
bun_node_modules_updater("opencode")
