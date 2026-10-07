"""Structural tests for the Reason (ara) version-pinned DMG fetch."""

import os
import shutil
import stat
import subprocess
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_file_binding_expr, nix_file_expr
from lib.tests._shell_ast import (
    command_name,
    command_texts,
    indented_string_body,
    iter_nodes,
    node_text,
    parse_shell,
)
from lib.tests._source_metadata import (
    assert_https_url,
    assert_platform_source_entry,
    assert_release_version,
)
from lib.update.paths import REPO_ROOT

_PUBLIC_DOWNLOAD_URLS = {
    "aarch64-darwin": "https://reasonmachines.com/api/desktop-download?arch=aarch64"
}
_VERSIONED_OBJECT_PREFIX = "/desktop/stable/"

_FAKE_CURL = r"""#!/usr/bin/env bash
set -euo pipefail
dump=""
output=""
head=0
url=""
while (($#)); do
  case "$1" in
    --dump-header)
      dump="$2"
      shift 2
      ;;
    -o)
      output="$2"
      shift 2
      ;;
    -I | -fsSI)
      head=1
      shift
      ;;
    -fsSL | -f | -s | -S | -L)
      shift
      ;;
    -*)
      shift
      ;;
    *)
      url="$1"
      shift
      ;;
  esac
done
printf '%s\n' "$url" >> "${CURL_LOG:?}"
if [[ -n "$dump" ]]; then
  cat "${CURL_HEADERS:?}" > "$dump"
fi
if ((head == 0)); then
  cat "${CURL_BODY:?}" > "${output:?}"
  printf '%s\n' "$url" >> "${CURL_GET_LOG:?}"
fi
"""


def _fetch_dmg_build_command() -> str:
    package = expect_instance(
        nix_file_expr("packages/ara/fetch-dmg.nix"), FunctionDefinition
    )
    derivation = expect_instance(package.output, FunctionCall)
    args = expect_instance(derivation.argument, AttributeSet)
    build = expect_instance(
        expect_binding(args.values, "buildCommand").value, IndentedString
    )
    return indented_string_body(build.rebuild())


def _run_fetch_dmg(
    tmp_path: Path,
    *,
    version: str,
    headers: str,
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True)
    curl = fake_bin / "curl"
    curl.write_text(_FAKE_CURL, encoding="utf-8")
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["url"] = "https://reasonmachines.com/api/desktop-download?arch=aarch64"
    env["version"] = version
    env["out"] = str(tmp_path / "Reason.dmg")
    env["CURL_HEADERS"] = str(tmp_path / "response.headers")
    env["CURL_BODY"] = str(tmp_path / "payload.bin")
    env["CURL_LOG"] = str(tmp_path / "curl.log")
    env["CURL_GET_LOG"] = str(tmp_path / "curl.get.log")
    Path(env["CURL_HEADERS"]).write_text(headers, encoding="utf-8")
    Path(env["CURL_BODY"]).write_bytes(b"reason-dmg-bytes")
    Path(env["CURL_LOG"]).write_text("", encoding="utf-8")
    Path(env["CURL_GET_LOG"]).write_text("", encoding="utf-8")
    bash = shutil.which("bash")
    assert bash is not None
    return subprocess.run(  # noqa: S603 -- owned fetch script under a fake curl
        [bash, "-c", _fetch_dmg_build_command()],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _redirect_headers(*, version: str, location: str) -> str:
    return (
        "HTTP/2 307\r\n"
        f"x-ara-desktop-version: {version}\r\n"
        f"location: {location}\r\n"
        "\r\n"
    )


def _assert_pin_current_reason(source: SourceEntry) -> None:
    version = assert_release_version(source.version)
    _, urls = assert_platform_source_entry(
        source,
        platforms=set(_PUBLIC_DOWNLOAD_URLS),
    )
    assert urls == _PUBLIC_DOWNLOAD_URLS
    for url in urls.values():
        parsed = urlsplit(url)
        assert_https_url(url, host="reasonmachines.com")
        assert version not in unquote(parsed.path)
        assert _VERSIONED_OBJECT_PREFIX not in parsed.path
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        assert query == {"arch": "aarch64"}


def test_ara_sources_pin_current_reason_stable() -> None:
    """37385596458 hashed 0.1.71 then fetched different latest bytes.

    Persist the public latest-only redirect plus an opaque version/hash.
    Update 37566013983 re-pinned 0.1.74; a frozen version/hash then failed
    publish after Darwin closures. The pin floats; the URL must not.
    """
    _assert_pin_current_reason(
        SourceEntry.model_validate_json(
            (REPO_ROOT / "packages/ara/sources.json").read_text(encoding="utf-8")
        )
    )


def test_ara_pin_current_contract_accepts_a_later_reason_pin() -> None:
    """A successful ara bump must not fail publish by freezing the last pin."""
    _assert_pin_current_reason(
        SourceEntry.model_validate({
            "version": "0.1.74",
            "urls": _PUBLIC_DOWNLOAD_URLS,
            "hashes": {
                "aarch64-darwin": (
                    "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
                )
            },
        })
    )


def test_ara_fetch_dmg_fail_closes_on_latest_redirect_drift() -> None:
    """Unsigned Tigris keys 403; signed ones expire; check version before GET."""
    package = expect_instance(
        nix_file_expr("packages/ara/fetch-dmg.nix"), FunctionDefinition
    )
    derivation = expect_instance(package.output, FunctionCall)
    assert_nix_ast_equal(derivation.name, "stdenvNoCC.mkDerivation")
    args = expect_instance(derivation.argument, AttributeSet)
    assert_nix_ast_equal(expect_binding(args.values, "outputHashMode").value, '"flat"')
    assert_nix_ast_equal(expect_binding(args.values, "outputHash").value, "hash")
    assert_nix_ast_equal(expect_binding(args.values, "preferLocalBuild").value, "true")
    assert_nix_ast_equal(
        expect_binding(args.values, "impureEnvVars").value,
        "lib.fetchers.proxyImpureEnvVars",
    )
    assert_nix_ast_equal(
        expect_binding(args.values, "nativeBuildInputs").value,
        "[ cacert curl ]",
    )
    cert_bundle = '"${cacert}/etc/ssl/certs/ca-bundle.crt"'
    assert_nix_ast_equal(
        expect_binding(args.values, "SSL_CERT_FILE").value,
        cert_bundle,
    )
    assert_nix_ast_equal(
        expect_binding(args.values, "NIX_SSL_CERT_FILE").value,
        cert_bundle,
    )
    shell = parse_shell(_fetch_dmg_build_command())
    assert command_texts(shell, "curl") == [
        'curl -fsSI --dump-header headers "$url" -o /dev/null',
        'curl -fsSL "$location" -o "$out"',
    ]
    assignments = [
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "variable_assignment")
    ]
    assert assignments == [
        "found_version=$(tr -d '\\r' < headers | awk 'BEGIN{IGNORECASE=1} "
        "/^x-ara-desktop-version:/ {print $2; exit}')",
        "location=$(tr -d '\\r' < headers | awk 'BEGIN{IGNORECASE=1} "
        "/^location:/ {print $2; exit}')",
    ]
    tests = command_texts(shell)
    assert '[ "$found_version" != "$version" ]' in tests
    cases = [
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "case_statement")
    ]
    assert len(cases) == 1
    assert 'case "$location" in' in cases[0]
    assert '*"/desktop/stable/$version/"*) ;;' in cases[0]
    assert "exit 1" in cases[0]
    commands = [
        node
        for node in iter_nodes(shell.tree.root_node, "command")
        if command_name(node, shell.sanitized) in {"curl", "exit"}
    ]
    if_gate = next(iter_nodes(shell.tree.root_node, "if_statement"))
    case_gate = next(iter_nodes(shell.tree.root_node, "case_statement"))
    head_curl, get_curl = (
        node for node in commands if command_name(node, shell.sanitized) == "curl"
    )
    assert (
        head_curl.end_byte
        < if_gate.start_byte
        < case_gate.start_byte
        < get_curl.start_byte
    )


def test_ara_fetch_dmg_script_rejects_version_and_location_drift(
    tmp_path: Path,
) -> None:
    """The fail-closed HEAD must run; a later latest must not be fetched."""
    public = "https://reasonmachines.com/api/desktop-download?arch=aarch64"
    signed = (
        "https://ara-desktop-releases.t3.storage.dev"
        "/desktop/stable/0.1.73/Reason.dmg?X-Amz-Expires=900"
    )
    drifted = (
        "https://ara-desktop-releases.t3.storage.dev"
        "/desktop/stable/0.1.71/Reason.dmg?X-Amz-Expires=900"
    )

    mismatch = _run_fetch_dmg(
        tmp_path / "version",
        version="0.1.73",
        headers=_redirect_headers(version="0.1.71", location=signed),
    )
    assert mismatch.returncode == 1
    assert (tmp_path / "version" / "curl.get.log").read_text(encoding="utf-8") == ""
    assert not (tmp_path / "version" / "Reason.dmg").exists()

    wrong_object = _run_fetch_dmg(
        tmp_path / "location",
        version="0.1.73",
        headers=_redirect_headers(version="0.1.73", location=drifted),
    )
    assert wrong_object.returncode == 1
    assert (tmp_path / "location" / "curl.get.log").read_text(encoding="utf-8") == ""
    assert not (tmp_path / "location" / "Reason.dmg").exists()

    pinned = _run_fetch_dmg(
        tmp_path / "ok",
        version="0.1.73",
        headers=_redirect_headers(version="0.1.73", location=signed),
    )
    assert pinned.returncode == 0
    assert (tmp_path / "ok" / "curl.log").read_text(encoding="utf-8") == (
        f"{public}\n{signed}\n"
    )
    assert (tmp_path / "ok" / "curl.get.log").read_text(
        encoding="utf-8"
    ) == f"{signed}\n"
    assert (tmp_path / "ok" / "Reason.dmg").read_bytes() == b"reason-dmg-bytes"


def test_ara_package_overrides_moving_url_src() -> None:
    """mkDmgApp7zz would fetch the latest-only URL without a version guard."""
    src = nix_file_binding_expr("packages/ara/default.nix", "src")
    assert_nix_ast_equal(
        src,
        """callPackage ./fetch-dmg.nix {
          inherit (selfSource) version;
          url = selfSource.urls.${stdenv.hostPlatform.system};
          hash = selfSource.hashes.${stdenv.hostPlatform.system};
        }""",
    )
