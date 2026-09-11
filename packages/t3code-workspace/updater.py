"""Updater for the internal T3 Code workspace dependency cache."""

from typing import TYPE_CHECKING, Literal

from lib.update.nix import _build_repo_package_attr_expr
from lib.update.updaters import register_updater
from lib.update.updaters.flake_backed import FlakeInputHashUpdater

if TYPE_CHECKING:
    from lib.nix.models.sources import SourceEntry


@register_updater
class T3CodeWorkspaceUpdater(FlakeInputHashUpdater):
    """Build and certify the same prepared workspace dependency derivation.

    Re-evaluating after a build can observe different mutable checkout/store
    state and attach a certificate to a derivation that was never probed. The
    shared prepared-probe flow retains the evaluated build identity instead.
    """

    DARWIN_PLATFORM = "aarch64-darwin"

    name = "t3code-workspace"
    input_name = "t3code"
    hash_type: Literal["nodeModulesHash"] = "nodeModulesHash"
    platform_specific = True
    materialize_when_current = True
    native_only = True
    supported_platforms = (DARWIN_PLATFORM,)

    @classmethod
    def _workspace_expr(cls) -> str:
        return _build_repo_package_attr_expr(
            "packages/t3code-workspace/default.nix",
            "",
            system=cls.DARWIN_PLATFORM,
        )

    def _probe_expressions(self, source: SourceEntry) -> dict[str, str]:
        _ = source
        return {self.DARWIN_PLATFORM: self._workspace_expr()}
