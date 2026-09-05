"""Policy checks for package and overlay source modifications."""

from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Final

from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.raw import RawExpression
from nix_manipulator.expressions.select import Select

from lib.codemods import packaging_source_policy
from lib.codemods.packaging_source_policy import (
    NixSourceIdentityAudit,
    NixSourceIdentitySite,
    NixSubstituteAudit,
    NixSubstituteSite,
    PythonRewriteAudit,
    PythonRewriteSite,
)
from lib.update.paths import REPO_ROOT, package_file_map_in
from lib.update.persistence import planned_update_paths
from lib.update.updaters import ensure_updaters_loaded

if TYPE_CHECKING:
    import pytest


def _nix_sites(path: str, *commands: str) -> tuple[NixSubstituteSite, ...]:
    return tuple((path, command) for command in commands)


def _python_sites(path: str, *calls: str) -> tuple[PythonRewriteSite, ...]:
    return tuple((path, call) for call in calls)


def test_nix_source_identity_audit_finds_literal_hashes(tmp_path: Path) -> None:
    """Handwritten Nix cannot hide source or attestation digests."""
    package = tmp_path / "packages" / "demo"
    package.mkdir(parents=True)
    derivation = package / "default.nix"
    derivation.write_text(
        """{ fetchurl }:
fetchurl {
  url = "https://example.invalid/demo.tar.gz";
  hash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
  legacyHash = "0000000000000000000000000000000000000000000000000000";
  rawHash = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
  sha512Hash = "sha512-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==";
  attestedPath = "/nix/store/00000000000000000000000000000000-demo";
}
""",
        encoding="utf-8",
    )

    audit = NixSourceIdentityAudit(
        root=tmp_path,
        roots=(tmp_path / "packages",),
    )

    assert audit.current_hash_sites() == (
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="attestedPath",
            kind="nix-store-path",
            value="/nix/store/00000000000000000000000000000000-demo",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="hash",
            kind="sri-sha256",
            value="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="legacyHash",
            kind="nix-base32-sha256",
            value="0000000000000000000000000000000000000000000000000000",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="rawHash",
            kind="hex-sha256",
            value="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="sha512Hash",
            kind="sri-sha512",
            value="sha512-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==",
        ),
    )


def test_nix_source_identity_audit_finds_legacy_hex_md5(tmp_path: Path) -> None:
    """Legacy raw MD5 fixed-output hashes remain updater-owned identities."""
    package = tmp_path / "packages" / "demo"
    package.mkdir(parents=True)
    (package / "default.nix").write_text(
        """{ fetchurl }:
fetchurl {
  url = "https://example.invalid/demo.tar.gz";
  md5 = "d41d8cd98f00b204e9800998ecf8427e";
}
""",
        encoding="utf-8",
    )

    assert NixSourceIdentityAudit(root=tmp_path).current_hash_sites() == (
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="md5",
            kind="hex-md5",
            value="d41d8cd98f00b204e9800998ecf8427e",
        ),
    )


def test_nix_source_identity_audit_distinguishes_hex_sha1_from_revision(
    tmp_path: Path,
) -> None:
    """A legacy SHA-1 digest is a hash while a same-width revision stays a pin."""
    package = tmp_path / "packages" / "demo"
    package.mkdir(parents=True)
    (package / "default.nix").write_text(
        """{ fetchurl }:
fetchurl {
  url = "https://example.invalid/demo.tar.gz";
  sha1 = "da39a3ee5e6b4b0d3255bfef95601890afd80709";
  rev = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
}
""",
        encoding="utf-8",
    )

    audit = NixSourceIdentityAudit(root=tmp_path)

    assert audit.current_hash_sites() == (
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="sha1",
            kind="hex-sha1",
            value="da39a3ee5e6b4b0d3255bfef95601890afd80709",
        ),
    )
    assert audit.current_pin_sites() == (
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="rev",
            kind="commit-pin",
            value="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ),
    )


def test_nix_source_identity_audit_finds_compound_hash_field_names(
    tmp_path: Path,
) -> None:
    """Algorithm-bearing field names own raw digests even without a hash suffix."""
    package = tmp_path / "packages" / "demo"
    package.mkdir(parents=True)
    (package / "default.nix").write_text(
        """{
  sourceSha1 = "da39a3ee5e6b4b0d3255bfef95601890afd80709";
  expectedMd5 = "d41d8cd98f00b204e9800998ecf8427e";
  sha256sum = "47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=";
}
""",
        encoding="utf-8",
    )

    assert NixSourceIdentityAudit(root=tmp_path).current_hash_sites() == (
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="expectedMd5",
            kind="hex-md5",
            value="d41d8cd98f00b204e9800998ecf8427e",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="sha256sum",
            kind="base64-sha256",
            value="47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="sourceSha1",
            kind="hex-sha1",
            value="da39a3ee5e6b4b0d3255bfef95601890afd80709",
        ),
    )


def test_nix_source_identity_audit_finds_legacy_nix_base32_hashes(
    tmp_path: Path,
) -> None:
    """All legacy Nix-base32 digest widths remain updater-owned identities."""
    package = tmp_path / "packages" / "demo"
    package.mkdir(parents=True)
    (package / "default.nix").write_text(
        """{
  ambiguousHash = "00000000000000000000000000000000";
  legacyMd5Hash = "00000000000000000000000000";
  legacySha1Hash = "00000000000000000000000000000000";
  legacySha512Hash = "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000";
  storeHash = "/nix/store/00000000000000000000000000000000-demo";
}
""",
        encoding="utf-8",
    )

    assert NixSourceIdentityAudit(root=tmp_path).current_hash_sites() == (
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="ambiguousHash",
            kind="raw-digest",
            value="00000000000000000000000000000000",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="legacyMd5Hash",
            kind="nix-base32-md5",
            value="00000000000000000000000000",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="legacySha1Hash",
            kind="nix-base32-sha1",
            value="00000000000000000000000000000000",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="legacySha512Hash",
            kind="nix-base32-sha512",
            value="0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="storeHash",
            kind="nix-store-path",
            value="/nix/store/00000000000000000000000000000000-demo",
        ),
    )


def test_nix_source_identity_audit_inherits_hash_ownership_from_parent_bindings(
    tmp_path: Path,
) -> None:
    """Checksum maps identify digest values through their parent binding."""
    package = tmp_path / "packages" / "demo"
    package.mkdir(parents=True)
    (package / "default.nix").write_text(
        """{
  checksums."demo-sha256.tar.gz" = "d41d8cd98f00b204e9800998ecf8427e";
  checksums."demo.tar.gz" = "d41d8cd98f00b204e9800998ecf8427e";
  hashes."artifact-md5" = "47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=";
  sha1Hashes.x86_64_linux = "da39a3ee5e6b4b0d3255bfef95601890afd80709";
  gpg.checksums."signing-key" = "d41d8cd98f00b204e9800998ecf8427e";
}
""",
        encoding="utf-8",
    )

    assert NixSourceIdentityAudit(root=tmp_path).current_hash_sites() == (
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding='checksums."demo-sha256.tar.gz"',
            kind="hex-md5",
            value="d41d8cd98f00b204e9800998ecf8427e",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding='checksums."demo.tar.gz"',
            kind="hex-md5",
            value="d41d8cd98f00b204e9800998ecf8427e",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding='hashes."artifact-md5"',
            kind="base64-sha256",
            value="47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="sha1Hashes.x86_64_linux",
            kind="hex-sha1",
            value="da39a3ee5e6b4b0d3255bfef95601890afd80709",
        ),
    )


def test_nix_source_identity_audit_finds_contextual_raw_base64_hashes(
    tmp_path: Path,
) -> None:
    """Raw Base64 digest widths are identities only in hash-owned bindings."""
    package = tmp_path / "packages" / "demo"
    package.mkdir(parents=True)
    (package / "default.nix").write_text(
        """{
  md5 = "1B2M2Y8AsgTpgAmY7PhCfg==";
  sha1 = "2jmj7l5rSw0yVb/vlWAYkK/YBwk=";
  rawHash = "47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=";
  sha512 = "z4PhNX7vuL3xVChQ1m2AB9Yg5AULVxXcg/SpIdNs6c5H0NE8XYXysP+DGNKHfuwvY7kxvUdBeoGlODJ6+SfaPg==";
  description = "47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=";
  sriHash = "sha256-47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=";
}
""",
        encoding="utf-8",
    )

    assert NixSourceIdentityAudit(root=tmp_path).current_hash_sites() == (
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="md5",
            kind="base64-md5",
            value="1B2M2Y8AsgTpgAmY7PhCfg==",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="rawHash",
            kind="base64-sha256",
            value="47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="sha1",
            kind="base64-sha1",
            value="2jmj7l5rSw0yVb/vlWAYkK/YBwk=",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="sha512",
            kind="base64-sha512",
            value="z4PhNX7vuL3xVChQ1m2AB9Yg5AULVxXcg/SpIdNs6c5H0NE8XYXysP+DGNKHfuwvY7kxvUdBeoGlODJ6+SfaPg==",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="sriHash",
            kind="sri-sha256",
            value="sha256-47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=",
        ),
    )


def test_nix_source_identity_audit_finds_bare_store_paths(tmp_path: Path) -> None:
    """An unquoted Nix path can carry the same immutable store identity."""
    package = tmp_path / "packages" / "demo"
    package.mkdir(parents=True)
    (package / "default.nix").write_text(
        """let
  attestedPath = /nix/store/00000000000000000000000000000000-demo;
in
{ inherit attestedPath; }
""",
        encoding="utf-8",
    )

    audit = NixSourceIdentityAudit(root=tmp_path)

    assert audit.current_hash_sites() == (
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="attestedPath",
            kind="nix-store-path",
            value="/nix/store/00000000000000000000000000000000-demo",
        ),
    )


def test_nix_source_identity_audit_finds_literal_source_pins(tmp_path: Path) -> None:
    """Source versions and commits are pins; compatibility floors are not."""
    package = tmp_path / "packages" / "demo"
    package.mkdir(parents=True)
    derivation = package / "default.nix"
    derivation.write_text(
        """let
  expectedVersion = "1.2.3";
  minimumMacOSVersion = "14.0";
  nativeMinimumMacosVersions = [ { path = "demo"; version = "13.0"; } ];
  source = {
    ref = "release/4.5.6";
    tag = "v4.5.6";
  };
  toolchain = rust.stable."1.89.0".default;
  rev = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
in
assert old.version == "4.5.6";
source
""",
        encoding="utf-8",
    )

    audit = NixSourceIdentityAudit(
        root=tmp_path,
        roots=(tmp_path / "packages",),
    )

    assert audit.current_pin_sites() == (
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="<expression>",
            kind="version-pin",
            value="4.5.6",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="expectedVersion",
            kind="version-pin",
            value="1.2.3",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="rev",
            kind="commit-pin",
            value="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="source.ref",
            kind="reference-pin",
            value="release/4.5.6",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="source.tag",
            kind="reference-pin",
            value="v4.5.6",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="toolchain",
            kind="version-pin",
            value="1.89.0",
        ),
    )


def test_nix_source_identity_audit_finds_pins_embedded_in_source_urls(
    tmp_path: Path,
) -> None:
    """Archive URLs cannot hide a release tag or abbreviated source revision."""
    package = tmp_path / "packages" / "demo"
    package.mkdir(parents=True)
    (package / "default.nix").write_text(
        """{ fetchurl }:
{
  tagged = fetchurl {
    url = "https://github.com/example/demo/archive/refs/tags/v1.2.3.tar.gz";
  };
  revised = fetchurl {
    url = "https://github.com/example/demo/archive/abcdef1.tar.gz";
  };
  queryRevision = fetchurl {
    url = "https://example.invalid/demo.tar.gz?rev=123abcd";
  };
  fragmentRevision = fetchurl {
    url = "https://example.invalid/demo.tar.gz#deadbee";
  };
}
""",
        encoding="utf-8",
    )

    audit = NixSourceIdentityAudit(root=tmp_path)

    assert audit.current_pin_sites() == (
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="fragmentRevision.url",
            kind="commit-pin",
            value="deadbee",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="queryRevision.url",
            kind="commit-pin",
            value="123abcd",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="revised.url",
            kind="commit-pin",
            value="abcdef1",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="tagged.url",
            kind="version-pin",
            value="v1.2.3",
        ),
    )


def test_nix_source_identity_audit_ignores_versioned_service_urls(
    tmp_path: Path,
) -> None:
    """API and documentation routes are not source archive identities."""
    package = tmp_path / "packages" / "demo"
    package.mkdir(parents=True)
    (package / "default.nix").write_text(
        """{
  apiUrl = "https://api.example.invalid/v1.2.3/query";
  documentationUrl = "https://docs.example.invalid/releases/v4.5.6/guide";
}
""",
        encoding="utf-8",
    )

    assert NixSourceIdentityAudit(root=tmp_path).current_pin_sites() == ()


def test_nix_source_identity_audit_finds_tool_and_compatibility_maps(
    tmp_path: Path,
) -> None:
    """Major tool versions and version maps are updater-owned locks."""
    package = tmp_path / "packages" / "demo"
    package.mkdir(parents=True)
    derivation = package / "default.nix"
    derivation.write_text(
        """let
  clangResourceVersion = "23";
  crateRustVersions = {
    "0.5.0" = "1.74.0";
  };
in
{ inherit clangResourceVersion crateRustVersions; }
""",
        encoding="utf-8",
    )

    audit = NixSourceIdentityAudit(
        root=tmp_path,
        roots=(tmp_path / "packages",),
    )

    assert audit.current_pin_sites() == (
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="clangResourceVersion",
            kind="version-pin",
            value="23",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding='crateRustVersions."0.5.0"',
            kind="version-pin",
            value="0.5.0",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding='crateRustVersions."0.5.0"',
            kind="version-pin",
            value="1.74.0",
        ),
    )


def test_nix_source_identity_audit_includes_derivation_builders_in_lib(
    tmp_path: Path,
) -> None:
    """Shared derivation constructors cannot hide compatibility pins."""
    library = tmp_path / "lib"
    library.mkdir()
    (library / "builder.nix").write_text(
        """{
  clangResourceVersion ? "22",
}:
clangResourceVersion
""",
        encoding="utf-8",
    )

    assert NixSourceIdentityAudit(root=tmp_path).current_pin_sites() == (
        NixSourceIdentitySite(
            path="lib/builder.nix",
            binding="clangResourceVersion",
            kind="version-pin",
            value="22",
        ),
    )


def test_nix_source_identity_audit_includes_home_and_module_derivations(
    tmp_path: Path,
) -> None:
    """Derivations outside package roots cannot hide handwritten identities."""
    fixtures = {
        "home/demo.nix": "1.2.3",
        "modules/demo.nix": "4.5.6",
    }
    for relative_path, version in fixtures.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"""{{ stdenv }}:
stdenv.mkDerivation {{
  pname = "demo";
  version = "{version}";
}}
""",
            encoding="utf-8",
        )
    for relative_path in ("flake.nix", "modules/nested/flake.nix"):
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{ inputs.demo.url = "github:example/demo/v9.9.9"; }\n',
            encoding="utf-8",
        )

    assert NixSourceIdentityAudit(root=tmp_path).current_pin_sites() == (
        NixSourceIdentitySite(
            path="home/demo.nix",
            binding="version",
            kind="version-pin",
            value="1.2.3",
        ),
        NixSourceIdentitySite(
            path="modules/demo.nix",
            binding="version",
            kind="version-pin",
            value="4.5.6",
        ),
    )


def test_nix_source_identity_audit_ignores_gpg_fingerprints(
    tmp_path: Path,
) -> None:
    """A 40-hex signing identity is not a source revision."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "meta.nix").write_text(
        """{
  gpg.keys.primary = "4FE536354F4B603BD260AD33EF550412DBEBCE71";
  gpg.keys.sha1Fingerprint = "da39a3ee5e6b4b0d3255bfef95601890afd80709";
  gpg.keys.sha256Fingerprint = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
  gpg.keys.sha512Fingerprint = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
  fingerprints.sha256 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
  keyFingerprints.primary = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
  source.commit = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
}
""",
        encoding="utf-8",
    )

    audit = NixSourceIdentityAudit(root=tmp_path)

    assert audit.current_hash_sites() == ()
    assert audit.current_pin_sites() == (
        NixSourceIdentitySite(
            path="home/meta.nix",
            binding="source.commit",
            kind="commit-pin",
            value="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ),
    )


def test_nix_source_identity_audit_finds_package_manager_specs(tmp_path: Path) -> None:
    """Exact runtime and patch package specs are updater-owned locks."""
    package = tmp_path / "packages" / "demo"
    package.mkdir(parents=True)
    derivation = package / "default.nix"
    derivation.write_text(
        """{ runCommand }:
let
  patches = { "demo@1.2.3" = ./demo.patch; };
in
runCommand "demo" { } ''
  npx helper@4.5.6
  manifest='{"nested":"^7.8.9"}'
''
""",
        encoding="utf-8",
    )

    audit = NixSourceIdentityAudit(
        root=tmp_path,
        roots=(tmp_path / "packages",),
    )

    assert audit.current_package_spec_sites() == (
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="<expression>",
            kind="package-spec-pin",
            value="helper@4.5.6",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="<expression>",
            kind="package-spec-pin",
            value="nested@^7.8.9",
        ),
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding='patches."demo@1.2.3"',
            kind="package-spec-pin",
            value="demo@1.2.3",
        ),
    )


def test_nix_source_identity_audit_finds_package_specs_in_default_roots(
    tmp_path: Path,
) -> None:
    """Home and module derivations share the package-spec policy by default."""
    fixtures = {
        "home/shared/runtime.nix": "helper@1.2.3",
        "modules/runtime.nix": "tool==4.5.6",
    }
    for relative_path, package_spec in fixtures.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"""{{ runCommand }}:
runCommand "runtime" {{ }} ''
  install-runtime {package_spec}
''
""",
            encoding="utf-8",
        )

    assert NixSourceIdentityAudit(root=tmp_path).current_package_spec_sites() == (
        NixSourceIdentitySite(
            path="home/shared/runtime.nix",
            binding="<expression>",
            kind="package-spec-pin",
            value="helper@1.2.3",
        ),
        NixSourceIdentitySite(
            path="modules/runtime.nix",
            binding="<expression>",
            kind="package-spec-pin",
            value="tool==4.5.6",
        ),
    )


def test_nix_source_identity_audit_finds_named_source_references(
    tmp_path: Path,
) -> None:
    """Named source metadata references are discoverable through the Nix AST."""
    package = tmp_path / "packages" / "demo"
    package.mkdir(parents=True)
    derivation = package / "default.nix"
    derivation.write_text(
        """{ sources }:
let
  info = sources.demo-source.commit;
in
info
""",
        encoding="utf-8",
    )

    audit = NixSourceIdentityAudit(
        root=tmp_path,
        roots=(tmp_path / "packages",),
    )

    assert audit.current_source_reference_sites() == (
        NixSourceIdentitySite(
            path="packages/demo/default.nix",
            binding="info",
            kind="source-reference",
            value="demo-source",
        ),
    )


def test_nix_string_payloads_traverse_builder_mappings_and_raw_expressions() -> None:
    """Builder-backed mappings retain fallback Nix text for identity audits."""
    expression = {
        "attestation": RawExpression(
            text="/nix/store/00000000000000000000000000000000-demo",
        ),
    }

    assert packaging_source_policy._nix_string_payloads(
        expression,
        binding="metadata",
    ) == (("metadata", "/nix/store/00000000000000000000000000000000-demo"),)


def test_nix_source_references_traverse_builder_mappings_and_quoted_attrs() -> None:
    """Quoted static source names survive builder mapping traversal."""
    quoted = packaging_source_policy.parse_nix_expr_for_policy(
        'sources."demo-source".commit',
        context="quoted source fixture",
    )
    malformed = Select(
        expression=Identifier(name="sources"),
        attribute='"unterminated',
    )

    assert packaging_source_policy._nix_source_references(
        {"quoted": quoted, "malformed": malformed},
        binding="source",
    ) == (("source", "demo-source"),)


def test_nix_selected_versions_traverse_builder_mappings() -> None:
    """Builder mapping values preserve selected toolchain-version pins."""
    selected = packaging_source_policy.parse_nix_expr_for_policy(
        'rust.stable."1.89.0".default',
        context="selected version fixture",
    )

    assert packaging_source_policy._nix_selected_versions(
        {"toolchain": selected},
        binding="toolchain",
    ) == (("toolchain", "1.89.0"),)


def test_nix_version_comparisons_traverse_builder_mappings() -> None:
    """Builder mapping values preserve literal source-version guards."""
    comparison = packaging_source_policy.parse_nix_expr_for_policy(
        'old.version == "4.5.6"',
        context="version comparison fixture",
    )

    assert packaging_source_policy._nix_literal_version_comparisons(
        {"guard": comparison},
        binding="guard",
    ) == (("guard", "4.5.6"),)


def test_handwritten_nix_source_hashes_are_externalized() -> None:
    """Only updater-generated Nix artifacts may contain literal digests."""
    updaters = ensure_updaters_loaded()
    generated_nix = frozenset(
        path
        for path in planned_update_paths(sorted(updaters), updaters)
        if path.suffix == ".nix"
    )
    audit = NixSourceIdentityAudit(
        root=REPO_ROOT,
        excluded_paths=generated_nix,
    )

    assert audit.current_hash_sites() == ()


def test_handwritten_nix_source_pins_are_externalized() -> None:
    """Handwritten package Nix consumes updater metadata instead of source pins."""
    updaters = ensure_updaters_loaded()
    generated_nix = frozenset(
        path
        for path in planned_update_paths(sorted(updaters), updaters)
        if path.suffix == ".nix"
    )
    audit = NixSourceIdentityAudit(
        root=REPO_ROOT,
        excluded_paths=generated_nix,
    )

    assert audit.current_pin_sites() == ()


def test_handwritten_nix_package_specs_are_externalized() -> None:
    """Package-manager specs in Nix are rendered from updater-owned metadata."""
    updaters = ensure_updaters_loaded()
    generated_nix = frozenset(
        path
        for path in planned_update_paths(sorted(updaters), updaters)
        if path.suffix == ".nix"
    )
    audit = NixSourceIdentityAudit(
        root=REPO_ROOT,
        excluded_paths=generated_nix,
    )

    assert audit.current_package_spec_sites() == ()


def test_named_nix_sources_have_updater_owned_metadata() -> None:
    """Every direct ``sources.<name>`` lookup has both updater sidecars."""
    audit = NixSourceIdentityAudit(root=REPO_ROOT)
    referenced_sources = {site.value for site in audit.current_source_reference_sites()}
    source_paths = package_file_map_in(REPO_ROOT, "sources.json")
    updater_paths = package_file_map_in(REPO_ROOT, "updater.py")

    assert referenced_sources - set(source_paths) == set()
    assert referenced_sources - set(updater_paths) == set()


# Baselines are not approval of these mechanisms. They make existing ad hoc source
# edits visible so new ones fail until they move to .patch files, lib.codemods, or
# are intentionally recorded as migration debt. Semantic identities avoid churn
# when unrelated edits move a rewrite to another line.
_ALLOWED_NIX_SUBSTITUTE_SITES: Final = (
    *_nix_sites(
        "overlays/github-desktop/default.nix",
        r"""substituteInPlace "$node_addon_api_header" --replace-fail 'static const napi_typedarray_type unknown_array_type = static_cast<napi_typedarray_type>(-1);' 'static const napi_typedarray_type unknown_array_type = static_cast<napi_typedarray_type>(0);'""",
    ),
    *_nix_sites(
        "overlays/rio/default.nix",
        r"""substituteInPlace "$out/Applications/Rio.app/Contents/Info.plist" --replace-fail '{{.Version}}.{{.Now.Format "20060102150405"}}' '${version}' --replace-fail '{{.Version}}' '${version}'""",
    ),
    # Buzz retains nixpkgs' inherited ONNX Runtime postPatch, then narrowly
    # reverses its Darwin tool/output pinning for static archive assembly.
    # outputChecks.allowedReferences and the validated candidate's
    # zero-store-reference gate independently reject any leaked store path.
    *_nix_sites(
        "packages/buzz/native/onnxruntime.nix",
        r"""substituteInPlace cmake/onnxruntime.cmake --replace-fail "/usr/bin/ar" "${cctools}/bin/ar" --replace-fail "/usr/bin/ld" "${ld64}/bin/ld" --replace-fail "/usr/bin/libtool" "${cctools.libtool}/bin/libtool" --replace-fail 'set(STATIC_FRAMEWORK_OUTPUT_DIR ''${CMAKE_CURRENT_BINARY_DIR}/''${CMAKE_BUILD_TYPE}-''${CMAKE_OSX_SYSROOT})' 'set(STATIC_FRAMEWORK_OUTPUT_DIR ''${CMAKE_CURRENT_BINARY_DIR}/buzz-static-framework-output)'""",
        # Restore the upstream empty runtime fallback that nixpkgs' inherited
        # postPatch pins to the package output; the reference gates stay final.
        r'''substituteInPlace onnxruntime/core/platform/posix/env.cc --replace-fail "$out/lib/" ""''',
        # Restore upstream's @rpath install name after the same inherited
        # postPatch rewrites it to the package output.
        r'''substituteInPlace cmake/onnxruntime.cmake --replace-fail "INSTALL_NAME_DIR $out/lib" "INSTALL_NAME_DIR @rpath"''',
    ),
    *_nix_sites(
        "packages/codex/default.nix",
        r"""substituteInPlace "${target}/src/tools/js_repl/mod.rs" --replace-fail '../../../../node-version.txt' '../../../node-version.txt'""",
    ),
    *_nix_sites(
        "packages/emdash/default.nix",
        r'''substituteInPlace "''${shell_env_capture_paths[0]}" --replace-fail "['-ilc', 'env']" "['-lc', 'env']"''',
        r'''substituteInPlace node_modules/debug/src/common.js --replace-fail "require('ms')" "require('../../../out/main/ms-shim.cjs')"''',
        r'''substituteInPlace "$out/bin/emdash" --replace-fail "#!/usr/bin/env bash" "#!${stdenv.shell}" --replace-fail "@out@" "$out"''',
        r'''substituteInPlace "$out/bin/emdash" --replace-fail "#!/usr/bin/env bash" "#!${stdenv.shell}" --replace-fail "@out@" "$out"''',
    ),
    *_nix_sites(
        "packages/goose-desktop/default.nix",
        r'''substituteInPlace desktop/src/updates.ts --replace-fail "export const UPDATES_ENABLED = true;" "export const UPDATES_ENABLED = false;"''',
    ),
    *_nix_sites(
        "packages/mole-app/default.nix",
        r'''substituteInPlace "$out/bin/mole" --replace-fail 'SCRIPT_DIR="$(cd "$(dirname "''${BASH_SOURCE[0]}")" && pwd)"' "SCRIPT_DIR='$out/libexec/mole'"''',
    ),
    # These exact anchors must interpolate the realized derivation output path;
    # a static patch cannot know `$out`, while --replace-fail keeps drift fail-closed.
    *_nix_sites(
        "packages/paseo/onnxruntime-source.nix",
        r'''substituteInPlace onnxruntime/core/platform/env.h --replace-fail "GetRuntimePath() const { return PathString(); }" "GetRuntimePath() const { return PathString(\"$out/lib/\"); }"''',
        r'''substituteInPlace cmake/onnxruntime.cmake --replace-fail "INSTALL_NAME_DIR @rpath" "INSTALL_NAME_DIR $out/lib"''',
    ),
    *_nix_sites(
        "packages/scratch/default.nix",
        r"""substituteInPlace "$out/nix-support/setup-hook" --replace-fail '"x86_64-unknown-linux-gnu"' '"${rustTarget}"' --replace-fail 'target/x86_64-unknown-linux-gnu' 'target/${rustTarget}' --replace-fail 'CC_X86_64_UNKNOWN_LINUX_GNU' 'CC_${rustTargetEnv}' --replace-fail 'CXX_X86_64_UNKNOWN_LINUX_GNU' 'CXX_${rustTargetEnv}' --replace-fail 'CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER' 'CARGO_TARGET_${rustTargetEnv}_LINKER'""",
    ),
    *_nix_sites(
        "packages/superset/default.nix",
        r"""substituteInPlace package.json --replace-fail '"postinstall": "./scripts/postinstall.sh"' '"postinstall": ""'""",
    ),
    *_nix_sites(
        "packages/zed-editor-nightly/default.nix",
        # Zed changed from direct rust-embed attributes to util::fs_embed!.
        # Keep both semantic source-location forms supported during updates;
        # an unknown third contract fails closed in the package preparation.
        r"""substituteInPlace ${sourceFile} --replace-fail 'crate_relative = "../../assets"' 'crate_relative = "workspace-assets"'""",
        r"""substituteInPlace ${sourceFile} --replace-fail '#[folder = "../../assets"]' '#[folder = "workspace-assets"]'""",
        r'''substituteInPlace "$crateRoot/src/assets.rs" --replace-fail 'use rust_embed::RustEmbed;' 'use rust_embed::{Embed, RustEmbed};' --replace-fail ".filter_map(|p| {" ".filter_map(|p: std::borrow::Cow<'static, str>| {"''',
        r"""substituteInPlace "$crateRoot/src/main.rs" --replace-fail 'include_bytes!("../../../script/uninstall.sh")' 'include_bytes!("../uninstall.sh")'""",
        r"""substituteInPlace "$crateRoot/build.rs" --replace-fail 'std::fs::read_to_string("../zed/Cargo.toml")' 'std::fs::read_to_string("./zed-Cargo.toml")'""",
        r"""substituteInPlace "$crateRoot/src/filter_languages.rs" --replace-fail '#[folder = "../grammars/src/"]' '#[folder = "workspace-language-configs-src/"]'""",
        r"""substituteInPlace "$crateRoot/src/filter_languages.rs" --replace-fail '#[folder = "../languages/src/"]' '#[folder = "workspace-language-configs-src/"]'""",
        r"""substituteInPlace "$crateRoot/src/filter_languages.rs" --replace-fail 'concat!(env!("CARGO_MANIFEST_DIR"), "/../grammars/src")' 'concat!(env!("CARGO_MANIFEST_DIR"), "/workspace-language-configs-src")'""",
        r"""substituteInPlace "$crateRoot/src/filter_languages.rs" --replace-fail 'concat!(env!("CARGO_MANIFEST_DIR"), "/../languages/src")' 'concat!(env!("CARGO_MANIFEST_DIR"), "/workspace-language-configs-src")'""",
        r"""substituteInPlace "$crateRoot/build.rs" --replace-fail 'std::fs::read_to_string("../zed/Cargo.toml")' 'std::fs::read_to_string("./zed-Cargo.toml")'""",
        r"""substituteInPlace "$crateRoot/build.rs" --replace-fail 'std::fs::read_to_string("../zed/Cargo.toml")' 'std::fs::read_to_string("./zed-Cargo.toml")' --replace-fail 'println!("cargo:rerun-if-changed=../zed/Cargo.toml");' 'println!("cargo:rerun-if-changed=./zed-Cargo.toml");'""",
        r"""substituteInPlace "$crateRoot/build.rs" --replace-fail 'PathBuf::from("../extension_api/wit")' 'PathBuf::from("workspace-extension-api-wit")'""",
        r"""substituteInPlace "$path" --replace-fail 'path: "../extension_api/wit/' 'path: "workspace-extension-api-wit/'""",
        r"""substituteInPlace "$crateRoot/build.rs" --replace-fail 'gpui::GPUI_MANIFEST_DIR.into()' 'PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap()).join("workspace-gpui")'""",
        r"""substituteInPlace "$crateRoot/build.rs" --replace-fail '.join("../gpui")' '.join("workspace-gpui")'""",
        r"""substituteInPlace "$crateRoot/build.rs" --replace-fail '    let mut path = std::path::PathBuf::from(&cargo_manifest_dir);' '    println!("cargo:rustc-env=ZED_REPO_DIR={}", cargo_manifest_dir);""",
        r"""substituteInPlace "$crateRoot/src/prompt_store.rs" --replace-fail 'include_str!("../../git_ui/src/commit_message_prompt.txt")' 'include_str!("../commit_message_prompt.txt")'""",
        r"""substituteInPlace "$crateRoot/src/lib.rs" --replace-fail 'include_str!("../../zed/RELEASE_CHANNEL")' 'include_str!("../RELEASE_CHANNEL")'""",
        r"""substituteInPlace "$crateRoot/build.rs" --replace-fail 'include_str!("../zed/Cargo.toml")' 'include_str!("./zed-Cargo.toml")'""",
        r"""substituteInPlace "$crateRoot/src/settings.rs" --replace-fail 'use rust_embed::RustEmbed;' 'use rust_embed::{Embed, RustEmbed};'""",
        r"""substituteInPlace src/lib.rs --replace-fail 'concat!("../", std::env!("CARGO_PKG_README"))' '"../README.md"'""",
    ),
)
_ALLOWED_PYTHON_AD_HOC_REWRITE_SITES: Final = (
    # pnpm records a patch hash in multiple lockfile sections. The normalizer
    # derives the replacement from the patched file and validates the exact
    # old-hash occurrence count before rewriting the generated lockfile.
    *_python_sites(
        "packages/bb/normalize_pnpm_patch_hashes.py",
        r"""normalized.replace(old_hash, new_hash)""",
    ),
    *_python_sites(
        "packages/codex/patch_allocator_weak_linkage.py",
        r"""original.replace(_WEAK_LINKAGE_ATTR, '')""",
    ),
    *_python_sites(
        "packages/gitbutler/normalize_cargo_nix.py",
        r"""_GITBUTLER_TAURI_PACKAGE_PREFIX.sub(replace_package, text, count=1)""",
        r"""_GIX_TRACE_REGISTRY_DEPENDENCY.sub(replace_dependency, text, count=1)""",
        r"""_GIX_TRACE_REGISTRY_PACKAGE.sub(replace_package, text, count=1)""",
        r"""_GIX_VALIDATE_REGISTRY_DEPENDENCY.sub(replace_dependency, text, count=1)""",
        r"""_GIX_VALIDATE_REGISTRY_PACKAGE.sub(replace_package, text, count=1)""",
        r"""dependency.replace(package_id_line, f'{package_id_line}{indent}  features = [ "{_REGISTRY_SOURCE_DISAMBIGUATOR}" ];\n', 1)""",
        r"""dependency.replace(package_id_line, f'{package_id_line}{indent}  features = [ "{_REGISTRY_SOURCE_DISAMBIGUATOR}" ];\n', 1)""",
        r"""package.replace('        resolvedDefaultFeatures = [ "default" ];', f'        resolvedDefaultFeatures = [ "{_REGISTRY_SOURCE_DISAMBIGUATOR}" "default" ];', 1)""",
        r"""package.replace('        resolvedDefaultFeatures = [ "default" ];', f'        resolvedDefaultFeatures = [ "{_REGISTRY_SOURCE_DISAMBIGUATOR}" "default" ];', 1).replace('      resolvedDefaultFeatures = [ "default" ];', f'      resolvedDefaultFeatures = [ "{_REGISTRY_SOURCE_DISAMBIGUATOR}" "default" ];', 1)""",
        r"""package.replace(closing, insertion + closing, 1)""",
        r"""package.replace(dependencies_match.group(0), dependencies_match.group(0) + dependency, 1)""",
        r"""package.replace(features_match.group(0), f'{features_match.group(0)}{source_line}', 1)""",
    ),
)

_NIX_SUBSTITUTE_AUDIT = NixSubstituteAudit(_ALLOWED_NIX_SUBSTITUTE_SITES)
_PYTHON_REWRITE_AUDIT = PythonRewriteAudit(
    _ALLOWED_PYTHON_AD_HOC_REWRITE_SITES,
)


def _format_site_delta(
    actual: tuple[NixSubstituteSite | PythonRewriteSite, ...],
    allowed: tuple[NixSubstituteSite | PythonRewriteSite, ...],
) -> str:
    actual_counts = Counter(actual)
    allowed_counts = Counter(allowed)
    unexpected = actual_counts - allowed_counts
    missing = allowed_counts - actual_counts
    return f"Unexpected: {unexpected}\nMissing: {missing}"


def test_package_overlay_substitute_in_place_sites_are_baselined() -> None:
    """Require new Nix source rewrites to be explicit migration debt."""
    actual = _NIX_SUBSTITUTE_AUDIT.current_sites()

    assert Counter(actual) == Counter(_NIX_SUBSTITUTE_AUDIT.allowed_sites), (
        _format_site_delta(actual, _NIX_SUBSTITUTE_AUDIT.allowed_sites)
    )


def test_paseo_only_retains_realized_output_path_substitutions() -> None:
    """Keep Paseo's narrow output-path debt separate from other migrations."""
    actual = tuple(
        site
        for site in _NIX_SUBSTITUTE_AUDIT.current_sites()
        if site[0].startswith("packages/paseo/")
    )
    allowed = tuple(
        site
        for site in _NIX_SUBSTITUTE_AUDIT.allowed_sites
        if site[0].startswith("packages/paseo/")
    )

    assert Counter(actual) == Counter(allowed), _format_site_delta(actual, allowed)


def test_package_overlay_python_ad_hoc_rewrite_sites_are_baselined() -> None:
    """Require new Python ad hoc rewrites to use codemod helpers or be baselined."""
    actual = _PYTHON_REWRITE_AUDIT.current_sites()

    assert Counter(actual) == Counter(_PYTHON_REWRITE_AUDIT.allowed_sites), (
        _format_site_delta(actual, _PYTHON_REWRITE_AUDIT.allowed_sites)
    )


def test_python_rewrite_audit_excludes_sibling_updater_tests(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Only sibling updater test modules are excluded from production auditing."""
    source = """def rewrite(target, payload):
    target.write_text(payload.replace("old", "new"))
"""
    package = tmp_path / "packages" / "demo"
    package.mkdir(parents=True)
    (package / "updater.py").write_text(source, encoding="utf-8")
    (package / "updater_test.py").write_text(source, encoding="utf-8")
    nested = package / "nested"
    nested.mkdir()
    (nested / "updater_test.py").write_text(source, encoding="utf-8")
    monkeypatch.setattr(packaging_source_policy, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        packaging_source_policy,
        "iter_target_paths",
        lambda *_args, **_kwargs: tuple(sorted(package.rglob("*.py"))),
    )

    actual = PythonRewriteAudit(allowed_sites=()).current_sites()

    assert actual == (
        *_python_sites(
            "packages/demo/nested/updater_test.py",
            "payload.replace('old', 'new')",
        ),
        *_python_sites(
            "packages/demo/updater.py",
            "payload.replace('old', 'new')",
        ),
    )
