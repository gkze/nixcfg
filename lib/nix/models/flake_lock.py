"""Hand-written model for Nix flake.lock format (no official schema exists)."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Source reference models
# ---------------------------------------------------------------------------


class LockedRef(BaseModel):
    """A fully-resolved ("locked") flake input reference.

    The ``locked`` field pins an input to an exact revision, content hash,
    and timestamp so that ``nix flake lock`` produces reproducible builds.

    Different source types (github, gitlab, git, path, tarball, ...) populate
    different subsets of these fields; ``extra="allow"`` accommodates any
    fields Nix may add for less common source types.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    type: str
    """Source type: ``github``, ``gitlab``, ``git``, ``path``, ``tarball``, etc."""

    nar_hash: str = Field(alias="narHash")
    """SRI content hash of the fetched source (e.g. ``sha256-...``)."""

    rev: str | None = None
    """Git revision (full SHA-1 hex digest)."""

    last_modified: int | None = Field(default=None, alias="lastModified")
    """Unix timestamp of the locked commit or file."""

    owner: str | None = None
    """Repository owner (github/gitlab types)."""

    repo: str | None = None
    """Repository name (github/gitlab types)."""

    url: str | None = None
    """Source URL (git/tarball/path types)."""

    ref: str | None = None
    """Git branch or tag name."""

    path: str | None = None
    """Filesystem path (path type)."""

    rev_count: int | None = Field(default=None, alias="revCount")
    """Number of ancestor commits (set by some fetchers)."""


class OriginalRef(BaseModel):
    """The *user-specified* ("original") flake input reference.

    This is the human-authored input spec before resolution — e.g. just
    ``owner`` + ``repo`` for a GitHub flake, possibly with a ``ref`` pin.
    """

    model_config = ConfigDict(extra="allow")

    type: str
    """Source type: ``github``, ``gitlab``, ``git``, ``path``, ``tarball``, etc."""

    owner: str | None = None
    """Repository owner (github/gitlab types)."""

    repo: str | None = None
    """Repository name (github/gitlab types)."""

    url: str | None = None
    """Source URL (git/tarball/path types)."""

    ref: str | None = None
    """Git branch or tag name."""

    path: str | None = None
    """Filesystem path (path type)."""


# ---------------------------------------------------------------------------
# Node and lock-file models
# ---------------------------------------------------------------------------


class FlakeLockNode(BaseModel):
    """A single node in the ``flake.lock`` dependency graph.

    The root node typically has only ``inputs`` (no ``locked``/``original``).
    Leaf nodes have ``locked`` and ``original`` but may omit ``inputs``.

    Input values are either a plain node name (``str``) or a path through
    another node's inputs (``list[str]``, e.g. ``["nixvim", "nixpkgs"]``).
    """

    model_config = ConfigDict(extra="allow")

    locked: LockedRef | None = None
    """Resolved source reference (absent on the root node)."""

    original: OriginalRef | None = None
    """User-specified source reference (absent on the root node)."""

    inputs: dict[str, str | list[str]] | None = None
    """Map of input names to node names or follow-through paths."""

    flake: bool | None = None
    """Explicitly ``False`` for non-flake inputs; omitted (``None``) when ``True``."""


class FlakeLock(BaseModel):
    """Top-level ``flake.lock`` file representation.

    Parse with :meth:`from_file` or :meth:`from_dict`, then inspect the
    dependency graph via :attr:`root_node`, :attr:`input_names`, and
    :meth:`get_locked`.
    """

    model_config = ConfigDict(extra="forbid")

    nodes: dict[str, FlakeLockNode]
    """All nodes in the lock graph, keyed by internal node name."""

    root: str = "root"
    """Name of the root node (always ``"root"`` in practice)."""

    version: int
    """Lock-file schema version (currently ``7``)."""

    # -- constructors -------------------------------------------------------

    @classmethod
    def from_file(cls, path: Path) -> FlakeLock:
        """Read and parse a ``flake.lock`` JSON file."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)

    @classmethod
    def from_dict(cls, data: dict) -> FlakeLock:
        """Parse from an already-loaded JSON dictionary."""
        return cls.model_validate(data)

    # -- properties ---------------------------------------------------------

    @property
    def root_node(self) -> FlakeLockNode:
        """Return the root node of the dependency graph."""
        return self.nodes[self.root]

    @property
    def input_names(self) -> list[str]:
        """Sorted list of the root node's direct input names."""
        inputs = self.root_node.inputs
        if inputs is None:
            return []
        return sorted(inputs.keys())

    # -- helpers ------------------------------------------------------------

    def get_locked(self, input_name: str) -> LockedRef | None:
        """Resolve a root-level input name to its :class:`LockedRef`.

        Follows single-level indirection: if the root's input value is a
        plain ``str``, that string names the target node directly.  If it is
        a ``list[str]`` path (e.g. ``["nixvim", "nixpkgs"]``), the path is
        walked through each intermediate node's ``inputs`` map.

        Returns ``None`` when the input or its target node has no ``locked``
        field.
        """
        node_name = self._resolve_target_node_name(input_name)
        if node_name is None:
            return None
        final = self.nodes.get(node_name)
        if final is None:
            return None
        return final.locked

    def _resolve_target_node_name(self, input_name: str) -> str | None:
        inputs = self.root_node.inputs
        if inputs is None or input_name not in inputs:
            return None

        target = inputs[input_name]
        if isinstance(target, str):
            return target

        node_name: str | None = None
        for i, segment in enumerate(target):
            if i == 0:
                node_name = segment
                continue

            if node_name is None:
                return None

            node = self.nodes.get(node_name)
            if node is None or node.inputs is None:
                return None

            ref = node.inputs.get(segment)
            if not isinstance(ref, str):
                return None
            node_name = ref

        return node_name
