"""Clean derivation models for the Nix derivation-v4 JSON schema.

Aligned with derivation-v4 schema from NixOS/nix. These models provide
a simplified, ergonomic interface over the auto-generated types in
``_generated.py``, collapsing the multiple output union variants into a
single ``DerivationOutput`` model with optional fields.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DerivationOutput(BaseModel):
    """A single derivation output specification.

    Aligned with derivation-v4 schema from NixOS/nix.  The generated schema
    represents outputs as a discriminated union of four variants:

    * **Input-addressed** -- only ``path`` is set.
    * **Fixed content-addressed** -- ``method`` and ``hash`` are set.
    * **Floating content-addressed** -- ``method`` and ``hashAlgo`` are set.
    * **Impure** -- ``impure`` is ``True``, plus ``method`` and ``hashAlgo``.

    This model flattens those variants into optional fields so callers can
    inspect whichever fields are present.
    """

    path: str | None = None
    """Store path for input-addressed outputs."""

    method: str | None = None
    """Content-addressing method (``flat``, ``nar``, ``text``, ``git``)."""

    hash_algo: str | None = Field(default=None, alias="hashAlgo")
    """Hash algorithm for floating CA or impure outputs."""

    hash: str | None = None
    """Expected content hash for fixed CA outputs (SRI string)."""

    impure: bool | None = None
    """``True`` for impure derivation outputs."""


class DerivationInputs(BaseModel):
    """Input dependencies for a derivation.

    Aligned with derivation-v4 schema from NixOS/nix.  In the v4 schema,
    inputs are split into plain source store paths (``srcs``) and derivation
    dependencies (``drvs``) that map a derivation path to the set of output
    names (or a richer ``Drvs`` object with ``dynamicOutputs``).
    """

    srcs: list[str] = Field(default_factory=list)
    """Input source store paths (non-derivation dependencies)."""

    drvs: dict[str, list[str] | dict[str, Any]] = Field(default_factory=dict)
    """Input derivation paths mapped to requested output names.

    Values are typically ``list[str]`` (output names), but may be a dict
    with ``outputs`` and ``dynamicOutputs`` keys for dynamic derivations.
    """


class Derivation(BaseModel):
    """A Nix store derivation (v4 JSON representation).

    Aligned with derivation-v4 schema from NixOS/nix.  This is the top-level
    model returned by ``nix derivation show`` (with ``--json`` /
    ``--extra-experimental-features nix-command``).  The ``extra="allow"``
    config accommodates forward-compatible fields that Nix may add in future
    schema revisions.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str
    """Derivation name, used when computing output store paths."""

    version: Literal[4] = 4
    """Schema version guard (must be ``4``)."""

    outputs: dict[str, DerivationOutput]
    """Mapping of output names to their specifications."""

    inputs: DerivationInputs
    """Source paths and derivation dependencies."""

    system: str
    """Target system type (e.g. ``x86_64-linux``, ``aarch64-darwin``)."""

    builder: str
    """Absolute path to the builder executable."""

    args: list[str] = Field(default_factory=list)
    """Command-line arguments passed to the builder."""

    env: dict[str, str] = Field(default_factory=dict)
    """Environment variables passed to the builder."""

    structured_attrs: dict[str, Any] | None = Field(
        default=None,
        alias="structuredAttrs",
    )
    """Structured attributes, if the derivation uses them."""

    # -- helpers -------------------------------------------------------------

    @property
    def output_names(self) -> list[str]:
        """Return a sorted list of output names defined by this derivation."""
        return sorted(self.outputs)

    @property
    def is_fixed_output(self) -> bool:
        """``True`` if any output has a fixed content hash."""
        return any(out.hash is not None for out in self.outputs.values())
