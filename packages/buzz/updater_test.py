"""Focused contracts for the blocked, source-first Buzz desktop foundation."""

import hashlib
import json
from types import ModuleType
from typing import cast

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    binding_map,
    expect_binding,
    nix_attrset_call,
    parse_nix_expr,
)
from lib.tests._nix_source import nix_file_expr, nix_source_fragment_expr
from lib.tests._package_registry import registry_override_metadata
from lib.tests._updater_helpers import (
    collect_events,
    install_fixed_hash_stream,
    load_repo_module,
    run_async,
)
from lib.update.events import UpdateEventKind, expect_source_hashes
from lib.update.net import github_raw_url
from lib.update.nix import (
    _build_fetch_from_github_call,
    _build_repo_package_attr_expr,
)
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo

_PACKAGE_DIR = REPO_ROOT / "packages/buzz"
_VERSION = "0.5.20"
_TAG = f"desktop-v{_VERSION}"
_COMMIT = "95154bee4034ca7a40b33095c2ddbde8c9aa1614"
_SOURCE_NAR_HASH = "sha256-+5fdFmxB9TOgYoeJrEs2FCYldku4OyEJVrpdC/FYRFQ="
_ONNX_SOURCE_NAR_HASH = "sha256-F7saqFMOErHI6OtGG4UCWnErvl1scRP51Y7UjBTIII4="
_SHERPA_SOURCE_NAR_HASH = "sha256-vzrc2Vn5IwORsA++UqN8UN3LkcqyPRO05qj7u8kyGkI="
_MESH_SOURCE_NAR_HASH = "sha256-RXjmM66u40cxnacbvTtCFJShMK4BM+MHOyJ2vQ7Gw60="
_LLAMA_SOURCE_NAR_HASH = "sha256-cN+27Zi0dGo30D08CxwpPwb2hY4NgkGNaWgkmz8I0f8="
_NPM_DEPS_HASH = "sha256-eYkZY0OWiQvC31HHaXhC4b7vtVT2N10OORMbZBOI080="
_ROOT_CARGO_HASH = "sha256-y067FJWvsJAe6mvtnLPSW1YK0/gcBrKuZX45OCO8/2U="
_DESKTOP_CARGO_HASH = "sha256-lY+RpF27tXET7RXhAHLQ3SwEJR+1g4EnMKY+QFPqavQ="
_ONNX_RUNTIME_VERSION = "1.27.0"
_ONNX_RUNTIME_COMMIT = "8f0278c77bf44b0cc83c098c6c722b92a36ac4b5"
_SHERPA_ONNX_VERSION = "1.13.4"
_SHERPA_ONNX_COMMIT = "142807252687d81b40d6315f23470a1512a00de3"
_MESH_COMMIT = "3295c902d4c4f859aaadf9240042ffdaf06dd07e"
_LLAMA_COMMIT = "8190848bb36c7df4251db4352bd81bc07d0a4385"
_MESH_URL = (
    "https://github.com/Mesh-LLM/mesh-llm/archive/"
    "3295c902d4c4f859aaadf9240042ffdaf06dd07e.tar.gz"
)
_LLAMA_URL = (
    "https://github.com/ggml-org/llama.cpp/archive/"
    "8190848bb36c7df4251db4352bd81bc07d0a4385.tar.gz"
)
_RUST_VERSION = "1.95.0"
_MESH_SOURCE = (
    f"git+https://github.com/Mesh-LLM/mesh-llm.git?tag=v0.75.1#{_MESH_COMMIT}"
)

_LOAD_PLAN_ITER_CHAIN = (
    b"self\n"
    b"            .manifest\n"
    b"            .runtime\n"
    b"            .libraries\n"
    b"            .iter()"
)
_LOAD_PLAN_REVERSED_ITER_CHAIN = (
    b"self\n"
    b"            .manifest\n"
    b"            .runtime\n"
    b"            .libraries\n"
    b"            .iter().rev()"
)
_SIDECAR_SPECS = (
    ("buzz-acp", "buzz-acp"),
    ("buzz-agent", "buzz-agent"),
    ("buzz-backend-kubernetes", "buzz-backend-kubernetes"),
    ("buzz-dev-mcp", "buzz-dev-mcp"),
    ("git-credential-nostr", "git-credential-nostr"),
    ("buzz-cli", "buzz"),
)


def _load_updater_module() -> ModuleType:
    return load_repo_module("packages/buzz/updater.py", "buzz_updater_test")


def _json_bytes(value: object) -> bytes:
    return json.dumps(value).encode()


def _lock_toml(*, mesh: bool) -> bytes:
    packages = [
        """
[[package]]
name = "sherpa-onnx"
version = "1.13.4"
source = "registry+https://github.com/rust-lang/crates.io-index"
""",
        """
[[package]]
name = "sherpa-onnx-sys"
version = "1.13.4"
source = "registry+https://github.com/rust-lang/crates.io-index"
""",
    ]
    if mesh:
        packages.append(f"""
[[package]]
name = "mesh-llm-sdk"
version = "0.75.1"
source = "{_MESH_SOURCE}"
""")
    return "\n".join(packages).encode()


def _sidecar_manifest(package: str, binary: str) -> bytes:
    return f"""
[package]
name = "{package}"
version = "0.1.0"

[[bin]]
name = "{binary}"
path = "src/main.rs"
""".encode()


def _source_payloads() -> dict[str, bytes]:
    common_dependency = (
        'git = "https://github.com/Mesh-LLM/mesh-llm.git", '
        'tag = "v0.75.1", optional = true'
    )
    mesh_dependencies = "\n".join((
        f'mesh-llm-sdk = {{ {common_dependency}, package = "mesh-llm-sdk", '
        'default-features = false, features = ["client", "serving"] }',
        f"mesh-llm-host-runtime = {{ {common_dependency}, "
        'package = "mesh-llm-host-runtime", default-features = false, '
        'features = ["dynamic-native-runtime"] }',
        *(
            f'{name} = {{ {common_dependency}, package = "{name}" }}'
            for name in (
                "mesh-llm-client",
                "mesh-llm-node",
                "mesh-llm-system",
                "mesh-llm-events",
            )
        ),
    ))
    mesh_feature = ", ".join(
        f'"dep:{name}"'
        for name in (
            "iroh",
            "mesh-llm-sdk",
            "mesh-llm-host-runtime",
            "mesh-llm-client",
            "mesh-llm-node",
            "mesh-llm-system",
            "mesh-llm-events",
        )
    )
    payloads = {
        "package.json": _json_bytes({
            "name": "buzz-workspace",
            "private": True,
            "packageManager": "pnpm@11.4.0",
        }),
        "desktop/package.json": _json_bytes({
            "name": "buzz",
            "private": True,
            "version": _VERSION,
        }),
        "desktop/src-tauri/tauri.conf.json": _json_bytes({
            "productName": "Buzz",
            "version": _VERSION,
            "identifier": "xyz.block.buzz.app",
            "plugins": {"updater": {"endpoints": []}},
            "bundle": {
                "externalBin": [
                    "binaries/buzz-acp",
                    "binaries/buzz-agent",
                    "binaries/buzz-backend-kubernetes",
                    "binaries/buzz-dev-mcp",
                    "binaries/git-credential-nostr",
                    "binaries/buzz",
                ]
            },
        }),
        "Cargo.toml": (
            "[workspace]\n"
            "members = [\n"
            + "".join(f'  "crates/{package}",\n' for package, _ in _SIDECAR_SPECS)
            + "]\n"
        ).encode(),
        "Cargo.lock": _lock_toml(mesh=False),
        "desktop/src-tauri/Cargo.toml": f"""
[package]
name = "buzz-desktop"
version = "{_VERSION}"

[features]
mesh-llm = [{mesh_feature}]

[dependencies]
sherpa-onnx = "1.12"
{mesh_dependencies}
""".encode(),
        "desktop/src-tauri/Cargo.lock": _lock_toml(mesh=True),
        "rust-toolchain.toml": (
            f'[toolchain]\nchannel = "{_RUST_VERSION}"\nprofile = "default"\n'
        ).encode(),
        "scripts/bundle-sidecars.sh": b"""#!/usr/bin/env bash
SIDECARS=(buzz-acp buzz-agent buzz-dev-mcp git-credential-nostr buzz)
if [[ "$TARGET" != *windows* ]]; then
    SIDECARS+=(buzz-backend-kubernetes)
fi
""",
        "desktop/src-tauri/build.rs": b"""
fn main() {
    let updater_public_key = std::env::var("BUZZ_UPDATER_PUBLIC_KEY")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty());
    let updater_endpoint = std::env::var("BUZZ_UPDATER_ENDPOINT")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty());
    if updater_public_key.is_some() && updater_endpoint.is_some() {
        println!("cargo:rustc-cfg=buzz_updater_enabled");
    }
}
""",
        "desktop/src-tauri/src/lib.rs": b"""
#[cfg(buzz_updater_enabled)]
let builder = if cfg!(debug_assertions) {
    builder
} else {
    builder.plugin(tauri_plugin_updater::Builder::new().build())
};
""",
        "desktop/src-tauri/src/mesh_llm/mod.rs": b"""
async fn initialize_mesh_native_runtime() -> anyhow::Result<()> {
    mesh_llm_host_runtime::initialize_host_runtime().await?;
    Ok(())
}
""",
    }
    payloads.update({
        f"crates/{package}/Cargo.toml": _sidecar_manifest(package, binary)
        for package, binary in _SIDECAR_SPECS
    })
    return payloads


def _mesh_source_payloads() -> dict[str, bytes]:
    return {
        "crates/mesh-llm-sdk/Cargo.toml": b"""
[features]
default = ["client"]
serving = ["node", "dep:anyhow", "dep:mesh-llm-embedded-runtime", "dep:mesh-llm-runtime-install", "mesh-llm-embedded-runtime/dynamic-native-runtime", "dep:reqwest", "dep:serde", "dep:serde_json"]
""",
        "crates/mesh-llm-embedded-runtime/Cargo.toml": b"""
[features]
default = []
dynamic-native-runtime = ["mesh-llm-host-runtime/dynamic-native-runtime"]
""",
        "crates/mesh-llm-host-runtime/Cargo.toml": b"""
[features]
default = ["web-ui", "dynamic-native-runtime"]
dynamic-native-runtime = ["mesh-llm-system/dynamic-native-runtime", "skippy-runtime/dynamic-native-runtime", "skippy-server/dynamic-native-runtime"]
""",
        "crates/mesh-llm-system/Cargo.toml": b"""
[features]
skippy-devices = ["dep:skippy-runtime"]
dynamic-native-runtime = ["skippy-devices", "skippy-runtime/dynamic-native-runtime"]
""",
        "crates/skippy-runtime/Cargo.toml": b"""
[features]
default = []
dynamic-native-runtime = ["skippy-ffi/dynamic-runtime"]
""",
        "crates/skippy-server/Cargo.toml": b"""
[features]
default = []
dynamic-native-runtime = ["skippy-runtime/dynamic-native-runtime"]
""",
        "crates/skippy-ffi/Cargo.toml": b"""
[features]
default = ["dynamic-runtime"]
dynamic-runtime = ["dep:libloading"]
""",
        "crates/skippy-ffi/build.rs": b"""
fn main() {
    if std::env::var_os("CARGO_FEATURE_DYNAMIC_RUNTIME").is_some() {
        return;
    }
    let _stage = std::env::var("LLAMA_STAGE_BUILD_DIR");
}
""",
        "crates/skippy-ffi/src/lib.rs": b"""
pub const ABI_VERSION_MAJOR: u32 = 0;
pub const ABI_VERSION_MINOR: u32 = 1;
pub const ABI_VERSION_PATCH: u32 = 35;
""",
        "crates/mesh-llm-host-runtime/src/lib.rs": (
            b"try_load_installed_native_runtime(startup_selection).await?;\n"
        ),
        "crates/mesh-llm-host-runtime/src/system/native_runtime.rs": b"""
fn default_install_options() -> NativeRuntimeInstallOptions {
    NativeRuntimeInstallOptions { ..Default::default() }
}
""",
        "crates/mesh-llm-native-runtime/src/manifest.rs": b"""
use std::collections::BTreeMap;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct NativeRuntimePlatform {
    pub os: String,
    pub arch: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct NativeRuntimeArtifact {
    pub id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mesh_version: Option<String>,
    pub skippy_abi: String,
    pub platform: NativeRuntimePlatform,
    pub backend: NativeRuntimeBackend,
    #[serde(default)]
    pub rank: i64,
    pub libraries: Vec<String>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub files: BTreeMap<String, String>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub tools: BTreeMap<String, String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sha256: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub signature: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct NativeRuntimeManifest {
    pub runtime: NativeRuntimeArtifact,
}

pub struct NativeRuntimeBackend {
    pub kind: NativeRuntimeBackendKind,
    pub cuda: Option<String>,
    pub rocm: Option<String>,
    pub vulkan: Option<String>,
}
impl NativeRuntimeManifest {
    fn verify_contents(&self, dir: &Path) -> Result<()> {
        if self.runtime.files.is_empty() {
            bail!(
                "native runtime artifact {} does not declare file checksums",
                self.runtime.id
            );
        }
        for library in &self.runtime.libraries {
            if !self.runtime.files.contains_key(library) {
                bail!(
                    "native runtime artifact {} library {} is missing a file checksum",
                    self.runtime.id,
                    library
                );
            }
        }
        verify_file_checksums(dir, "file", &self.runtime.files)?;
        verify_file_checksums(dir, "tool", &self.runtime.tools)
    }
}
""",
        "crates/mesh-llm-native-runtime/src/flavor.rs": b"""
impl NativeRuntimeBackend {
    pub fn metal() -> Self {
        Self {
            kind: NativeRuntimeBackendKind::Metal,
            cuda: None,
            rocm: None,
            vulkan: None,
        }
    }
}
""",
        "crates/mesh-llm-native-runtime/src/load_plan.rs": b"""
impl InstalledNativeRuntime {
    pub fn load_plan(&self) -> Result<NativeRuntimeLoadPlan> {
        let libraries = self
            .manifest
            .runtime
            .libraries
            .iter()
            .map(|path| self.path.join(path))
            .collect::<Vec<_>>();
        if libraries.is_empty() {
            bail!(
                "native runtime {} does not declare loadable libraries",
                self.native_runtime_id
            );
        }
        for library in &libraries {
            if !library.is_file() {
                bail!("native runtime library is missing: {}", library.display());
            }
        }
        Ok(NativeRuntimeLoadPlan {
            mesh_version: self.mesh_version.clone(),
            native_runtime_id: self.native_runtime_id.clone(),
            root: self.path.clone(),
            libraries,
        })
    }
}
""",
        "crates/mesh-llm-runtime-install/src/lib.rs": b"""
const MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL_ENV: &str =
    "MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL";
impl Default for NativeRuntimeManifestOptions {
    fn default() -> Self {
        Self {
            allow_default_manifest_url: true,
        }
    }
}
impl Default for NativeRuntimeInstallOptions {
    fn default() -> Self {
        Self {
            verification_policy: NativeRuntimeVerificationPolicy::RequireChecksum,
            allow_download: true,
        }
    }
}
pub async fn install_native_runtime() {
    let manifest_url = std::env::var(MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL_ENV).ok();
    let manifest_options = NativeRuntimeManifestOptions {
        manifest_url,
        allow_default_manifest_url: true,
    };
}
bail!("native runtime signature verification is not implemented yet");
""",
        "crates/mesh-llm-runtime-install/src/discovery.rs": b"""
pub const NATIVE_RUNTIME_BUNDLE_DIR_ENV: &str = "MESH_LLM_NATIVE_RUNTIME_BUNDLE_DIR";
""",
        "scripts/build-llama.sh": b"cmake --build source\n",
        "scripts/package-native-runtime.sh": (
            b"LLAMA_STAGE_LINK_MODE=dynamic LLAMA_STAGE_BACKEND=metal ./build-llama.sh\n"
        ),
        "scripts/prepare-llama.sh": b"git apply third_party/llama.cpp/patches/*.patch\n",
        "third_party/llama.cpp/upstream.txt": f"{_LLAMA_COMMIT}\n".encode(),
    }


def _onnx_source_payloads() -> dict[str, bytes]:
    return {
        "VERSION_NUMBER": f"{_ONNX_RUNTIME_VERSION}\n".encode(),
        ".gitmodules": b'[submodule "cmake/external/onnx"]\n',
        "cmake/deps.txt": b"onnx;https://example.invalid/onnx.tar.gz\n",
        "cmake/onnxruntime.cmake": b"add_library(onnxruntime STATIC)\n",
    }


def _sherpa_source_payloads() -> dict[str, bytes]:
    return {
        "CMakeLists.txt": (
            f'set(SHERPA_ONNX_VERSION "{_SHERPA_ONNX_VERSION}")\n'.encode()
        ),
        "cmake/onnxruntime.cmake": b"function(download_onnxruntime)\nendfunction()\n",
        "sherpa-onnx/csrc/CMakeLists.txt": b"add_library(sherpa-onnx-core STATIC)\n",
        "sherpa-onnx/c-api/CMakeLists.txt": b"add_library(sherpa-onnx-c-api STATIC)\n",
        "cmake/sherpa-onnx-static.pc.in": b"Libs: -lsherpa-onnx-c-api\n",
        "cmake/kaldi-native-fbank.cmake": b"FetchContent_Declare(kaldi_native_fbank)\n",
        "cmake/kaldi-decoder.cmake": b"FetchContent_Declare(kaldi_decoder)\n",
        "cmake/simple-sentencepiece.cmake": b"FetchContent_Declare(sentencepiece)\n",
        "cmake/piper-phonemize.cmake": b"FetchContent_Declare(piper_phonemize)\n",
        "cmake/espeak-ng-for-piper.cmake": b"FetchContent_Declare(espeak_ng)\n",
        "cmake/openfst.cmake": b"FetchContent_Declare(openfst)\n",
        "cmake/hclust-cpp.cmake": b"FetchContent_Declare(hclust_cpp)\n",
        "sherpa-onnx/rust/sherpa-onnx-sys/Cargo.toml": b"""
[package]
name = "sherpa-onnx-sys"
version = "1.13.4"

[features]
default = ["static"]
static = []
shared = []
""",
        "sherpa-onnx/rust/sherpa-onnx-sys/build.rs": b"""
const SHERPA_ONNX_STATIC_LIBS: &[&str] = &[
    "sherpa-onnx-c-api",
    "sherpa-onnx-core",
    "kaldi-decoder-core",
    "sherpa-onnx-kaldifst-core",
    "sherpa-onnx-fstfar",
    "sherpa-onnx-fst",
    "kaldi-native-fbank-core",
    "kissfft-float",
    "piper_phonemize",
    "espeak-ng",
    "ucd",
    "onnxruntime",
    "ssentencepiece_core",
];
fn try_main() -> Result<(), DynError> {
    if env::var_os("DOCS_RS").is_some() {
        // docs.rs skips native setup.
        return Ok(());
    }
    let link_mode = resolve_link_mode()?;
    let lib_dir = resolve_lib_dir(link_mode, &target_os, &target_arch)?;
    Ok(())
}
fn resolve_link_mode() -> Result<LinkMode, DynError> {
    let static_enabled = env::var_os("CARGO_FEATURE_STATIC").is_some();
    let shared_enabled = env::var_os("CARGO_FEATURE_SHARED").is_some();
    if static_enabled && shared_enabled {
        return Err("static and shared cannot both be enabled".into());
    }
    Ok(LinkMode::Static)
}
fn resolve_lib_dir(link_mode: LinkMode, target_os: &str, target_arch: &str) -> Result<PathBuf, DynError> {
    if let Some(path) = env::var_os("SHERPA_ONNX_LIB_DIR") {
        let path = PathBuf::from(path);
        if !path.is_dir() {
            return Err("local library directory is invalid".into());
        }
        return Ok(path);
    }
    download_prebuilt_libs(link_mode, target_os, target_arch)
}
fn download_prebuilt_libs() {
    if let Some(local_archive_dir) = env::var_os("SHERPA_ONNX_ARCHIVE_DIR") {
        let local_archive_path = PathBuf::from(local_archive_dir).join(&archive_name);
        copy_file_atomically(&local_archive_path, &archive_path)?;
    } else {
        let response = ureq::builder().build().get(&url).call()?;
    }
}
fn emit_static_link_directives(target_os: &str) {
    for lib in SHERPA_ONNX_STATIC_LIBS {
        println!("cargo:rustc-link-lib=static={lib}");
    }
    match target_os {
        "macos" => {
            println!("cargo:rustc-link-lib=dylib=c++");
            println!("cargo:rustc-link-lib=framework=Foundation");
        }
        _ => {}
    }
}
""",
        "sherpa-onnx/rust/sherpa-onnx/Cargo.toml": b"""
[package]
name = "sherpa-onnx"
version = "1.13.4"

[dependencies.sherpa-onnx-sys]
version = "1.13.4"
path = "../sherpa-onnx-sys"
default-features = false

[features]
default = ["static"]
static = ["sherpa-onnx-sys/static"]
shared = ["sherpa-onnx-sys/shared"]
""",
    }


def _archive_url(owner: str, repo: str, commit: str) -> str:
    return f"https://github.com/{owner}/{repo}/archive/{commit}.tar.gz"


def _urls() -> dict[str, str]:
    return {
        "buzzUrl": _archive_url("block", "buzz", _COMMIT),
        "llamaCppUrl": _LLAMA_URL,
        "meshLlmUrl": _MESH_URL,
        "onnxruntimeUrl": _archive_url(
            "microsoft",
            "onnxruntime",
            _ONNX_RUNTIME_COMMIT,
        ),
        "sherpaOnnxUrl": _archive_url(
            "k2-fsa",
            "sherpa-onnx",
            _SHERPA_ONNX_COMMIT,
        ),
    }


def _install_digest_contracts(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    buzz_payloads: dict[str, bytes],
    mesh_payloads: dict[str, bytes],
    onnx_payloads: dict[str, bytes] | None = None,
    sherpa_payloads: dict[str, bytes] | None = None,
) -> None:
    buzz_paths = tuple(module._BUZZ_SOURCE_DIGESTS)
    mesh_paths = tuple(module._MESH_SOURCE_DIGESTS)
    monkeypatch.setattr(
        module,
        "_BUZZ_SOURCE_DIGESTS",
        {path: hashlib.sha256(buzz_payloads[path]).hexdigest() for path in buzz_paths},
    )
    monkeypatch.setattr(
        module,
        "_MESH_SOURCE_DIGESTS",
        {path: hashlib.sha256(mesh_payloads[path]).hexdigest() for path in mesh_paths},
    )
    monkeypatch.setattr(module, "_MESH_SOURCE_PATHS", mesh_paths)
    if onnx_payloads is not None:
        onnx_paths = tuple(module._ONNX_SOURCE_DIGESTS)
        monkeypatch.setattr(
            module,
            "_ONNX_SOURCE_DIGESTS",
            {
                path: hashlib.sha256(onnx_payloads[path]).hexdigest()
                for path in onnx_paths
            },
        )
        monkeypatch.setattr(module, "_ONNX_SOURCE_PATHS", onnx_paths)
    if sherpa_payloads is not None:
        sherpa_paths = tuple(module._SHERPA_SOURCE_DIGESTS)
        monkeypatch.setattr(
            module,
            "_SHERPA_SOURCE_DIGESTS",
            {
                path: hashlib.sha256(sherpa_payloads[path]).hexdigest()
                for path in sherpa_paths
            },
        )
        monkeypatch.setattr(module, "_SHERPA_SOURCE_PATHS", sherpa_paths)


def _version_info() -> VersionInfo:
    return VersionInfo(
        _VERSION,
        {"commit": _COMMIT, "tag": _TAG, **_urls()},
    )


def _source_override(
    updater: object,
    *,
    src_hash: str,
    onnx_hash: str,
    sherpa_hash: str,
    mesh_hash: str,
    llama_hash: str,
    npm_hash: str,
    root_hash: str,
    desktop_hash: str,
) -> SourceEntry:
    assert hasattr(updater, "name")
    return SourceEntry(
        version=_VERSION,
        commit=_COMMIT,
        hashes=HashCollection.from_value([
            HashEntry.create("srcHash", src_hash, url=_urls()["buzzUrl"]),
            HashEntry.create(
                "srcHash",
                onnx_hash,
                url=_urls()["onnxruntimeUrl"],
            ),
            HashEntry.create(
                "srcHash",
                sherpa_hash,
                url=_urls()["sherpaOnnxUrl"],
            ),
            HashEntry.create(
                "srcHash",
                mesh_hash,
                url=_urls()["meshLlmUrl"],
            ),
            HashEntry.create(
                "srcHash",
                llama_hash,
                url=_urls()["llamaCppUrl"],
            ),
            HashEntry.create("npmDepsHash", npm_hash, url=_urls()["buzzUrl"]),
            HashEntry.create("vendorHash", root_hash, url=_urls()["buzzUrl"]),
            HashEntry.create("cargoHash", desktop_hash, url=_urls()["buzzUrl"]),
        ]),
        urls={
            "buzz": _urls()["buzzUrl"],
            "llamaCpp": _urls()["llamaCppUrl"],
            "meshLlm": _urls()["meshLlmUrl"],
            "onnxruntime": _urls()["onnxruntimeUrl"],
            "sherpaOnnx": _urls()["sherpaOnnxUrl"],
        },
    )


def test_buzz_audits_the_exact_release_source_topology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Release discovery must verify every source-level build boundary."""
    module = _load_updater_module()
    updater = module.BuzzUpdater()
    payloads = _source_payloads()
    mesh_payloads = _mesh_source_payloads()
    onnx_payloads = _onnx_source_payloads()
    sherpa_payloads = _sherpa_source_payloads()
    _install_digest_contracts(
        monkeypatch,
        module,
        payloads,
        mesh_payloads,
        onnx_payloads,
        sherpa_payloads,
    )
    raw_payloads = {
        github_raw_url("block", "buzz", _COMMIT, path): payload
        for path, payload in payloads.items()
    }
    raw_payloads.update({
        github_raw_url("Mesh-LLM", "mesh-llm", _MESH_COMMIT, path): payload
        for path, payload in mesh_payloads.items()
    })
    # Mesh's digest-pinned upstream.txt is the available behavioral anchor for
    # llama.cpp. Direct llama raw-byte checks stay absent until independently
    # authoritative digests exist; its archive identity is still hashed below.
    raw_payloads.update({
        github_raw_url(
            "microsoft",
            "onnxruntime",
            _ONNX_RUNTIME_COMMIT,
            path,
        ): payload
        for path, payload in onnx_payloads.items()
    })
    raw_payloads.update({
        github_raw_url(
            "k2-fsa",
            "sherpa-onnx",
            _SHERPA_ONNX_COMMIT,
            path,
        ): payload
        for path, payload in sherpa_payloads.items()
    })
    api_paths: list[str] = []
    fetched_urls: list[str] = []

    async def release_payload(
        _session: object,
        path: str,
        *,
        config: object,
    ) -> dict[str, str]:
        assert config == updater.config
        api_paths.append(path)
        return {"tag_name": _TAG}

    async def commit_payload(
        _session: object,
        path: str,
        *,
        config: object,
    ) -> dict[str, str]:
        assert config == updater.config
        api_paths.append(path)
        return {"sha": _COMMIT}

    async def raw_payload(
        _session: object,
        url: str,
        *,
        config: object,
    ) -> bytes:
        assert config == updater.config
        fetched_urls.append(url)
        return raw_payloads[url]

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        release_payload,
    )
    monkeypatch.setattr(module, "fetch_github_api", commit_payload)
    monkeypatch.setattr(module, "fetch_url", raw_payload)

    assert run_async(updater.fetch_latest(object())) == _version_info()
    assert api_paths == [
        "repos/block/buzz/releases/latest",
        f"repos/block/buzz/commits/{_TAG}",
    ]
    assert set(fetched_urls) == set(raw_payloads)


def test_buzz_onnx_source_contract_pins_the_exact_audited_revision() -> None:
    """ONNX source acceptance must remain tied to the reviewed v1.27.0 bytes."""
    module = _load_updater_module()

    assert module._ONNX_RUNTIME_VERSION == _ONNX_RUNTIME_VERSION
    assert module._ONNX_RUNTIME_COMMIT == _ONNX_RUNTIME_COMMIT
    assert module._ONNX_SOURCE_DIGESTS == {
        "VERSION_NUMBER": (
            "7ef1ea58fece676ff7345f6edac427e671daf20f0d7499ef2e42ada241d4fe24"
        ),
        ".gitmodules": (
            "88baf1a643d03b2c6c4ef4caf3463ac9eefdbd150c5e9054b1df23578eb4a160"
        ),
        "cmake/deps.txt": (
            "e411468ead299e3386b2e5e9d773e50e1939b5fc0baca599666ca5757eeb3f71"
        ),
        "cmake/onnxruntime.cmake": (
            "4f73825c1782b0309cbad11d04c1a8ae5d7460b2464e08905064dcb11fdcd9c6"
        ),
    }


def test_buzz_mesh_runtime_schema_pins_the_exact_audited_sources() -> None:
    """Manifest shape and load order must remain tied to reviewed Mesh bytes."""
    module = _load_updater_module()
    expected = {
        "crates/mesh-llm-native-runtime/src/manifest.rs": (
            "db91c4ef173269900f6bfd37af8906f37f6d4785f4113c918ad5873b8f1c324c"
        ),
        "crates/mesh-llm-native-runtime/src/flavor.rs": (
            "64de68e348eff4fbb46f9105568a3d39d5b22c276fcdc927e639fb790a66f437"
        ),
        "crates/mesh-llm-native-runtime/src/load_plan.rs": (
            "75b2616f429e59a13a5b202ff8373b832360c5b6e91b4d5bd4130b17c8f6941b"
        ),
    }

    assert {path: module._MESH_SOURCE_DIGESTS[path] for path in expected} == expected


def test_buzz_rust_method_extractor_rejects_malformed_structure() -> None:
    """Malformed or incomplete Rust blocks cannot satisfy semantic source audits."""
    module = _load_updater_module()
    method_pattern = r"(?m)^[ \t]*pub\s+fn\s+reviewed\s*\(\s*\)\s*\{"

    assert module._matching_rust_brace("x", 0) is None
    assert module._matching_rust_brace("{", 0) is None
    assert module.rust_delimiter_stack("}", 1) is None
    assert (
        module._normalized_rust_method(
            "impl Reviewed {",
            owner="Reviewed",
            method_pattern=method_pattern,
        )
        is None
    )
    assert (
        module._normalized_rust_method(
            "impl Reviewed {}",
            owner="Reviewed",
            method_pattern=method_pattern,
        )
        is None
    )


@pytest.mark.parametrize(
    ("path", "old", "new"),
    [
        (
            "crates/mesh-llm-native-runtime/src/manifest.rs",
            b"pub files: BTreeMap<String, String>",
            b"pub files: Vec<String>",
        ),
        (
            "crates/mesh-llm-native-runtime/src/manifest.rs",
            b"pub backend: NativeRuntimeBackend",
            b"pub backend: String",
        ),
        (
            "crates/mesh-llm-native-runtime/src/manifest.rs",
            b"pub target: Option<String>",
            b"pub target: String",
        ),
        (
            "crates/mesh-llm-native-runtime/src/manifest.rs",
            b"self.runtime.files.contains_key(library)",
            b"true",
        ),
        (
            "crates/mesh-llm-native-runtime/src/load_plan.rs",
            _LOAD_PLAN_ITER_CHAIN,
            _LOAD_PLAN_REVERSED_ITER_CHAIN,
        ),
        (
            "crates/mesh-llm-native-runtime/src/flavor.rs",
            b"cuda: None",
            b"cuda: Some(12)",
        ),
        (
            "crates/mesh-llm-native-runtime/src/manifest.rs",
            b"if self.runtime.files.is_empty()",
            b"if !self.runtime.files.is_empty()",
        ),
        (
            "crates/mesh-llm-native-runtime/src/manifest.rs",
            b"if !self.runtime.files.contains_key(library)",
            b"if self.runtime.files.contains_key(library)",
        ),
        (
            "crates/mesh-llm-native-runtime/src/manifest.rs",
            b'verify_file_checksums(dir, "tool", &self.runtime.tools)',
            b"Ok(())",
        ),
    ],
)
def test_buzz_mesh_runtime_schema_fails_closed_on_semantic_drift(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    old: bytes,
    new: bytes,
) -> None:
    """A reviewed digest update cannot weaken the runtime loader contract."""
    module = _load_updater_module()
    mesh_payloads = _mesh_source_payloads()
    mesh_payloads[path] = mesh_payloads[path].replace(old, new)
    _install_digest_contracts(
        monkeypatch,
        module,
        _source_payloads(),
        mesh_payloads,
    )

    with pytest.raises(RuntimeError, match="Mesh native runtime manifest semantics"):
        module._validate_mesh_source_contract(mesh_payloads)


@pytest.mark.parametrize(
    ("path", "old", "new", "unrelated_source"),
    [
        (
            "crates/mesh-llm-native-runtime/src/manifest.rs",
            b"pub arch: String,",
            b"pub architecture: String,",
            b"\npub struct UnrelatedPlatform { pub arch: String, }\n",
        ),
        (
            "crates/mesh-llm-native-runtime/src/manifest.rs",
            b"pub backend: NativeRuntimeBackend,",
            b"pub backend: String,",
            b"\npub struct Unrelated { pub backend: NativeRuntimeBackend, }\n",
        ),
        (
            "crates/mesh-llm-native-runtime/src/flavor.rs",
            b"cuda: None,",
            b"cuda: Some(12),",
            b"""
pub fn unrelated() -> Self {
    Self {
        kind: NativeRuntimeBackendKind::Metal,
        cuda: None,
        rocm: None,
        vulkan: None,
    }
}
""",
        ),
        (
            "crates/mesh-llm-native-runtime/src/load_plan.rs",
            _LOAD_PLAN_ITER_CHAIN,
            _LOAD_PLAN_REVERSED_ITER_CHAIN,
            b"\nfn unrelated(artifact: &Artifact) { "
            b"artifact.libraries.iter().map(PathBuf::from); }\n",
        ),
        (
            "crates/mesh-llm-native-runtime/src/flavor.rs",
            b"cuda: None,",
            b"cuda: Some(12),",
            b"""
impl UnrelatedBackend {
    pub fn metal() -> Self {
        Self {
            kind: NativeRuntimeBackendKind::Metal,
            cuda: None,
            rocm: None,
            vulkan: None,
        }
    }
}
""",
        ),
        (
            "crates/mesh-llm-native-runtime/src/load_plan.rs",
            _LOAD_PLAN_ITER_CHAIN,
            _LOAD_PLAN_REVERSED_ITER_CHAIN,
            b"""
impl UnrelatedRuntime {
    pub fn load_plan(&self) -> Result<NativeRuntimeLoadPlan> {
        let libraries = self.manifest.runtime.libraries.iter()
            .map(|path| self.path.join(path))
            .collect::<Vec<_>>();
        Ok(NativeRuntimeLoadPlan { libraries })
    }
}
""",
        ),
        (
            "crates/mesh-llm-native-runtime/src/manifest.rs",
            b"if self.runtime.files.is_empty()",
            b"if !self.runtime.files.is_empty()",
            b"""
impl UnrelatedManifest {
    fn verify_contents(&self, dir: &Path) -> Result<()> {
        if self.runtime.files.is_empty() {
            bail!("runtime does not declare file checksums");
        }
        for library in &self.runtime.libraries {
            if !self.runtime.files.contains_key(library) {
                bail!("library is missing a file checksum");
            }
        }
        verify_file_checksums(dir, "file", &self.runtime.files)?;
        verify_file_checksums(dir, "tool", &self.runtime.tools)
    }
}
""",
        ),
        (
            "crates/mesh-llm-native-runtime/src/flavor.rs",
            b"cuda: None,",
            b"cuda: Some(12),",
            b"""
#[cfg(any())]
impl NativeRuntimeBackend {
    pub fn metal() -> Self {
        Self {
            kind: NativeRuntimeBackendKind::Metal,
            cuda: None,
            rocm: None,
            vulkan: None,
        }
    }
}
""",
        ),
        (
            "crates/mesh-llm-native-runtime/src/load_plan.rs",
            _LOAD_PLAN_ITER_CHAIN,
            _LOAD_PLAN_REVERSED_ITER_CHAIN,
            b"""
#[cfg(any())]
impl InstalledNativeRuntime {
    pub fn load_plan(&self) -> Result<NativeRuntimeLoadPlan> {
        let libraries = self.manifest.runtime.libraries.iter()
            .map(|path| self.path.join(path))
            .collect::<Vec<_>>();
        Ok(NativeRuntimeLoadPlan { libraries })
    }
}
""",
        ),
        (
            "crates/mesh-llm-native-runtime/src/manifest.rs",
            b"if self.runtime.files.is_empty()",
            b"if !self.runtime.files.is_empty()",
            b"""
#[cfg(any())]
impl NativeRuntimeManifest {
    fn verify_contents(&self, dir: &Path) -> Result<()> {
        if self.runtime.files.is_empty() {
            bail!("runtime does not declare file checksums");
        }
        for library in &self.runtime.libraries {
            if !self.runtime.files.contains_key(library) {
                bail!("library is missing a file checksum");
            }
        }
        verify_file_checksums(dir, "file", &self.runtime.files)?;
        verify_file_checksums(dir, "tool", &self.runtime.tools)
    }
}
""",
        ),
        (
            "crates/mesh-llm-native-runtime/src/flavor.rs",
            b"cuda: None,",
            b"cuda: Some(12),",
            b"""
/*
impl NativeRuntimeBackend {
    pub fn metal() -> Self {
        Self {
            kind: NativeRuntimeBackendKind::Metal,
            cuda: None,
            rocm: None,
            vulkan: None,
        }
    }
}
*/
""",
        ),
        (
            "crates/mesh-llm-native-runtime/src/load_plan.rs",
            _LOAD_PLAN_ITER_CHAIN,
            _LOAD_PLAN_REVERSED_ITER_CHAIN,
            b"""
/*
impl InstalledNativeRuntime {
    pub fn load_plan(&self) -> Result<NativeRuntimeLoadPlan> {
        let libraries = self.manifest.runtime.libraries.iter()
            .map(|path| self.path.join(path))
            .collect::<Vec<_>>();
        Ok(NativeRuntimeLoadPlan { libraries })
    }
}
*/
""",
        ),
        (
            "crates/mesh-llm-native-runtime/src/manifest.rs",
            b"if self.runtime.files.is_empty()",
            b"if !self.runtime.files.is_empty()",
            b"""
/*
impl NativeRuntimeManifest {
    fn verify_contents(&self, dir: &Path) -> Result<()> {
        if self.runtime.files.is_empty() {
            bail!("runtime does not declare file checksums");
        }
        for library in &self.runtime.libraries {
            if !self.runtime.files.contains_key(library) {
                bail!("library is missing a file checksum");
            }
        }
        verify_file_checksums(dir, "file", &self.runtime.files)?;
        verify_file_checksums(dir, "tool", &self.runtime.tools)
    }
}
*/
""",
        ),
        (
            "crates/mesh-llm-native-runtime/src/flavor.rs",
            b"cuda: None,",
            b"cuda: Some(12),",
            b"""\nconst DECOY: &str = r#"\nimpl NativeRuntimeBackend {
    pub fn metal() -> Self {
        Self {
            kind: NativeRuntimeBackendKind::Metal,
            cuda: None,
            rocm: None,
            vulkan: None,
        }
    }
}\n"#;\n""",
        ),
    ],
)
def test_buzz_mesh_runtime_schema_ignores_unrelated_matching_tokens(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    old: bytes,
    new: bytes,
    unrelated_source: bytes,
) -> None:
    """Decoy declarations cannot mask drift in the behavior-bearing item."""
    module = _load_updater_module()
    mesh_payloads = _mesh_source_payloads()
    mesh_payloads[path] = mesh_payloads[path].replace(old, new) + unrelated_source
    _install_digest_contracts(
        monkeypatch,
        module,
        _source_payloads(),
        mesh_payloads,
    )

    with pytest.raises(RuntimeError, match="Mesh native runtime manifest semantics"):
        module._validate_mesh_source_contract(mesh_payloads)


@pytest.mark.parametrize(
    ("path", "method"),
    [
        (
            "crates/mesh-llm-native-runtime/src/flavor.rs",
            b"    pub fn metal() -> Self {",
        ),
        (
            "crates/mesh-llm-native-runtime/src/load_plan.rs",
            b"    pub fn load_plan(&self) -> Result<NativeRuntimeLoadPlan> {",
        ),
        (
            "crates/mesh-llm-native-runtime/src/manifest.rs",
            b"    fn verify_contents(&self, dir: &Path) -> Result<()> {",
        ),
    ],
    ids=["metal", "load-plan", "manifest-verifier"],
)
def test_buzz_mesh_runtime_schema_rejects_disabled_audited_methods(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    method: bytes,
) -> None:
    """A cfg-disabled reviewed method cannot attest the active runtime behavior."""
    module = _load_updater_module()
    mesh_payloads = _mesh_source_payloads()
    mesh_payloads[path] = mesh_payloads[path].replace(
        method,
        b"    #[cfg(any())]\n" + method,
        1,
    )
    _install_digest_contracts(
        monkeypatch,
        module,
        _source_payloads(),
        mesh_payloads,
    )

    with pytest.raises(RuntimeError, match="Mesh native runtime manifest semantics"):
        module._validate_mesh_source_contract(mesh_payloads)


@pytest.mark.parametrize(
    ("path", "method"),
    [
        (
            "crates/mesh-llm-native-runtime/src/flavor.rs",
            b"    pub fn metal() -> Self {",
        ),
        (
            "crates/mesh-llm-native-runtime/src/load_plan.rs",
            b"    pub fn load_plan(&self) -> Result<NativeRuntimeLoadPlan> {",
        ),
        (
            "crates/mesh-llm-native-runtime/src/manifest.rs",
            b"    fn verify_contents(&self, dir: &Path) -> Result<()> {",
        ),
    ],
    ids=["metal", "load-plan", "manifest-verifier"],
)
@pytest.mark.parametrize(
    "inner_attribute",
    [b"#![cfg(any())]", b"#![cfg_attr(all(), cfg(any()))]"],
    ids=["cfg", "cfg-attr"],
)
def test_buzz_mesh_runtime_schema_rejects_inner_cfg_before_audited_methods(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    method: bytes,
    inner_attribute: bytes,
) -> None:
    """An impl-level inner cfg cannot disable the reviewed runtime method."""
    module = _load_updater_module()
    mesh_payloads = _mesh_source_payloads()
    mesh_payloads[path] = mesh_payloads[path].replace(
        method,
        b"    " + inner_attribute + b"\n" + method,
        1,
    )
    _install_digest_contracts(
        monkeypatch,
        module,
        _source_payloads(),
        mesh_payloads,
    )

    with pytest.raises(RuntimeError, match="Mesh native runtime manifest semantics"):
        module._validate_mesh_source_contract(mesh_payloads)


@pytest.mark.parametrize(
    ("path", "method"),
    [
        (
            "crates/mesh-llm-native-runtime/src/flavor.rs",
            b"    pub fn metal() -> Self {",
        ),
        (
            "crates/mesh-llm-native-runtime/src/load_plan.rs",
            b"    pub fn load_plan(&self) -> Result<NativeRuntimeLoadPlan> {",
        ),
        (
            "crates/mesh-llm-native-runtime/src/manifest.rs",
            b"    fn verify_contents(&self, dir: &Path) -> Result<()> {",
        ),
    ],
    ids=["metal", "load-plan", "manifest-verifier"],
)
def test_buzz_mesh_runtime_schema_allows_benign_impl_method_preamble(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    method: bytes,
) -> None:
    """Comments and whitespace before a reviewed method do not disable it."""
    module = _load_updater_module()
    mesh_payloads = _mesh_source_payloads()
    mesh_payloads[path] = mesh_payloads[path].replace(
        method,
        b"    /* reviewed method follows */\n\n" + method,
        1,
    )
    _install_digest_contracts(
        monkeypatch,
        module,
        _source_payloads(),
        mesh_payloads,
    )

    module._validate_mesh_source_contract(mesh_payloads)


@pytest.mark.parametrize(
    "path",
    [
        "crates/mesh-llm-native-runtime/src/flavor.rs",
        "crates/mesh-llm-native-runtime/src/load_plan.rs",
        "crates/mesh-llm-native-runtime/src/manifest.rs",
    ],
    ids=["metal", "load-plan", "manifest-verifier"],
)
@pytest.mark.parametrize(
    "source_prefix",
    [b"#![cfg(any())]\n", b"\xef\xbb\xbf#![cfg(any())]\n"],
    ids=["plain", "utf8-bom"],
)
def test_buzz_mesh_runtime_schema_rejects_disabled_source_files(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    source_prefix: bytes,
) -> None:
    """A file-level cfg cannot make the reviewed runtime behavior inactive."""
    module = _load_updater_module()
    mesh_payloads = _mesh_source_payloads()
    mesh_payloads[path] = source_prefix + mesh_payloads[path]
    _install_digest_contracts(
        monkeypatch,
        module,
        _source_payloads(),
        mesh_payloads,
    )

    with pytest.raises(RuntimeError, match="Mesh native runtime manifest semantics"):
        module._validate_mesh_source_contract(mesh_payloads)


@pytest.mark.parametrize(
    ("path", "owner"),
    [
        (
            "crates/mesh-llm-native-runtime/src/flavor.rs",
            b"NativeRuntimeBackend",
        ),
        (
            "crates/mesh-llm-native-runtime/src/load_plan.rs",
            b"InstalledNativeRuntime",
        ),
        (
            "crates/mesh-llm-native-runtime/src/manifest.rs",
            b"NativeRuntimeManifest",
        ),
    ],
    ids=["metal", "load-plan", "manifest-verifier"],
)
def test_buzz_mesh_runtime_schema_rejects_nested_owner_decoys(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    owner: bytes,
) -> None:
    """A cfg-disabled nested impl cannot attest the root runtime behavior."""
    module = _load_updater_module()
    mesh_payloads = _mesh_source_payloads()
    reviewed = mesh_payloads[path]
    drifted = reviewed.replace(b"impl " + owner, b"impl Drifted" + owner, 1)
    mesh_payloads[path] = (
        drifted + b"\n#[cfg(any())]\nmod decoy {\n" + reviewed + b"}\n"
    )
    _install_digest_contracts(
        monkeypatch,
        module,
        _source_payloads(),
        mesh_payloads,
    )

    with pytest.raises(RuntimeError, match="Mesh native runtime manifest semantics"):
        module._validate_mesh_source_contract(mesh_payloads)


def test_buzz_mesh_runtime_schema_rejects_nested_method_decoy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A macro body cannot supply the reviewed method for an active owner impl."""
    module = _load_updater_module()
    mesh_payloads = _mesh_source_payloads()
    path = "crates/mesh-llm-native-runtime/src/flavor.rs"
    reviewed = mesh_payloads[path]
    method = reviewed.split(b"impl NativeRuntimeBackend {\n", 1)[1].rsplit(
        b"\n}\n",
        1,
    )[0]
    drifted = reviewed.replace(b"pub fn metal", b"pub fn drifted_metal", 1)
    prefix = drifted.rsplit(b"\n}\n", 1)[0]
    mesh_payloads[path] = (
        prefix
        + b"\n    macro_rules! decoy {\n        () => {\n"
        + method
        + b"\n        };\n    }\n}\n"
    )
    _install_digest_contracts(
        monkeypatch,
        module,
        _source_payloads(),
        mesh_payloads,
    )

    with pytest.raises(RuntimeError, match="Mesh native runtime manifest semantics"):
        module._validate_mesh_source_contract(mesh_payloads)


@pytest.mark.parametrize(
    ("opening", "closing"),
    [(b"(", b")"), (b"[", b"]")],
    ids=["parentheses", "brackets"],
)
def test_buzz_mesh_runtime_schema_rejects_macro_owner_decoy(
    monkeypatch: pytest.MonkeyPatch,
    opening: bytes,
    closing: bytes,
) -> None:
    """An impl inside inactive macro tokens cannot attest active runtime code."""
    module = _load_updater_module()
    mesh_payloads = _mesh_source_payloads()
    path = "crates/mesh-llm-native-runtime/src/flavor.rs"
    reviewed = mesh_payloads[path]
    drifted = reviewed.replace(
        b"impl NativeRuntimeBackend",
        b"impl self::NativeRuntimeBackend",
        1,
    )
    mesh_payloads[path] = (
        drifted
        + b"\nmacro_rules! decoy "
        + opening
        + b" () => "
        + opening
        + b"\n"
        + reviewed
        + b"\n"
        + closing
        + b"; "
        + closing
        + b";\n"
    )
    _install_digest_contracts(
        monkeypatch,
        module,
        _source_payloads(),
        mesh_payloads,
    )

    with pytest.raises(RuntimeError, match="Mesh native runtime manifest semantics"):
        module._validate_mesh_source_contract(mesh_payloads)


def test_buzz_mesh_runtime_schema_rejects_nested_struct_decoy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disabled nested schema struct cannot mask the active field layout."""
    module = _load_updater_module()
    mesh_payloads = _mesh_source_payloads()
    path = "crates/mesh-llm-native-runtime/src/manifest.rs"
    reviewed = b"""#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct NativeRuntimePlatform {
    pub os: String,
    pub arch: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target: Option<String>,
}"""
    mesh_payloads[path] = (
        mesh_payloads[path].replace(
            b"pub target: Option<String>,",
            b"pub target: String,",
            1,
        )
        + b"\n#[cfg(any())]\nmod decoy {\n"
        + reviewed
        + b"\n}\n"
    )
    _install_digest_contracts(
        monkeypatch,
        module,
        _source_payloads(),
        mesh_payloads,
    )

    with pytest.raises(RuntimeError, match="Mesh native runtime manifest semantics"):
        module._validate_mesh_source_contract(mesh_payloads)


@pytest.mark.parametrize(
    ("opening", "closing"),
    [(b"(", b")"), (b"[", b"]")],
    ids=["parentheses", "brackets"],
)
def test_buzz_mesh_runtime_schema_rejects_macro_struct_decoy(
    monkeypatch: pytest.MonkeyPatch,
    opening: bytes,
    closing: bytes,
) -> None:
    """A schema struct inside inactive macro tokens cannot attest active code."""
    module = _load_updater_module()
    mesh_payloads = _mesh_source_payloads()
    path = "crates/mesh-llm-native-runtime/src/manifest.rs"
    reviewed = b"""#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct NativeRuntimePlatform {
    pub os: String,
    pub arch: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target: Option<String>,
}"""
    active = mesh_payloads[path].replace(
        b"pub target: Option<String>,",
        b"pub target: String,",
        1,
    )
    mesh_payloads[path] = (
        active
        + b"\nmacro_rules! decoy "
        + opening
        + b" () => "
        + opening
        + b"\n"
        + reviewed
        + b"\n"
        + closing
        + b"; "
        + closing
        + b";\n"
    )
    _install_digest_contracts(
        monkeypatch,
        module,
        _source_payloads(),
        mesh_payloads,
    )

    with pytest.raises(RuntimeError, match="Mesh native runtime manifest semantics"):
        module._validate_mesh_source_contract(mesh_payloads)


def test_buzz_mesh_runtime_schema_rejects_disabled_schema_struct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cfg-disabled schema declaration cannot attest the runtime manifest."""
    module = _load_updater_module()
    mesh_payloads = _mesh_source_payloads()
    path = "crates/mesh-llm-native-runtime/src/manifest.rs"
    mesh_payloads[path] = mesh_payloads[path].replace(
        b"pub struct NativeRuntimePlatform {",
        b"#[cfg(any())]\npub struct NativeRuntimePlatform {",
        1,
    )
    _install_digest_contracts(
        monkeypatch,
        module,
        _source_payloads(),
        mesh_payloads,
    )

    with pytest.raises(RuntimeError, match="Mesh native runtime manifest semantics"):
        module._validate_mesh_source_contract(mesh_payloads)


def test_buzz_mesh_runtime_schema_rejects_post_collection_reordering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The load plan must return the audited manifest order without later mutation."""
    module = _load_updater_module()
    mesh_payloads = _mesh_source_payloads()
    path = "crates/mesh-llm-native-runtime/src/load_plan.rs"
    insertion = (
        b"            .collect::<Vec<_>>();\n"
        b"        let libraries = "
        b"libraries.into_iter().rev().collect::<Vec<_>>();\n"
    )
    mesh_payloads[path] = mesh_payloads[path].replace(
        b"            .collect::<Vec<_>>();\n",
        insertion,
        1,
    )
    _install_digest_contracts(
        monkeypatch,
        module,
        _source_payloads(),
        mesh_payloads,
    )

    with pytest.raises(RuntimeError, match="Mesh native runtime manifest semantics"):
        module._validate_mesh_source_contract(mesh_payloads)


def test_buzz_sherpa_source_contract_pins_the_exact_audited_revision() -> None:
    """Sherpa source acceptance must remain tied to the reviewed v1.13.4 bytes."""
    module = _load_updater_module()

    assert module._SHERPA_ONNX_VERSION == _SHERPA_ONNX_VERSION
    assert module._SHERPA_ONNX_COMMIT == _SHERPA_ONNX_COMMIT
    assert module._SHERPA_SOURCE_DIGESTS == {
        "CMakeLists.txt": (
            "9f75d36e8f19358b5d23368a5f59ecdfec507f5f2ff47c84ea9296f14399a8e3"
        ),
        "cmake/onnxruntime.cmake": (
            "63cb67a92e7e484e5b8428f33386165175a0f999215dc17ccfa5c424eecca01d"
        ),
        "sherpa-onnx/csrc/CMakeLists.txt": (
            "d21596eed2e94ba15779c5d0377a38faa48477cb4cf30f02951b30c4c9348ae4"
        ),
        "sherpa-onnx/c-api/CMakeLists.txt": (
            "da0eac0a143ee0c80df5a696b0bd2c338eeaa29bc94e1c8696bfd1bcf246de1d"
        ),
        "cmake/sherpa-onnx-static.pc.in": (
            "f4e14f4a48a563dbbb7cc70cd726bab9b0e0c48688ae4b27c2d14b417099e051"
        ),
        "cmake/kaldi-native-fbank.cmake": (
            "619f0046f568e85790f4180899e666053f246f5c0fc36c951421441f3e992e38"
        ),
        "cmake/kaldi-decoder.cmake": (
            "af34b17974474a70b12978d49ac0831276839082924e5ac93bd9bb194a7fd310"
        ),
        "cmake/simple-sentencepiece.cmake": (
            "a67b170dd0aaa441a7c096bde75764bb437933bbe0bdf2d3d567026fe81f072e"
        ),
        "cmake/piper-phonemize.cmake": (
            "b062750c938e4f4e778b9c0605a250f4abebdf17656c0d654fbace43a800258b"
        ),
        "cmake/espeak-ng-for-piper.cmake": (
            "d353f2becec6c8a2064e314b7d0d9c4e4388b48c7e5742a3ba8a62355952407c"
        ),
        "cmake/openfst.cmake": (
            "66d36da995c5d80a1eb668131ff1b2213f088438305216be77ae1f955cd63dcf"
        ),
        "cmake/hclust-cpp.cmake": (
            "093ae7b3712119d140df2629b5d20b71bafb3d49ba0124dd7a391f278f4db357"
        ),
        "sherpa-onnx/rust/sherpa-onnx-sys/Cargo.toml": (
            "daec007dee9c36ea8bfa77ffb48c842e07c7a7f9cc6f45cb07cd395e9888de5f"
        ),
        "sherpa-onnx/rust/sherpa-onnx-sys/build.rs": (
            "6e45edebae2256484f0c061e931feec9f04ceee61f6119f4f233e0f7b3779df7"
        ),
        "sherpa-onnx/rust/sherpa-onnx/Cargo.toml": (
            "18e6dcbe08e4531419869a1f401ab59e80b4339e4472e8085fd441b3a84c6346"
        ),
    }


@pytest.mark.parametrize(
    ("path", "old", "new"),
    [
        (
            "sherpa-onnx/rust/sherpa-onnx-sys/Cargo.toml",
            b'default = ["static"]',
            b'default = ["shared"]',
        ),
        (
            "sherpa-onnx/rust/sherpa-onnx/Cargo.toml",
            b'static = ["sherpa-onnx-sys/static"]',
            b"static = []",
        ),
        (
            "sherpa-onnx/rust/sherpa-onnx-sys/build.rs",
            b"SHERPA_ONNX_LIB_DIR",
            b"SHERPA_ONNX_UNAUDITED_LIB_DIR",
        ),
        (
            "sherpa-onnx/rust/sherpa-onnx-sys/build.rs",
            b'"sherpa-onnx-c-api",\n    "sherpa-onnx-core",',
            b'"sherpa-onnx-core",\n    "sherpa-onnx-c-api",',
        ),
        (
            "sherpa-onnx/rust/sherpa-onnx-sys/build.rs",
            b'var_os("DOCS_RS").is_some()',
            b'var_os("DOCS_RS").is_none()',
        ),
        (
            "sherpa-onnx/rust/sherpa-onnx-sys/build.rs",
            b"for lib in SHERPA_ONNX_STATIC_LIBS {",
            b"for lib in SHERPA_ONNX_STATIC_LIBS.iter().rev() {",
        ),
        (
            "sherpa-onnx/rust/sherpa-onnx-sys/build.rs",
            b"if !path.is_dir()",
            b"if path.is_dir()",
        ),
        (
            "sherpa-onnx/rust/sherpa-onnx-sys/build.rs",
            b"cargo:rustc-link-lib=dylib=c++",
            b"cargo:rustc-link-lib=dylib=stdc++",
        ),
    ],
)
def test_buzz_sherpa_rust_build_contract_fails_closed_on_semantic_drift(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    old: bytes,
    new: bytes,
) -> None:
    """Reviewed source updates cannot bypass the local static-link contract."""
    module = _load_updater_module()
    payloads = _sherpa_source_payloads()
    payloads[path] = payloads[path].replace(old, new)
    mesh_payloads = _mesh_source_payloads()
    _install_digest_contracts(
        monkeypatch,
        module,
        _source_payloads(),
        mesh_payloads,
        sherpa_payloads=payloads,
    )

    with pytest.raises(RuntimeError, match="sherpa-onnx Rust build semantics"):
        module._validate_sherpa_source_contract(payloads)


@pytest.mark.parametrize(
    ("old", "new", "unrelated_source"),
    [
        (
            b'var_os("DOCS_RS").is_some()',
            b'var_os("DOCS_RS").is_none()',
            b"""
fn unrelated_docs_gate() -> Result<(), DynError> {
    if env::var_os("DOCS_RS").is_some() {
        return Ok(());
    }
    Err("not a docs build".into())
}
""",
        ),
        (
            b"for lib in SHERPA_ONNX_STATIC_LIBS {",
            b"for lib in SHERPA_ONNX_STATIC_LIBS.iter().rev() {",
            b"""
fn unrelated_link_loop() {
    for lib in SHERPA_ONNX_STATIC_LIBS {
        println!("cargo:rustc-link-lib=static={lib}");
    }
}
""",
        ),
    ],
)
def test_buzz_sherpa_rust_build_contract_ignores_unrelated_matching_tokens(
    monkeypatch: pytest.MonkeyPatch,
    old: bytes,
    new: bytes,
    unrelated_source: bytes,
) -> None:
    """Decoy control flow cannot mask drift in the audited build functions."""
    module = _load_updater_module()
    payloads = _sherpa_source_payloads()
    build_path = "sherpa-onnx/rust/sherpa-onnx-sys/build.rs"
    payloads[build_path] = payloads[build_path].replace(old, new) + unrelated_source
    _install_digest_contracts(
        monkeypatch,
        module,
        _source_payloads(),
        _mesh_source_payloads(),
        sherpa_payloads=payloads,
    )

    with pytest.raises(RuntimeError, match="sherpa-onnx Rust build semantics"):
        module._validate_sherpa_source_contract(payloads)


@pytest.mark.parametrize(
    "drift",
    [
        "pnpm",
        "sidecars",
        "desktop-lock",
        "updater-gate",
        "rust-toolchain",
        "mesh-feature-graph",
        "runtime-manifest-default",
        "runtime-download",
        "runtime-install-manifest",
        "runtime-manifest-env",
        "llama-pin",
    ],
)
def test_buzz_source_audit_fails_closed_on_build_topology_drift(
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    """A release is unusable when one audited source boundary changes."""
    module = _load_updater_module()
    payloads = _source_payloads()
    mesh_payloads = _mesh_source_payloads()
    if drift == "pnpm":
        payloads["package.json"] = _json_bytes({"packageManager": "pnpm@11.5.0"})
    elif drift == "sidecars":
        config = json.loads(payloads["desktop/src-tauri/tauri.conf.json"])
        config["bundle"]["externalBin"].pop()
        payloads["desktop/src-tauri/tauri.conf.json"] = _json_bytes(config)
    elif drift == "desktop-lock":
        payloads["desktop/src-tauri/Cargo.lock"] = _lock_toml(mesh=True).replace(
            b"1.13.4",
            b"1.13.5",
            1,
        )
    elif drift == "updater-gate":
        payloads["desktop/src-tauri/build.rs"] = payloads[
            "desktop/src-tauri/build.rs"
        ].replace(b" && ", b" || ")
    elif drift == "rust-toolchain":
        payloads["rust-toolchain.toml"] = payloads["rust-toolchain.toml"].replace(
            b"1.95.0",
            b"1.94.0",
        )
    elif drift == "mesh-feature-graph":
        mesh_payloads["crates/mesh-llm-sdk/Cargo.toml"] = mesh_payloads[
            "crates/mesh-llm-sdk/Cargo.toml"
        ].replace(b'"dep:reqwest", ', b"")
    elif drift == "runtime-manifest-default":
        mesh_payloads["crates/mesh-llm-runtime-install/src/lib.rs"] = mesh_payloads[
            "crates/mesh-llm-runtime-install/src/lib.rs"
        ].replace(
            b"allow_default_manifest_url: true",
            b"allow_default_manifest_url: false",
            1,
        )
    elif drift == "runtime-download":
        mesh_payloads["crates/mesh-llm-runtime-install/src/lib.rs"] = mesh_payloads[
            "crates/mesh-llm-runtime-install/src/lib.rs"
        ].replace(b"allow_download: true", b"allow_download: false")
    elif drift == "runtime-install-manifest":
        mesh_payloads["crates/mesh-llm-runtime-install/src/lib.rs"] = mesh_payloads[
            "crates/mesh-llm-runtime-install/src/lib.rs"
        ].replace(
            b"pub async fn install_native_runtime() {\n"
            b"    let manifest_url = "
            b"std::env::var(MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL_ENV).ok();\n"
            b"    let manifest_options = NativeRuntimeManifestOptions {\n"
            b"        manifest_url,\n"
            b"        allow_default_manifest_url: true",
            b"pub async fn install_native_runtime() {\n"
            b"    let manifest_url = "
            b"std::env::var(MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL_ENV).ok();\n"
            b"    let manifest_options = NativeRuntimeManifestOptions {\n"
            b"        manifest_url,\n"
            b"        allow_default_manifest_url: false",
        )
    elif drift == "runtime-manifest-env":
        mesh_payloads["crates/mesh-llm-runtime-install/src/lib.rs"] = mesh_payloads[
            "crates/mesh-llm-runtime-install/src/lib.rs"
        ].replace(
            b"MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL",
            b"MESH_LLM_UNAUDITED_MANIFEST_URL",
        )
    else:
        mesh_payloads["third_party/llama.cpp/upstream.txt"] = (
            b"0000000000000000000000000000000000000000\n"
        )

    # Re-pin synthetic bytes so this test exercises the semantic contract
    # independently from the separate digest-contract tests.
    _install_digest_contracts(monkeypatch, module, payloads, mesh_payloads)

    with pytest.raises(RuntimeError):
        module._validate_source_contract(payloads, mesh_payloads=mesh_payloads)


def test_buzz_contract_helpers_reject_malformed_or_incomplete_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every structural boundary must fail closed with actionable input errors."""
    module = _load_updater_module()
    payloads = _source_payloads()
    mesh_payloads = _mesh_source_payloads()

    with pytest.raises(TypeError, match="not an object"):
        module._require_object([], context="object")
    with pytest.raises(TypeError, match="not a list"):
        module._require_list({}, context="list")
    with pytest.raises(TypeError, match="missing key"):
        module._require_string({}, "key", context="mapping")
    with pytest.raises(RuntimeError, match="valid UTF-8 JSON"):
        module._decode_json(b"\xff", context="JSON")
    with pytest.raises(RuntimeError, match="valid UTF-8 TOML"):
        module._decode_toml(b"[", context="TOML")
    with pytest.raises(RuntimeError, match="missing paths"):
        module._validate_digest_contract({}, {"required": "digest"}, context="digest")
    with pytest.raises(RuntimeError, match="exactly one absent"):
        module._validate_locked_package(
            {"package": []},
            "absent",
            context="lock",
            version="1",
        )
    with pytest.raises(RuntimeError, match="target gate"):
        module._sidecars_for_target("", "aarch64-apple-darwin")
    with pytest.raises(RuntimeError, match="release-only"):
        module._validate_updater_gate(
            payloads["desktop/src-tauri/build.rs"].decode(),
            "",
        )
    with pytest.raises(RuntimeError, match="no longer emits"):
        module._validate_sidecar_manifest(
            {"package": {"name": "sidecar"}, "bin": [{"name": "other"}]},
            package_name="sidecar",
            binary_name="expected",
        )

    root_manifest = payloads["Cargo.toml"]
    payloads["Cargo.toml"] = b"[workspace]\nmembers = []\n"
    with pytest.raises(RuntimeError, match="missing sidecar crates"):
        module._validate_cargo_contract(payloads)
    payloads["Cargo.toml"] = root_manifest

    with pytest.raises(RuntimeError, match="source audit is missing paths"):
        module._validate_source_contract({}, mesh_payloads={})

    mesh_payloads["crates/skippy-ffi/build.rs"] = b"fn main() {}\n"
    _install_digest_contracts(monkeypatch, module, payloads, mesh_payloads)
    with pytest.raises(RuntimeError, match="bypasses LLAMA_STAGE_BUILD_DIR"):
        module._validate_mesh_source_contract(mesh_payloads)


def test_buzz_digest_contract_detects_independent_byte_drift() -> None:
    """Pinned digests must reject changed bytes even when semantics still parse."""
    module = _load_updater_module()
    with pytest.raises(RuntimeError, match="digest for contract"):
        module._validate_digest_contract(
            {"contract": b"changed"},
            {"contract": hashlib.sha256(b"expected").hexdigest()},
            context="source",
        )


@pytest.mark.parametrize(
    ("public_key", "endpoint", "expected"),
    [
        (None, None, False),
        ("key", None, False),
        (None, "https://updates.example", False),
        (" ", "https://updates.example", False),
        ("key", "\n\t", False),
        (" key ", " https://updates.example ", True),
    ],
)
def test_buzz_updater_stays_inert_until_both_values_are_nonblank(
    public_key: str | None,
    endpoint: str | None,
    expected: bool,
) -> None:
    """The semantic model must match build.rs's two-value cfg gate."""
    module = _load_updater_module()
    assert module.updater_enabled(public_key, endpoint) is expected


def test_buzz_sidecar_script_semantics_match_tauri_on_darwin() -> None:
    """The shell topology should add Kubernetes only outside Windows."""
    module = _load_updater_module()
    script = _source_payloads()["scripts/bundle-sidecars.sh"].decode()

    assert module._sidecars_for_target(script, "aarch64-apple-darwin") == (
        "buzz-acp",
        "buzz-agent",
        "buzz-dev-mcp",
        "git-credential-nostr",
        "buzz",
        "buzz-backend-kubernetes",
    )
    assert module._sidecars_for_target(script, "x86_64-pc-windows-msvc") == (
        "buzz-acp",
        "buzz-agent",
        "buzz-dev-mcp",
        "git-credential-nostr",
        "buzz",
    )


def test_buzz_hashes_source_pnpm_and_both_cargo_locks_without_exporting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hashing must preserve all five exact source identities and lock closures."""
    module = _load_updater_module()
    updater = module.BuzzUpdater()
    info = _version_info()
    calls = install_fixed_hash_stream(
        monkeypatch,
        (
            ("hashing Buzz source", _SOURCE_NAR_HASH),
            (None, _ONNX_SOURCE_NAR_HASH),
            (None, _SHERPA_SOURCE_NAR_HASH),
            (None, _MESH_SOURCE_NAR_HASH),
            (None, _LLAMA_SOURCE_NAR_HASH),
            (None, _NPM_DEPS_HASH),
            (None, _ROOT_CARGO_HASH),
            (None, _DESKTOP_CARGO_HASH),
        ),
    )

    events = run_async(collect_events(updater.fetch_hashes(info, object())))

    assert updater.supported_platforms == ("aarch64-darwin",)
    assert events[0].kind is UpdateEventKind.STATUS
    assert events[0].source == "buzz"
    assert events[0].message == "hashing Buzz source"
    assert {call["isolate_by_drv_hash"] for call in calls} == {True}
    assert_nix_ast_equal(
        str(calls[0]["expr"]),
        _build_fetch_from_github_call(
            "block",
            "buzz",
            rev=_COMMIT,
            fetch_submodules=False,
        ),
    )
    assert_nix_ast_equal(
        str(calls[1]["expr"]),
        _build_fetch_from_github_call(
            "microsoft",
            "onnxruntime",
            rev=_ONNX_RUNTIME_COMMIT,
            fetch_submodules=False,
        ),
    )
    assert_nix_ast_equal(
        str(calls[2]["expr"]),
        _build_fetch_from_github_call(
            "k2-fsa",
            "sherpa-onnx",
            rev=_SHERPA_ONNX_COMMIT,
            fetch_submodules=False,
        ),
    )
    assert_nix_ast_equal(
        str(calls[3]["expr"]),
        _build_fetch_from_github_call(
            "Mesh-LLM",
            "mesh-llm",
            rev=_MESH_COMMIT,
            fetch_submodules=False,
        ),
    )
    assert_nix_ast_equal(
        str(calls[4]["expr"]),
        _build_fetch_from_github_call(
            "ggml-org",
            "llama.cpp",
            rev=_LLAMA_COMMIT,
            fetch_submodules=False,
        ),
    )
    fake_hash = updater.config.fake_hash
    expected_steps = (
        (
            ".pnpmDeps",
            _source_override(
                updater,
                src_hash=_SOURCE_NAR_HASH,
                onnx_hash=_ONNX_SOURCE_NAR_HASH,
                sherpa_hash=_SHERPA_SOURCE_NAR_HASH,
                mesh_hash=_MESH_SOURCE_NAR_HASH,
                llama_hash=_LLAMA_SOURCE_NAR_HASH,
                npm_hash=fake_hash,
                root_hash=fake_hash,
                desktop_hash=fake_hash,
            ),
        ),
        (
            ".rootCargoDeps",
            _source_override(
                updater,
                src_hash=_SOURCE_NAR_HASH,
                onnx_hash=_ONNX_SOURCE_NAR_HASH,
                sherpa_hash=_SHERPA_SOURCE_NAR_HASH,
                mesh_hash=_MESH_SOURCE_NAR_HASH,
                llama_hash=_LLAMA_SOURCE_NAR_HASH,
                npm_hash=_NPM_DEPS_HASH,
                root_hash=fake_hash,
                desktop_hash=fake_hash,
            ),
        ),
        (
            ".desktopCargoDeps",
            _source_override(
                updater,
                src_hash=_SOURCE_NAR_HASH,
                onnx_hash=_ONNX_SOURCE_NAR_HASH,
                sherpa_hash=_SHERPA_SOURCE_NAR_HASH,
                mesh_hash=_MESH_SOURCE_NAR_HASH,
                llama_hash=_LLAMA_SOURCE_NAR_HASH,
                npm_hash=_NPM_DEPS_HASH,
                root_hash=_ROOT_CARGO_HASH,
                desktop_hash=fake_hash,
            ),
        ),
    )
    for call, (attribute, source_override) in zip(
        calls[5:], expected_steps, strict=True
    ):
        assert_nix_ast_equal(
            str(call["expr"]),
            _build_repo_package_attr_expr(
                "packages/buzz/package.nix",
                attribute,
                system="aarch64-darwin",
                source_overrides={"buzz": source_override},
            ),
        )

    hashes = [
        HashEntry.create("srcHash", _SOURCE_NAR_HASH, url=_urls()["buzzUrl"]),
        HashEntry.create(
            "srcHash",
            _ONNX_SOURCE_NAR_HASH,
            url=_urls()["onnxruntimeUrl"],
        ),
        HashEntry.create(
            "srcHash",
            _SHERPA_SOURCE_NAR_HASH,
            url=_urls()["sherpaOnnxUrl"],
        ),
        HashEntry.create(
            "srcHash",
            _MESH_SOURCE_NAR_HASH,
            url=_urls()["meshLlmUrl"],
        ),
        HashEntry.create(
            "srcHash",
            _LLAMA_SOURCE_NAR_HASH,
            url=_urls()["llamaCppUrl"],
        ),
        HashEntry.create(
            "npmDepsHash",
            _NPM_DEPS_HASH,
            url=_urls()["buzzUrl"],
        ),
        HashEntry.create(
            "vendorHash",
            _ROOT_CARGO_HASH,
            url=_urls()["buzzUrl"],
        ),
        HashEntry.create(
            "cargoHash",
            _DESKTOP_CARGO_HASH,
            url=_urls()["buzzUrl"],
        ),
    ]
    value_events = [event for event in events if event.kind is UpdateEventKind.VALUE]
    assert (
        cast("list[HashEntry]", expect_source_hashes(value_events[-1].payload))
        == hashes
    )
    expected_result = SourceEntry(
        version=_VERSION,
        commit=_COMMIT,
        hashes=HashCollection.from_value(hashes),
        urls={
            "buzz": _urls()["buzzUrl"],
            "llamaCpp": _urls()["llamaCppUrl"],
            "meshLlm": _urls()["meshLlmUrl"],
            "onnxruntime": _urls()["onnxruntimeUrl"],
            "sherpaOnnx": _urls()["sherpaOnnxUrl"],
        },
    )
    assert updater.build_result(info, hashes) == expected_result
    assert run_async(updater._is_latest(None, info)) is False


def test_buzz_rejects_partial_or_unpinned_hash_results() -> None:
    """No updater result may weaken the exact eight-entry source contract."""
    module = _load_updater_module()
    updater = module.BuzzUpdater()
    partial = [HashEntry.create("srcHash", _SOURCE_NAR_HASH, url=_urls()["buzzUrl"])]

    with pytest.raises(RuntimeError, match="exact closure keys"):
        updater.build_result(_version_info(), partial)
    with pytest.raises(RuntimeError, match="only accepts the audited"):
        updater.build_result(
            VersionInfo("0.5.15", {"commit": _COMMIT}),
            partial,
        )
    with pytest.raises(TypeError, match="structured source hash entries"):
        updater.build_result(
            _version_info(),
            {"aarch64-darwin": _SOURCE_NAR_HASH},
        )

    complete_entries = _source_override(
        updater,
        src_hash=_SOURCE_NAR_HASH,
        onnx_hash=_ONNX_SOURCE_NAR_HASH,
        sherpa_hash=_SHERPA_SOURCE_NAR_HASH,
        mesh_hash=_MESH_SOURCE_NAR_HASH,
        llama_hash=_LLAMA_SOURCE_NAR_HASH,
        npm_hash=_NPM_DEPS_HASH,
        root_hash=_ROOT_CARGO_HASH,
        desktop_hash=_DESKTOP_CARGO_HASH,
    ).hashes.entries
    assert complete_entries is not None
    wrong_url_entries = [
        HashEntry.create(
            entry.hash_type,
            entry.hash,
            url="https://example.invalid/wrong-source.tar.gz",
        )
        for entry in complete_entries
    ]
    with pytest.raises(RuntimeError, match="exact closure keys"):
        updater.build_result(_version_info(), wrong_url_entries)


def test_buzz_source_metadata_pins_every_authoritative_fixed_output() -> None:
    """Promoted metadata must pin both sources and all three dependency closures."""
    source = SourceEntry.model_validate_json(
        (_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8")
    )

    assert source == SourceEntry(
        version=_VERSION,
        commit=_COMMIT,
        hashes=HashCollection.from_value([
            HashEntry.create(
                "cargoHash",
                _DESKTOP_CARGO_HASH,
                url=_urls()["buzzUrl"],
            ),
            HashEntry.create(
                "npmDepsHash",
                _NPM_DEPS_HASH,
                url=_urls()["buzzUrl"],
            ),
            HashEntry.create(
                "srcHash",
                _MESH_SOURCE_NAR_HASH,
                url=_urls()["meshLlmUrl"],
            ),
            HashEntry.create(
                "srcHash",
                _SOURCE_NAR_HASH,
                url=_urls()["buzzUrl"],
            ),
            HashEntry.create(
                "srcHash",
                _LLAMA_SOURCE_NAR_HASH,
                url=_urls()["llamaCppUrl"],
            ),
            HashEntry.create(
                "srcHash",
                _SHERPA_SOURCE_NAR_HASH,
                url=_urls()["sherpaOnnxUrl"],
            ),
            HashEntry.create(
                "srcHash",
                _ONNX_SOURCE_NAR_HASH,
                url=_urls()["onnxruntimeUrl"],
            ),
            HashEntry.create(
                "vendorHash",
                _ROOT_CARGO_HASH,
                url=_urls()["buzzUrl"],
            ),
        ]),
        urls={
            "buzz": _urls()["buzzUrl"],
            "llamaCpp": _urls()["llamaCppUrl"],
            "meshLlm": _urls()["meshLlmUrl"],
            "onnxruntime": _urls()["onnxruntimeUrl"],
            "sherpaOnnx": _urls()["sherpaOnnxUrl"],
        },
    )


def test_buzz_onnxruntime_slot_uses_the_url_scoped_promoted_hash() -> None:
    """The native slot must consume only ONNX Runtime's promoted source hash."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    package_arguments = {argument.rebuild() for argument in package.argument_set}
    assert {
        "abseil-cpp_202601",
        "cctools",
        "fetchFromGitHub",
        "ld64",
        "onnxruntime",
        "protobuf",
        "python3",
        "stdenv",
    } <= package_arguments
    output = expect_instance(
        expect_instance(package.output, Assertion).body,
        IfExpression,
    )
    scope = output.scope

    assert_nix_ast_equal(
        expect_binding(scope, "hashEntryFor").value,
        """hashType: url:
          lib.findFirst
            (entry: entry.hashType == hashType && (entry.url or null) == url)
            null
            source.hashes""",
    )
    for binding, hash_type in (
        ("srcHashEntry", "srcHash"),
        ("npmDepsHashEntry", "npmDepsHash"),
        ("rootCargoHashEntry", "vendorHash"),
        ("desktopCargoHashEntry", "cargoHash"),
    ):
        assert_nix_ast_equal(
            expect_binding(scope, binding).value,
            f'hashEntryFor "{hash_type}" source.urls.buzz',
        )
    assert_nix_ast_equal(
        expect_binding(scope, "onnxRuntimeSrcHashEntry").value,
        'hashEntryFor "srcHash" source.urls.onnxruntime',
    )

    gates = []
    pending = [expect_binding(scope, "unresolvedBuildGates").value]
    while pending:
        gate = pending.pop(0)
        if isinstance(gate, BinaryExpression) and gate.operator.name == "++":
            pending[0:0] = [gate.left, gate.right]
        else:
            gates.append(gate)
    assert_nix_ast_equal(
        gates[4],
        "lib.optional (onnxRuntimeSrcHashEntry == null) "
        '"ONNX Runtime ${expectedOnnxRuntimeVersion} srcHash is missing"',
    )

    native_slots = expect_instance(
        expect_binding(scope, "nativeFoundationSlots").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(scope, "onnxRuntimeNative").value,
        """if onnxRuntimeSrcHashEntry == null then
          null
        else
          import ./native/onnxruntime.nix {
            inherit abseil-cpp_202601 cctools fetchFromGitHub ld64 lib onnxruntime protobuf python3 stdenv;
            srcHash = onnxRuntimeSrcHashEntry.hash;
          }""",
    )
    assert_nix_ast_equal(
        expect_binding(native_slots.values, "onnxRuntime").value,
        "onnxRuntimeNative",
    )

    expected_contracts = expect_instance(
        expect_binding(scope, "expectedNativeContracts").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(expected_contracts.values, "onnxRuntime").value,
        """{
          kind = "onnxruntime";
          version = expectedOnnxRuntimeVersion;
          commit = expectedOnnxRuntimeCommit;
          target = "aarch64-apple-darwin";
          configuration = "Release";
          assemblyBuildSharedLib = true;
          assemblyBuildAppleFramework = true;
          deliveredSharedLib = false;
          deliveredAppleFramework = false;
          skipTests = true;
          monolithicStaticArchive = true;
        }""",
    )


def test_buzz_sherpa_slot_selects_only_its_url_scoped_promoted_hash() -> None:
    """Sherpa must require its audited source and the package-local ONNX slot."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    output = expect_instance(
        expect_instance(package.output, Assertion).body,
        IfExpression,
    )
    scope = output.scope

    assert_nix_ast_equal(
        expect_binding(scope, "sherpaOnnxSrcHashEntry").value,
        """if source.urls ? sherpaOnnx then
          hashEntryFor "srcHash" source.urls.sherpaOnnx
        else
          null""",
    )
    gates = []
    pending = [expect_binding(scope, "unresolvedBuildGates").value]
    while pending:
        gate = pending.pop(0)
        if isinstance(gate, BinaryExpression) and gate.operator.name == "++":
            pending[0:0] = [gate.left, gate.right]
        else:
            gates.append(gate)
    assert_nix_ast_equal(
        gates[5],
        "lib.optional (sherpaOnnxSrcHashEntry == null) "
        '"sherpa-onnx ${expectedSherpaOnnxVersion} srcHash is missing"',
    )

    assert_nix_ast_equal(
        expect_binding(scope, "onnxRuntimeNative").value,
        """if onnxRuntimeSrcHashEntry == null then
          null
        else
          import ./native/onnxruntime.nix {
            inherit abseil-cpp_202601 cctools fetchFromGitHub ld64 lib onnxruntime protobuf python3 stdenv;
            srcHash = onnxRuntimeSrcHashEntry.hash;
          }""",
    )
    native_slots = expect_instance(
        expect_binding(scope, "nativeFoundationSlots").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(native_slots.values, "onnxRuntime").value,
        "onnxRuntimeNative",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "sherpaOnnxNative").value,
        """if sherpaOnnxSrcHashEntry == null || onnxRuntimeNative == null then
          null
        else
          import ./native/sherpa-onnx.nix {
            inherit cctools fetchFromGitHub fetchurl lib sherpa-onnx stdenv;
            inherit (pkgs) eigen_5 libarchive nlohmann_json;
            onnxRuntime = onnxRuntimeNative;
            srcHash = sherpaOnnxSrcHashEntry.hash;
          }""",
    )
    assert_nix_ast_equal(
        expect_binding(native_slots.values, "sherpaOnnx").value,
        "sherpaOnnxNative",
    )
    expected_contracts = expect_instance(
        expect_binding(scope, "expectedNativeContracts").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(expected_contracts.values, "sherpaOnnx").value,
        """{
          kind = "sherpa-onnx";
          version = expectedSherpaOnnxVersion;
          commit = expectedSherpaOnnxCommit;
          target = "aarch64-apple-darwin";
          linkMode = "static";
          usePreinstalledOnnxRuntime = true;
          precompiledReleaseArchivesAllowed = false;
          cmakeOptions = {
            BUILD_SHARED_LIBS = false;
            SHERPA_ONNX_ENABLE_BINARY = false;
            SHERPA_ONNX_ENABLE_C_API = true;
            SHERPA_ONNX_ENABLE_GPU = false;
            SHERPA_ONNX_ENABLE_TESTS = false;
            SHERPA_ONNX_ENABLE_TTS = false;
          };
        }""",
    )


def test_buzz_onnxruntime_native_slot_is_static_cpu_only_darwin() -> None:
    """Apple framework assembly must deliver one static CPU-only archive."""
    native_path = _PACKAGE_DIR / "native/onnxruntime.nix"
    native = expect_instance(
        parse_nix_expr(native_path.read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    assert {
        "abseil-cpp_202601",
        "cctools",
        "fetchFromGitHub",
        "ld64",
        "lib",
        "onnxruntime",
        "protobuf",
        "python3",
        "srcHash",
        "stdenv",
    } == {
        argument.name
        for argument in native.argument_set
        if isinstance(argument, Identifier)
    }

    platform_assertion = expect_instance(native.output, Assertion)
    assert_nix_ast_equal(
        platform_assertion.expression,
        'stdenv.hostPlatform.system == "aarch64-darwin"',
    )
    override_call = expect_instance(platform_assertion.body, FunctionCall)
    assert_nix_ast_equal(override_call.name, "base.overrideAttrs")
    scope = override_call.scope
    assert_nix_ast_equal(expect_binding(scope, "version").value, '"1.27.0"')
    assert_nix_ast_equal(
        expect_binding(scope, "commit").value,
        '"8f0278c77bf44b0cc83c098c6c722b92a36ac4b5"',
    )
    assert_nix_ast_equal(
        expect_binding(scope, "normalizeStaticDependency").value,
        r"""dependency:
          dependency.overrideAttrs (old: {
            preConfigure = (old.preConfigure or "") + ''
              export NIX_CFLAGS_COMPILE="''${NIX_CFLAGS_COMPILE-} -ffile-prefix-map=$NIX_BUILD_TOP=/build -ffile-prefix-map=/nix/store=/source-store"
              export NIX_CXXFLAGS_COMPILE="''${NIX_CXXFLAGS_COMPILE-} -ffile-prefix-map=$NIX_BUILD_TOP=/build -ffile-prefix-map=/nix/store=/source-store"
            '';
          })""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "staticAbseil").value,
        "normalizeStaticDependency (abseil-cpp_202601.override { static = true; })",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "staticProtobuf").value,
        """normalizeStaticDependency (protobuf.override {
          abseil-cpp = staticAbseil;
          enableShared = false;
        })""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "base").value,
        """onnxruntime.override {
          abseil-cpp = staticAbseil;
          coremlSupport = false;
          cudaSupport = false;
          ncclSupport = false;
          openvinoSupport = false;
          protobuf = staticProtobuf;
          pythonSupport = false;
          rocmSupport = false;
        }""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "cmakeSourcePath").value,
        """name:
          let
            prefix = "-DFETCHCONTENT_SOURCE_DIR_${name}:STRING=";
          in
          lib.removePrefix prefix (
            lib.findSingle
              (lib.hasPrefix prefix)
              (throw "ONNX Runtime is missing ${name} source metadata")
              (throw "ONNX Runtime has duplicate ${name} source metadata")
              base.cmakeFlags
          )""",
    )

    override = expect_instance(
        expect_instance(override_call.argument, Parenthesis).value,
        FunctionDefinition,
    )
    attrs = expect_instance(override.output, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(attrs.values, "pname").value, '"buzz-onnxruntime"'
    )
    assert_nix_ast_equal(expect_binding(attrs.values, "version").value, "version")
    assert_nix_ast_equal(
        expect_binding(attrs.values, "src").value,
        nix_attrset_call(
            Identifier(name="fetchFromGitHub"),
            owner="microsoft",
            repo="onnxruntime",
            rev=Identifier(name="commit"),
            fetchSubmodules=False,
            hash=Identifier(name="srcHash"),
        ),
    )
    for binding in ("doCheck", "doInstallCheck", "separateDebugInfo"):
        assert_nix_ast_equal(expect_binding(attrs.values, binding).value, "false")
    for binding in ("checkInputs", "nativeCheckInputs"):
        assert_nix_ast_equal(expect_binding(attrs.values, binding).value, "[ ]")
    assert_nix_ast_equal(
        expect_binding(attrs.values, "outputChecks").value,
        """lib.recursiveUpdate (old.outputChecks or { }) {
          out.allowedReferences = [ ];
          dev.allowedReferences = [ "out" ];
        }""",
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "patches").value,
        """(old.patches or [ ]) ++ [
          ./onnxruntime-macos-static-archive.patch
        ]""",
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "cmakeBuildType").value,
        '"Release"',
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "cmakeFlags").value,
        """(old.cmakeFlags or [ ]) ++ [
          (lib.cmakeBool "onnxruntime_BUILD_SHARED_LIB" true)
          (lib.cmakeBool "onnxruntime_BUILD_APPLE_FRAMEWORK" true)
          (lib.cmakeBool "onnxruntime_BUILD_UNIT_TESTS" false)
          (lib.cmakeBool "onnxruntime_ENABLE_PYTHON" false)
          (lib.cmakeBool "onnxruntime_USE_COREML" false)
          (lib.cmakeBool "onnxruntime_USE_CUDA" false)
          (lib.cmakeBool "onnxruntime_USE_NCCL" false)
          (lib.cmakeBool "onnxruntime_USE_MIGRAPHX" false)
          (lib.cmakeBool "onnxruntime_USE_OPENVINO" false)
          (lib.cmakeBool "onnxruntime_USE_ROCM" false)
          (lib.cmakeBool "onnxruntime_ENABLE_LTO" false)
          (lib.cmakeFeature "CMAKE_OSX_ARCHITECTURES" "arm64")
          (lib.cmakeFeature "PLATFORM_NAME" "macosx")
        ]""",
    )

    def assert_old_expression_equal(binding: str, expected: str) -> None:
        actual = expect_binding(attrs.values, binding).value.rebuild()
        bindings = (
            "old: cctools: cmakeSourcePath: ld64: lib: python3: "
            "staticProtobuf: stdenv: version:"
        )
        assert_nix_ast_equal(f"{bindings} {actual}", f"{bindings} {expected}")

    assert_old_expression_equal(
        "postPatch",
        """(old.postPatch or "") + ''
          substituteInPlace cmake/onnxruntime.cmake \\
            --replace-fail "/usr/bin/ar" "${cctools}/bin/ar" \\
            --replace-fail "/usr/bin/ld" "${ld64}/bin/ld" \\
            --replace-fail "/usr/bin/libtool" "${cctools.libtool}/bin/libtool" \\
            --replace-fail \\
              'set(STATIC_FRAMEWORK_OUTPUT_DIR ''${CMAKE_CURRENT_BINARY_DIR}/''${CMAKE_BUILD_TYPE}-''${CMAKE_OSX_SYSROOT})' \\
              'set(STATIC_FRAMEWORK_OUTPUT_DIR ''${CMAKE_CURRENT_BINARY_DIR}/buzz-static-framework-output)'
          substituteInPlace onnxruntime/core/platform/posix/env.cc \\
            --replace-fail "$out/lib/" ""
          substituteInPlace cmake/onnxruntime.cmake \\
            --replace-fail "INSTALL_NAME_DIR $out/lib" "INSTALL_NAME_DIR @rpath"
        ''""",
    )
    assert_old_expression_equal(
        "preConfigure",
        r"""(old.preConfigure or "") + ''
          sourcePrefixMaps="-ffile-prefix-map=$NIX_BUILD_TOP=/build -ffile-prefix-map=${cmakeSourcePath "ONNX"}=/source/onnx -ffile-prefix-map=${cmakeSourcePath "ABSEIL_CPP"}=/source/abseil -ffile-prefix-map=${cmakeSourcePath "RE2"}=/source/re2 -ffile-prefix-map=${staticProtobuf}=/source/protobuf"
          export NIX_CFLAGS_COMPILE="$sourcePrefixMaps ''${NIX_CFLAGS_COMPILE-}"
          export NIX_CXXFLAGS_COMPILE="$sourcePrefixMaps ''${NIX_CXXFLAGS_COMPILE-}"
        ''""",
    )
    assert_old_expression_equal(
        "postInstall",
        r"""(old.postInstall or "") + ''
          staticFrameworkBinary="buzz-static-framework-output/static_framework/onnxruntime.framework/onnxruntime"
          if [ ! -f "$staticFrameworkBinary" ]; then
            echo "missing assembled static ONNX Runtime framework binary: $staticFrameworkBinary" >&2
            exit 1
          fi

          mkdir -p "$out/lib"
          install -m 444 "$staticFrameworkBinary" "$out/lib/libonnxruntime.a"
          rm -f "$out/include/coreml_provider_factory.h"
          find "$out" -name '*.framework' -prune -exec rm -rf {} +
          find "$out" \( \
            -name '*.dylib' -o -name '*.dylib.*' -o -name '*.so' -o -name '*.so.*' \
          \) -exec rm -f {} +
          rm -rf "$out/lib/cmake" "$out/lib/pkgconfig"
          if [ -d "$out/bin" ]; then
            rmdir "$out/bin"
          fi
        ''""",
    )
    assert_old_expression_equal(
        "postFixup",
        r"""(old.postFixup or "") + ''
          archive="$out/lib/libonnxruntime.a"
          ${python3}/bin/python3 ${./normalize_ar.py} "$archive"
          for requiredPath in \
            "$archive" \
            "$dev/include/onnxruntime_c_api.h" \
            "$dev/include/cpu_provider_factory.h" \
            "$dev/include/provider_options.h"; do
            if [ ! -f "$requiredPath" ]; then
              echo "missing required ONNX Runtime output: $requiredPath" >&2
              exit 1
            fi
          done

          staticArchiveCount="$(
            find "$out" "$dev" -type f -name 'libonnxruntime.a' -print |
              wc -l | tr -d '[:space:]'
          )"
          if [ "$staticArchiveCount" -ne 1 ]; then
            echo "expected exactly one delivered libonnxruntime.a, found $staticArchiveCount" >&2
            exit 1
          fi

          archiveMembers="$(${cctools}/bin/ar -t "$archive")"
          expectedArchiveMembers='__.SYMDEF SORTED
          prelinked_objects.o'
          if [ "$archiveMembers" != "$expectedArchiveMembers" ]; then
            echo "unexpected libonnxruntime.a member inventory:" >&2
            printf '%s\n' "$archiveMembers" >&2
            exit 1
          fi
          archiveOwners="$(
            ${cctools}/bin/ar -tv "$archive" |
              awk '{ print $2 }' |
              LC_ALL=C sort -u
          )"
          if [ "$archiveOwners" != "0/0" ]; then
            echo "libonnxruntime.a contains nondeterministic member owners: $archiveOwners" >&2
            exit 1
          fi

          archiveArchitectures="$(${cctools}/bin/lipo -archs "$archive")"
          if [ "$archiveArchitectures" != "arm64" ]; then
            echo "expected arm64-only libonnxruntime.a, found: $archiveArchitectures" >&2
            exit 1
          fi
          if ! ${cctools}/bin/nm -gU "$archive" | grep -E '(^|[[:space:]])_OrtGetApiBase$' >/dev/null; then
            echo "libonnxruntime.a does not export _OrtGetApiBase" >&2
            exit 1
          fi

          archiveStrings="$TMPDIR/buzz-onnxruntime-archive.strings"
          ${stdenv.cc.bintools}/bin/strings -a "$archive" > "$archiveStrings"
          if grep -F "$NIX_BUILD_TOP/" "$archiveStrings" >/dev/null \
            || grep -F '/nix/var/nix/builds/' "$archiveStrings" >/dev/null; then
            echo "libonnxruntime.a contains an ephemeral Nix build path" >&2
            exit 1
          fi
          if grep -F '/source-store/' "$archiveStrings" >/dev/null; then
            echo "libonnxruntime.a contains a hash-preserving normalized store path" >&2
            exit 1
          fi
          storeRoots="$(
            grep -Eo '/nix/store/[^/[:space:]]+' \
              "$archiveStrings" |
              LC_ALL=C sort -u || true
          )"
          if [ -n "$storeRoots" ]; then
            echo "libonnxruntime.a contains Nix store references:" >&2
            printf '%s\n' "$storeRoots" >&2
            exit 1
          fi

          projectUndefined="$(
            ${cctools}/bin/nm -u "$archive" |
              ${stdenv.cc.bintools}/bin/c++filt |
              grep -E '(^|[[:space:]])(onnxruntime::|onnx::|google::protobuf::|absl::|re2::|flatbuffers::|_?utf8_range_)' || true
          )"
          if [ -n "$projectUndefined" ]; then
            echo "libonnxruntime.a retains undefined project symbols:" >&2
            printf '%s\n' "$projectUndefined" >&2
            exit 1
          fi

          smokeSource="$TMPDIR/ort-link-smoke.cc"
          smokeBinary="$TMPDIR/ort-link-smoke"
          printf '%s\n' '#include <onnxruntime_c_api.h>' \
            'int main() {' \
            '  const OrtApiBase* base = OrtGetApiBase();' \
            '  return base && base->GetApi(ORT_API_VERSION) ? 0 : 1;' \
            '}' > "$smokeSource"
          "$CXX" -std=c++17 -I"$dev/include" "$smokeSource" \
            -Wl,-force_load,"$archive" -Wl,-undefined,error \
            -framework CoreFoundation -framework Foundation \
            -o "$smokeBinary"

          if [ -e "$dev/lib/cmake" ] || [ -e "$dev/lib/pkgconfig" ]; then
            echo "ONNX Runtime dev output contains misleading dynamic-library metadata" >&2
            exit 1
          fi

          if find "$out" "$dev" \
            \( \
              -name '*.framework' -o \
              -name '*.dylib' -o -name '*.dylib.*' -o -name '*.so' -o -name '*.so.*' \
            \) \
            -print -quit | grep -q .; then
            echo "ONNX Runtime output contains a framework or dynamic library" >&2
            exit 1
          fi
        ''""",
    )
    assert_old_expression_equal(
        "passthru",
        """(old.passthru or { }) // {
          buzzNativeContract = {
            kind = "onnxruntime";
            version = "1.27.0";
            commit = "8f0278c77bf44b0cc83c098c6c722b92a36ac4b5";
            target = "aarch64-apple-darwin";
            configuration = "Release";
            assemblyBuildSharedLib = true;
            assemblyBuildAppleFramework = true;
            deliveredSharedLib = false;
            deliveredAppleFramework = false;
            skipTests = true;
            monolithicStaticArchive = true;
          };
        }""",
    )
    assert_old_expression_equal(
        "meta",
        """(old.meta or { }) // {
          description = "Static ONNX Runtime foundation for Buzz";
          changelog = "https://github.com/microsoft/onnxruntime/releases/tag/v${version}";
          platforms = [ "aarch64-darwin" ];
          sourceProvenance = with lib.sourceTypes; [ fromSource ];
        }""",
    )


def test_buzz_sherpa_native_slot_matches_the_rust_static_link_contract() -> None:
    """The source build must deliver only the exact Rust sys archive closure."""
    native_path = _PACKAGE_DIR / "native/sherpa-onnx.nix"
    native = expect_instance(
        parse_nix_expr(native_path.read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    assert {
        "cctools",
        "eigen_5",
        "fetchFromGitHub",
        "fetchurl",
        "lib",
        "libarchive",
        "nlohmann_json",
        "onnxRuntime",
        "sherpa-onnx",
        "srcHash",
        "stdenv",
    } == {
        argument.name
        for argument in native.argument_set
        if isinstance(argument, Identifier)
    }

    platform_assertion = expect_instance(native.output, Assertion)
    assert_nix_ast_equal(
        platform_assertion.expression,
        'stdenv.hostPlatform.system == "aarch64-darwin"',
    )
    override_call = expect_instance(platform_assertion.body, FunctionCall)
    assert_nix_ast_equal(override_call.name, "base.overrideAttrs")
    scope = override_call.scope
    assert_nix_ast_equal(expect_binding(scope, "version").value, '"1.13.4"')
    assert_nix_ast_equal(
        expect_binding(scope, "commit").value,
        '"142807252687d81b40d6315f23470a1512a00de3"',
    )
    assert_nix_ast_equal(
        expect_binding(scope, "expectedArchiveNames").value,
        """[
          "libsherpa-onnx-c-api.a"
          "libsherpa-onnx-core.a"
          "libkaldi-decoder-core.a"
          "libsherpa-onnx-kaldifst-core.a"
          "libsherpa-onnx-fstfar.a"
          "libsherpa-onnx-fst.a"
          "libkaldi-native-fbank-core.a"
          "libkissfft-float.a"
          "libonnxruntime.a"
          "libssentencepiece_core.a"
        ]""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "activeCache").value,
        """[
          {
            cmakeVariable = "KALDI_NATIVE_FBANK";
            name = "kaldi-native-fbank-1.22.3.tar.gz";
            src = fetchurl {
              url = "https://github.com/csukuangfj/kaldi-native-fbank/archive/refs/tags/v1.22.3.tar.gz";
              hash = "sha256-kXbMZvx84e34XPNVsG4yDFfbYpffdCd/V1GDRoiTz2E=";
            };
          }
          {
            cmakeVariable = "SIMPLE-SENTENCEPIECE";
            name = "simple-sentencepiece-0.7.tar.gz";
            src = fetchurl {
              url = "https://github.com/pkufool/simple-sentencepiece/archive/refs/tags/v0.7.tar.gz";
              hash = "sha256-F0ioIgYKNbqp9mCfhO/I61TcDnS57OPYI2e3EZ/cda8=";
            };
          }
          {
            cmakeVariable = "KALDIFST";
            name = "kaldifst-1.8.0.tar.gz";
            src = fetchurl {
              url = "https://github.com/k2-fsa/kaldifst/archive/refs/tags/v1.8.0.tar.gz";
              hash = "sha256-PyR7flokCQcSAvXivGIABg9mcowKNEPAOSOtJyPgQLM=";
            };
          }
          {
            cmakeVariable = "KALDI_DECODER";
            name = "kaldi-decoder-0.3.0.tar.gz";
            src = fetchurl {
              url = "https://github.com/k2-fsa/kaldi-decoder/archive/refs/tags/v0.3.0.tar.gz";
              hash = "sha256-ufNM+0/TsTRBAO6tee9NN6oVliJ0ueMFbeNFAh92obA=";
            };
          }
          {
            cmakeVariable = "OPENFST";
            name = "openfst-1.8.5-2026-04-11.tar.gz";
            src = fetchurl {
              url = "https://github.com/csukuangfj/openfst/archive/refs/tags/v1.8.5-2026-04-11.tar.gz";
              hash = "sha256-V/vEuVCugbGg4eKYrxVlLalopnI6WSt4dOm0AnqApbQ=";
            };
          }
          {
            cmakeVariable = "KISSFFT";
            name = "kissfft-febd4caeed32e33ad8b2e0bb5ea77542c40f18ec.zip";
            src = fetchurl {
              url = "https://github.com/mborgerding/kissfft/archive/febd4caeed32e33ad8b2e0bb5ea77542c40f18ec.zip";
              hash = "sha256-SXED5mQWjr45WAt1etvmFvbPhaFlcq9YHKe8QtCrE/0=";
            };
          }
        ]""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "base").value,
        """sherpa-onnx.override {
          cudaSupport = false;
          onnxruntime = onnxRuntime;
          pythonSupport = false;
          websocketSupport = false;
        }""",
    )

    override = expect_instance(
        expect_instance(override_call.argument, Parenthesis).value,
        FunctionDefinition,
    )
    attrs = expect_instance(override.output, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(attrs.values, "pname").value,
        '"buzz-sherpa-onnx"',
    )
    assert_nix_ast_equal(expect_binding(attrs.values, "version").value, "version")
    assert_nix_ast_equal(
        expect_binding(attrs.values, "src").value,
        nix_attrset_call(
            Identifier(name="fetchFromGitHub"),
            owner="k2-fsa",
            repo="sherpa-onnx",
            rev=Identifier(name="commit"),
            fetchSubmodules=False,
            hash=Identifier(name="srcHash"),
        ),
    )
    assert_nix_ast_equal(expect_binding(attrs.values, "outputs").value, '[ "out" ]')
    assert_nix_ast_equal(expect_binding(attrs.values, "patches").value, "[ ]")
    assert "__impureHostDeps" not in binding_map(attrs.values)
    assert_nix_ast_equal(
        expect_binding(attrs.values, "nativeBuildInputs").value,
        "(old.nativeBuildInputs or [ ]) ++ [ libarchive ]",
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "__darwinAllowLocalNetworking").value,
        "false",
    )
    for binding in (
        "doCheck",
        "doInstallCheck",
        "dontStrip",
        "separateDebugInfo",
    ):
        expected = "true" if binding == "dontStrip" else "false"
        assert_nix_ast_equal(expect_binding(attrs.values, binding).value, expected)
    for binding in ("checkInputs", "nativeCheckInputs"):
        assert_nix_ast_equal(expect_binding(attrs.values, binding).value, "[ ]")
    assert_nix_ast_equal(
        expect_binding(attrs.values, "cmakeBuildType").value,
        '"Release"',
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "env").value,
        """(old.env or { }) // {
          SHERPA_ONNXRUNTIME_INCLUDE_DIR = "${lib.getDev onnxRuntime}/include";
          SHERPA_ONNXRUNTIME_LIB_DIR = "${lib.getLib onnxRuntime}/lib";
        }""",
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "cmakeFlags").value,
        """[
          (lib.cmakeBool "FETCHCONTENT_QUIET" false)
          (lib.cmakeBool "FETCHCONTENT_UPDATES_DISCONNECTED" true)
          (lib.cmakeBool "FETCHCONTENT_FULLY_DISCONNECTED" true)
          (lib.cmakeBool "BUILD_SHARED_LIBS" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_BINARY" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_C_API" true)
          (lib.cmakeBool "SHERPA_ONNX_BUILD_C_API_EXAMPLES" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_TESTS" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_CHECK" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_PYTHON" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_JNI" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_WEBSOCKET" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_PORTAUDIO" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_GPU" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_DIRECTML" false)
          (lib.cmakeBool "SHERPA_ONNX_LINK_D3D" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_RKNN" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_AXERA" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_AXCL" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_ASCEND_NPU" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_QNN" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_SPACEMIT" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_WASM" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_WASM_SPEAKER_DIARIZATION" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_WASM_TTS" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_WASM_ASR" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_WASM_KWS" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_WASM_VAD" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_WASM_VAD_ASR" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_WASM_NODEJS" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_WASM_SPEECH_ENHANCEMENT" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_SPEAKER_DIARIZATION" false)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_TTS" false)
          (lib.cmakeBool "SHERPA_ONNX_USE_PRE_INSTALLED_ONNXRUNTIME_IF_AVAILABLE" true)
          (lib.cmakeBool "SHERPA_ONNX_ENABLE_SANITIZER" false)
          (lib.cmakeFeature "onnxruntime_SOURCE_DIR" "${lib.getDev onnxRuntime}")
          (lib.cmakeFeature "FETCHCONTENT_SOURCE_DIR_JSON" "${nlohmann_json.src}")
          (lib.cmakeFeature "FETCHCONTENT_SOURCE_DIR_EIGEN" "${eigen_5.src}")
          (lib.cmakeFeature "CMAKE_CXX_FLAGS" "-DSHERPA_ONNX_DISABLE_COREML")
          (lib.cmakeFeature "CMAKE_OSX_ARCHITECTURES" "arm64")
          "-Wno-dev"
        ]""",
    )

    def assert_old_expression_equal(binding: str, expected: str) -> None:
        actual = expect_binding(attrs.values, binding).value.rebuild()
        bindings = (
            "old: activeCache: cctools: eigen_5: expectedArchiveNames: "
            "lib: libarchive: nlohmann_json: onnxRuntime:"
        )
        assert_nix_ast_equal(f"{bindings} {actual}", f"{bindings} {expected}")

    assert_old_expression_equal(
        "preConfigure",
        r"""''
          ${lib.concatMapStringsSep "\n" (entry: "cp ${entry.src} ./${entry.name}") activeCache}

          offlineSources="$PWD/.buzz-offline-sources"
          rm -rf "$offlineSources"
          mkdir -p "$offlineSources"
          for archiveName in ${lib.concatMapStringsSep " " (entry: lib.escapeShellArg entry.name) activeCache}; do
            if [ ! -s "$PWD/$archiveName" ]; then
              echo "missing reviewed local Sherpa dependency archive: $archiveName" >&2
              exit 1
            fi
          done
          ${lib.concatMapStringsSep "\n" (entry: ''
            sourceDirectory="$offlineSources/${entry.cmakeVariable}"
            sourceInventory="$TMPDIR/buzz-sherpa-${entry.cmakeVariable}.inventory"
            mkdir -p "$sourceDirectory"
            ${lib.getExe' libarchive "bsdtar"} \
              --extract \
              --file "$PWD/${entry.name}" \
              --strip-components 1 \
              --directory "$sourceDirectory"
            if ! find "$sourceDirectory" -mindepth 1 -print -quit > "$sourceInventory"; then
              echo "failed to inspect reviewed Sherpa dependency source: ${entry.cmakeVariable}" >&2
              exit 1
            fi
            if [ ! -s "$sourceInventory" ]; then
              echo "reviewed Sherpa dependency source is empty: ${entry.cmakeVariable}" >&2
              exit 1
            fi
            cmakeFlagsArray+=(
              "-DFETCHCONTENT_SOURCE_DIR_${entry.cmakeVariable}=$sourceDirectory"
            )
          '') activeCache}
          for sourceDir in \
            ${lib.escapeShellArg nlohmann_json.src} \
            ${lib.escapeShellArg eigen_5.src}; do
            if [ ! -d "$sourceDir" ]; then
              echo "missing reviewed local Sherpa dependency source: $sourceDir" >&2
              exit 1
            fi
          done
          if [ ! -f "${lib.getDev onnxRuntime}/include/onnxruntime_c_api.h" ] \
            || [ ! -f "${lib.getLib onnxRuntime}/lib/libonnxruntime.a" ]; then
            echo "missing package-local ONNX Runtime development inputs" >&2
            exit 1
          fi
        ''""",
    )

    assert_old_expression_equal(
        "postInstall",
        r"""(old.postInstall or "") + ''
          localOnnxArchive="${lib.getLib onnxRuntime}/lib/libonnxruntime.a"
          if [ ! -f "$localOnnxArchive" ]; then
            echo "missing package-local ONNX Runtime archive: $localOnnxArchive" >&2
            exit 1
          fi

          rm -f "$out/lib/libonnxruntime.a"
          ln -s "$localOnnxArchive" "$out/lib/libonnxruntime.a"
          rm -f \
            "$out/lib/libsherpa-onnx-cxx-api.a" \
            "$out/include/sherpa-onnx/c-api/cxx-api.h" \
            "$out/sherpa-onnx.pc"
        ''""",
    )
    assert_old_expression_equal(
        "postFixup",
        r"""(old.postFixup or "") + ''
          expectedInventory="$(
            printf '%s\n' ${lib.concatMapStringsSep " " lib.escapeShellArg expectedArchiveNames} |
              LC_ALL=C sort
          )"
          actualInventory="$(
            find "$out/lib" -maxdepth 1 \( -type f -o -type l \) -name '*.a' \
              -exec basename {} \; |
              LC_ALL=C sort
          )"
          if [ "$actualInventory" != "$expectedInventory" ]; then
            echo "unexpected sherpa-onnx static archive inventory" >&2
            printf 'expected:\n%s\nactual:\n%s\n' "$expectedInventory" "$actualInventory" >&2
            exit 1
          fi

          if [ ! -f "$out/include/sherpa-onnx/c-api/c-api.h" ]; then
            echo "missing sherpa-onnx C API header" >&2
            exit 1
          fi
          if [ -e "$out/lib/libsherpa-onnx-cxx-api.a" ] \
            || [ -e "$out/include/sherpa-onnx/c-api/cxx-api.h" ] \
            || [ -e "$out/sherpa-onnx.pc" ]; then
            echo "sherpa-onnx output retains a pruned C++ API or root pkg-config file" >&2
            exit 1
          fi
          if [ "$(readlink "$out/lib/libonnxruntime.a")" != \
            "${lib.getLib onnxRuntime}/lib/libonnxruntime.a" ]; then
            echo "sherpa-onnx does not reuse the package-local ONNX Runtime archive" >&2
            exit 1
          fi

          for archiveName in $expectedInventory; do
            archive="$out/lib/$archiveName"
            archiveArchitectures="$(${cctools}/bin/lipo -archs "$archive")"
            if [ "$archiveArchitectures" != "arm64" ]; then
              echo "expected arm64-only $archiveName, found: $archiveArchitectures" >&2
              exit 1
            fi
          done
          if ! ${cctools}/bin/nm -gU "$out/lib/libsherpa-onnx-c-api.a" \
            | grep -E '(^|[[:space:]])_SherpaOnnxCreateOfflineRecognizer$' >/dev/null; then
            echo "sherpa-onnx C API archive does not export _SherpaOnnxCreateOfflineRecognizer" >&2
            exit 1
          fi
          if ! ${cctools}/bin/nm -gU "$out/lib/libonnxruntime.a" \
            | grep -E '(^|[[:space:]])_OrtGetApiBase$' >/dev/null; then
            echo "package-local ONNX Runtime archive does not export _OrtGetApiBase" >&2
            exit 1
          fi

          if find "$out" \
            \( \
              -name '*.framework' -o \
              -name '*.dylib' -o -name '*.dylib.*' -o \
              -name '*.so' -o -name '*.so.*' \
            \) \
            -print -quit | grep -q .; then
            echo "sherpa-onnx output contains a framework or dynamic library" >&2
            exit 1
          fi
          if [ -d "$out/bin" ] && find "$out/bin" -type f -print -quit | grep -q .; then
            echo "sherpa-onnx output contains an executable" >&2
            exit 1
          fi

          smokeSource="$TMPDIR/buzz-sherpa-link-smoke.cc"
          printf '%s\n' \
            '#include <sherpa-onnx/c-api/c-api.h>' \
            'int main() { return SherpaOnnxCreateOfflineRecognizer(nullptr) == nullptr; }' \
            > "$smokeSource"
          "$CXX" -std=c++17 -I"$out/include" "$smokeSource" -L"$out/lib" \
            -lsherpa-onnx-c-api \
            -lsherpa-onnx-core \
            -lkaldi-decoder-core \
            -lsherpa-onnx-kaldifst-core \
            -lsherpa-onnx-fstfar \
            -lsherpa-onnx-fst \
            -lkaldi-native-fbank-core \
            -lkissfft-float \
            -lonnxruntime \
            -lssentencepiece_core \
            -lc++ \
            -framework Foundation \
            -o "$TMPDIR/buzz-sherpa-link-smoke"
        ''""",
    )
    assert_old_expression_equal(
        "passthru",
        """(old.passthru or { }) // {
          buzzNativeContract = {
            kind = "sherpa-onnx";
            version = "1.13.4";
            commit = "142807252687d81b40d6315f23470a1512a00de3";
            target = "aarch64-apple-darwin";
            linkMode = "static";
            usePreinstalledOnnxRuntime = true;
            precompiledReleaseArchivesAllowed = false;
            cmakeOptions = {
              BUILD_SHARED_LIBS = false;
              SHERPA_ONNX_ENABLE_BINARY = false;
              SHERPA_ONNX_ENABLE_C_API = true;
              SHERPA_ONNX_ENABLE_GPU = false;
              SHERPA_ONNX_ENABLE_TESTS = false;
              SHERPA_ONNX_ENABLE_TTS = false;
            };
          };
          buzzStaticArchiveLinkOrder = expectedArchiveNames;
        }""",
    )


def test_buzz_rust_toolchain_slot_reuses_the_exact_pinned_input_derivation() -> None:
    """The native Rust slot must work in probes without duplicating the toolchain."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    package_arguments = {argument.rebuild() for argument in package.argument_set}
    assert {"inputs", "pkgs", "stdenv"} <= package_arguments
    platform_assertion = expect_instance(package.output, Assertion)
    output = expect_instance(platform_assertion.body, IfExpression)
    native_slots = expect_instance(
        expect_binding(output.scope, "nativeFoundationSlots").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(output.scope, "rustBin").value,
        "inputs.rust-overlay.lib.mkRustBin { } pkgs",
    )
    assert_nix_ast_equal(
        expect_binding(output.scope, "rustToolchainNative").value,
        "import ./native/rust-toolchain.nix { inherit lib rustBin stdenv; }",
    )
    assert_nix_ast_equal(
        expect_binding(native_slots.values, "rustToolchain").value,
        "rustToolchainNative",
    )

    assert_nix_ast_equal(
        (_PACKAGE_DIR / "native/rust-toolchain.nix").read_text(encoding="utf-8"),
        """
        { lib, rustBin, stdenv }:
        assert stdenv.hostPlatform.system == "aarch64-darwin";
        let
          toolchain = rustBin.stable."1.95.0".default;
        in
        lib.extendDerivation true {
          passthru = (toolchain.passthru or { }) // {
            buzzNativeContract = {
              kind = "rust-toolchain";
              channel = "1.95.0";
              profile = "default";
              target = "aarch64-apple-darwin";
            };
          };
        } toolchain
        """,
    )


def test_buzz_internal_nix_foundation_promotes_only_the_validated_candidate() -> None:
    """The source-built package must keep a real candidate and blocked fallback."""
    assert_nix_ast_equal(
        (_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8"),
        "import ./package.nix",
    )
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    platform_assertion = expect_instance(package.output, Assertion)
    output = expect_instance(platform_assertion.body, IfExpression)
    scope = output.scope

    assert_nix_ast_equal(
        platform_assertion.expression,
        'stdenv.hostPlatform.system == "aarch64-darwin"',
    )
    assert Identifier(name="buzzNativeFoundation") not in package.argument_set
    assert Identifier(name="fetchurl") in package.argument_set
    assert Identifier(name="fetchPnpmDeps") in package.argument_set
    assert Identifier(name="sherpa-onnx") in package.argument_set
    assert_nix_ast_equal(
        expect_binding(scope, "expectedVersion").value, f'"{_VERSION}"'
    )
    assert_nix_ast_equal(expect_binding(scope, "expectedCommit").value, f'"{_COMMIT}"')
    assert_nix_ast_equal(expect_binding(scope, "expectedRustVersion").value, '"1.95.0"')
    assert_nix_ast_equal(
        expect_binding(scope, "expectedSherpaOnnxCommit").value,
        '"142807252687d81b40d6315f23470a1512a00de3"',
    )
    assert_nix_ast_equal(
        expect_binding(scope, "expectedOnnxRuntimeCommit").value,
        '"8f0278c77bf44b0cc83c098c6c722b92a36ac4b5"',
    )
    assert_nix_ast_equal(
        expect_binding(scope, "pnpm").value,
        """(pnpm_11.override { nodejs-slim = nodejs_24; }).overrideAttrs (_: {
          version = expectedPnpmVersion;
          src = fetchurl {
            url = "https://registry.npmjs.org/pnpm/-/pnpm-${expectedPnpmVersion}.tgz";
            hash = "sha256-50EGpaDrJWn0WDUEQg6tX8HCY+QXoyFsqxy+DM3LTq4=";
          };
        })""",
    )
    pnpm_deps = expect_instance(
        expect_binding(scope, "pnpmDeps").value,
        FunctionCall,
    )
    assert_nix_ast_equal(pnpm_deps.name, "fetchPnpmDeps")
    assert_nix_ast_equal(pnpm_deps.argument, "args")
    pnpm_args = expect_instance(
        expect_binding(pnpm_deps.scope, "args").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(pnpm_args.values, "fetcherVersion").value,
        "4",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "rootCargoDeps").value,
        """rustPlatform.fetchCargoVendor {
          inherit src;
          name = "${pname}-${version}-root-cargo-vendor";
          cargoRoot = ".";
          hash = hashOrFake rootCargoHashEntry;
        }""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "desktopCargoDeps").value,
        """rustPlatform.fetchCargoVendor {
          inherit src;
          name = "${pname}-${version}-desktop-cargo-vendor";
          cargoRoot = "desktop/src-tauri";
          hash = hashOrFake desktopCargoHashEntry;
        }""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "sidecarSpecs").value,
        """[
          { package = "buzz-acp"; binary = "buzz-acp"; }
          { package = "buzz-agent"; binary = "buzz-agent"; }
          { package = "buzz-backend-kubernetes"; binary = "buzz-backend-kubernetes"; }
          { package = "buzz-dev-mcp"; binary = "buzz-dev-mcp"; }
          { package = "git-credential-nostr"; binary = "git-credential-nostr"; }
          { package = "buzz-cli"; binary = "buzz"; }
        ]""",
    )
    native_slots = expect_binding(scope, "nativeFoundationSlots").value.rebuild()
    native_slot_bindings = (
        "onnxRuntimeNative: sherpaOnnxNative: meshLlmNative: llamaCppNative: "
        "meshRuntimeBundleNative: buzzRuntimePolicySource: rustToolchainNative: "
        "sidecarsNative:"
    )
    assert_nix_ast_equal(
        f"{native_slot_bindings} {native_slots}",
        f"""{native_slot_bindings} {{
          rustToolchain = rustToolchainNative;
          sidecars = sidecarsNative;
          onnxRuntime = onnxRuntimeNative;
          sherpaOnnx = sherpaOnnxNative;
          meshLlm = meshLlmNative;
          llamaCpp = llamaCppNative;
          meshRuntimeBundle = meshRuntimeBundleNative;
          patchedBuzzSource = buzzRuntimePolicySource;
        }}""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "slotMatches").value,
        """name:
          let
            candidate = nativeFoundationSlots.${name};
          in
          lib.isDerivation candidate
          && (candidate.passthru.buzzNativeContract or null) == expectedNativeContracts.${name}""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "nativeFoundationReady").value,
        "builtins.all slotMatches nativeFoundationNames",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "missingNativeFoundation").value,
        "builtins.filter (name: !(slotMatches name)) nativeFoundationNames",
    )

    passthru = expect_instance(
        expect_binding(scope, "commonPassthru").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "buzzNativeBuildPlan").value,
        """{
          cargoLocks = [ "Cargo.lock" "desktop/src-tauri/Cargo.lock" ];
          sidecars = sidecarSpecs;
          tauri = {
            workingDirectory = "desktop";
            cargoRoot = "desktop/src-tauri";
            feature = "mesh-llm";
            bundles = [ "app" ];
          };
          nativeRuntime = {
            currentBehavior = {
              dynamicNativeRuntime = true;
              llamaStageBuildDirEffective = false;
              firstUseDownloadAllowed = true;
              verification = "checksum-only";
              signatureVerificationImplemented = false;
            };
              requiredReplacement = {
                bundle = "repo-owned meshRuntimeBundle derivation";
                discoveryEnvironment = "MESH_LLM_NATIVE_RUNTIME_BUNDLE_DIR";
                sourcePatch = "repo-owned patchedBuzzSource derivation";
                launchEnvironment = buzzRuntimePolicySource.passthru.requiredLaunchEnvironment;
                allowDefaultManifestUrl = false;
                allowDownload = false;
              };
          };
          updaterEnvironment = {
            BUZZ_UPDATER_ENDPOINT = "";
            BUZZ_UPDATER_PUBLIC_KEY = "";
          };
        }""",
    )

    expected_contracts = expect_instance(
        expect_binding(scope, "expectedNativeContracts").value,
        AttributeSet,
    )
    for slot in (
        "rustToolchain",
        "sidecars",
        "onnxRuntime",
        "sherpaOnnx",
        "meshLlm",
        "llamaCpp",
        "meshRuntimeBundle",
        "patchedBuzzSource",
    ):
        expect_binding(expected_contracts.values, slot)
    patched_source = expect_instance(
        expect_binding(expected_contracts.values, "patchedBuzzSource").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(patched_source.values, "allowDownload").value,
        "false",
    )
    assert_nix_ast_equal(
        expect_binding(patched_source.values, "allowDefaultManifestUrl").value,
        "false",
    )

    assert_nix_ast_equal(
        expect_binding(scope, "validatedPackage").value,
        """desktopCandidateNative.overrideAttrs (old: {
          passthru = (old.passthru or { }) // commonPassthru;
        })""",
    )
    blocked_package = expect_instance(
        expect_binding(scope, "blockedPackage").value,
        FunctionCall,
    )
    assert_nix_ast_equal(blocked_package.name, "stdenvNoCC.mkDerivation")
    blocked_args = expect_instance(blocked_package.argument, AttributeSet)
    blocked_meta = expect_instance(
        expect_binding(blocked_args.values, "meta").value,
        AttributeSet,
    )
    assert_nix_ast_equal(expect_binding(blocked_meta.values, "broken").value, "true")
    assert_nix_ast_equal(output.condition, "unresolvedBuildGates == [ ]")
    assert_nix_ast_equal(output.consequence, "validatedPackage")
    assert_nix_ast_equal(output.alternative, "blockedPackage")


def test_buzz_enters_the_effective_arm64_darwin_app_graph() -> None:
    """Discovery, overlay export, and system routing must promote the same app."""
    assert_nix_ast_equal(
        (_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8"),
        "import ./package.nix",
    )

    registry = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/registry.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    assert_nix_ast_equal(
        expect_binding(registry.output.scope, "discoveredPackages").value,
        """discovery.discoverDefaultNixEntries {
          root = pkgDir;
          excludeFiles = [ "default.nix" "registry.nix" ];
          includeFile = fileName: _: builtins.match "^_.*\\\\.nix$" fileName == null;
        }""",
    )
    registry_output = expect_instance(registry.output, AttributeSet)
    assert registry_override_metadata(registry_output)["buzz"] == {
        "constraint": ["aarch64-darwin"]
    }

    materializer = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/default.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    assert_nix_ast_equal(
        materializer.output,
        """let
          packageMaterialization = import ../lib/package-materialization.nix {
            src = ../.;
            inherit lib outputs;
          };
          systemEval = builtins.tryEval system;
          resolvedSystem =
            if systemEval.success && systemEval.value != null
            then systemEval.value
            else "x86_64-linux";
        in
        packageMaterialization.packageFunctionsForSystem resolvedSystem""",
    )

    overlay = expect_instance(
        nix_file_expr("overlays/binary-darwin-apps.nix"),
        FunctionDefinition,
    )
    overlay_exports = expect_instance(overlay.output, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(overlay_exports.values, "buzz").value,
        'callDarwinAppPackage "buzz"',
    )

    routing = expect_instance(
        nix_source_fragment_expr(
            "home/george/work.nix",
            "  routing = ",
            ";\n  projection =",
        ),
        AttributeSet,
    )
    buzz_route = expect_instance(
        expect_binding(routing.values, "buzz").value,
        FunctionCall,
    )
    assert_nix_ast_equal(buzz_route.name, "systemApp")
    assert buzz_route.argument is not None
    assert_nix_ast_equal(buzz_route.argument, "pkgs.buzz")
