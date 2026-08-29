"""Focused Python-boundary tests for crate2nix source-slice generation."""

import json
import subprocess
from pathlib import Path

import pytest

from lib.nix.models.flake_lock import FlakeLockNode, LockedRef
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.update import crate2nix


def _target(
    *,
    source_input: str | None = "demo",
    root_src_relpath: Path = Path(),
    crate_sources: Path | None = Path("demo/crate-sources.json"),
    externally_overridden_source_paths: tuple[str, ...] = (),
) -> crate2nix.Crate2NixTarget:
    return crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo-crate2nix-src",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("linux",),
        source_input=source_input,
        root_src_relpath=root_src_relpath,
        crate_sources=crate_sources,
        externally_overridden_source_paths=externally_overridden_source_paths,
    )


def test_source_contract_rewrites_the_root_without_claiming_external_sources() -> None:
    """Only rootSrc-backed local sources belong to the injected slice contract."""
    generated = """{ lib
, rootSrc ? ./.
, crateSource ? relativePath: null
}:
{
  root = {
    src = lib.cleanSourceWith {
      filter = sourceFilter;
      src = "${rootSrc}";
    };
  };
  external = {
    src = lib.cleanSourceWith {
      filter = sourceFilter;
      src = externalSource;
    };
  };
}
"""

    normalized, source_paths = crate2nix._apply_crate_source_contract(generated)

    assert source_paths == (".",)
    assert_nix_ast_equal(
        normalized,
        """{ lib
, rootSrc ? ./.
, crateSource ? relativePath: null
}:
{
  root = {
    src = crateSource sourceFilter ".";
  };
  external = {
    src = lib.cleanSourceWith {
      filter = sourceFilter;
      src = externalSource;
    };
  };
}
""",
    )


def test_source_contract_requires_a_root_argument_for_injection() -> None:
    """A generated local source cannot be made injectable without a function seam."""
    generated = """rec {
  demo.src = lib.cleanSourceWith {
    filter = sourceFilter;
    src = "${rootSrc}/demo";
  };
}
"""

    with pytest.raises(
        RuntimeError,
        match="Could not find rootSrc argument for crateSource injection",
    ):
        crate2nix._apply_crate_source_contract(generated)


def test_source_contract_rejects_unrecognized_local_clean_source_shape() -> None:
    """Generator formatting drift must not silently restore evaluator-time filters."""
    generated = """{ lib
, rootSrc ? ./.
}:
{
  demo.src = lib.cleanSourceWith {
    src = "${rootSrc}/demo";
    filter = sourceFilter;
  };
}
"""

    with pytest.raises(
        RuntimeError,
        match="Unconverted rootSrc-backed cleanSourceWith expression",
    ):
        crate2nix._apply_crate_source_contract(generated)


def test_source_contract_rejects_changed_local_filter_expression() -> None:
    """A renamed generated filter must not bypass rootSrc source conversion."""
    generated = """{ lib
, rootSrc ? ./.
}:
{
  demo.src = lib.cleanSourceWith {
    src = "${rootSrc}/demo";
    filter = internal.sourceFilter;
  };
}
"""

    with pytest.raises(
        RuntimeError,
        match="Unconverted rootSrc-backed cleanSourceWith expression",
    ):
        crate2nix._apply_crate_source_contract(generated)


def test_source_contract_rejects_root_source_in_merged_argument() -> None:
    """Root provenance must be found below non-literal cleanSource arguments."""
    generated = """{ lib
, rootSrc ? ./.
}:
{
  demo.src = lib.cleanSourceWith ({
    filter = sourceFilter;
    src = "${rootSrc}/demo";
  } // {});
}
"""

    with pytest.raises(
        RuntimeError,
        match="Unconverted rootSrc-backed cleanSourceWith expression",
    ):
        crate2nix._apply_crate_source_contract(generated)


@pytest.mark.parametrize(
    "source_expression",
    [
        '"${rootSrc + "/demo"}"',
        '"${builtins.toString rootSrc}/demo"',
    ],
)
def test_source_contract_rejects_root_source_inside_string_expression(
    source_expression: str,
) -> None:
    """Root references nested inside interpolation expressions must fail closed."""
    generated = """{ lib
, rootSrc ? ./.
}:
{
  demo.src = lib.cleanSourceWith {
    filter = sourceFilter;
    src = __SOURCE_EXPRESSION__;
  };
}
""".replace("__SOURCE_EXPRESSION__", source_expression)

    with pytest.raises(
        RuntimeError,
        match="Unconverted rootSrc-backed cleanSourceWith expression",
    ):
        crate2nix._apply_crate_source_contract(generated)


def test_source_contract_rejects_parenthesized_clean_source_callee() -> None:
    """Callee-only formatting changes must not bypass root source detection."""
    generated = """{ lib
, rootSrc ? ./.
}:
{
  demo.src = (lib.cleanSourceWith) {
    filter = sourceFilter;
    src = "${rootSrc}/demo";
  };
}
"""

    with pytest.raises(
        RuntimeError,
        match="Unconverted rootSrc-backed cleanSourceWith expression",
    ):
        crate2nix._apply_crate_source_contract(generated)


def test_source_contract_inspection_accepts_crate2nix_dangling_formal_comma() -> None:
    """Parser compatibility cleanup must retain fail-closed source inspection."""
    generated = """{ lib
, rootSrc ? ./.
}:
let
  generatedHelper =
    { crate
    , version
    ,
    }:
    "${crate}-${version}";
in
{
  demo = {
    inherit generatedHelper;
    src = lib.cleanSourceWith {
      src = "${rootSrc}/demo";
      filter = sourceFilter;
    };
  };
}
"""

    with pytest.raises(
        RuntimeError,
        match="Unconverted rootSrc-backed cleanSourceWith expression",
    ):
        crate2nix._apply_crate_source_contract(generated)


def test_production_root_requires_a_declared_flake_input() -> None:
    """A target without a production source identity cannot bind slice hashes."""
    with pytest.raises(
        RuntimeError,
        match="Missing production source input for demo",
    ):
        crate2nix._resolve_production_root(_target(source_input=None))


def test_production_root_rejects_an_empty_evaluator_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Nix boundary must return the source path Cargo.nix will evaluate."""
    commands: list[list[str]] = []

    def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="\n", stderr="")

    monkeypatch.setattr(crate2nix, "_run", _run)

    with pytest.raises(
        RuntimeError,
        match="Flake input 'demo' has no evaluator-visible outPath",
    ):
        crate2nix._resolve_production_root(_target())

    assert commands[0][:-1] == ["nix", "eval", "--impure", "--raw", "--expr"]
    assert_nix_ast_equal(
        commands[0][-1],
        f"""let flake = builtins.getFlake {json.dumps(crate2nix.local_flake_url())};
        in flake.inputs."demo".outPath""",
    )


@pytest.mark.parametrize(
    ("root_src_relpath", "expected_root"),
    [
        (Path(), Path("/nix/store/demo-input")),
        (Path("workspace"), Path("/nix/store/demo-input/workspace")),
    ],
    ids=("input-root", "input-subdirectory"),
)
def test_production_root_uses_the_locked_input_identity(
    monkeypatch: pytest.MonkeyPatch,
    root_src_relpath: Path,
    expected_root: Path,
) -> None:
    """Resolved paths and hashes must describe the same locked flake input."""
    commands: list[list[str]] = []

    def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="/nix/store/demo-input\n",
            stderr="",
        )

    node = FlakeLockNode(
        locked=LockedRef(type="path", narHash="sha256-locked-input="),
    )
    monkeypatch.setattr(crate2nix, "_run", _run)
    monkeypatch.setattr(crate2nix, "get_flake_input_node", lambda _name: node)

    resolved = crate2nix._resolve_production_root(
        _target(root_src_relpath=root_src_relpath)
    )

    assert resolved == (expected_root, "sha256-locked-input=")
    assert commands[0][:-1] == ["nix", "eval", "--impure", "--raw", "--expr"]
    assert_nix_ast_equal(
        commands[0][-1],
        f"""let flake = builtins.getFlake {json.dumps(crate2nix.local_flake_url())};
        in flake.inputs."demo".outPath""",
    )


@pytest.mark.parametrize(
    "node",
    [
        FlakeLockNode(),
        FlakeLockNode(locked=LockedRef(type="path", narHash="")),
    ],
    ids=("missing-lock", "missing-nar-hash"),
)
def test_production_root_requires_locked_source_metadata(
    monkeypatch: pytest.MonkeyPatch,
    node: FlakeLockNode,
) -> None:
    """An evaluator path without its locked NAR identity must fail closed."""
    monkeypatch.setattr(
        crate2nix,
        "_run",
        lambda args: subprocess.CompletedProcess(
            args,
            0,
            stdout="/nix/store/demo-input\n",
            stderr="",
        ),
    )
    monkeypatch.setattr(crate2nix, "get_flake_input_node", lambda _name: node)

    with pytest.raises(
        RuntimeError,
        match="Flake input 'demo' has no locked source metadata",
    ):
        crate2nix._resolve_production_root(_target())


def test_root_slice_uses_the_explicit_stable_logical_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A root slice name must not inherit a Nix store input's hash-prefixed basename."""
    root = tmp_path / "hash-prefixed-input-name"
    responses = iter((
        '{".": "/nix/store/demo-stable-workspace"}',
        '{"/nix/store/demo-stable-workspace": {"narHash": "sha256-demo="}}',
    ))
    monkeypatch.setattr(
        crate2nix,
        "_run",
        lambda args: subprocess.CompletedProcess(
            args,
            0,
            stdout=next(responses),
            stderr="",
        ),
    )

    slices = crate2nix._materialize_source_slices(
        root,
        (".",),
        tmp_path / "Cargo.nix",
        root_source_name="stable-workspace",
    )

    assert slices == {
        ".": {
            "hash": "sha256-demo=",
            "name": "stable-workspace",
        }
    }


def test_empty_source_inventory_never_crosses_the_nix_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cargo.nix files without local crates need no source realization."""

    def _unexpected_run(_args: list[str]) -> subprocess.CompletedProcess[str]:
        pytest.fail("empty source inventory invoked Nix")

    monkeypatch.setattr(crate2nix, "_run", _unexpected_run)

    slices = crate2nix._materialize_source_slices(
        tmp_path / "source",
        (),
        tmp_path / "Cargo.nix",
        root_source_name="workspace",
    )

    assert slices == {}


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        '{"unexpected": "/nix/store/unexpected"}',
        '{"crate": 7}',
    ],
    ids=("non-object", "wrong-source-inventory", "non-path-value"),
)
def test_source_materialization_rejects_invalid_eval_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: str,
) -> None:
    """The eval response must map every requested slice to one store path."""
    commands: list[list[str]] = []

    def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=payload, stderr="")

    monkeypatch.setattr(crate2nix, "_run", _run)

    with pytest.raises(
        TypeError,
        match="Nix returned invalid crate source materialization metadata",
    ):
        crate2nix._materialize_source_slices(
            tmp_path / "source",
            ("crate",),
            tmp_path / "Cargo.nix",
            root_source_name="workspace",
        )

    assert len(commands) == 1
    assert commands[0][:-1] == ["nix", "eval", "--impure", "--json", "--expr"]


def test_source_materialization_rejects_non_object_path_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Path-info must return keyed metadata for the realized source paths."""
    commands: list[list[str]] = []
    responses = iter(('{"crate": "/nix/store/demo-crate"}', "[]"))

    def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=next(responses),
            stderr="",
        )

    monkeypatch.setattr(crate2nix, "_run", _run)

    with pytest.raises(
        TypeError,
        match="Nix returned invalid crate source path metadata",
    ):
        crate2nix._materialize_source_slices(
            tmp_path / "source",
            ("crate",),
            tmp_path / "Cargo.nix",
            root_source_name="workspace",
        )

    assert commands[1] == [
        "nix",
        "path-info",
        "--json",
        "--json-format",
        "1",
        "/nix/store/demo-crate",
    ]


def test_manifest_uses_source_as_the_stable_input_root_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A flake input root should use a stable name even when it has no subdirectory."""
    cargo_nix = tmp_path / "Cargo.nix"
    cargo_nix.write_text("generated Cargo.nix\n", encoding="utf-8")
    commands: list[list[str]] = []
    responses = iter((
        "/nix/store/demo-input\n",
        '{".": "/nix/store/demo-source-slice"}',
        '{"/nix/store/demo-source-slice": {"narHash": "sha256-slice="}}',
    ))

    def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=next(responses),
            stderr="",
        )

    node = FlakeLockNode(
        locked=LockedRef(type="path", narHash="sha256-input="),
    )
    monkeypatch.setattr(crate2nix, "_run", _run)
    monkeypatch.setattr(crate2nix, "get_flake_input_node", lambda _name: node)

    rendered = crate2nix._render_crate_source_manifest(
        _target(),
        (".",),
        cargo_nix=cargo_nix,
    )

    assert json.loads(rendered) == {
        "source": {
            "cargoNixSha256": (
                "22baa6ec12c7a24ee961ea5c6e349e55aaeb3389ebf596b3049cc12dd0fbf95a"
            ),
            "input": "demo",
            "narHash": "sha256-input=",
            "subdir": ".",
        },
        "slices": {
            ".": {
                "hash": "sha256-slice=",
                "name": "source",
            }
        },
    }
    assert commands[2] == [
        "nix",
        "path-info",
        "--json",
        "--json-format",
        "1",
        "/nix/store/demo-source-slice",
    ]


@pytest.mark.parametrize(
    ("source_input", "crate_sources"),
    [
        (None, Path("demo/crate-sources.json")),
        ("demo", None),
    ],
    ids=("missing-input", "missing-manifest-path"),
)
def test_manifest_requires_complete_source_metadata(
    source_input: str | None,
    crate_sources: Path | None,
    tmp_path: Path,
) -> None:
    """A target must declare both the input identity and manifest artifact."""
    with pytest.raises(
        RuntimeError,
        match="Missing crate source manifest metadata for demo",
    ):
        crate2nix._render_crate_source_manifest(
            _target(source_input=source_input, crate_sources=crate_sources),
            (),
            cargo_nix=tmp_path / "Cargo.nix",
        )


def test_manifest_rejects_an_obsolete_external_source_exemption(
    tmp_path: Path,
) -> None:
    """An exemption must keep naming a generated source path or fail closed."""
    target = _target(
        externally_overridden_source_paths=("vendor/v8-goose-src",),
    )

    with pytest.raises(
        RuntimeError,
        match="Externally overridden crate source paths are absent",
    ):
        crate2nix._render_crate_source_manifest(
            target,
            ("crates/demo",),
            cargo_nix=tmp_path / "Cargo.nix",
        )
