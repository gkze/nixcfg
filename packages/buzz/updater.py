"""Fail-closed updater for the internal Buzz desktop source foundation."""

import asyncio
import hashlib
import json
import re
import shlex
import tomllib
import urllib.parse
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, cast

from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import (
    HashCollection,
    HashEntry,
    HashType,
    SourceEntry,
    SourceHashes,
)
from lib.update import nix as update_nix
from lib.update.artifacts import GeneratedArtifact
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import (
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_str,
    require_value,
)
from lib.update.net import fetch_github_api, fetch_url, github_raw_url
from lib.update.nix import (
    _build_fetch_from_github_expr,
    _build_repo_package_attr_expr,
)
from lib.update.nix_expr import compact_nix_expr, identifier_attr_path
from lib.update.paths import updater_dir_for
from lib.update.updaters import (
    GitHubReleaseUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import metadata_as_mapping
from packages.buzz.native.patch_runtime_policy import (
    mask_rust_non_code,
    rust_delimiter_stack,
    rust_file_has_inner_attribute,
    rust_item_has_outer_attribute,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    import aiohttp

    from lib.update.config import UpdateConfig
    from lib.update.events import EventStream

_APP_ID = "xyz.block.buzz.app"
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_EXACT_VERSION_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_MESH_LOCK_SOURCE_PATTERN = re.compile(
    r"^git\+https://github\.com/Mesh-LLM/mesh-llm\.git\?"
    r"tag=(?P<tag>v[^#]+)#(?P<commit>[0-9a-f]{40})$"
)
_RUST_INNER_ATTRIBUTE_MARKER = re.compile(r"#\s*!\s*\[")
_ABI_COMPONENTS = ("MAJOR", "MINOR", "PATCH")
type _HashIdentity = tuple[HashType, str]

_SHERPA_FETCHCONTENT_LOCK = {
    "kaldiDecoder": {
        "cmakeVariable": "KALDI_DECODER",
        "file": "kaldi-decoder-0.3.0.tar.gz",
        "url": (
            "https://github.com/k2-fsa/kaldi-decoder/archive/refs/tags/v0.3.0.tar.gz"
        ),
    },
    "kaldiNativeFbank": {
        "cmakeVariable": "KALDI_NATIVE_FBANK",
        "file": "kaldi-native-fbank-1.22.3.tar.gz",
        "url": (
            "https://github.com/csukuangfj/kaldi-native-fbank/archive/refs/tags/"
            "v1.22.3.tar.gz"
        ),
    },
    "kaldifst": {
        "cmakeVariable": "KALDIFST",
        "file": "kaldifst-1.8.0.tar.gz",
        "url": ("https://github.com/k2-fsa/kaldifst/archive/refs/tags/v1.8.0.tar.gz"),
    },
    "kissfft": {
        "cmakeVariable": "KISSFFT",
        "file": "kissfft-febd4caeed32e33ad8b2e0bb5ea77542c40f18ec.zip",
        "url": (
            "https://github.com/mborgerding/kissfft/archive/"
            "febd4caeed32e33ad8b2e0bb5ea77542c40f18ec.zip"
        ),
    },
    "openfst": {
        "cmakeVariable": "OPENFST",
        "file": "openfst-1.8.5-2026-04-11.tar.gz",
        "url": (
            "https://github.com/csukuangfj/openfst/archive/refs/tags/"
            "v1.8.5-2026-04-11.tar.gz"
        ),
    },
    "simpleSentencepiece": {
        "cmakeVariable": "SIMPLE-SENTENCEPIECE",
        "file": "simple-sentencepiece-0.7.tar.gz",
        "url": (
            "https://github.com/pkufool/simple-sentencepiece/archive/refs/tags/"
            "v0.7.tar.gz"
        ),
    },
}
_SHERPA_FETCHCONTENT_ORDER = (
    "kaldiNativeFbank",
    "simpleSentencepiece",
    "kaldifst",
    "kaldiDecoder",
    "openfst",
    "kissfft",
)


@dataclass(frozen=True, slots=True)
class _HashRequest:
    hash_type: HashType
    url: str
    error: str
    expr: Callable[[dict[_HashIdentity, str]], str]


@dataclass(frozen=True, slots=True)
class _ReleaseMetadataContract:
    pnpm_version: str
    rust_version: str


@dataclass(frozen=True, slots=True)
class _CargoContract:
    mesh_commit: str
    mesh_version: str
    sherpa_version: str


@dataclass(frozen=True, slots=True)
class _BuzzReleaseContract:
    mesh_commit: str
    mesh_version: str
    pnpm_version: str
    rust_version: str
    sherpa_version: str


@dataclass(frozen=True, slots=True)
class _MeshSourceContract:
    llama_cpp_commit: str
    skippy_abi: str


# Each tuple is (workspace package, emitted binary). The final mapping is the
# upstream buzz-cli package intentionally renamed to the `buzz` sidecar.
_SIDECAR_SPECS: tuple[tuple[str, str], ...] = (
    ("buzz-acp", "buzz-acp"),
    ("buzz-agent", "buzz-agent"),
    ("buzz-backend-kubernetes", "buzz-backend-kubernetes"),
    ("buzz-dev-mcp", "buzz-dev-mcp"),
    ("git-credential-nostr", "git-credential-nostr"),
    ("buzz-cli", "buzz"),
)
_TAURI_SIDECARS = tuple(f"binaries/{binary}" for _, binary in _SIDECAR_SPECS)
_BASE_BUNDLE_SIDECARS = (
    "buzz-acp",
    "buzz-agent",
    "buzz-dev-mcp",
    "git-credential-nostr",
    "buzz",
)
_DARWIN_ONLY_SIDECARS = ("buzz-backend-kubernetes",)

_ROOT_PACKAGE_JSON = "package.json"
_DESKTOP_PACKAGE_JSON = "desktop/package.json"
_TAURI_CONFIG = "desktop/src-tauri/tauri.conf.json"
_ROOT_MANIFEST = "Cargo.toml"
_ROOT_LOCK = "Cargo.lock"
_DESKTOP_MANIFEST = "desktop/src-tauri/Cargo.toml"
_DESKTOP_LOCK = "desktop/src-tauri/Cargo.lock"
_RUST_TOOLCHAIN = "rust-toolchain.toml"
_SIDECAR_SCRIPT = "scripts/bundle-sidecars.sh"
_BUILD_SCRIPT = "desktop/src-tauri/build.rs"
_DESKTOP_LIB = "desktop/src-tauri/src/lib.rs"
_MESH_RUNTIME_ENTRYPOINT = "desktop/src-tauri/src/mesh_llm/mod.rs"
_SIDECAR_MANIFESTS = tuple(
    f"crates/{package}/Cargo.toml" for package, _binary in _SIDECAR_SPECS
)
_BUZZ_SOURCE_PATHS = (
    _ROOT_PACKAGE_JSON,
    _DESKTOP_PACKAGE_JSON,
    _TAURI_CONFIG,
    _ROOT_MANIFEST,
    _ROOT_LOCK,
    _DESKTOP_MANIFEST,
    _DESKTOP_LOCK,
    _RUST_TOOLCHAIN,
    _SIDECAR_SCRIPT,
    _BUILD_SCRIPT,
    _DESKTOP_LIB,
    _MESH_RUNTIME_ENTRYPOINT,
    *_SIDECAR_MANIFESTS,
)

# These digests belong only to the explicitly supported native foundation.
# Buzz release files are validated semantically so a routine desktop release
# does not require copying new byte digests into the updater.
_MESH_SOURCE_DIGESTS: dict[str, str] = {
    "crates/mesh-llm-sdk/Cargo.toml": (
        "b575570e2400cac09ca86197453826a94da143c104a93d6c30e3af33d3a92ed1"
    ),
    "crates/mesh-llm-embedded-runtime/Cargo.toml": (
        "0238f31c785e812097d68c1d8c729f947c13ed6386528a231dff4f75d93aa040"
    ),
    "crates/mesh-llm-host-runtime/Cargo.toml": (
        "fdad576eb5ec818a5d66220d63b60aa1ee1cc01733876e144e00cb8d12e57a03"
    ),
    "crates/mesh-llm-system/Cargo.toml": (
        "d59ed62184fee33789ddd5d9b867328bf5a54e318bc4155d9b6a9dae9fd22463"
    ),
    "crates/skippy-runtime/Cargo.toml": (
        "96bfa6b6ff30aa7e3a2c7ef65e12414a2ea2a65b69932784990a1eac1663aea7"
    ),
    "crates/skippy-server/Cargo.toml": (
        "1d4774d9711ee92442bfa8b1a92081aec52f7aa6623528933f337baaf0e141ab"
    ),
    "crates/skippy-ffi/Cargo.toml": (
        "b1319982c9651cb9d0e5d3b1ceadedde696a4f42ebbcaa8c6fa50a2bb6476a40"
    ),
    "crates/skippy-ffi/build.rs": (
        "cd4f7efc4953832c145ca82b4a4aa93ee9ef0c1685fb816f6606b94fca54d07b"
    ),
    "crates/skippy-ffi/src/lib.rs": (
        "a2f8f672ce6bcd161c127ebc63465e9090abdfe866b17834d4463d224f02f07f"
    ),
    "crates/mesh-llm-host-runtime/src/lib.rs": (
        "a5546d3aec12830c3a5ef391a9da14a60a8010dedd8bf4b2005861d6607368bb"
    ),
    "crates/mesh-llm-host-runtime/src/system/native_runtime.rs": (
        "d869c1c42e3112a9f1afd3c35d959ea8bb44bff913a34331cd365c41ecca028d"
    ),
    "crates/mesh-llm-native-runtime/src/manifest.rs": (
        "db91c4ef173269900f6bfd37af8906f37f6d4785f4113c918ad5873b8f1c324c"
    ),
    "crates/mesh-llm-native-runtime/src/flavor.rs": (
        "64de68e348eff4fbb46f9105568a3d39d5b22c276fcdc927e639fb790a66f437"
    ),
    "crates/mesh-llm-native-runtime/src/load_plan.rs": (
        "75b2616f429e59a13a5b202ff8373b832360c5b6e91b4d5bd4130b17c8f6941b"
    ),
    "crates/mesh-llm-runtime-install/src/lib.rs": (
        "941c1363c0af2e10b631994924077ef5bf56430af6f5270b8db9d04afce6031d"
    ),
    "crates/mesh-llm-runtime-install/src/discovery.rs": (
        "f241c1a5fdae0d6f5898fb97d91dda04c600c67654fb3bb5e4bccf515191be55"
    ),
    "scripts/build-llama.sh": (
        "94b0ee9f8d902e7e1ff7fd5050137eec4c956886ad780766d1209a7b0beceba2"
    ),
    "scripts/package-native-runtime.sh": (
        "5baea3d467630eb4235a717583ea89ed5aab328c2e7fc94789188f44bc3229ed"
    ),
    "scripts/prepare-llama.sh": (
        "caab15f2f9680c5493ca2a4a302d5c9674fe04aa6d0ab3540a19ef7acc2c2b0d"
    ),
    "third_party/llama.cpp/upstream.txt": (
        "bac5d6f06e193dff7866055e4c25daf800d33b46964870cf942659f478e2042f"
    ),
}
_ONNX_SOURCE_DIGESTS: dict[str, str] = {
    "VERSION_NUMBER": (
        "7ef1ea58fece676ff7345f6edac427e671daf20f0d7499ef2e42ada241d4fe24"
    ),
    ".gitmodules": ("88baf1a643d03b2c6c4ef4caf3463ac9eefdbd150c5e9054b1df23578eb4a160"),
    "cmake/deps.txt": (
        "e411468ead299e3386b2e5e9d773e50e1939b5fc0baca599666ca5757eeb3f71"
    ),
    "cmake/onnxruntime.cmake": (
        "4f73825c1782b0309cbad11d04c1a8ae5d7460b2464e08905064dcb11fdcd9c6"
    ),
}
_SHERPA_SOURCE_DIGESTS: dict[str, str] = {
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
_SHERPA_STATIC_LINK_LIBRARIES = (
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
)


def _require_object(value: object, *, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = f"Buzz {context} is not an object"
        raise TypeError(msg)
    return cast("dict[str, object]", value)


def _require_list(value: object, *, context: str) -> list[object]:
    if not isinstance(value, list):
        msg = f"Buzz {context} is not a list"
        raise TypeError(msg)
    return cast("list[object]", value)


def _require_string(mapping: dict[str, object], key: str, *, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        msg = f"Buzz {context} is missing {key}"
        raise TypeError(msg)
    return value


def _require_exact(actual: object, expected: object, *, context: str) -> None:
    if actual != expected:
        msg = f"Buzz {context} drifted: expected {expected!r}, got {actual!r}"
        raise RuntimeError(msg)


def _require_exact_version(value: str, *, context: str) -> str:
    if _EXACT_VERSION_PATTERN.fullmatch(value) is None:
        msg = f"Buzz {context} must be an exact semantic version, got {value!r}"
        raise RuntimeError(msg)
    return value


def _require_prefixed_version(value: str, prefix: str, *, context: str) -> str:
    if not value.startswith(prefix):
        msg = f"Buzz {context} must start with {prefix!r}, got {value!r}"
        raise RuntimeError(msg)
    return _require_exact_version(value.removeprefix(prefix), context=context)


def _require_commit(value: str, *, context: str) -> str:
    if _COMMIT_PATTERN.fullmatch(value) is None:
        msg = f"Buzz {context} must be an immutable commit, got {value!r}"
        raise RuntimeError(msg)
    return value


def _decode_json(payload: bytes, *, context: str) -> dict[str, object]:
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = f"Buzz {context} is not valid UTF-8 JSON"
        raise RuntimeError(msg) from exc
    return _require_object(decoded, context=context)


def _decode_toml(payload: bytes, *, context: str) -> dict[str, object]:
    try:
        decoded = tomllib.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        msg = f"Buzz {context} is not valid UTF-8 TOML"
        raise RuntimeError(msg) from exc
    return _require_object(decoded, context=context)


def _validate_digest_contract(
    payloads: dict[str, bytes],
    expected: dict[str, str],
    *,
    context: str,
) -> None:
    missing = sorted(set(expected).difference(payloads))
    if missing:
        msg = f"Buzz {context} is missing paths: {', '.join(missing)}"
        raise RuntimeError(msg)
    for path, expected_digest in expected.items():
        actual_digest = hashlib.sha256(payloads[path]).hexdigest()
        _require_exact(
            actual_digest,
            expected_digest,
            context=f"{context} digest for {path}",
        )


def _feature_tuple(
    manifest: dict[str, object],
    feature: str,
    *,
    context: str,
) -> tuple[object, ...]:
    features = _require_object(manifest.get("features"), context=f"{context} features")
    return tuple(
        _require_list(features.get(feature), context=f"{context} {feature} feature")
    )


def _matching_rust_brace(source: str, opening: int) -> int | None:
    """Return the closing brace for one lexically masked Rust block."""
    if opening >= len(source) or source[opening] != "{":
        return None
    depth = 1
    for cursor in range(opening + 1, len(source)):
        if source[cursor] == "{":
            depth += 1
        elif source[cursor] == "}":
            depth -= 1
            if depth == 0:
                return cursor
    return None


def _has_exact_active_root_rust_item(source: str, reviewed_item: str) -> bool:
    """Require one byte-reviewed Rust item in the active root token stream."""
    masked_source = mask_rust_non_code(source)
    masked_item = mask_rust_non_code(reviewed_item)
    if masked_source.count(masked_item) != 1:
        return False
    start = masked_source.index(masked_item)
    return (
        rust_delimiter_stack(masked_source, start) == ()
        and not rust_file_has_inner_attribute(masked_source, start)
        and not rust_item_has_outer_attribute(masked_source, start)
        and source[start : start + len(reviewed_item)] == reviewed_item
    )


def _normalized_rust_method(
    source: str,
    *,
    owner: str,
    method_pattern: str,
) -> str | None:
    """Extract one active method through its balanced closing brace."""
    owner_matches = tuple(re.finditer(rf"(?m)^impl\s+{re.escape(owner)}\s*\{{", source))
    if len(owner_matches) != 1:
        return None
    owner_match = owner_matches[0]
    if (
        rust_delimiter_stack(source, owner_match.start()) != ()
        or rust_file_has_inner_attribute(source, owner_match.start())
        or rust_item_has_outer_attribute(source, owner_match.start())
    ):
        return None
    owner_end = _matching_rust_brace(source, owner_match.end() - 1)
    if owner_end is None:
        return None
    method_matches = tuple(
        re.compile(method_pattern).finditer(source, owner_match.end(), owner_end)
    )
    if len(method_matches) != 1:
        return None
    method_match = method_matches[0]
    inner_attribute_matches = _RUST_INNER_ATTRIBUTE_MARKER.finditer(
        source,
        owner_match.end(),
        method_match.start(),
    )
    if (
        rust_delimiter_stack(source, method_match.start()) != ("{",)
        or rust_item_has_outer_attribute(source, method_match.start())
        or any(
            rust_delimiter_stack(source, match.start()) == ("{",)
            for match in inner_attribute_matches
        )
    ):
        return None
    # A balanced owner guarantees every nested opening brace closes within it.
    method_end = cast("int", _matching_rust_brace(source, method_match.end() - 1))
    return re.sub(r"\s+", "", source[method_match.start() : method_end + 1])


def _validate_mesh_source_contract(
    payloads: dict[str, bytes],
) -> _MeshSourceContract:
    """Validate the supported Mesh graph and derive its nested native identity."""
    _validate_digest_contract(
        payloads,
        BuzzUpdater.get_compatibility_source_digest_contract("meshLlm"),
        context="Mesh source contract",
    )
    manifest_text = payloads["crates/mesh-llm-native-runtime/src/manifest.rs"].decode(
        "utf-8"
    )
    manifest_source = mask_rust_non_code(manifest_text)
    flavor_source = mask_rust_non_code(
        payloads["crates/mesh-llm-native-runtime/src/flavor.rs"].decode("utf-8")
    )
    load_plan_source = mask_rust_non_code(
        payloads["crates/mesh-llm-native-runtime/src/load_plan.rs"].decode("utf-8")
    )
    manifest_semantics = {
        "platform object": """#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct NativeRuntimePlatform {
    pub os: String,
    pub arch: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target: Option<String>,
}""",
        "runtime artifact object": """#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
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
}""",
        "runtime wrapper object": """#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct NativeRuntimeManifest {
    pub runtime: NativeRuntimeArtifact,
}""",
    }
    drifted_manifest_semantics = [
        name
        for name, reviewed_item in manifest_semantics.items()
        if not _has_exact_active_root_rust_item(manifest_text, reviewed_item)
    ]
    method_contracts = (
        (
            "checksum verifier rejects empty and uncovered libraries",
            manifest_source,
            "NativeRuntimeManifest",
            r"(?m)^[ \t]*fn\s+verify_contents\s*\(\s*&self\s*,\s*"
            r"dir\s*:\s*&Path\s*\)\s*->\s*Result\s*<\s*\(\s*\)\s*>\s*\{",
            """fn verify_contents(&self, dir: &Path) -> Result<()> {
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
            }""",
        ),
        (
            "Metal backend object constructor",
            flavor_source,
            "NativeRuntimeBackend",
            r"(?m)^[ \t]*pub\s+fn\s+metal\s*\(\s*\)\s*->\s*Self\s*\{",
            """pub fn metal() -> Self {
                Self {
                    kind: NativeRuntimeBackendKind::Metal,
                    cuda: None,
                    rocm: None,
                    vulkan: None,
                }
            }""",
        ),
        (
            "manifest-order load plan",
            load_plan_source,
            "InstalledNativeRuntime",
            r"(?m)^[ \t]*pub\s+fn\s+load_plan\s*\(\s*&self\s*\)\s*->\s*"
            r"Result\s*<\s*NativeRuntimeLoadPlan\s*>\s*\{",
            """pub fn load_plan(&self) -> Result<NativeRuntimeLoadPlan> {
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
            }""",
        ),
    )
    drifted_manifest_semantics.extend(
        name
        for name, source, owner, method_pattern, expected_method in method_contracts
        if _normalized_rust_method(
            source,
            owner=owner,
            method_pattern=method_pattern,
        )
        != re.sub(r"\s+", "", mask_rust_non_code(expected_method))
    )
    _require_exact(
        drifted_manifest_semantics,
        [],
        context="Mesh native runtime manifest semantics",
    )
    feature_contracts: dict[str, dict[str, tuple[str, ...]]] = {
        "crates/mesh-llm-sdk/Cargo.toml": {
            "default": ("client",),
            "serving": (
                "node",
                "dep:anyhow",
                "dep:mesh-llm-embedded-runtime",
                "dep:mesh-llm-runtime-install",
                "mesh-llm-embedded-runtime/dynamic-native-runtime",
                "dep:reqwest",
                "dep:serde",
                "dep:serde_json",
            ),
        },
        "crates/mesh-llm-embedded-runtime/Cargo.toml": {
            "default": (),
            "dynamic-native-runtime": ("mesh-llm-host-runtime/dynamic-native-runtime",),
        },
        "crates/mesh-llm-host-runtime/Cargo.toml": {
            "default": ("web-ui", "dynamic-native-runtime"),
            "dynamic-native-runtime": (
                "mesh-llm-system/dynamic-native-runtime",
                "skippy-runtime/dynamic-native-runtime",
                "skippy-server/dynamic-native-runtime",
            ),
        },
        "crates/mesh-llm-system/Cargo.toml": {
            "skippy-devices": ("dep:skippy-runtime",),
            "dynamic-native-runtime": (
                "skippy-devices",
                "skippy-runtime/dynamic-native-runtime",
            ),
        },
        "crates/skippy-runtime/Cargo.toml": {
            "default": (),
            "dynamic-native-runtime": ("skippy-ffi/dynamic-runtime",),
        },
        "crates/skippy-server/Cargo.toml": {
            "default": (),
            "dynamic-native-runtime": ("skippy-runtime/dynamic-native-runtime",),
        },
        "crates/skippy-ffi/Cargo.toml": {
            "default": ("dynamic-runtime",),
            "dynamic-runtime": ("dep:libloading",),
        },
    }
    for path, expected_features in feature_contracts.items():
        manifest = _decode_toml(payloads[path], context=f"Mesh {path}")
        for feature, expected_dependencies in expected_features.items():
            _require_exact(
                _feature_tuple(manifest, feature, context=f"Mesh {path}"),
                expected_dependencies,
                context=f"Mesh {path} {feature} feature graph",
            )

    llama_cpp_commit = _require_commit(
        payloads["third_party/llama.cpp/upstream.txt"].decode("utf-8").strip(),
        context="mesh-llm llama.cpp source pin",
    )
    abi_entries = re.findall(
        r"(?m)^pub const ABI_VERSION_(MAJOR|MINOR|PATCH): u32 = ([0-9]+);$",
        payloads["crates/skippy-ffi/src/lib.rs"].decode("utf-8"),
    )
    if len(abi_entries) != len(_ABI_COMPONENTS) or {
        name for name, _value in abi_entries
    } != set(_ABI_COMPONENTS):
        msg = "Buzz Mesh Skippy ABI must declare one major, minor, and patch constant"
        raise RuntimeError(msg)
    abi_constants = dict(abi_entries)
    skippy_abi = ".".join(abi_constants[component] for component in _ABI_COMPONENTS)

    skippy_build = payloads["crates/skippy-ffi/build.rs"].decode("utf-8")
    dynamic_return = re.search(
        r'if std::env::var_os\("CARGO_FEATURE_DYNAMIC_RUNTIME"\)\.is_some\(\)\s*'
        r"\{\s*return;\s*\}",
        skippy_build,
    )
    llama_stage_use = skippy_build.find('std::env::var("LLAMA_STAGE_BUILD_DIR")')
    if (
        dynamic_return is None
        or llama_stage_use == -1
        or dynamic_return.end() >= llama_stage_use
    ):
        msg = "Buzz Mesh dynamic runtime no longer bypasses LLAMA_STAGE_BUILD_DIR as audited"
        raise RuntimeError(msg)

    runtime_behavior_checks = {
        "host initialization installs or loads a runtime": (
            "crates/mesh-llm-host-runtime/src/lib.rs",
            r"try_load_installed_native_runtime\(startup_selection\)\.await\?",
        ),
        "host options inherit installer defaults": (
            "crates/mesh-llm-host-runtime/src/system/native_runtime.rs",
            r"fn default_install_options\(\).*?\.\.Default::default\(\)",
        ),
        "installer defaults to checksum-only downloads": (
            "crates/mesh-llm-runtime-install/src/lib.rs",
            r"(?m)^impl\s+Default\s+for\s+NativeRuntimeInstallOptions\s*\{"
            r"(?:(?!^\}).)*?fn\s+default\(\)\s*->\s*Self\s*\{"
            r"(?:(?!^\}).)*?"
            r"verification_policy:\s*NativeRuntimeVerificationPolicy::RequireChecksum,"
            r"(?:(?!^\}).)*?"
            r"allow_download:\s*true,",
        ),
        "manifest options default to the release manifest URL": (
            "crates/mesh-llm-runtime-install/src/lib.rs",
            r"(?m)^impl\s+Default\s+for\s+NativeRuntimeManifestOptions\s*\{"
            r"(?:(?!^\}).)*?fn\s+default\(\)\s*->\s*Self\s*\{"
            r"(?:(?!^\}).)*?allow_default_manifest_url:\s*true,",
        ),
        "installer entrypoint explicitly permits the default manifest URL": (
            "crates/mesh-llm-runtime-install/src/lib.rs",
            r"(?m)^(?:pub(?:\([^)]*\))?\s+)?async\s+fn\s+"
            r"install_native_runtime\s*\([^)]*\)[^\{]*\{"
            r"(?:(?!^\}).)*?NativeRuntimeManifestOptions\s*\{"
            r"(?:(?!^\}).)*?allow_default_manifest_url:\s*true,",
        ),
        "runtime manifest URL keeps its explicit environment hook": (
            "crates/mesh-llm-runtime-install/src/lib.rs",
            r'"MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL"',
        ),
        "signature verification remains unimplemented": (
            "crates/mesh-llm-runtime-install/src/lib.rs",
            r'bail!\("native runtime signature verification is not implemented yet"\)',
        ),
        "runtime bundles have an explicit discovery environment": (
            "crates/mesh-llm-runtime-install/src/discovery.rs",
            r"pub const NATIVE_RUNTIME_BUNDLE_DIR_ENV: &str = "
            r'"MESH_LLM_NATIVE_RUNTIME_BUNDLE_DIR";',
        ),
        "source runtime packaging forces dynamic Metal-capable libraries": (
            "scripts/package-native-runtime.sh",
            r"LLAMA_STAGE_LINK_MODE=dynamic.*?LLAMA_STAGE_BACKEND=",
        ),
    }
    drifted_behavior = [
        name
        for name, (path, pattern) in runtime_behavior_checks.items()
        if re.search(pattern, payloads[path].decode("utf-8"), re.DOTALL) is None
    ]
    _require_exact(
        drifted_behavior,
        [],
        context="Mesh dynamic runtime installer behavior",
    )
    return _MeshSourceContract(
        llama_cpp_commit=llama_cpp_commit,
        skippy_abi=skippy_abi,
    )


def _package_rows(
    lock: dict[str, object],
    name: str,
    *,
    context: str,
) -> tuple[dict[str, object], ...]:
    packages = _require_list(lock.get("package"), context=f"{context} package table")
    return tuple(
        package
        for item in packages
        if (package := _require_object(item, context=f"{context} package entry")).get(
            "name"
        )
        == name
    )


def _validate_locked_package(
    lock: dict[str, object],
    name: str,
    *,
    context: str,
    version: str,
    source: str | None = None,
) -> None:
    rows = _package_rows(lock, name, context=context)
    if len(rows) != 1:
        msg = f"Buzz {context} must lock exactly one {name} package"
        raise RuntimeError(msg)
    _require_exact(rows[0].get("version"), version, context=f"{context} {name} version")
    if source is not None:
        _require_exact(
            rows[0].get("source"), source, context=f"{context} {name} source"
        )


def _locked_package_version(
    lock: dict[str, object],
    name: str,
    *,
    context: str,
) -> str:
    rows = _package_rows(lock, name, context=context)
    if len(rows) != 1:
        msg = f"Buzz {context} must lock exactly one {name} package"
        raise RuntimeError(msg)
    return _require_exact_version(
        _require_string(rows[0], "version", context=f"{context} {name}"),
        context=f"{context} {name} version",
    )


def _sidecars_for_target(script: str, target: str) -> tuple[str, ...]:
    """Interpret the two declarative sidecar lists in the upstream shell script."""
    base_match = re.search(r"(?m)^SIDECARS=\(([^\n]*)\)$", script)
    extra_match = re.search(r"(?m)^\s*SIDECARS\+=\(([^\n]*)\)$", script)
    condition_match = re.search(
        r'(?m)^if \[\[ "\$TARGET" != \*windows\* \]\]; then$',
        script,
    )
    if base_match is None or extra_match is None or condition_match is None:
        msg = "Buzz sidecar bundling script no longer has the audited target gate"
        raise RuntimeError(msg)
    base = tuple(shlex.split(base_match.group(1)))
    extra = tuple(shlex.split(extra_match.group(1)))
    return base if "windows" in target else (*base, *extra)


def updater_enabled(public_key: str | None, endpoint: str | None) -> bool:
    """Mirror build.rs: updater cfg requires two present, non-blank values."""
    normalized = tuple(
        value.strip() if value is not None else "" for value in (public_key, endpoint)
    )
    return all(normalized)


def _validate_updater_gate(build_script: str, desktop_lib: str) -> None:
    env_pipeline = re.compile(
        r"let (?P<binding>updater_(?:public_key|endpoint)) = "
        r'std::env::var\("(?P<env>BUZZ_UPDATER_(?:PUBLIC_KEY|ENDPOINT))"\)\s*'
        r"\.ok\(\)\s*"
        r"\.map\(\|value\| value\.trim\(\)\.to_string\(\)\)\s*"
        r"\.filter\(\|value\| !value\.is_empty\(\)\);",
        re.MULTILINE,
    )
    gates = {
        (match["binding"], match["env"])
        for match in env_pipeline.finditer(build_script)
    }
    _require_exact(
        gates,
        {
            ("updater_public_key", "BUZZ_UPDATER_PUBLIC_KEY"),
            ("updater_endpoint", "BUZZ_UPDATER_ENDPOINT"),
        },
        context="updater environment normalization",
    )
    cfg_gate = re.compile(
        r"if updater_public_key\.is_some\(\)\s*&&\s*updater_endpoint\.is_some\(\)\s*\{\s*"
        r'println!\("cargo:rustc-cfg=buzz_updater_enabled"\);\s*\}',
        re.MULTILINE,
    )
    if cfg_gate.search(build_script) is None:
        msg = "Buzz updater cfg is no longer gated by both normalized variables"
        raise RuntimeError(msg)
    plugin_gate = re.compile(
        r"#\[cfg\(buzz_updater_enabled\)\]\s*"
        r"let builder = if cfg!\(debug_assertions\) \{\s*builder\s*\} else \{\s*"
        r"builder\.plugin\(tauri_plugin_updater::Builder::new\(\)\.build\(\)\)\s*\};",
        re.MULTILINE,
    )
    if plugin_gate.search(desktop_lib) is None:
        msg = "Buzz updater plugin is no longer cfg-gated and release-only"
        raise RuntimeError(msg)


def _validate_sidecar_manifest(
    manifest: dict[str, object],
    *,
    package_name: str,
    binary_name: str,
) -> None:
    package = _require_object(
        manifest.get("package"), context=f"{package_name} package table"
    )
    _require_exact(
        package.get("name"), package_name, context=f"{package_name} package name"
    )
    binaries = _require_list(
        manifest.get("bin"), context=f"{package_name} binary table"
    )
    binary_names = {
        _require_string(
            _require_object(binary, context=f"{package_name} binary entry"),
            "name",
            context=f"{package_name} binary entry",
        )
        for binary in binaries
    }
    if binary_name not in binary_names:
        msg = f"Buzz {package_name} no longer emits the {binary_name} sidecar"
        raise RuntimeError(msg)


def _validate_release_metadata_contract(
    payloads: dict[str, bytes],
    *,
    release_version: str,
) -> _ReleaseMetadataContract:
    root_package = _decode_json(
        payloads[_ROOT_PACKAGE_JSON], context="root package.json"
    )
    desktop_package = _decode_json(
        payloads[_DESKTOP_PACKAGE_JSON],
        context="desktop package.json",
    )
    tauri = _decode_json(payloads[_TAURI_CONFIG], context="Tauri config")
    rust_toolchain = _decode_toml(
        payloads[_RUST_TOOLCHAIN],
        context="Rust toolchain",
    )

    release_version = _require_exact_version(
        release_version,
        context="release version",
    )
    pnpm_version = _require_prefixed_version(
        _require_string(root_package, "packageManager", context="root package.json"),
        "pnpm@",
        context="pnpm package manager",
    )
    toolchain = _require_object(
        rust_toolchain.get("toolchain"),
        context="Rust toolchain table",
    )
    rust_version = _require_exact_version(
        _require_string(toolchain, "channel", context="Rust toolchain table"),
        context="Rust toolchain channel",
    )
    _require_exact(
        toolchain.get("profile"),
        "default",
        context="Rust toolchain profile",
    )
    _require_exact(
        desktop_package.get("version"),
        release_version,
        context="desktop npm version",
    )
    _require_exact(tauri.get("productName"), "Buzz", context="Tauri product name")
    _require_exact(tauri.get("version"), release_version, context="Tauri version")
    _require_exact(tauri.get("identifier"), _APP_ID, context="Tauri identifier")
    bundle = _require_object(tauri.get("bundle"), context="Tauri bundle")
    _require_exact(
        tuple(_require_list(bundle.get("externalBin"), context="Tauri externalBin")),
        _TAURI_SIDECARS,
        context="Tauri externalBin",
    )
    plugins = _require_object(tauri.get("plugins"), context="Tauri plugins")
    updater = _require_object(plugins.get("updater"), context="Tauri updater")
    _require_exact(updater.get("endpoints"), [], context="base updater endpoints")
    return _ReleaseMetadataContract(
        pnpm_version=pnpm_version,
        rust_version=rust_version,
    )


def _validate_cargo_contract(
    payloads: dict[str, bytes],
    *,
    release_version: str,
) -> _CargoContract:
    root_manifest = _decode_toml(
        payloads[_ROOT_MANIFEST], context="root Cargo manifest"
    )
    root_lock = _decode_toml(payloads[_ROOT_LOCK], context="root Cargo lock")
    desktop_manifest = _decode_toml(
        payloads[_DESKTOP_MANIFEST],
        context="desktop Cargo manifest",
    )
    desktop_lock = _decode_toml(payloads[_DESKTOP_LOCK], context="desktop Cargo lock")
    workspace = _require_object(
        root_manifest.get("workspace"), context="root workspace"
    )
    members = set(
        _require_list(workspace.get("members"), context="root workspace members")
    )
    expected_members = {f"crates/{package}" for package, _binary in _SIDECAR_SPECS}
    if not expected_members.issubset(members):
        missing_members = sorted(expected_members.difference(members))
        msg = f"Buzz root workspace is missing sidecar crates: {', '.join(missing_members)}"
        raise RuntimeError(msg)

    desktop_package_table = _require_object(
        desktop_manifest.get("package"),
        context="desktop Cargo package",
    )
    _require_exact(
        desktop_package_table.get("version"),
        release_version,
        context="desktop Cargo version",
    )
    desktop_dependencies = _require_object(
        desktop_manifest.get("dependencies"),
        context="desktop Cargo dependencies",
    )
    if "sherpa-onnx" not in desktop_dependencies:
        msg = "Buzz desktop Cargo dependencies are missing sherpa-onnx"
        raise RuntimeError(msg)
    desktop_features = _require_object(
        desktop_manifest.get("features"),
        context="desktop Cargo features",
    )
    mesh_dependency_names = (
        "mesh-llm-sdk",
        "mesh-llm-host-runtime",
        "mesh-llm-client",
        "mesh-llm-node",
        "mesh-llm-system",
        "mesh-llm-events",
    )
    _require_exact(
        tuple(
            _require_list(
                desktop_features.get("mesh-llm"),
                context="desktop mesh-llm feature",
            )
        ),
        tuple(f"dep:{name}" for name in ("iroh", *mesh_dependency_names)),
        context="desktop mesh-llm feature graph",
    )
    sdk_dependency = _require_object(
        desktop_dependencies.get("mesh-llm-sdk"),
        context="desktop mesh-llm-sdk dependency",
    )
    mesh_version = _require_prefixed_version(
        _require_string(
            sdk_dependency,
            "tag",
            context="desktop mesh-llm-sdk dependency",
        ),
        "v",
        context="desktop Mesh tag",
    )
    mesh_tag = f"v{mesh_version}"
    common_mesh_dependency = {
        "git": "https://github.com/Mesh-LLM/mesh-llm.git",
        "tag": mesh_tag,
        "optional": True,
    }
    expected_mesh_dependencies = {
        "mesh-llm-sdk": common_mesh_dependency
        | {
            "package": "mesh-llm-sdk",
            "default-features": False,
            "features": ["client", "serving"],
        },
        "mesh-llm-host-runtime": common_mesh_dependency
        | {
            "package": "mesh-llm-host-runtime",
            "default-features": False,
            "features": ["dynamic-native-runtime"],
        },
        **{
            name: common_mesh_dependency | {"package": name}
            for name in mesh_dependency_names[2:]
        },
    }
    for dependency_name, expected_dependency in expected_mesh_dependencies.items():
        _require_exact(
            _require_object(
                desktop_dependencies.get(dependency_name),
                context=f"desktop {dependency_name} dependency",
            ),
            expected_dependency,
            context=f"desktop {dependency_name} dependency contract",
        )

    sherpa_versions = {
        _locked_package_version(lock, package, context=context)
        for lock, context in (
            (root_lock, "root lock"),
            (desktop_lock, "desktop lock"),
        )
        for package in ("sherpa-onnx", "sherpa-onnx-sys")
    }
    if len(sherpa_versions) != 1:
        msg = f"Buzz Cargo locks disagree on sherpa-onnx: {sorted(sherpa_versions)!r}"
        raise RuntimeError(msg)
    sherpa_version = next(iter(sherpa_versions))

    mesh_row = _package_rows(desktop_lock, "mesh-llm-sdk", context="desktop lock")
    if len(mesh_row) != 1:
        msg = "Buzz desktop lock must lock exactly one mesh-llm-sdk package"
        raise RuntimeError(msg)
    mesh_source = _require_string(
        mesh_row[0],
        "source",
        context="desktop lock mesh-llm-sdk",
    )
    mesh_source_match = _MESH_LOCK_SOURCE_PATTERN.fullmatch(mesh_source)
    if mesh_source_match is None:
        msg = "Buzz desktop lock Mesh source is not one immutable tagged commit"
        raise RuntimeError(msg)
    mesh_commit = mesh_source_match.group("commit")
    expected_mesh_source = (
        f"git+https://github.com/Mesh-LLM/mesh-llm.git?tag={mesh_tag}#{mesh_commit}"
    )
    _require_exact(
        mesh_source_match.group("tag"),
        mesh_tag,
        context="desktop lock Mesh tag",
    )
    for dependency_name in mesh_dependency_names:
        _validate_locked_package(
            desktop_lock,
            dependency_name,
            context="desktop lock",
            version=mesh_version,
            source=expected_mesh_source,
        )
    return _CargoContract(
        mesh_commit=mesh_commit,
        mesh_version=mesh_version,
        sherpa_version=sherpa_version,
    )


def _validate_sidecar_and_runtime_contract(payloads: dict[str, bytes]) -> None:
    script = payloads[_SIDECAR_SCRIPT].decode("utf-8")
    _require_exact(
        _sidecars_for_target(script, "aarch64-apple-darwin"),
        (*_BASE_BUNDLE_SIDECARS, *_DARWIN_ONLY_SIDECARS),
        context="Darwin sidecar bundle",
    )
    _require_exact(
        _sidecars_for_target(script, "x86_64-pc-windows-msvc"),
        _BASE_BUNDLE_SIDECARS,
        context="Windows sidecar bundle",
    )
    for (package_name, binary_name), path in zip(
        _SIDECAR_SPECS,
        _SIDECAR_MANIFESTS,
        strict=True,
    ):
        _validate_sidecar_manifest(
            _decode_toml(payloads[path], context=f"{package_name} Cargo manifest"),
            package_name=package_name,
            binary_name=binary_name,
        )

    _validate_updater_gate(
        payloads[_BUILD_SCRIPT].decode("utf-8"),
        payloads[_DESKTOP_LIB].decode("utf-8"),
    )
    runtime_entrypoint = payloads[_MESH_RUNTIME_ENTRYPOINT].decode("utf-8")
    _require_exact(
        re.search(
            r"async fn initialize_mesh_native_runtime\(\).*?"
            r"mesh_llm_host_runtime::initialize_host_runtime\(\)\s*\.await",
            runtime_entrypoint,
            re.DOTALL,
        )
        is not None,
        expected=True,
        context="Buzz first-use Mesh runtime initialization",
    )


def _validate_source_contract(
    payloads: dict[str, bytes],
    *,
    release_version: str,
) -> _BuzzReleaseContract:
    """Validate one immutable release's build topology before hashing it."""
    missing = sorted(set(_BUZZ_SOURCE_PATHS).difference(payloads))
    if missing:
        msg = f"Buzz source audit is missing paths: {', '.join(missing)}"
        raise RuntimeError(msg)
    release_contract = _validate_release_metadata_contract(
        payloads,
        release_version=release_version,
    )
    cargo_contract = _validate_cargo_contract(
        payloads,
        release_version=release_version,
    )
    _validate_sidecar_and_runtime_contract(payloads)
    return _BuzzReleaseContract(
        mesh_commit=cargo_contract.mesh_commit,
        mesh_version=cargo_contract.mesh_version,
        pnpm_version=release_contract.pnpm_version,
        rust_version=release_contract.rust_version,
        sherpa_version=cargo_contract.sherpa_version,
    )


def _validate_onnx_source_contract(
    payloads: dict[str, bytes],
    *,
    expected_version: str,
) -> None:
    """Validate the exact ONNX Runtime tree used by the native foundation."""
    _validate_digest_contract(
        payloads,
        BuzzUpdater.get_compatibility_source_digest_contract("onnxruntime"),
        context="ONNX Runtime source contract",
    )
    _require_exact(
        payloads["VERSION_NUMBER"].decode("utf-8").strip(),
        expected_version,
        context="ONNX Runtime version",
    )


def _validate_sherpa_source_contract(
    payloads: dict[str, bytes],
    *,
    expected_version: str,
) -> None:
    """Validate the exact sherpa-onnx tree selected by both Buzz Cargo locks."""
    _validate_digest_contract(
        payloads,
        BuzzUpdater.get_compatibility_source_digest_contract("sherpaOnnx"),
        context="sherpa-onnx source contract",
    )
    version_matches = re.findall(
        r'set\(SHERPA_ONNX_VERSION\s+"([^"]+)"\)',
        payloads["CMakeLists.txt"].decode("utf-8"),
    )
    _require_exact(
        version_matches,
        [expected_version],
        context="sherpa-onnx version",
    )
    sys_manifest = _decode_toml(
        payloads["sherpa-onnx/rust/sherpa-onnx-sys/Cargo.toml"],
        context="sherpa-onnx-sys Cargo manifest",
    )
    wrapper_manifest = _decode_toml(
        payloads["sherpa-onnx/rust/sherpa-onnx/Cargo.toml"],
        context="sherpa-onnx Cargo manifest",
    )
    wrapper_dependencies = _require_object(
        wrapper_manifest.get("dependencies"),
        context="sherpa-onnx Cargo dependencies",
    )
    sys_dependency = _require_object(
        wrapper_dependencies.get("sherpa-onnx-sys"),
        context="sherpa-onnx-sys wrapper dependency",
    )
    build_source = payloads["sherpa-onnx/rust/sherpa-onnx-sys/build.rs"].decode("utf-8")
    static_libraries_match = re.search(
        r"const\s+SHERPA_ONNX_STATIC_LIBS\s*:\s*&\[\s*&str\s*\]\s*=\s*&\["
        r"(?P<libraries>.*?)\]\s*;",
        build_source,
        re.DOTALL,
    )
    emitted_static_libraries = (
        tuple(re.findall(r'"([^"\n]+)"', static_libraries_match["libraries"]))
        if static_libraries_match is not None
        else ()
    )
    rust_build_semantics = {
        "sys defaults to static": (
            _feature_tuple(
                sys_manifest,
                "default",
                context="sherpa-onnx-sys",
            )
            == ("static",)
        ),
        "wrapper defaults to static": (
            _feature_tuple(
                wrapper_manifest,
                "default",
                context="sherpa-onnx",
            )
            == ("static",)
        ),
        "wrapper forwards static": (
            _feature_tuple(
                wrapper_manifest,
                "static",
                context="sherpa-onnx",
            )
            == ("sherpa-onnx-sys/static",)
        ),
        "wrapper forwards shared": (
            _feature_tuple(
                wrapper_manifest,
                "shared",
                context="sherpa-onnx",
            )
            == ("sherpa-onnx-sys/shared",)
        ),
        "wrapper disables implicit sys defaults": (
            sys_dependency.get("version") == expected_version
            and sys_dependency.get("default-features") is False
        ),
        "docs builds return before native setup": (
            re.search(
                r"fn\s+try_main\s*\(\s*\)\s*->\s*"
                r"Result\s*<\s*\(\s*\)\s*,\s*DynError\s*>\s*\{\s*"
                r"(?:(?![{}]).)*?"
                r"if\s+env::var_os\(\"DOCS_RS\"\)\.is_some\(\)\s*\{\s*"
                r"(?://[^\n]*\n\s*)*return\s+Ok\(\(\)\)\s*;\s*\}",
                build_source,
                re.DOTALL,
            )
            is not None
        ),
        "local library directory short-circuits acquisition": (
            re.search(
                r"(?m)^fn\s+resolve_lib_dir\s*\([^{}]*\)\s*->\s*"
                r"Result\s*<\s*PathBuf\s*,\s*DynError\s*>\s*\{\s*"
                r"if\s+let\s+Some\(path\)\s*=\s*"
                r"env::var_os\(\"SHERPA_ONNX_LIB_DIR\"\)\s*\{"
                r"(?:(?!^\}).)*?if\s+!path\.is_dir\(\)\s*\{"
                r"(?:(?!^\}).)*?return\s+Err\(.*?\);\s*\}"
                r"(?:(?!^\}).)*?return\s+Ok\(path\)\s*;\s*\}\s*"
                r"download_prebuilt_libs\(\s*link_mode\s*,\s*target_os\s*,\s*"
                r"target_arch\s*\)",
                build_source,
                re.DOTALL,
            )
            is not None
        ),
        "local archive precedes network fallback": (
            re.search(
                r"if\s+let\s+Some\(local_archive_dir\)\s*=\s*"
                r"env::var_os\(\"SHERPA_ONNX_ARCHIVE_DIR\"\)\s*\{.*?"
                r"copy_file_atomically\(\s*&local_archive_path\s*,\s*"
                r"&archive_path\s*\)\?\s*;\s*\}\s*else\s*\{.*?"
                r"ureq::builder\(\)",
                build_source,
                re.DOTALL,
            )
            is not None
        ),
        "static and shared feature gates remain enabled predicates": (
            re.search(
                r"env::var_os\(\"CARGO_FEATURE_STATIC\"\)\.is_some\(\)",
                build_source,
            )
            is not None
            and re.search(
                r"env::var_os\(\"CARGO_FEATURE_SHARED\"\)\.is_some\(\)",
                build_source,
            )
            is not None
        ),
        "static link order": (
            emitted_static_libraries == _SHERPA_STATIC_LINK_LIBRARIES
            and re.search(
                r"fn\s+emit_static_link_directives\s*\(\s*target_os\s*:\s*"
                r"&str\s*\)\s*\{\s*"
                r"for\s+lib\s+in\s+SHERPA_ONNX_STATIC_LIBS\s*\{\s*"
                r"println!\(\"cargo:rustc-link-lib=static=\{lib\}\"\)\s*;\s*\}",
                build_source,
            )
            is not None
        ),
        "macOS C++ runtime and Foundation framework": (
            re.search(
                r'"macos"\s*=>\s*\{.*?'
                r'println!\("cargo:rustc-link-lib=dylib=c\+\+"\)\s*;.*?'
                r'println!\("cargo:rustc-link-lib=framework=Foundation"\)\s*;',
                build_source,
                re.DOTALL,
            )
            is not None
        ),
    }
    _require_exact(
        [name for name, matches in rust_build_semantics.items() if not matches],
        [],
        context="sherpa-onnx Rust build semantics",
    )


@register_updater
class BuzzUpdater(GitHubReleaseUpdater):
    """Resolve Buzz releases against the explicitly supported native foundation."""

    name = "buzz"
    GITHUB_OWNER = "block"
    GITHUB_REPO = "buzz"
    TAG_PREFIX = "desktop-v"
    DARWIN_PLATFORM: ClassVar[str] = "aarch64-darwin"
    supported_platforms = (DARWIN_PLATFORM,)
    generated_artifact_files = ("native-lock.json",)
    compatibility_pin_rationale = (
        "The package-local static ONNX Runtime recipe is deliberately reviewed "
        "against one upstream release independently of Buzz's release manifests."
    )
    compatibility_pins: ClassVar[dict[str, str]] = {"onnxruntimeVersion": "1.27.0"}
    compatibility_source_digest_rationale = (
        "Buzz's source-built native foundation depends on reviewed build, loader, "
        "and archive semantics that cannot be reduced to release-version metadata."
    )
    compatibility_source_digests: ClassVar[dict[str, dict[str, str]]] = {
        "meshLlm": _MESH_SOURCE_DIGESTS,
        "onnxruntime": _ONNX_SOURCE_DIGESTS,
        "sherpaOnnx": _SHERPA_SOURCE_DIGESTS,
    }
    derivation_validations = (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=(DARWIN_PLATFORM,),
            mode="build",
        ),
    )

    @staticmethod
    async def _resolve_tag_commit(
        session: aiohttp.ClientSession,
        *,
        owner: str,
        repo: str,
        tag: str,
        config: UpdateConfig,
    ) -> str:
        payload = await fetch_github_api(
            session,
            f"repos/{owner}/{repo}/commits/{urllib.parse.quote(tag, safe='')}",
            config=config,
        )
        commit = _require_string(
            _require_object(payload, context=f"{owner}/{repo} commit response"),
            "sha",
            context=f"{owner}/{repo} commit response",
        )
        return _require_commit(commit, context=f"{owner}/{repo} tag {tag}")

    @staticmethod
    def _archive_url(owner: str, repo: str, commit: str) -> str:
        return f"https://github.com/{owner}/{repo}/archive/{commit}.tar.gz"

    @staticmethod
    def _fetchurl_expr(url: str) -> str:
        expression = FunctionCall(
            name=identifier_attr_path("pkgs", "fetchurl"),
            argument=AttributeSet(
                values=[
                    Binding(name="url", value=StringPrimitive(value=url)),
                    Binding(
                        name="hash",
                        value=identifier_attr_path("pkgs", "lib", "fakeHash"),
                    ),
                ]
            ),
        )
        return compact_nix_expr(expression.rebuild())

    @staticmethod
    def _pnpm_url(version: str) -> str:
        return f"https://registry.npmjs.org/pnpm/-/pnpm-{version}.tgz"

    @staticmethod
    def _native_lock_expr(native_lock: dict[str, object]) -> FunctionCall:
        """Embed one prospective lock without depending on the checked-in file."""
        return FunctionCall(
            name=identifier_attr_path("builtins", "fromJSON"),
            argument=StringPrimitive(
                value=json.dumps(
                    native_lock,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ),
        )

    @classmethod
    def _native_lock_payload(
        cls,
        metadata: dict[str, str],
        hashes: dict[str, str],
    ) -> dict[str, object]:
        pnpm_url = cls._pnpm_url(metadata["pnpmVersion"])
        return {
            "schemaVersion": 1,
            "buzz": {
                "version": metadata["version"],
                "commit": metadata["commit"],
                "rustVersion": metadata["rustVersion"],
            },
            "onnxruntime": {
                "version": metadata["onnxruntimeVersion"],
                "commit": metadata["onnxruntimeCommit"],
            },
            "meshLlm": {
                "version": metadata["meshLlmVersion"],
                "commit": metadata["meshLlmCommit"],
                "skippyAbi": metadata["skippyAbi"],
            },
            "llamaCpp": {
                "commit": metadata["llamaCppCommit"],
            },
            "pnpm": {
                "version": metadata["pnpmVersion"],
                "url": pnpm_url,
                "hash": hashes[pnpm_url],
            },
            "sherpaOnnx": {
                "version": metadata["sherpaOnnxVersion"],
                "commit": metadata["sherpaOnnxCommit"],
                "dependencyOrder": list(_SHERPA_FETCHCONTENT_ORDER),
                "dependencies": {
                    name: {**dependency, "hash": hashes[dependency["url"]]}
                    for name, dependency in _SHERPA_FETCHCONTENT_LOCK.items()
                },
            },
        }

    @classmethod
    def _required_metadata(cls, info: VersionInfo) -> dict[str, str]:
        metadata = metadata_as_mapping(info.metadata, context="Buzz release metadata")
        result = {
            key: _require_string(metadata, key, context="release metadata")
            for key in (
                "buzzUrl",
                "commit",
                "llamaCppCommit",
                "llamaCppUrl",
                "meshLlmCommit",
                "meshLlmUrl",
                "meshLlmVersion",
                "onnxruntimeCommit",
                "onnxruntimeUrl",
                "onnxruntimeVersion",
                "pnpmVersion",
                "rustVersion",
                "sherpaOnnxCommit",
                "sherpaOnnxUrl",
                "sherpaOnnxVersion",
                "skippyAbi",
                "tag",
            )
        }
        version = _require_exact_version(info.version, context="release version")
        result["version"] = version
        for key in (
            "meshLlmVersion",
            "onnxruntimeVersion",
            "pnpmVersion",
            "rustVersion",
            "sherpaOnnxVersion",
            "skippyAbi",
        ):
            _require_exact_version(result[key], context=key)
        for key in (
            "commit",
            "llamaCppCommit",
            "meshLlmCommit",
            "onnxruntimeCommit",
            "sherpaOnnxCommit",
        ):
            _require_commit(result[key], context=key)
        _require_exact(
            result["tag"],
            f"{cls.TAG_PREFIX}{version}",
            context="release tag",
        )
        _require_exact(
            result["onnxruntimeVersion"],
            cls.get_compatibility_pin("onnxruntimeVersion"),
            context="supported onnxruntimeVersion",
        )
        _require_exact(
            result["buzzUrl"],
            cls._archive_url(cls.GITHUB_OWNER, cls.GITHUB_REPO, result["commit"]),
            context="Buzz source URL",
        )
        _require_exact(
            result["onnxruntimeUrl"],
            cls._archive_url(
                "microsoft",
                "onnxruntime",
                result["onnxruntimeCommit"],
            ),
            context="ONNX Runtime source URL",
        )
        _require_exact(
            result["sherpaOnnxUrl"],
            cls._archive_url(
                "k2-fsa",
                "sherpa-onnx",
                result["sherpaOnnxCommit"],
            ),
            context="sherpa-onnx source URL",
        )
        _require_exact(
            result["meshLlmUrl"],
            cls._archive_url("Mesh-LLM", "mesh-llm", result["meshLlmCommit"]),
            context="Mesh source URL",
        )
        _require_exact(
            result["llamaCppUrl"],
            cls._archive_url("ggml-org", "llama.cpp", result["llamaCppCommit"]),
            context="llama.cpp source URL",
        )
        return result

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve the latest release against the supported native foundation."""
        release = await self._fetch_latest_release_payload(session)
        tag = self._release_tag_from_payload(release)
        version = _require_exact_version(
            self._normalize_release_version(tag),
            context="release version",
        )
        commit = await self._resolve_tag_commit(
            session,
            owner=self.GITHUB_OWNER,
            repo=self.GITHUB_REPO,
            tag=tag,
            config=self.config,
        )

        source_payloads = await asyncio.gather(
            *(
                fetch_url(
                    session,
                    github_raw_url(
                        self.GITHUB_OWNER,
                        self.GITHUB_REPO,
                        commit,
                        path,
                    ),
                    config=self.config,
                )
                for path in _BUZZ_SOURCE_PATHS
            )
        )
        release_contract = _validate_source_contract(
            dict(zip(_BUZZ_SOURCE_PATHS, source_payloads, strict=True)),
            release_version=version,
        )
        onnxruntime_version = self.get_compatibility_pin("onnxruntimeVersion")

        mesh_commit, sherpa_commit, onnxruntime_commit = await asyncio.gather(
            self._resolve_tag_commit(
                session,
                owner="Mesh-LLM",
                repo="mesh-llm",
                tag=f"v{release_contract.mesh_version}",
                config=self.config,
            ),
            self._resolve_tag_commit(
                session,
                owner="k2-fsa",
                repo="sherpa-onnx",
                tag=f"v{release_contract.sherpa_version}",
                config=self.config,
            ),
            self._resolve_tag_commit(
                session,
                owner="microsoft",
                repo="onnxruntime",
                tag=f"v{onnxruntime_version}",
                config=self.config,
            ),
        )
        _require_exact(
            mesh_commit,
            release_contract.mesh_commit,
            context="Mesh tag commit",
        )
        mesh_source_paths = tuple(
            self.get_compatibility_source_digest_contract("meshLlm")
        )
        onnx_source_paths = tuple(
            self.get_compatibility_source_digest_contract("onnxruntime")
        )
        sherpa_source_paths = tuple(
            self.get_compatibility_source_digest_contract("sherpaOnnx")
        )
        mesh_source_payloads = await asyncio.gather(
            *(
                fetch_url(
                    session,
                    github_raw_url(
                        "Mesh-LLM",
                        "mesh-llm",
                        mesh_commit,
                        path,
                    ),
                    config=self.config,
                )
                for path in mesh_source_paths
            )
        )
        onnx_source_payloads = await asyncio.gather(
            *(
                fetch_url(
                    session,
                    github_raw_url(
                        "microsoft",
                        "onnxruntime",
                        onnxruntime_commit,
                        path,
                    ),
                    config=self.config,
                )
                for path in onnx_source_paths
            )
        )
        sherpa_source_payloads = await asyncio.gather(
            *(
                fetch_url(
                    session,
                    github_raw_url(
                        "k2-fsa",
                        "sherpa-onnx",
                        sherpa_commit,
                        path,
                    ),
                    config=self.config,
                )
                for path in sherpa_source_paths
            )
        )
        mesh_contract = _validate_mesh_source_contract(
            dict(zip(mesh_source_paths, mesh_source_payloads, strict=True)),
        )
        _validate_onnx_source_contract(
            dict(zip(onnx_source_paths, onnx_source_payloads, strict=True)),
            expected_version=onnxruntime_version,
        )
        _validate_sherpa_source_contract(
            dict(zip(sherpa_source_paths, sherpa_source_payloads, strict=True)),
            expected_version=release_contract.sherpa_version,
        )
        return VersionInfo(
            version=version,
            metadata={
                "buzzUrl": self._archive_url(
                    self.GITHUB_OWNER,
                    self.GITHUB_REPO,
                    commit,
                ),
                "commit": commit,
                "llamaCppCommit": mesh_contract.llama_cpp_commit,
                "llamaCppUrl": self._archive_url(
                    "ggml-org",
                    "llama.cpp",
                    mesh_contract.llama_cpp_commit,
                ),
                "meshLlmCommit": mesh_commit,
                "meshLlmUrl": self._archive_url(
                    "Mesh-LLM",
                    "mesh-llm",
                    mesh_commit,
                ),
                "meshLlmVersion": release_contract.mesh_version,
                "onnxruntimeCommit": onnxruntime_commit,
                "onnxruntimeUrl": self._archive_url(
                    "microsoft",
                    "onnxruntime",
                    onnxruntime_commit,
                ),
                "onnxruntimeVersion": onnxruntime_version,
                "pnpmVersion": release_contract.pnpm_version,
                "rustVersion": release_contract.rust_version,
                "sherpaOnnxCommit": sherpa_commit,
                "sherpaOnnxUrl": self._archive_url(
                    "k2-fsa",
                    "sherpa-onnx",
                    sherpa_commit,
                ),
                "sherpaOnnxVersion": release_contract.sherpa_version,
                "skippyAbi": mesh_contract.skippy_abi,
                "tag": tag,
            },
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Never skip the dual-lock/source audit on metadata equality."""
        _ = (context, info)
        return False

    @classmethod
    def _src_expr(cls, commit: str) -> str:
        return _build_fetch_from_github_expr(
            cls.GITHUB_OWNER,
            cls.GITHUB_REPO,
            rev=commit,
            fetch_submodules=False,
        )

    def _source_override(
        self,
        info: VersionInfo,
        *,
        src_hash: str,
        onnx_src_hash: str,
        sherpa_src_hash: str,
        mesh_src_hash: str,
        llama_src_hash: str,
        npm_deps_hash: str,
        root_cargo_hash: str,
        desktop_cargo_hash: str,
    ) -> SourceEntry:
        metadata = self._required_metadata(info)
        buzz_url = metadata["buzzUrl"]
        llama_cpp_url = metadata["llamaCppUrl"]
        mesh_llm_url = metadata["meshLlmUrl"]
        onnxruntime_url = metadata["onnxruntimeUrl"]
        sherpa_onnx_url = metadata["sherpaOnnxUrl"]
        return SourceEntry(
            version=info.version,
            commit=metadata["commit"],
            hashes=HashCollection.from_value([
                HashEntry.create("srcHash", src_hash, url=buzz_url),
                HashEntry.create("srcHash", onnx_src_hash, url=onnxruntime_url),
                HashEntry.create("srcHash", sherpa_src_hash, url=sherpa_onnx_url),
                HashEntry.create("srcHash", mesh_src_hash, url=mesh_llm_url),
                HashEntry.create("srcHash", llama_src_hash, url=llama_cpp_url),
                HashEntry.create("npmDepsHash", npm_deps_hash, url=buzz_url),
                HashEntry.create("vendorHash", root_cargo_hash, url=buzz_url),
                HashEntry.create("cargoHash", desktop_cargo_hash, url=buzz_url),
            ]),
            urls={
                "buzz": buzz_url,
                "llamaCpp": llama_cpp_url,
                "meshLlm": mesh_llm_url,
                "onnxruntime": onnxruntime_url,
                "sherpaOnnx": sherpa_onnx_url,
            },
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Hash all five exact sources plus Buzz's three lock closures."""
        _ = (session, context)
        metadata = self._required_metadata(info)
        commit = metadata["commit"]
        buzz_url = metadata["buzzUrl"]
        llama_cpp_url = metadata["llamaCppUrl"]
        mesh_llm_url = metadata["meshLlmUrl"]
        onnxruntime_url = metadata["onnxruntimeUrl"]
        sherpa_onnx_url = metadata["sherpaOnnxUrl"]
        fake_hash = self.config.fake_hash
        package_file = "packages/buzz/package.nix"

        def override(
            resolved: dict[_HashIdentity, str],
            *,
            npm: str,
            root: str,
            desktop: str,
        ) -> dict[str, SourceEntry]:
            return {
                self.name: self._source_override(
                    info,
                    src_hash=resolved[("srcHash", buzz_url)],
                    onnx_src_hash=resolved[("srcHash", onnxruntime_url)],
                    sherpa_src_hash=resolved[("srcHash", sherpa_onnx_url)],
                    mesh_src_hash=resolved[("srcHash", mesh_llm_url)],
                    llama_src_hash=resolved[("srcHash", llama_cpp_url)],
                    npm_deps_hash=npm,
                    root_cargo_hash=root,
                    desktop_cargo_hash=desktop,
                )
            }

        source_requests = (
            _HashRequest(
                hash_type="srcHash",
                url=buzz_url,
                error="Missing Buzz srcHash output",
                expr=lambda _resolved: self._src_expr(commit),
            ),
            _HashRequest(
                hash_type="srcHash",
                url=onnxruntime_url,
                error="Missing ONNX Runtime srcHash output",
                expr=lambda _resolved: _build_fetch_from_github_expr(
                    "microsoft",
                    "onnxruntime",
                    rev=metadata["onnxruntimeCommit"],
                    fetch_submodules=False,
                ),
            ),
            _HashRequest(
                hash_type="srcHash",
                url=sherpa_onnx_url,
                error="Missing sherpa-onnx srcHash output",
                expr=lambda _resolved: _build_fetch_from_github_expr(
                    "k2-fsa",
                    "sherpa-onnx",
                    rev=metadata["sherpaOnnxCommit"],
                    fetch_submodules=False,
                ),
            ),
            _HashRequest(
                hash_type="srcHash",
                url=mesh_llm_url,
                error="Missing Mesh srcHash output",
                expr=lambda _resolved: _build_fetch_from_github_expr(
                    "Mesh-LLM",
                    "mesh-llm",
                    rev=metadata["meshLlmCommit"],
                    fetch_submodules=False,
                ),
            ),
            _HashRequest(
                hash_type="srcHash",
                url=llama_cpp_url,
                error="Missing llama.cpp srcHash output",
                expr=lambda _resolved: _build_fetch_from_github_expr(
                    "ggml-org",
                    "llama.cpp",
                    rev=metadata["llamaCppCommit"],
                    fetch_submodules=False,
                ),
            ),
        )
        resolved: dict[_HashIdentity, str] = {}
        entries: list[HashEntry] = []

        async def hash_requests(requests: tuple[_HashRequest, ...]) -> EventStream:
            for request in requests:
                drain = ValueDrain[str]()
                async for event in drain_value_events(
                    update_nix.compute_fixed_output_hash(
                        self.name,
                        request.expr(resolved),
                        isolate_by_drv_hash=True,
                        config=self.config,
                    ),
                    drain,
                    parse=expect_str,
                ):
                    yield event
                value = require_value(drain, request.error)
                identity = (request.hash_type, request.url)
                resolved[identity] = value
                entries.append(
                    HashEntry.create(request.hash_type, value, url=request.url),
                )

        async for event in hash_requests(source_requests):
            yield event

        native_hashes: dict[str, str] = {}
        native_sources = (
            {
                "url": self._pnpm_url(metadata["pnpmVersion"]),
            },
            *_SHERPA_FETCHCONTENT_LOCK.values(),
        )
        for native_source in native_sources:
            url = native_source["url"]
            drain = ValueDrain[str]()
            async for event in drain_value_events(
                update_nix.compute_fixed_output_hash(
                    self.name,
                    self._fetchurl_expr(url),
                    config=self.config,
                ),
                drain,
                parse=expect_str,
            ):
                yield event
            native_hashes[url] = require_value(
                drain,
                f"Missing native lock hash output for {url}",
            )

        prospective_native_lock = self._native_lock_payload(metadata, native_hashes)
        package_args = {
            "nativeLock": self._native_lock_expr(prospective_native_lock),
        }
        dependency_requests = (
            _HashRequest(
                hash_type="npmDepsHash",
                url=buzz_url,
                error="Missing Buzz npmDepsHash output",
                expr=lambda resolved: _build_repo_package_attr_expr(
                    package_file,
                    ".pnpmDeps",
                    system=self.DARWIN_PLATFORM,
                    package_args=package_args,
                    source_overrides=override(
                        resolved,
                        npm=fake_hash,
                        root=fake_hash,
                        desktop=fake_hash,
                    ),
                ),
            ),
            _HashRequest(
                hash_type="vendorHash",
                url=buzz_url,
                error="Missing Buzz root vendorHash output",
                expr=lambda resolved: _build_repo_package_attr_expr(
                    package_file,
                    ".rootCargoDeps",
                    system=self.DARWIN_PLATFORM,
                    package_args=package_args,
                    source_overrides=override(
                        resolved,
                        npm=resolved[("npmDepsHash", buzz_url)],
                        root=fake_hash,
                        desktop=fake_hash,
                    ),
                ),
            ),
            _HashRequest(
                hash_type="cargoHash",
                url=buzz_url,
                error="Missing Buzz desktop cargoHash output",
                expr=lambda resolved: _build_repo_package_attr_expr(
                    package_file,
                    ".desktopCargoDeps",
                    system=self.DARWIN_PLATFORM,
                    package_args=package_args,
                    source_overrides=override(
                        resolved,
                        npm=resolved[("npmDepsHash", buzz_url)],
                        root=resolved[("vendorHash", buzz_url)],
                        desktop=fake_hash,
                    ),
                ),
            ),
        )
        async for event in hash_requests(dependency_requests):
            yield event

        package_dir = updater_dir_for(self.name)
        if package_dir is None:
            msg = f"Package directory not found for {self.name}"
            raise RuntimeError(msg)
        yield UpdateEvent.artifact(
            self.name,
            GeneratedArtifact.json(
                package_dir / self.generated_artifact_files[0],
                prospective_native_lock,
            ),
        )
        yield UpdateEvent.value(self.name, entries)

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist only the complete, URL-keyed eight-entry source closure."""
        metadata = self._required_metadata(info)
        collection = HashCollection.from_value(hashes)
        entries = collection.entries
        if entries is None:
            msg = "Buzz updater requires structured source hash entries"
            raise TypeError(msg)
        expected = {
            ("srcHash", metadata["buzzUrl"]),
            ("srcHash", metadata["onnxruntimeUrl"]),
            ("srcHash", metadata["sherpaOnnxUrl"]),
            ("srcHash", metadata["meshLlmUrl"]),
            ("srcHash", metadata["llamaCppUrl"]),
            ("npmDepsHash", metadata["buzzUrl"]),
            ("vendorHash", metadata["buzzUrl"]),
            ("cargoHash", metadata["buzzUrl"]),
        }
        actual = {(entry.hash_type, entry.url) for entry in entries}
        if actual != expected or len(entries) != len(expected):
            msg = f"Buzz updater expected exact closure keys {expected}, got {actual}"
            raise RuntimeError(msg)
        return SourceEntry(
            version=info.version,
            commit=metadata["commit"],
            hashes=collection,
            pins=self.source_pins_for(info),
            urls={
                "buzz": metadata["buzzUrl"],
                "llamaCpp": metadata["llamaCppUrl"],
                "meshLlm": metadata["meshLlmUrl"],
                "onnxruntime": metadata["onnxruntimeUrl"],
                "sherpaOnnx": metadata["sherpaOnnxUrl"],
            },
        )
