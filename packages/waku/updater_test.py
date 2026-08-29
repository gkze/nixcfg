"""Focused contracts for the source-built Waku macOS package."""

import json
from types import ModuleType

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    nix_apply,
    nix_attrset_call,
    parse_nix_expr,
)
from lib.tests._shell_ast import (
    command_texts,
    indented_string_body,
    iter_nodes,
    node_text,
    parse_shell,
)
from lib.tests._updater_helpers import (
    collect_events,
    install_fixed_hash_stream,
    load_repo_module,
    run_async,
)
from lib.update.derivation_validation import DerivationValidation
from lib.update.net import github_raw_url
from lib.update.nix import (
    _build_fetch_from_github_call,
    _build_package_path_attr_expr,
)
from lib.update.nix_expr import identifier_attr_path
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo

_PACKAGE_DIR = REPO_ROOT / "packages/waku"
_VERSION = "0.1.15"
_BUILD = "1015"
_COMMIT = "f0645da06b046e0e58176105d69e2d56ae4fc342"
_SRC_HASH = "sha256-CJTfiWfvUXI58eM9W42omzh547OQ+HKx+BXyAAuMTJI="
_CARGO_HASH = "sha256-3mHNISEGQQFwdhAkI9LJxpYV6f3RbhvHVEPuLG4oguY="
_CHANGELOG_BODY = "- Codex thread goals and provider-native command discovery."


def _load_updater_module() -> ModuleType:
    return load_repo_module(
        "packages/waku/updater.py",
        "waku_updater_dedicated_test",
    )


def _appcast(
    *,
    version: str = _VERSION,
    build: str = _BUILD,
    artifact_url: str | None = None,
    notes_url: str | None = None,
) -> bytes:
    artifact = artifact_url or f"https://releases.waku.sh/Waku-{version}.zip"
    notes = notes_url or f"https://releases.waku.sh/Waku-{version}.md"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
  <channel>
    <item>
      <sparkle:version>{build}</sparkle:version>
      <sparkle:shortVersionString>{version}</sparkle:shortVersionString>
      <sparkle:releaseNotesLink>{notes}</sparkle:releaseNotesLink>
      <enclosure url="{artifact}" />
    </item>
  </channel>
</rss>
""".encode()


def _install_release_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    *,
    appcast: bytes | None = None,
    commit_payload: object = None,
    manifest: bytes | None = None,
    changelog: bytes | None = None,
    release_notes: bytes | None = None,
) -> list[str]:
    urls: list[str] = []
    resolved_commit = {"sha": _COMMIT} if commit_payload is None else commit_payload
    payloads = {
        module.APPCAST_URL: _appcast() if appcast is None else appcast,
        github_raw_url("egoist", "waku", _COMMIT, "Cargo.toml"): (
            f'[package]\nname = "waku"\nversion = "{_VERSION}"\n'.encode()
            if manifest is None
            else manifest
        ),
        github_raw_url("egoist", "waku", _COMMIT, "CHANGELOG.md"): (
            f"# Changelog\n\n## [{_VERSION}]\n\n{_CHANGELOG_BODY}\n".encode()
            if changelog is None
            else changelog
        ),
        f"https://releases.waku.sh/Waku-{_VERSION}.md": (
            _CHANGELOG_BODY.encode() if release_notes is None else release_notes
        ),
    }

    async def fetch_url(
        _session: object,
        url: str,
        **_kwargs: object,
    ) -> bytes:
        urls.append(url)
        return payloads[url]

    async def fetch_github_api(
        _session: object,
        path: str,
        *,
        config: object,
    ) -> object:
        assert config == module.WakuUpdater().config
        assert path == f"repos/egoist/waku/commits/v{_VERSION}"
        return resolved_commit

    monkeypatch.setattr(module, "fetch_url", fetch_url)
    monkeypatch.setattr(module, "fetch_github_api", fetch_github_api)
    return urls


def test_waku_derives_sparkle_build_numbers_from_semver() -> None:
    """Sparkle build identity must stay aligned with upstream release tooling."""
    module = _load_updater_module()

    assert module.sparkle_build_number("0.1.2") == "1002"
    assert module.sparkle_build_number("1.2.3") == "1002003"


@pytest.mark.parametrize("version", ["1.2", "v1.2.3", "1.2.3-beta", "01.2.3"])
def test_waku_rejects_versions_outside_the_release_semver_contract(
    version: str,
) -> None:
    """Unexpected version syntax must not silently derive the wrong build."""
    with pytest.raises(RuntimeError, match="semantic version"):
        _load_updater_module().sparkle_build_number(version)


def test_waku_resolves_appcast_identity_without_using_the_binary_as_source() -> None:
    """The appcast is identity evidence only; its ZIP is never package input."""
    module = _load_updater_module()

    assert module.resolve_appcast_release(_appcast()) == module.AppcastRelease(
        version=_VERSION,
        build=_BUILD,
        artifact_url=f"https://releases.waku.sh/Waku-{_VERSION}.zip",
        notes_url=f"https://releases.waku.sh/Waku-{_VERSION}.md",
    )


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        (b"<rss>", "Invalid Waku appcast XML"),
        (b"<rss><channel /></rss>", "contains no release items"),
        (
            b"<rss><channel><item /></channel></rss>",
            "missing sparkle:shortVersionString",
        ),
        (
            _appcast().replace(
                f"<sparkle:shortVersionString>{_VERSION}</sparkle:shortVersionString>".encode(),
                b"<sparkle:shortVersionString>  </sparkle:shortVersionString>",
            ),
            "missing sparkle:shortVersionString",
        ),
        (
            _appcast().replace(
                f"<sparkle:version>{_BUILD}</sparkle:version>".encode(),
                b"",
            ),
            "missing sparkle:version",
        ),
        (
            _appcast().replace(
                f"<sparkle:releaseNotesLink>https://releases.waku.sh/Waku-{_VERSION}.md</sparkle:releaseNotesLink>".encode(),
                b"",
            ),
            "missing sparkle:releaseNotesLink",
        ),
        (
            _appcast().replace(b'<enclosure url="', b'<enclosure nope="'),
            "missing enclosure URL",
        ),
        (
            _appcast().replace(
                f'<enclosure url="https://releases.waku.sh/Waku-{_VERSION}.zip" />'.encode(),
                b"",
            ),
            "missing enclosure URL",
        ),
    ],
)
def test_waku_rejects_malformed_appcast_identity(payload: bytes, error: str) -> None:
    """Every appcast identity field is required before resolving public source."""
    with pytest.raises(RuntimeError, match=error):
        _load_updater_module().resolve_appcast_release(payload)


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        (_appcast(build="999"), "build 999 does not match"),
        (
            _appcast(artifact_url="https://example.invalid/Waku.zip"),
            "unexpected artifact URL",
        ),
        (
            _appcast(notes_url="https://example.invalid/notes.md"),
            "unexpected release-notes URL",
        ),
    ],
)
def test_waku_rejects_appcast_fields_that_break_release_conventions(
    payload: bytes,
    error: str,
) -> None:
    """A release must match the repository's deterministic publishing contract."""
    with pytest.raises(RuntimeError, match=error):
        _load_updater_module().resolve_appcast_release(payload)


def test_waku_parses_manifest_and_versioned_changelog_semantically() -> None:
    """Source identity checks operate on TOML and changelog sections, not grep."""
    module = _load_updater_module()

    assert (
        module.manifest_version(
            f'[package]\nname = "waku"\nversion = "{_VERSION}"\n'.encode()
        )
        == _VERSION
    )
    assert (
        module.changelog_notes(
            f"## [Unreleased]\n\n- Soon\n\n## [{_VERSION}]\n\n{_CHANGELOG_BODY}\n\n## [0.1.1]\n\n- Old\n".encode(),
            _VERSION,
        )
        == _CHANGELOG_BODY
    )


@pytest.mark.parametrize(
    ("payload", "error_type", "error"),
    [
        (b"\xff", RuntimeError, "valid UTF-8 TOML"),
        (b"[package\n", RuntimeError, "valid UTF-8 TOML"),
        (b'name = "waku"\n', TypeError, "package table"),
        (b"[package]\nversion = 12\n", TypeError, "string package.version"),
        (b'[package]\nversion = ""\n', TypeError, "string package.version"),
    ],
)
def test_waku_rejects_malformed_source_manifest(
    payload: bytes,
    error_type: type[Exception],
    error: str,
) -> None:
    """The repository manifest must prove a string release version."""
    with pytest.raises(error_type, match=error):
        _load_updater_module().manifest_version(payload)


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        (b"\xff", "valid UTF-8"),
        (b"# Changelog\n", f"has no {_VERSION} section"),
        (
            f"## [{_VERSION}]\n\n## [0.1.14]\n- old\n".encode(),
            f"has an empty {_VERSION} section",
        ),
    ],
)
def test_waku_rejects_unusable_changelog_evidence(
    payload: bytes,
    error: str,
) -> None:
    """A matching non-empty source changelog section is mandatory evidence."""
    with pytest.raises(RuntimeError, match=error):
        _load_updater_module().changelog_notes(payload, _VERSION)


def test_waku_resolver_correlates_three_authoritative_release_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The updater may infer provenance only when every public surface agrees."""
    module = _load_updater_module()
    urls = _install_release_boundaries(monkeypatch, module)

    assert run_async(module.WakuUpdater().fetch_latest(object())) == VersionInfo(
        version=_VERSION,
        metadata={
            "commit": _COMMIT,
            "tag": f"v{_VERSION}",
            "sourceRelationship": module.PROVENANCE_INFERENCE,
        },
    )
    assert urls == [
        module.APPCAST_URL,
        github_raw_url("egoist", "waku", _COMMIT, "Cargo.toml"),
        github_raw_url("egoist", "waku", _COMMIT, "CHANGELOG.md"),
        f"https://releases.waku.sh/Waku-{_VERSION}.md",
    ]


@pytest.mark.parametrize(
    ("commit_payload", "error_type"),
    [
        ([], TypeError),
        ({"sha": "main"}, RuntimeError),
        ({"sha": "A" * 40}, RuntimeError),
    ],
)
def test_waku_rejects_release_without_an_immutable_commit(
    monkeypatch: pytest.MonkeyPatch,
    commit_payload: object,
    error_type: type[Exception],
) -> None:
    """A mutable or malformed tag target must never enter sources.json."""
    module = _load_updater_module()
    _install_release_boundaries(
        monkeypatch,
        module,
        commit_payload=commit_payload,
    )

    with pytest.raises(error_type, match="has no immutable source commit"):
        run_async(module.WakuUpdater().fetch_latest(object()))


@pytest.mark.parametrize(
    ("manifest", "changelog", "notes", "error"),
    [
        (
            b'[package]\nname = "waku"\nversion = "0.1.1"\n',
            None,
            None,
            "manifest version 0.1.1 does not match",
        ),
        (
            None,
            f"## [{_VERSION}]\n\n- Different source notes\n".encode(),
            None,
            "release notes do not match",
        ),
        (
            None,
            None,
            b"- Different published notes",
            "release notes do not match",
        ),
        (None, None, b"", "published release notes are empty"),
        (None, None, b"\xff", "published release notes are not valid UTF-8"),
    ],
)
def test_waku_rejects_divergent_source_provenance(
    monkeypatch: pytest.MonkeyPatch,
    manifest: bytes | None,
    changelog: bytes | None,
    notes: bytes | None,
    error: str,
) -> None:
    """Reject any divergence because this is an evidence-backed inference."""
    module = _load_updater_module()
    _install_release_boundaries(
        monkeypatch,
        module,
        manifest=manifest,
        changelog=changelog,
        release_notes=notes,
    )

    with pytest.raises(RuntimeError, match=error):
        run_async(module.WakuUpdater().fetch_latest(object()))


def test_waku_hashes_the_exact_source_and_cargo_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cargo vendoring must be derived from the same immutable public commit."""
    module = _load_updater_module()
    updater = module.WakuUpdater()
    info = VersionInfo(
        _VERSION,
        {
            "commit": _COMMIT,
            "tag": f"v{_VERSION}",
            "sourceRelationship": module.PROVENANCE_INFERENCE,
        },
    )
    calls = install_fixed_hash_stream(
        monkeypatch,
        ((None, _SRC_HASH), (None, _CARGO_HASH)),
    )

    run_async(collect_events(updater.fetch_hashes(info, object())))

    assert updater.supported_platforms == ("aarch64-darwin",)
    assert_nix_ast_equal(
        str(calls[0]["expr"]),
        _build_fetch_from_github_call(
            "egoist",
            "waku",
            rev=_COMMIT,
            fetch_submodules=False,
        ),
    )
    source_override = SourceEntry(
        version=_VERSION,
        commit=_COMMIT,
        hashes=HashCollection.from_value([
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("cargoHash", updater.config.fake_hash),
        ]),
    )
    assert_nix_ast_equal(
        str(calls[1]["expr"]),
        _build_package_path_attr_expr(
            "waku",
            ".cargoDeps",
            system="aarch64-darwin",
            source_overrides={"waku": source_override},
        ),
    )

    assert updater.build_result(
        info,
        [
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("cargoHash", _CARGO_HASH),
        ],
    ) == SourceEntry(
        version=_VERSION,
        commit=_COMMIT,
        hashes=HashCollection.from_value([
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("cargoHash", _CARGO_HASH),
        ]),
    )


def test_waku_requires_commit_when_building_source_result() -> None:
    """Hand-written metadata cannot bypass immutable source ownership."""
    with pytest.raises(RuntimeError, match="missing an immutable source commit"):
        _load_updater_module().WakuUpdater().build_result(
            VersionInfo(_VERSION),
            [HashEntry.create("srcHash", _SRC_HASH)],
        )
    with pytest.raises(RuntimeError, match="missing an immutable source commit"):
        _load_updater_module().WakuUpdater().build_result(
            VersionInfo(_VERSION, {"commit": "main"}),
            [HashEntry.create("srcHash", _SRC_HASH)],
        )


def test_waku_source_metadata_is_exact_and_never_names_a_vendor_binary() -> None:
    """Bootstrap metadata pins source only; the ZIP/DMG remain evidence."""
    source = json.loads((_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8"))

    assert source == {
        "commit": _COMMIT,
        "hashes": [
            {"hash": _CARGO_HASH, "hashType": "cargoHash"},
            {"hash": _SRC_HASH, "hashType": "srcHash"},
        ],
        "version": _VERSION,
    }
    assert ".zip" not in json.dumps(source)
    assert ".dmg" not in json.dumps(source)


def test_waku_validates_the_materialized_bootstrap_source() -> None:
    """Validation must build the package after source metadata promotion."""
    module = _load_updater_module()

    assert module.WakuUpdater.derivation_validations == (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            mode="build",
        ),
    )


def test_waku_package_is_a_source_built_nix_owned_arm64_app() -> None:
    """The derivation must compile public source and enable runtime shaders."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    assertion = expect_instance(package.output, Assertion)
    derivation = expect_instance(assertion.body, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)

    formal_names = {
        argument.name
        for argument in package.argument_set
        if isinstance(argument, Identifier)
    }
    assert "outputs" in formal_names
    assert "selfSource" not in formal_names
    assert "swift" in formal_names
    assert_nix_ast_equal(
        expect_binding(derivation.scope, "source").value,
        "outputs.lib.sourceEntry pname",
    )
    assert_nix_ast_equal(
        expect_binding(derivation.scope, "version").value,
        "source.version",
    )
    assert_nix_ast_equal(
        expect_binding(derivation.scope, "buildNumber").value,
        """toString (
          ((lib.elemAt versionParts 0) * 1000000)
          + ((lib.elemAt versionParts 1) * 1000)
          + (lib.elemAt versionParts 2)
        )""",
    )
    assert_nix_ast_equal(
        expect_binding(derivation.scope, "minimumMacosVersion").value,
        StringPrimitive(value="14.0"),
    )
    assert_nix_ast_equal(
        expect_binding(derivation.scope, "minimumMacosTarget").value,
        '"arm64-apple-macos${minimumMacosVersion}"',
    )

    assert_nix_ast_equal(
        assertion.expression,
        'stdenv.hostPlatform.system == "aarch64-darwin"',
    )
    assert_nix_ast_equal(
        derivation.name,
        Identifier(name="rustPlatform.buildRustPackage"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "src").value,
        nix_attrset_call(
            Identifier(name="fetchFromGitHub"),
            owner="egoist",
            repo="waku",
            rev=identifier_attr_path("source", "commit"),
            hash=nix_apply(
                identifier_attr_path("outputs", "lib", "sourceHash"),
                Identifier(name="pname"),
                StringPrimitive(value="srcHash"),
            ),
        ),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "cargoHash").value,
        'outputs.lib.sourceHash pname "cargoHash"',
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "buildFeatures").value,
        '[ "gpui_platform/runtime_shaders" ]',
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "cargoBuildFlags").value,
        """[
          "--package"
          "waku"
          "--bin"
          "waku"
          "--bin"
          "waku_js_repl"
          "--package"
          "waku-daemon"
          "--bin"
          "waku-daemon"
        ]""",
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "nativeBuildInputs").value,
        """[
          cmake
          lld
          pkg-config
          python3
          rustPlatform.bindgenHook
          swift
        ]""",
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "doCheck").value,
        Primitive(value=False),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "strictDeps").value,
        Primitive(value=True),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "dontFixup").value,
        Primitive(value=True),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "doInstallCheck").value,
        Primitive(value=True),
    )

    assert_nix_ast_equal(
        expect_binding(arguments.values, "patches").value,
        "[ ./runtime-shaders.patch ]",
    )


def test_waku_bundle_has_tcc_identity_no_sparkle_and_leaf_first_signing() -> None:
    """Bundle assembly must retain helper identity while suppressing self-update."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    assertion = expect_instance(package.output, Assertion)
    derivation = expect_instance(assertion.body, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)

    install = expect_instance(
        expect_binding(arguments.values, "installPhase").value,
        IndentedString,
    )
    install_commands = parse_shell(indented_string_body(install.rebuild()))
    install_texts = command_texts(install_commands)
    assert command_texts(install_commands, "env") == []
    assert [
        command
        for command in command_texts(install_commands, "/usr/bin/plutil")
        if "LSMinimumSystemVersion" in command
    ] == [
        "/usr/bin/plutil -replace LSMinimumSystemVersion -string \\\n"
        '      "__NIX_INTERP__" "$contents/Info.plist"',
        "/usr/bin/plutil -replace LSMinimumSystemVersion -string \\\n"
        '      "__NIX_INTERP__" "$helperContents/Info.plist"',
    ]
    assert command_texts(install_commands, "__NIX_INTERP__") == [
        "__NIX_INTERP__ \\\n"
        "      -O \\\n"
        "      -parse-as-library \\\n"
        '      -module-cache-path "$swiftModuleCache" \\\n'
        "      -target __NIX_INTERP__ \\\n"
        "      resources/computer-use/WakuComputerUse.swift \\\n"
        '      -o "$helperExecutable"',
        "__NIX_INTERP__ -version",
    ]
    assert command_texts(install_commands, "printf") == [
        "printf '%s\\n' \\\n"
        '        "standalone-service-v2" \\\n'
        '        "__NIX_INTERP__" \\\n'
        '        "sh.waku.computer-use" \\\n'
        '        "-" \\\n'
        '        "__NIX_INTERP__"',
        "printf '%s\\n' \"$helperFingerprint\"",
    ]
    signing_commands = [
        '/usr/bin/codesign --force --identifier "sh.waku.computer-use" --sign - "$helper"',
        '/usr/bin/codesign --force --identifier "sh.waku.js-repl" --sign - "$repl"',
        '/usr/bin/codesign --force --identifier "sh.waku.daemon" --sign - "$daemon"',
        '/usr/bin/codesign --force --identifier "sh.waku" --sign - "$app"',
    ]
    assert command_texts(install_commands, "/usr/bin/codesign") == signing_commands
    assert install_texts.index("runHook postInstall") < install_texts.index(
        signing_commands[0]
    )

    check = expect_instance(
        expect_binding(arguments.values, "installCheckPhase").value,
        IndentedString,
    )
    check_commands = parse_shell(indented_string_body(check.rebuild()))
    minimum_version_loop = list(
        iter_nodes(check_commands.tree.root_node, "for_statement")
    )[-1]
    assert node_text(minimum_version_loop, check_commands.sanitized) == (
        'for machO in "$executable" "$repl" "$daemon" "$helperExecutable"; do\n'
        '      test "$(\n'
        '        /usr/bin/otool -l "$machO" |\n'
        '          awk \'$1 == "cmd" && $2 == "LC_BUILD_VERSION" { inBuildVersion = 1; next } \\\n'
        '            inBuildVersion && $1 == "minos" { print $2; exit }\'\n'
        '      )" = "__NIX_INTERP__"\n'
        "    done"
    )
    assert command_texts(check_commands, "/usr/bin/vtool") == []
    assert command_texts(check_commands, "/usr/bin/otool") == [
        '/usr/bin/otool -l "$machO"',
    ]
    assert command_texts(check_commands, "awk") == [
        'awk \'$1 == "cmd" && $2 == "LC_BUILD_VERSION" { inBuildVersion = 1; next } \\\n'
        '            inBuildVersion && $1 == "minos" { print $2; exit }\'',
    ]
    assert command_texts(check_commands, "/usr/libexec/PlistBuddy")[:6] == [
        "/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' \"$infoPlist\"",
        "/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \"$infoPlist\"",
        "/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' \"$infoPlist\"",
        "/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' \"$infoPlist\"",
        "/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' \"$helperInfoPlist\"",
        "/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' \"$helperInfoPlist\"",
    ]
    assert command_texts(check_commands, "/usr/bin/codesign") == [
        '/usr/bin/codesign --verify --strict --verbose=2 "$helper"',
        '/usr/bin/codesign --verify --strict --verbose=2 "$repl"',
        '/usr/bin/codesign --verify --strict --verbose=2 "$daemon"',
        '/usr/bin/codesign --verify --deep --strict --verbose=2 "$app"',
    ]
    assert command_texts(check_commands, "/usr/bin/lipo") == [
        '/usr/bin/lipo "$executable" -verify_arch arm64',
        '/usr/bin/lipo "$repl" -verify_arch arm64',
        '/usr/bin/lipo "$daemon" -verify_arch arm64',
        '/usr/bin/lipo "$helperExecutable" -verify_arch arm64',
    ]

    passthru_set = expect_instance(
        expect_binding(arguments.values, "passthru").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(passthru_set.values, "buildNumber").value,
        "buildNumber",
    )
    mac_app = expect_instance(
        expect_binding(passthru_set.values, "macApp").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(mac_app.values, "bundleId").value,
        StringPrimitive(value="sh.waku"),
    )
    assert_nix_ast_equal(
        expect_binding(mac_app.values, "bundleRelPath").value,
        StringPrimitive(value="Applications/Waku.app"),
    )

    metadata = expect_instance(
        expect_binding(arguments.values, "meta").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(metadata.values, "license").value,
        "lib.licenses.gpl3Only",
    )
    assert_nix_ast_equal(
        expect_binding(metadata.values, "platforms").value,
        '[ "aarch64-darwin" ]',
    )
