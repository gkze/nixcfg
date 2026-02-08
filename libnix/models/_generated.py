"""Auto-generated Pydantic models from Nix JSON schemas.

DO NOT EDIT MANUALLY. Regenerate with:
    python -m libnix.schemas._codegen
"""

from __future__ import annotations

from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field, RootModel
from typing import Annotated, Any, Literal

# === hash-v1 ===


class Hash(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="A cryptographic hash value used throughout Nix for content addressing and integrity verification.\n\nThis schema describes the JSON representation of Nix's `Hash` type as an [SRI](https://developer.mozilla.org/en-US/docs/Web/Security/Subresource_Integrity) string.\n",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="Hash",
        ),
    ]


class Algorithm(StrEnum):
    BLAKE3 = "blake3"
    MD5 = "md5"
    SHA1 = "sha1"
    SHA256 = "sha256"
    SHA512 = "sha512"


# === store-path-v1 ===


class StorePath(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description='A [store path](@docroot@/store/store-path.md) identifying a store object.\n\nThis schema describes the JSON representation of store paths as used in various Nix JSON APIs.\n\n> **Warning**\n>\n> This JSON format is currently\n> [**experimental**](@docroot@/development/experimental-features.md#xp-feature-nix-command)\n> and subject to change.\n\n## Format\n\nStore paths in JSON are represented as strings containing just the hash and name portion, without the store directory prefix.\n\nFor example: `"g1w7hy3qg1w7hy3qg1w7hy3qg1w7hy3q-foo.drv"`\n\n(If the store dir is `/nix/store`, then this corresponds to the path `/nix/store/g1w7hy3qg1w7hy3qg1w7hy3qg1w7hy3q-foo.drv`.)\n\n## Structure\n\nThe format follows this pattern: `${digest}-${name}`\n\n- **hash**: Digest rendered in [Nix32](@docroot@/protocols/nix32.md), a variant of base-32 (20 hash bytes become 32 ASCII characters)\n- **name**: The package name and optional version/suffix information\n',
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Store Path",
        ),
    ]


# === content-address-v1 ===


class Method(StrEnum):
    FLAT = "flat"
    NAR = "nar"
    TEXT = "text"
    GIT = "git"


class ContentAddress(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    method: Annotated[
        Method,
        Field(
            description="A string representing the [method](@docroot@/store/store-object/content-address.md) of content addressing that is chosen.\n\nValid method strings are:\n\n- [`flat`](@docroot@/store/store-object/content-address.md#method-flat) (provided the contents are a single file)\n- [`nar`](@docroot@/store/store-object/content-address.md#method-nix-archive)\n- [`text`](@docroot@/store/store-object/content-address.md#method-text)\n- [`git`](@docroot@/store/store-object/content-address.md#method-git)\n",
            title="Content-Addressing Method",
        ),
    ]
    hash: Annotated[
        str,
        Field(
            description="This would be the content-address itself.\n\nFor all current methods, this is just a content address of the file system object of the store object, [as described in the store chapter](@docroot@/store/file-system-object/content-address.md), and not of the store object as a whole.\nIn particular, the references of the store object are *not* taken into account with this hash (and currently-supported methods).\n",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="Content Address",
        ),
    ]


# === file-system-object-v1 ===


class Type(StrEnum):
    REGULAR = "regular"
    SYMLINK = "symlink"
    DIRECTORY = "directory"


class FileSystemObject1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["regular"] = "regular"
    contents: Annotated[str, Field(description="File contents")]
    executable: Annotated[
        bool | None, Field(description="Whether the file is executable.")
    ] = False


class FileSystemObject3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["symlink"] = "symlink"
    target: Annotated[str, Field(description="Target path of the symlink.")]


class Regular(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["regular"] = "regular"
    contents: Annotated[str, Field(description="File contents")]
    executable: Annotated[
        bool | None, Field(description="Whether the file is executable.")
    ] = False


class Symlink(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["symlink"] = "symlink"
    target: Annotated[str, Field(description="Target path of the symlink.")]


class FileSystemObject2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["directory"] = "directory"
    entries: Annotated[
        dict[str, FileSystemObject],
        Field(
            description="Map of names to nested file system objects (for type=directory)\n"
        ),
    ]


class Directory(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["directory"] = "directory"
    entries: Annotated[
        dict[str, FileSystemObject],
        Field(
            description="Map of names to nested file system objects (for type=directory)\n"
        ),
    ]


class FileSystemObject(
    RootModel[FileSystemObject1 | FileSystemObject2 | FileSystemObject3]
):
    root: Annotated[
        FileSystemObject1 | FileSystemObject2 | FileSystemObject3,
        Field(
            description="This schema describes the JSON representation of Nix's [File System Object](@docroot@/store/file-system-object.md).\n\nThe schema is recursive because file system objects contain other file system objects.\n",
            title="File System Object",
        ),
    ]


FileSystemObject2.model_rebuild()
Directory.model_rebuild()
# === build-trace-entry-v2 ===


class BuildTraceEntry(BaseModel):
    id: Annotated[
        str,
        Field(
            description='Unique identifier for the derivation output that was built.\n\nFormat: `{hash-quotient-drv}!{output-name}`\n\n- **hash-quotient-drv**: SHA-256 [hash of the quotient derivation](@docroot@/store/derivation/outputs/input-address.md#hash-quotient-drv).\n  Begins with `sha256:`.\n\n- **output-name**: Name of the specific output (e.g., "out", "dev", "doc")\n\nExample: `"sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad!foo"`\n',
            pattern="^sha256:[0-9a-f]{64}![a-zA-Z_][a-zA-Z0-9_-]*$",
            title="Derivation Output ID",
        ),
    ]
    outPath: Annotated[
        dict[str, dict[str, Any]],
        Field(
            description="The path to the store object that resulted from building this derivation for the given output name.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Output Store Path",
        ),
    ]
    signatures: Annotated[
        list[str],
        Field(
            description="A set of cryptographic signatures attesting to the authenticity of this build trace entry.\n",
            title="Build Signatures",
        ),
    ]


class Key(BaseModel):
    id: Annotated[
        str,
        Field(
            description='Unique identifier for the derivation output that was built.\n\nFormat: `{hash-quotient-drv}!{output-name}`\n\n- **hash-quotient-drv**: SHA-256 [hash of the quotient derivation](@docroot@/store/derivation/outputs/input-address.md#hash-quotient-drv).\n  Begins with `sha256:`.\n\n- **output-name**: Name of the specific output (e.g., "out", "dev", "doc")\n\nExample: `"sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad!foo"`\n',
            pattern="^sha256:[0-9a-f]{64}![a-zA-Z_][a-zA-Z0-9_-]*$",
            title="Derivation Output ID",
        ),
    ]


class Value(BaseModel):
    outPath: Annotated[
        dict[str, dict[str, Any]],
        Field(
            description="The path to the store object that resulted from building this derivation for the given output name.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Output Store Path",
        ),
    ]
    signatures: Annotated[
        list[str],
        Field(
            description="A set of cryptographic signatures attesting to the authenticity of this build trace entry.\n",
            title="Build Signatures",
        ),
    ]


# === build-result-v1 ===


class Status(StrEnum):
    BUILT = "Built"
    SUBSTITUTED = "Substituted"
    ALREADY_VALID = "AlreadyValid"
    RESOLVES_TO_ALREADY_VALID = "ResolvesToAlreadyValid"


class BuiltOutputs(BaseModel):
    id: Annotated[
        str,
        Field(
            description='Unique identifier for the derivation output that was built.\n\nFormat: `{hash-quotient-drv}!{output-name}`\n\n- **hash-quotient-drv**: SHA-256 [hash of the quotient derivation](@docroot@/store/derivation/outputs/input-address.md#hash-quotient-drv).\n  Begins with `sha256:`.\n\n- **output-name**: Name of the specific output (e.g., "out", "dev", "doc")\n\nExample: `"sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad!foo"`\n',
            pattern="^sha256:[0-9a-f]{64}![a-zA-Z_][a-zA-Z0-9_-]*$",
            title="Derivation Output ID",
        ),
    ]
    outPath: Annotated[
        dict[str, dict[str, Any]],
        Field(
            description="The path to the store object that resulted from building this derivation for the given output name.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Output Store Path",
        ),
    ]
    signatures: Annotated[
        list[str],
        Field(
            description="A set of cryptographic signatures attesting to the authenticity of this build trace entry.\n",
            title="Build Signatures",
        ),
    ]


class BuildResult1(BaseModel):
    timesBuilt: Annotated[
        int | None,
        Field(
            description="How many times this build was performed.\n",
            ge=0,
            title="Times built",
        ),
    ] = None
    startTime: Annotated[
        int | None,
        Field(
            description="The start time of the build (or one of the rounds, if it was repeated), as a Unix timestamp.\n",
            ge=0,
            title="Start time",
        ),
    ] = None
    stopTime: Annotated[
        int | None,
        Field(
            description="The stop time of the build (or one of the rounds, if it was repeated), as a Unix timestamp.\n",
            ge=0,
            title="Stop time",
        ),
    ] = None
    cpuUser: Annotated[
        int | None,
        Field(
            description="User CPU time the build took, in microseconds.\n",
            ge=0,
            title="User CPU time",
        ),
    ] = None
    cpuSystem: Annotated[
        int | None,
        Field(
            description="System CPU time the build took, in microseconds.\n",
            ge=0,
            title="System CPU time",
        ),
    ] = None
    success: Annotated[
        Literal[True],
        Field(
            description="Always true for successful build results.\n",
            title="Success indicator",
        ),
    ] = True
    status: Annotated[
        Status,
        Field(
            description="Status string for successful builds.\n", title="Success status"
        ),
    ]
    builtOutputs: Annotated[
        dict[str, BuiltOutputs],
        Field(
            description="A mapping from output names to their build trace entries.\n",
            title="Built outputs",
        ),
    ]


class Status1(StrEnum):
    PERMANENT_FAILURE = "PermanentFailure"
    INPUT_REJECTED = "InputRejected"
    OUTPUT_REJECTED = "OutputRejected"
    TRANSIENT_FAILURE = "TransientFailure"
    CACHED_FAILURE = "CachedFailure"
    TIMED_OUT = "TimedOut"
    MISC_FAILURE = "MiscFailure"
    DEPENDENCY_FAILED = "DependencyFailed"
    LOG_LIMIT_EXCEEDED = "LogLimitExceeded"
    NOT_DETERMINISTIC = "NotDeterministic"
    NO_SUBSTITUTERS = "NoSubstituters"
    HASH_MISMATCH = "HashMismatch"


class BuildResult2(BaseModel):
    timesBuilt: Annotated[
        int | None,
        Field(
            description="How many times this build was performed.\n",
            ge=0,
            title="Times built",
        ),
    ] = None
    startTime: Annotated[
        int | None,
        Field(
            description="The start time of the build (or one of the rounds, if it was repeated), as a Unix timestamp.\n",
            ge=0,
            title="Start time",
        ),
    ] = None
    stopTime: Annotated[
        int | None,
        Field(
            description="The stop time of the build (or one of the rounds, if it was repeated), as a Unix timestamp.\n",
            ge=0,
            title="Stop time",
        ),
    ] = None
    cpuUser: Annotated[
        int | None,
        Field(
            description="User CPU time the build took, in microseconds.\n",
            ge=0,
            title="User CPU time",
        ),
    ] = None
    cpuSystem: Annotated[
        int | None,
        Field(
            description="System CPU time the build took, in microseconds.\n",
            ge=0,
            title="System CPU time",
        ),
    ] = None
    success: Annotated[
        Literal[False],
        Field(
            description="Always false for failed build results.\n",
            title="Success indicator",
        ),
    ] = False
    status: Annotated[
        Status1,
        Field(description="Status string for failed builds.\n", title="Failure status"),
    ]
    errorMsg: Annotated[
        str,
        Field(
            description="Information about the error if the build failed.\n",
            title="Error message",
        ),
    ]
    isNonDeterministic: Annotated[
        bool | None,
        Field(
            description="If timesBuilt > 1, whether some builds did not produce the same result.\n\nNote that 'isNonDeterministic = false' does not mean the build is deterministic,\njust that we don't have evidence of non-determinism.\n",
            title="Non-deterministic flag",
        ),
    ] = None


class BuildResult(RootModel[BuildResult1 | BuildResult2]):
    root: Annotated[
        BuildResult1 | BuildResult2,
        Field(
            description="This schema describes the JSON representation of Nix's `BuildResult` type, which represents the result of building a derivation or substituting store paths.\n\nBuild results can represent either successful builds (with built outputs) or various types of failures.\n",
            title="Build Result",
        ),
    ]


class Status2(StrEnum):
    BUILT = "Built"
    SUBSTITUTED = "Substituted"
    ALREADY_VALID = "AlreadyValid"
    RESOLVES_TO_ALREADY_VALID = "ResolvesToAlreadyValid"


class Success(BaseModel):
    success: Annotated[
        Literal[True],
        Field(
            description="Always true for successful build results.\n",
            title="Success indicator",
        ),
    ] = True
    status: Annotated[
        Status2,
        Field(
            description="Status string for successful builds.\n", title="Success status"
        ),
    ]
    builtOutputs: Annotated[
        dict[str, BuiltOutputs],
        Field(
            description="A mapping from output names to their build trace entries.\n",
            title="Built outputs",
        ),
    ]


class Status3(StrEnum):
    PERMANENT_FAILURE = "PermanentFailure"
    INPUT_REJECTED = "InputRejected"
    OUTPUT_REJECTED = "OutputRejected"
    TRANSIENT_FAILURE = "TransientFailure"
    CACHED_FAILURE = "CachedFailure"
    TIMED_OUT = "TimedOut"
    MISC_FAILURE = "MiscFailure"
    DEPENDENCY_FAILED = "DependencyFailed"
    LOG_LIMIT_EXCEEDED = "LogLimitExceeded"
    NOT_DETERMINISTIC = "NotDeterministic"
    NO_SUBSTITUTERS = "NoSubstituters"
    HASH_MISMATCH = "HashMismatch"


class Failure(BaseModel):
    success: Annotated[
        Literal[False],
        Field(
            description="Always false for failed build results.\n",
            title="Success indicator",
        ),
    ] = False
    status: Annotated[
        Status3,
        Field(description="Status string for failed builds.\n", title="Failure status"),
    ]
    errorMsg: Annotated[
        str,
        Field(
            description="Information about the error if the build failed.\n",
            title="Error message",
        ),
    ]
    isNonDeterministic: Annotated[
        bool | None,
        Field(
            description="If timesBuilt > 1, whether some builds did not produce the same result.\n\nNote that 'isNonDeterministic = false' does not mean the build is deterministic,\njust that we don't have evidence of non-determinism.\n",
            title="Non-deterministic flag",
        ),
    ] = None


# === deriving-path-v1 ===


class DerivingPath1(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="See [Constant](@docroot@/store/derivation/index.md#deriving-path-constant) deriving path.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class DerivingPath2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drvPath: Annotated[
        DerivingPath,
        Field(
            description="A deriving path to a [Derivation](@docroot@/store/derivation/index.md#store-derivation), whose output is being referred to.\n"
        ),
    ]
    output: Annotated[
        str,
        Field(
            description='The name of an output produced by that derivation (e.g. "out", "doc", etc.).\n'
        ),
    ]


class DerivingPath(RootModel[DerivingPath1 | DerivingPath2]):
    root: Annotated[
        DerivingPath1 | DerivingPath2,
        Field(
            description="This schema describes the JSON representation of Nix's [Deriving Path](@docroot@/store/derivation/index.md#deriving-path).\n",
            title="Deriving Path",
        ),
    ]


DerivingPath2.model_rebuild()
# === derivation-v4 ===


class Outputs(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    path: Annotated[
        str,
        Field(
            description="The output path determined from the derivation itself.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Output path",
        ),
    ]


class Outputs1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    method: Annotated[
        Any, Field(description="Method of content addressing used for this output.\n")
    ]
    hash: Annotated[
        Any,
        Field(description="The expected content hash.\n", title="Expected hash value"),
    ]


class HashAlgo(StrEnum):
    BLAKE3 = "blake3"
    MD5 = "md5"
    SHA1 = "sha1"
    SHA256 = "sha256"
    SHA512 = "sha512"


class Outputs2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    method: Annotated[
        Method,
        Field(
            description="Method of content addressing used for this output.\n",
            title="Content-Addressing Method",
        ),
    ]
    hashAlgo: Annotated[
        HashAlgo,
        Field(
            description="What hash algorithm to use for the given method of content-addressing.\n",
            title="Hash algorithm",
        ),
    ]


class Outputs3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    impure: Literal[True] = True
    method: Annotated[
        Method,
        Field(
            description="How the file system objects will be serialized for hashing.\n",
            title="Content-Addressing Method",
        ),
    ]
    hashAlgo: Annotated[
        HashAlgo,
        Field(
            description="How the serialization will be hashed.\n",
            title="Hash algorithm",
        ),
    ]


class Src(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description='A [store path](@docroot@/store/store-path.md) identifying a store object.\n\nThis schema describes the JSON representation of store paths as used in various Nix JSON APIs.\n\n> **Warning**\n>\n> This JSON format is currently\n> [**experimental**](@docroot@/development/experimental-features.md#xp-feature-nix-command)\n> and subject to change.\n\n## Format\n\nStore paths in JSON are represented as strings containing just the hash and name portion, without the store directory prefix.\n\nFor example: `"g1w7hy3qg1w7hy3qg1w7hy3qg1w7hy3q-foo.drv"`\n\n(If the store dir is `/nix/store`, then this corresponds to the path `/nix/store/g1w7hy3qg1w7hy3qg1w7hy3qg1w7hy3q-foo.drv`.)\n\n## Structure\n\nThe format follows this pattern: `${digest}-${name}`\n\n- **hash**: Digest rendered in [Nix32](@docroot@/protocols/nix32.md), a variant of base-32 (20 hash bytes become 32 ASCII characters)\n- **name**: The package name and optional version/suffix information\n',
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Store Path",
        ),
    ]


class Drvs(BaseModel):
    outputs: Annotated[
        list[str] | None,
        Field(
            description="Set of names of derivation outputs to depend on",
            title="Output Names",
        ),
    ] = None
    dynamicOutputs: Annotated[
        dict[str, Any] | None, Field(description="Circular ref: #/$defs/dynamicOutputs")
    ] = None


class Inputs(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    srcs: Annotated[
        list[Src],
        Field(
            description='List of store paths on which this derivation depends.\n\n> **Example**\n>\n> ```json\n> "srcs": [\n>   "b8nwz167km1yciqpwzjj24f8jcy8pq1h-separate-debug-info.sh",\n>   "ihzmilr413r8fb3ah30yjnhlb18c1laz-fix-pop-var-context-error.patch"\n> ]\n> ```\n',
            title="Input source paths",
        ),
    ]
    drvs: Annotated[
        dict[str, list[str] | Drvs],
        Field(
            description='Mapping of derivation paths to lists of output names they provide.\n\n> **Example**\n>\n> ```json\n> "drvs": {\n>   "6lkh5yi7nlb7l6dr8fljlli5zfd9hq58-curl-7.73.0.drv": ["dev"],\n>   "fn3kgnfzl5dzym26j8g907gq3kbm8bfh-unzip-6.0.drv": ["out"]\n> }\n> ```\n>\n> specifies that this derivation depends on the `dev` output of `curl`, and the `out` output of `unzip`.\n',
            title="Input derivations",
        ),
    ]


class Derivation(BaseModel):
    name: Annotated[
        str,
        Field(
            description="The name of the derivation.\nUsed when calculating store paths for the derivation’s outputs.\n",
            title="Derivation name",
        ),
    ]
    version: Annotated[
        Literal[4],
        Field(
            description='Must be `4`.\nThis is a guard that allows us to continue evolving this format.\nThe choice of `3` is fairly arbitrary, but corresponds to this informal version:\n\n- Version 0: ATerm format\n\n- Version 1: Original JSON format, with ugly `"r:sha256"` inherited from ATerm format.\n\n- Version 2: Separate `method` and `hashAlgo` fields in output specs\n\n- Version 3: Drop store dir from store paths, just include base name.\n\n- Version 4: Two cleanups, batched together to lesson churn:\n\n  - Reorganize inputs into nested structure (`inputs.srcs` and `inputs.drvs`)\n\n  - Use canonical content address JSON format for floating content addressed derivation outputs.\n\nNote that while this format is experimental, the maintenance of versions is best-effort, and not promised to identify every change.\n',
            title="Format version (must be 4)",
        ),
    ] = 4
    outputs: Annotated[
        dict[str, Outputs | Outputs1 | Outputs2 | dict[str, Any] | Outputs3],
        Field(
            description='Information about the output paths of the derivation.\nThis is a JSON object with one member per output, where the key is the output name and the value is a JSON object as described.\n\n > **Example**\n >\n > ```json\n > "outputs": {\n >   "out": {\n >     "method": "nar",\n >     "hashAlgo": "sha256",\n >     "hash": "6fc80dcc62179dbc12fc0b5881275898f93444833d21b89dfe5f7fbcbb1d0d62"\n >   }\n > }\n > ```\n',
            title="Output specifications",
        ),
    ]
    inputs: Annotated[
        Inputs,
        Field(
            description="Input dependencies for the derivation, organized into source paths and derivation dependencies.\n",
            title="Derivation inputs",
        ),
    ]
    system: Annotated[
        str,
        Field(
            description="The system type on which this derivation is to be built\n(e.g. `x86_64-linux`).\n",
            title="Build system type",
        ),
    ]
    builder: Annotated[
        str,
        Field(
            description="Absolute path of the program used to perform the build.\nTypically this is the `bash` shell\n(e.g. `/nix/store/p4xlj4imjbnm4v0x5jf4qysvyjjlgq1d-bash-4.4-p23/bin/bash`).\n",
            title="Build program path",
        ),
    ]
    args: Annotated[
        list[str],
        Field(
            description="Command-line arguments passed to the `builder`.\n",
            title="Builder arguments",
        ),
    ]
    env: Annotated[
        dict[str, str],
        Field(
            description="Environment variables passed to the `builder`.\n",
            title="Environment variables",
        ),
    ]
    structuredAttrs: Annotated[
        dict[str, Any] | None,
        Field(
            description="[Structured Attributes](@docroot@/store/derivation/index.md#structured-attrs), only defined if the derivation contains them.\nStructured attributes are JSON, and thus embedded as-is.\n",
            title="Structured attributes",
        ),
    ] = None


class Output(RootModel[Any]):
    root: Any


class OutputName(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="Name of the derivation output to depend on",
            title="Output name",
        ),
    ]


class OutputNames(RootModel[list[str]]):
    root: Annotated[
        list[str],
        Field(
            description="Set of names of derivation outputs to depend on",
            title="Output Names",
        ),
    ]


class DynamicOutputs1(BaseModel):
    outputs: Annotated[
        list[str] | None,
        Field(
            description="Set of names of derivation outputs to depend on",
            title="Output Names",
        ),
    ] = None
    dynamicOutputs: Annotated[
        dict[str, Any] | None, Field(description="Circular ref: #/$defs/dynamicOutputs")
    ] = None


class DynamicOutputs(BaseModel):
    outputs: Annotated[
        list[str] | None,
        Field(
            description="Set of names of derivation outputs to depend on",
            title="Output Names",
        ),
    ] = None
    dynamicOutputs: Annotated[
        DynamicOutputs1 | None,
        Field(
            description="**Experimental feature**: [`dynamic-derivations`](@docroot@/development/experimental-features.md#xp-feature-dynamic-derivations)\n\nThis recursive data type allows for depending on outputs of outputs.\n",
            title="Dynamic Outputs",
        ),
    ] = None


# === derivation-options-v1 ===


class AllowedReferences(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drvPath: Annotated[
        Literal["self"],
        Field(
            description="Won't be confused for a deriving path\n",
            title="This derivation",
        ),
    ] = "self"
    output: Annotated[
        str,
        Field(
            description="The name of the output being referenced.\n",
            title="Output Name",
        ),
    ]


class AllowedReferences1(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="See [Constant](@docroot@/store/derivation/index.md#deriving-path-constant) deriving path.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class AllowedRequisites(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drvPath: Annotated[
        Literal["self"],
        Field(
            description="Won't be confused for a deriving path\n",
            title="This derivation",
        ),
    ] = "self"
    output: Annotated[
        str,
        Field(
            description="The name of the output being referenced.\n",
            title="Output Name",
        ),
    ]


class AllowedRequisites1(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="See [Constant](@docroot@/store/derivation/index.md#deriving-path-constant) deriving path.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class DisallowedReferences(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drvPath: Annotated[
        Literal["self"],
        Field(
            description="Won't be confused for a deriving path\n",
            title="This derivation",
        ),
    ] = "self"
    output: Annotated[
        str,
        Field(
            description="The name of the output being referenced.\n",
            title="Output Name",
        ),
    ]


class DisallowedReferences1(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="See [Constant](@docroot@/store/derivation/index.md#deriving-path-constant) deriving path.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class DisallowedRequisites(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drvPath: Annotated[
        Literal["self"],
        Field(
            description="Won't be confused for a deriving path\n",
            title="This derivation",
        ),
    ] = "self"
    output: Annotated[
        str,
        Field(
            description="The name of the output being referenced.\n",
            title="Output Name",
        ),
    ]


class DisallowedRequisites1(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="See [Constant](@docroot@/store/derivation/index.md#deriving-path-constant) deriving path.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class AllowedReferences3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drvPath: Annotated[
        Literal["self"],
        Field(
            description="Won't be confused for a deriving path\n",
            title="This derivation",
        ),
    ] = "self"
    output: Annotated[
        str,
        Field(
            description="The name of the output being referenced.\n",
            title="Output Name",
        ),
    ]


class AllowedReferences4(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="See [Constant](@docroot@/store/derivation/index.md#deriving-path-constant) deriving path.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class AllowedRequisites3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drvPath: Annotated[
        Literal["self"],
        Field(
            description="Won't be confused for a deriving path\n",
            title="This derivation",
        ),
    ] = "self"
    output: Annotated[
        str,
        Field(
            description="The name of the output being referenced.\n",
            title="Output Name",
        ),
    ]


class AllowedRequisites4(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="See [Constant](@docroot@/store/derivation/index.md#deriving-path-constant) deriving path.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class DisallowedReferences3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drvPath: Annotated[
        Literal["self"],
        Field(
            description="Won't be confused for a deriving path\n",
            title="This derivation",
        ),
    ] = "self"
    output: Annotated[
        str,
        Field(
            description="The name of the output being referenced.\n",
            title="Output Name",
        ),
    ]


class DisallowedReferences4(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="See [Constant](@docroot@/store/derivation/index.md#deriving-path-constant) deriving path.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class DisallowedRequisites3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drvPath: Annotated[
        Literal["self"],
        Field(
            description="Won't be confused for a deriving path\n",
            title="This derivation",
        ),
    ] = "self"
    output: Annotated[
        str,
        Field(
            description="The name of the output being referenced.\n",
            title="Output Name",
        ),
    ]


class DisallowedRequisites4(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="See [Constant](@docroot@/store/derivation/index.md#deriving-path-constant) deriving path.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class ExportReferencesGraph(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="See [Constant](@docroot@/store/derivation/index.md#deriving-path-constant) deriving path.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class AllowedReferences6(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drvPath: Annotated[
        Literal["self"],
        Field(
            description="Won't be confused for a deriving path\n",
            title="This derivation",
        ),
    ] = "self"
    output: Annotated[
        str,
        Field(
            description="The name of the output being referenced.\n",
            title="Output Name",
        ),
    ]


class AllowedReferences7(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="See [Constant](@docroot@/store/derivation/index.md#deriving-path-constant) deriving path.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class AllowedRequisites6(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drvPath: Annotated[
        Literal["self"],
        Field(
            description="Won't be confused for a deriving path\n",
            title="This derivation",
        ),
    ] = "self"
    output: Annotated[
        str,
        Field(
            description="The name of the output being referenced.\n",
            title="Output Name",
        ),
    ]


class AllowedRequisites7(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="See [Constant](@docroot@/store/derivation/index.md#deriving-path-constant) deriving path.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class DisallowedReferences6(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drvPath: Annotated[
        Literal["self"],
        Field(
            description="Won't be confused for a deriving path\n",
            title="This derivation",
        ),
    ] = "self"
    output: Annotated[
        str,
        Field(
            description="The name of the output being referenced.\n",
            title="Output Name",
        ),
    ]


class DisallowedReferences7(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="See [Constant](@docroot@/store/derivation/index.md#deriving-path-constant) deriving path.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class DisallowedRequisites6(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drvPath: Annotated[
        Literal["self"],
        Field(
            description="Won't be confused for a deriving path\n",
            title="This derivation",
        ),
    ] = "self"
    output: Annotated[
        str,
        Field(
            description="The name of the output being referenced.\n",
            title="Output Name",
        ),
    ]


class DisallowedRequisites7(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="See [Constant](@docroot@/store/derivation/index.md#deriving-path-constant) deriving path.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class DrvRef1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drvPath: Annotated[
        Literal["self"],
        Field(
            description="Won't be confused for a deriving path\n",
            title="This derivation",
        ),
    ] = "self"
    output: Annotated[
        str,
        Field(
            description="The name of the output being referenced.\n",
            title="Output Name",
        ),
    ]


class DrvRef2(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="See [Constant](@docroot@/store/derivation/index.md#deriving-path-constant) deriving path.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class AllowedReferences2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drvPath: Annotated[
        DerivationOptions,
        Field(
            description="A deriving path to a [Derivation](@docroot@/store/derivation/index.md#store-derivation), whose output is being referred to.\n"
        ),
    ]
    output: Annotated[
        str,
        Field(
            description='The name of an output produced by that derivation (e.g. "out", "doc", etc.).\n'
        ),
    ]


class AllowedRequisites2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drvPath: Annotated[
        DerivationOptions,
        Field(
            description="A deriving path to a [Derivation](@docroot@/store/derivation/index.md#store-derivation), whose output is being referred to.\n"
        ),
    ]
    output: Annotated[
        str,
        Field(
            description='The name of an output produced by that derivation (e.g. "out", "doc", etc.).\n'
        ),
    ]


class DisallowedReferences2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drvPath: Annotated[
        DerivationOptions,
        Field(
            description="A deriving path to a [Derivation](@docroot@/store/derivation/index.md#store-derivation), whose output is being referred to.\n"
        ),
    ]
    output: Annotated[
        str,
        Field(
            description='The name of an output produced by that derivation (e.g. "out", "doc", etc.).\n'
        ),
    ]


class DisallowedRequisites2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drvPath: Annotated[
        DerivationOptions,
        Field(
            description="A deriving path to a [Derivation](@docroot@/store/derivation/index.md#store-derivation), whose output is being referred to.\n"
        ),
    ]
    output: Annotated[
        str,
        Field(
            description='The name of an output produced by that derivation (e.g. "out", "doc", etc.).\n'
        ),
    ]


class ForAllOutputs(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    ignoreSelfRefs: Annotated[
        bool,
        Field(
            description="Whether references from this output to itself should be ignored when checking references.\n",
            title="Ignore Self References",
        ),
    ]
    maxSize: Annotated[
        int | None,
        Field(
            description="Maximum allowed size of this output in bytes, or null for no limit.\n",
            ge=0,
            title="Maximum Size",
        ),
    ]
    maxClosureSize: Annotated[
        int | None,
        Field(
            description="Maximum allowed size of this output's closure in bytes, or null for no limit.\n",
            ge=0,
            title="Maximum Closure Size",
        ),
    ]
    allowedReferences: Annotated[
        list[AllowedReferences | AllowedReferences1 | AllowedReferences2] | None,
        Field(
            description="If set, the output can only reference paths in this list.\nIf null, no restrictions apply.\n",
            title="Allowed References",
        ),
    ]
    allowedRequisites: Annotated[
        list[AllowedRequisites | AllowedRequisites1 | AllowedRequisites2] | None,
        Field(
            description="If set, the output's closure can only contain paths in this list.\nIf null, no restrictions apply.\n",
            title="Allowed Requisites",
        ),
    ]
    disallowedReferences: Annotated[
        list[DisallowedReferences | DisallowedReferences1 | DisallowedReferences2],
        Field(
            description="The output must not reference any paths in this list.\n",
            title="Disallowed References",
        ),
    ]
    disallowedRequisites: Annotated[
        list[DisallowedRequisites | DisallowedRequisites1 | DisallowedRequisites2],
        Field(
            description="The output's closure must not contain any paths in this list.\n",
            title="Disallowed Requisites",
        ),
    ]


class OutputChecks(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    forAllOutputs: Annotated[
        ForAllOutputs,
        Field(
            description="Constraints on what a specific output can reference.\n",
            title="Output Check Specification",
        ),
    ]


class PerOutput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    ignoreSelfRefs: Annotated[
        bool,
        Field(
            description="Whether references from this output to itself should be ignored when checking references.\n",
            title="Ignore Self References",
        ),
    ]
    maxSize: Annotated[
        int | None,
        Field(
            description="Maximum allowed size of this output in bytes, or null for no limit.\n",
            ge=0,
            title="Maximum Size",
        ),
    ]
    maxClosureSize: Annotated[
        int | None,
        Field(
            description="Maximum allowed size of this output's closure in bytes, or null for no limit.\n",
            ge=0,
            title="Maximum Closure Size",
        ),
    ]
    allowedReferences: Annotated[
        list[AllowedReferences3 | AllowedReferences4 | AllowedReferences2] | None,
        Field(
            description="If set, the output can only reference paths in this list.\nIf null, no restrictions apply.\n",
            title="Allowed References",
        ),
    ]
    allowedRequisites: Annotated[
        list[AllowedRequisites3 | AllowedRequisites4 | AllowedRequisites2] | None,
        Field(
            description="If set, the output's closure can only contain paths in this list.\nIf null, no restrictions apply.\n",
            title="Allowed Requisites",
        ),
    ]
    disallowedReferences: Annotated[
        list[DisallowedReferences3 | DisallowedReferences4 | DisallowedReferences2],
        Field(
            description="The output must not reference any paths in this list.\n",
            title="Disallowed References",
        ),
    ]
    disallowedRequisites: Annotated[
        list[DisallowedRequisites3 | DisallowedRequisites4 | DisallowedRequisites2],
        Field(
            description="The output's closure must not contain any paths in this list.\n",
            title="Disallowed Requisites",
        ),
    ]


class OutputChecks1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    perOutput: dict[str, PerOutput]


class ExportReferencesGraph1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drvPath: Annotated[
        DerivationOptions,
        Field(
            description="A deriving path to a [Derivation](@docroot@/store/derivation/index.md#store-derivation), whose output is being referred to.\n"
        ),
    ]
    output: Annotated[
        str,
        Field(
            description='The name of an output produced by that derivation (e.g. "out", "doc", etc.).\n'
        ),
    ]


class DerivationOptions(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    outputChecks: Annotated[
        OutputChecks | OutputChecks1,
        Field(
            description="Constraints on what the derivation's outputs can and cannot reference.\nCan either apply to all outputs or be specified per output.\n",
            title="Output Check",
        ),
    ]
    unsafeDiscardReferences: Annotated[
        dict[str, list[str]],
        Field(
            description="A map specifying which references should be unsafely discarded from each output.\nThis is generally not recommended and requires special permissions.\n",
            title="Unsafe Discard References",
        ),
    ]
    passAsFile: Annotated[
        list[str],
        Field(
            description="List of environment variable names whose values should be passed as files rather than directly.\n",
            title="Pass As File",
        ),
    ]
    exportReferencesGraph: Annotated[
        dict[str, list[ExportReferencesGraph | ExportReferencesGraph1]],
        Field(
            description="Specify paths whose references graph should be exported to files.\n",
            title="Export References Graph",
        ),
    ]
    additionalSandboxProfile: Annotated[
        str,
        Field(
            description="Additional sandbox profile directives (macOS specific).\n",
            title="Additional Sandbox Profile",
        ),
    ]
    noChroot: Annotated[
        bool,
        Field(
            description="Whether to disable the build sandbox, if allowed.\n",
            title="No Chroot",
        ),
    ]
    impureHostDeps: Annotated[
        list[str],
        Field(
            description="List of host paths that the build can access.\n",
            title="Impure Host Dependencies",
        ),
    ]
    impureEnvVars: Annotated[
        list[str],
        Field(
            description="List of environment variable names that should be passed through to the build from the calling environment.\n",
            title="Impure Environment Variables",
        ),
    ]
    allowLocalNetworking: Annotated[
        bool,
        Field(
            description="Whether the build should have access to local network (macOS specific).\n",
            title="Allow Local Networking",
        ),
    ]
    requiredSystemFeatures: Annotated[
        list[str],
        Field(
            description='List of system features required to build this derivation (e.g., "kvm", "nixos-test").\n',
            title="Required System Features",
        ),
    ]
    preferLocalBuild: Annotated[
        bool,
        Field(
            description="Whether this derivation should preferably be built locally rather than its outputs substituted.\n",
            title="Prefer Local Build",
        ),
    ]
    allowSubstitutes: Annotated[
        bool,
        Field(
            description="Whether substituting from other stores should be allowed for this derivation's outputs.\n",
            title="Allow Substitutes",
        ),
    ]


class OutputCheckSpec(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    ignoreSelfRefs: Annotated[
        bool,
        Field(
            description="Whether references from this output to itself should be ignored when checking references.\n",
            title="Ignore Self References",
        ),
    ]
    maxSize: Annotated[
        int | None,
        Field(
            description="Maximum allowed size of this output in bytes, or null for no limit.\n",
            ge=0,
            title="Maximum Size",
        ),
    ]
    maxClosureSize: Annotated[
        int | None,
        Field(
            description="Maximum allowed size of this output's closure in bytes, or null for no limit.\n",
            ge=0,
            title="Maximum Closure Size",
        ),
    ]
    allowedReferences: Annotated[
        list[AllowedReferences6 | AllowedReferences7 | AllowedReferences2] | None,
        Field(
            description="If set, the output can only reference paths in this list.\nIf null, no restrictions apply.\n",
            title="Allowed References",
        ),
    ]
    allowedRequisites: Annotated[
        list[AllowedRequisites6 | AllowedRequisites7 | AllowedRequisites2] | None,
        Field(
            description="If set, the output's closure can only contain paths in this list.\nIf null, no restrictions apply.\n",
            title="Allowed Requisites",
        ),
    ]
    disallowedReferences: Annotated[
        list[DisallowedReferences6 | DisallowedReferences7 | DisallowedReferences2],
        Field(
            description="The output must not reference any paths in this list.\n",
            title="Disallowed References",
        ),
    ]
    disallowedRequisites: Annotated[
        list[DisallowedRequisites6 | DisallowedRequisites7 | DisallowedRequisites2],
        Field(
            description="The output's closure must not contain any paths in this list.\n",
            title="Disallowed Requisites",
        ),
    ]


class DrvRef3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drvPath: Annotated[
        DerivationOptions,
        Field(
            description="A deriving path to a [Derivation](@docroot@/store/derivation/index.md#store-derivation), whose output is being referred to.\n"
        ),
    ]
    output: Annotated[
        str,
        Field(
            description='The name of an output produced by that derivation (e.g. "out", "doc", etc.).\n'
        ),
    ]


class DrvRef(RootModel[DrvRef1 | DrvRef2 | DrvRef3]):
    root: DrvRef1 | DrvRef2 | DrvRef3


AllowedReferences2.model_rebuild()
AllowedRequisites2.model_rebuild()
DisallowedReferences2.model_rebuild()
DisallowedRequisites2.model_rebuild()
ExportReferencesGraph1.model_rebuild()
# === store-object-info-v2 ===


class Reference(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description='A [store path](@docroot@/store/store-path.md) identifying a store object.\n\nThis schema describes the JSON representation of store paths as used in various Nix JSON APIs.\n\n> **Warning**\n>\n> This JSON format is currently\n> [**experimental**](@docroot@/development/experimental-features.md#xp-feature-nix-command)\n> and subject to change.\n\n## Format\n\nStore paths in JSON are represented as strings containing just the hash and name portion, without the store directory prefix.\n\nFor example: `"g1w7hy3qg1w7hy3qg1w7hy3qg1w7hy3q-foo.drv"`\n\n(If the store dir is `/nix/store`, then this corresponds to the path `/nix/store/g1w7hy3qg1w7hy3qg1w7hy3qg1w7hy3q-foo.drv`.)\n\n## Structure\n\nThe format follows this pattern: `${digest}-${name}`\n\n- **hash**: Digest rendered in [Nix32](@docroot@/protocols/nix32.md), a variant of base-32 (20 hash bytes become 32 ASCII characters)\n- **name**: The package name and optional version/suffix information\n',
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Store Path",
        ),
    ]


class Ca(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    method: Annotated[
        Method,
        Field(
            description="A string representing the [method](@docroot@/store/store-object/content-address.md) of content addressing that is chosen.\n\nValid method strings are:\n\n- [`flat`](@docroot@/store/store-object/content-address.md#method-flat) (provided the contents are a single file)\n- [`nar`](@docroot@/store/store-object/content-address.md#method-nix-archive)\n- [`text`](@docroot@/store/store-object/content-address.md#method-text)\n- [`git`](@docroot@/store/store-object/content-address.md#method-git)\n",
            title="Content-Addressing Method",
        ),
    ]
    hash: Annotated[
        str,
        Field(
            description="This would be the content-address itself.\n\nFor all current methods, this is just a content address of the file system object of the store object, [as described in the store chapter](@docroot@/store/file-system-object/content-address.md), and not of the store object as a whole.\nIn particular, the references of the store object are *not* taken into account with this hash (and currently-supported methods).\n",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="Content Address",
        ),
    ]


class StoreObjectInfoV21(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    version: Annotated[
        Literal[2],
        Field(
            description='Must be `2`.\nThis is a guard that allows us to continue evolving this format.\nHere is the rough version history:\n\n- Version 0: `.narinfo` line-oriented format\n\n- Version 1: Original JSON format, with ugly `"r:sha256"` inherited from `.narinfo` format.\n\n- Version 2: Use structured JSON type for `ca`\n',
            title="Format version (must be 2)",
        ),
    ] = 2
    path: Annotated[
        str | None,
        Field(
            description="[Store path](@docroot@/store/store-path.md) to the given store object.\n\nNote: This field may not be present in all contexts, such as when the path is used as the key and the the store object info the value in map.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Store Path",
        ),
    ] = None
    narHash: Annotated[
        str,
        Field(
            description="Hash of the [file system object](@docroot@/store/file-system-object.md) part of the store object when serialized as a [Nix Archive](@docroot@/store/file-system-object/content-address.md#serial-nix-archive).\n",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="NAR Hash",
        ),
    ]
    narSize: Annotated[
        int,
        Field(
            description="Size of the [file system object](@docroot@/store/file-system-object.md) part of the store object when serialized as a [Nix Archive](@docroot@/store/file-system-object/content-address.md#serial-nix-archive).\n",
            ge=0,
            title="NAR Size",
        ),
    ]
    references: Annotated[
        list[Reference],
        Field(
            description="An array of [store paths](@docroot@/store/store-path.md), possibly including this one.\n",
            title="References",
        ),
    ]
    ca: Annotated[
        Ca | None,
        Field(
            description="If the store object is [content-addressed](@docroot@/store/store-object/content-address.md),\nthis is the content address of this store object's file system object, used to compute its store path.\nOtherwise (i.e. if it is [input-addressed](@docroot@/glossary.md#gloss-input-addressed-store-object)), this is `null`.\n",
            title="Content Address",
        ),
    ]
    storeDir: Annotated[
        str,
        Field(
            description="The [store directory](@docroot@/store/store-path.md#store-directory) this store object belongs to (e.g. `/nix/store`).\n",
            title="Store Directory",
        ),
    ]


class Ca1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    method: Annotated[
        Method,
        Field(
            description="A string representing the [method](@docroot@/store/store-object/content-address.md) of content addressing that is chosen.\n\nValid method strings are:\n\n- [`flat`](@docroot@/store/store-object/content-address.md#method-flat) (provided the contents are a single file)\n- [`nar`](@docroot@/store/store-object/content-address.md#method-nix-archive)\n- [`text`](@docroot@/store/store-object/content-address.md#method-text)\n- [`git`](@docroot@/store/store-object/content-address.md#method-git)\n",
            title="Content-Addressing Method",
        ),
    ]
    hash: Annotated[
        str,
        Field(
            description="This would be the content-address itself.\n\nFor all current methods, this is just a content address of the file system object of the store object, [as described in the store chapter](@docroot@/store/file-system-object/content-address.md), and not of the store object as a whole.\nIn particular, the references of the store object are *not* taken into account with this hash (and currently-supported methods).\n",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="Content Address",
        ),
    ]


class Deriver(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description='A [store path](@docroot@/store/store-path.md) identifying a store object.\n\nThis schema describes the JSON representation of store paths as used in various Nix JSON APIs.\n\n> **Warning**\n>\n> This JSON format is currently\n> [**experimental**](@docroot@/development/experimental-features.md#xp-feature-nix-command)\n> and subject to change.\n\n## Format\n\nStore paths in JSON are represented as strings containing just the hash and name portion, without the store directory prefix.\n\nFor example: `"g1w7hy3qg1w7hy3qg1w7hy3qg1w7hy3q-foo.drv"`\n\n(If the store dir is `/nix/store`, then this corresponds to the path `/nix/store/g1w7hy3qg1w7hy3qg1w7hy3qg1w7hy3q-foo.drv`.)\n\n## Structure\n\nThe format follows this pattern: `${digest}-${name}`\n\n- **hash**: Digest rendered in [Nix32](@docroot@/protocols/nix32.md), a variant of base-32 (20 hash bytes become 32 ASCII characters)\n- **name**: The package name and optional version/suffix information\n',
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Store Path",
        ),
    ]


class StoreObjectInfoV22(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    version: Annotated[
        Literal[2],
        Field(
            description='Must be `2`.\nThis is a guard that allows us to continue evolving this format.\nHere is the rough version history:\n\n- Version 0: `.narinfo` line-oriented format\n\n- Version 1: Original JSON format, with ugly `"r:sha256"` inherited from `.narinfo` format.\n\n- Version 2: Use structured JSON type for `ca`\n',
            title="Format version (must be 2)",
        ),
    ] = 2
    path: Annotated[
        str | None,
        Field(
            description="[Store path](@docroot@/store/store-path.md) to the given store object.\n\nNote: This field may not be present in all contexts, such as when the path is used as the key and the the store object info the value in map.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Store Path",
        ),
    ] = None
    narHash: Annotated[
        str,
        Field(
            description="Hash of the [file system object](@docroot@/store/file-system-object.md) part of the store object when serialized as a [Nix Archive](@docroot@/store/file-system-object/content-address.md#serial-nix-archive).\n",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="NAR Hash",
        ),
    ]
    narSize: Annotated[
        int,
        Field(
            description="Size of the [file system object](@docroot@/store/file-system-object.md) part of the store object when serialized as a [Nix Archive](@docroot@/store/file-system-object/content-address.md#serial-nix-archive).\n",
            ge=0,
            title="NAR Size",
        ),
    ]
    references: Annotated[
        list[Reference],
        Field(
            description="An array of [store paths](@docroot@/store/store-path.md), possibly including this one.\n",
            title="References",
        ),
    ]
    ca: Annotated[
        Ca1 | None,
        Field(
            description="If the store object is [content-addressed](@docroot@/store/store-object/content-address.md),\nthis is the content address of this store object's file system object, used to compute its store path.\nOtherwise (i.e. if it is [input-addressed](@docroot@/glossary.md#gloss-input-addressed-store-object)), this is `null`.\n",
            title="Content Address",
        ),
    ]
    storeDir: Annotated[
        str,
        Field(
            description="The [store directory](@docroot@/store/store-path.md#store-directory) this store object belongs to (e.g. `/nix/store`).\n",
            title="Store Directory",
        ),
    ]
    deriver: Annotated[
        Deriver | None,
        Field(
            description='If known, the path to the [store derivation](@docroot@/glossary.md#gloss-store-derivation) from which this store object was produced.\nOtherwise `null`.\n\n> This is an "impure" field that may not be included in certain contexts.\n',
            title="Deriver",
        ),
    ]
    registrationTime: Annotated[
        int | None,
        Field(
            description='If known, when this derivation was added to the store (Unix timestamp).\nOtherwise `null`.\n\n> This is an "impure" field that may not be included in certain contexts.\n',
            title="Registration Time",
        ),
    ]
    ultimate: Annotated[
        bool,
        Field(
            description='Whether this store object is trusted because we built it ourselves, rather than substituted a build product from elsewhere.\n\n> This is an "impure" field that may not be included in certain contexts.\n',
            title="Ultimate",
        ),
    ]
    signatures: Annotated[
        list[str],
        Field(
            description='Signatures claiming that this store object is what it claims to be.\nNot relevant for [content-addressed](@docroot@/store/store-object/content-address.md) store objects,\nbut useful for [input-addressed](@docroot@/glossary.md#gloss-input-addressed-store-object) store objects.\n\n> This is an "impure" field that may not be included in certain contexts.\n',
            title="Signatures",
        ),
    ]
    closureSize: Annotated[
        int | None,
        Field(
            description="The total size of this store object and every other object in its [closure](@docroot@/glossary.md#gloss-closure).\n\n> This field is not stored at all, but computed by traversing the other fields across all the store objects in a closure.\n",
            ge=0,
            title="Closure Size",
        ),
    ] = None


class Ca2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    method: Annotated[
        Method,
        Field(
            description="A string representing the [method](@docroot@/store/store-object/content-address.md) of content addressing that is chosen.\n\nValid method strings are:\n\n- [`flat`](@docroot@/store/store-object/content-address.md#method-flat) (provided the contents are a single file)\n- [`nar`](@docroot@/store/store-object/content-address.md#method-nix-archive)\n- [`text`](@docroot@/store/store-object/content-address.md#method-text)\n- [`git`](@docroot@/store/store-object/content-address.md#method-git)\n",
            title="Content-Addressing Method",
        ),
    ]
    hash: Annotated[
        str,
        Field(
            description="This would be the content-address itself.\n\nFor all current methods, this is just a content address of the file system object of the store object, [as described in the store chapter](@docroot@/store/file-system-object/content-address.md), and not of the store object as a whole.\nIn particular, the references of the store object are *not* taken into account with this hash (and currently-supported methods).\n",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="Content Address",
        ),
    ]


class StoreObjectInfoV23(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    version: Annotated[
        Literal[2],
        Field(
            description='Must be `2`.\nThis is a guard that allows us to continue evolving this format.\nHere is the rough version history:\n\n- Version 0: `.narinfo` line-oriented format\n\n- Version 1: Original JSON format, with ugly `"r:sha256"` inherited from `.narinfo` format.\n\n- Version 2: Use structured JSON type for `ca`\n',
            title="Format version (must be 2)",
        ),
    ] = 2
    path: Annotated[
        str | None,
        Field(
            description="[Store path](@docroot@/store/store-path.md) to the given store object.\n\nNote: This field may not be present in all contexts, such as when the path is used as the key and the the store object info the value in map.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Store Path",
        ),
    ] = None
    narHash: Annotated[
        str,
        Field(
            description="Hash of the [file system object](@docroot@/store/file-system-object.md) part of the store object when serialized as a [Nix Archive](@docroot@/store/file-system-object/content-address.md#serial-nix-archive).\n",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="NAR Hash",
        ),
    ]
    narSize: Annotated[
        int,
        Field(
            description="Size of the [file system object](@docroot@/store/file-system-object.md) part of the store object when serialized as a [Nix Archive](@docroot@/store/file-system-object/content-address.md#serial-nix-archive).\n",
            ge=0,
            title="NAR Size",
        ),
    ]
    references: Annotated[
        list[Reference],
        Field(
            description="An array of [store paths](@docroot@/store/store-path.md), possibly including this one.\n",
            title="References",
        ),
    ]
    ca: Annotated[
        Ca2 | None,
        Field(
            description="If the store object is [content-addressed](@docroot@/store/store-object/content-address.md),\nthis is the content address of this store object's file system object, used to compute its store path.\nOtherwise (i.e. if it is [input-addressed](@docroot@/glossary.md#gloss-input-addressed-store-object)), this is `null`.\n",
            title="Content Address",
        ),
    ]
    storeDir: Annotated[
        str,
        Field(
            description="The [store directory](@docroot@/store/store-path.md#store-directory) this store object belongs to (e.g. `/nix/store`).\n",
            title="Store Directory",
        ),
    ]
    deriver: Annotated[
        Deriver | None,
        Field(
            description='If known, the path to the [store derivation](@docroot@/glossary.md#gloss-store-derivation) from which this store object was produced.\nOtherwise `null`.\n\n> This is an "impure" field that may not be included in certain contexts.\n',
            title="Deriver",
        ),
    ]
    registrationTime: Annotated[
        int | None,
        Field(
            description='If known, when this derivation was added to the store (Unix timestamp).\nOtherwise `null`.\n\n> This is an "impure" field that may not be included in certain contexts.\n',
            title="Registration Time",
        ),
    ]
    ultimate: Annotated[
        bool,
        Field(
            description='Whether this store object is trusted because we built it ourselves, rather than substituted a build product from elsewhere.\n\n> This is an "impure" field that may not be included in certain contexts.\n',
            title="Ultimate",
        ),
    ]
    signatures: Annotated[
        list[str],
        Field(
            description='Signatures claiming that this store object is what it claims to be.\nNot relevant for [content-addressed](@docroot@/store/store-object/content-address.md) store objects,\nbut useful for [input-addressed](@docroot@/glossary.md#gloss-input-addressed-store-object) store objects.\n\n> This is an "impure" field that may not be included in certain contexts.\n',
            title="Signatures",
        ),
    ]
    closureSize: Annotated[
        int | None,
        Field(
            description="The total size of this store object and every other object in its [closure](@docroot@/glossary.md#gloss-closure).\n\n> This field is not stored at all, but computed by traversing the other fields across all the store objects in a closure.\n",
            ge=0,
            title="Closure Size",
        ),
    ] = None
    url: Annotated[
        str,
        Field(
            description='Where to download a compressed archive of the file system objects of this store object.\n\n> This is an impure "`.narinfo`" field that may not be included in certain contexts.\n',
            title="URL",
        ),
    ]
    compression: Annotated[
        str,
        Field(
            description='The compression format that the archive is in.\n\n> This is an impure "`.narinfo`" field that may not be included in certain contexts.\n',
            title="Compression",
        ),
    ]
    downloadHash: Annotated[
        str,
        Field(
            description='A digest for the compressed archive itself, as opposed to the data contained within.\n\n> This is an impure "`.narinfo`" field that may not be included in certain contexts.\n',
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="Download Hash",
        ),
    ]
    downloadSize: Annotated[
        int,
        Field(
            description='The size of the compressed archive itself.\n\n> This is an impure "`.narinfo`" field that may not be included in certain contexts.\n',
            ge=0,
            title="Download Size",
        ),
    ]
    closureDownloadSize: Annotated[
        int | None,
        Field(
            description='The total size of the compressed archive itself for this object, and the compressed archive of every object in this object\'s [closure](@docroot@/glossary.md#gloss-closure).\n\n> This is an impure "`.narinfo`" field that may not be included in certain contexts.\n\n> This field is not stored at all, but computed by traversing the other fields across all the store objects in a closure.\n',
            ge=0,
            title="Closure Download Size",
        ),
    ] = None


class StoreObjectInfoV2(
    RootModel[StoreObjectInfoV21 | StoreObjectInfoV22 | StoreObjectInfoV23]
):
    root: Annotated[
        StoreObjectInfoV21 | StoreObjectInfoV22 | StoreObjectInfoV23,
        Field(
            description='Information about a [store object](@docroot@/store/store-object.md).\n\nThis schema describes the JSON representation of store object metadata as returned by commands like [`nix path-info --json`](@docroot@/command-ref/new-cli/nix3-path-info.md).\n\n> **Warning**\n>\n> This JSON format is currently\n> [**experimental**](@docroot@/development/experimental-features.md#xp-feature-nix-command)\n> and subject to change.\n\n### Field Categories\n\nStore object information can come in a few different variations.\n\nFirstly, "impure" fields, which contain non-intrinsic information about the store object, may or may not be included.\n\nSecond, binary cache stores have extra non-intrinsic infomation about the store objects they contain.\n\nThirdly, [`nix path-info --json --closure-size`](@docroot@/command-ref/new-cli/nix3-path-info.html#opt-closure-size) can compute some extra information about not just the single store object in question, but the store object and its [closure](@docroot@/glossary.md#gloss-closure).\n\nThe impure and NAR fields are grouped into separate variants below.\nSee their descriptions for additional information.\nThe closure fields however as just included as optional fields, to avoid a combinatorial explosion of variants.\n',
            title="Store Object Info v2",
        ),
    ]


class Ca3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    method: Annotated[
        Method,
        Field(
            description="A string representing the [method](@docroot@/store/store-object/content-address.md) of content addressing that is chosen.\n\nValid method strings are:\n\n- [`flat`](@docroot@/store/store-object/content-address.md#method-flat) (provided the contents are a single file)\n- [`nar`](@docroot@/store/store-object/content-address.md#method-nix-archive)\n- [`text`](@docroot@/store/store-object/content-address.md#method-text)\n- [`git`](@docroot@/store/store-object/content-address.md#method-git)\n",
            title="Content-Addressing Method",
        ),
    ]
    hash: Annotated[
        str,
        Field(
            description="This would be the content-address itself.\n\nFor all current methods, this is just a content address of the file system object of the store object, [as described in the store chapter](@docroot@/store/file-system-object/content-address.md), and not of the store object as a whole.\nIn particular, the references of the store object are *not* taken into account with this hash (and currently-supported methods).\n",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="Content Address",
        ),
    ]


class Base(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    version: Annotated[
        Literal[2],
        Field(
            description='Must be `2`.\nThis is a guard that allows us to continue evolving this format.\nHere is the rough version history:\n\n- Version 0: `.narinfo` line-oriented format\n\n- Version 1: Original JSON format, with ugly `"r:sha256"` inherited from `.narinfo` format.\n\n- Version 2: Use structured JSON type for `ca`\n',
            title="Format version (must be 2)",
        ),
    ] = 2
    path: Annotated[
        str | None,
        Field(
            description="[Store path](@docroot@/store/store-path.md) to the given store object.\n\nNote: This field may not be present in all contexts, such as when the path is used as the key and the the store object info the value in map.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Store Path",
        ),
    ] = None
    narHash: Annotated[
        str,
        Field(
            description="Hash of the [file system object](@docroot@/store/file-system-object.md) part of the store object when serialized as a [Nix Archive](@docroot@/store/file-system-object/content-address.md#serial-nix-archive).\n",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="NAR Hash",
        ),
    ]
    narSize: Annotated[
        int,
        Field(
            description="Size of the [file system object](@docroot@/store/file-system-object.md) part of the store object when serialized as a [Nix Archive](@docroot@/store/file-system-object/content-address.md#serial-nix-archive).\n",
            ge=0,
            title="NAR Size",
        ),
    ]
    references: Annotated[
        list[Reference],
        Field(
            description="An array of [store paths](@docroot@/store/store-path.md), possibly including this one.\n",
            title="References",
        ),
    ]
    ca: Annotated[
        Ca3 | None,
        Field(
            description="If the store object is [content-addressed](@docroot@/store/store-object/content-address.md),\nthis is the content address of this store object's file system object, used to compute its store path.\nOtherwise (i.e. if it is [input-addressed](@docroot@/glossary.md#gloss-input-addressed-store-object)), this is `null`.\n",
            title="Content Address",
        ),
    ]
    storeDir: Annotated[
        str,
        Field(
            description="The [store directory](@docroot@/store/store-path.md#store-directory) this store object belongs to (e.g. `/nix/store`).\n",
            title="Store Directory",
        ),
    ]


class Ca4(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    method: Annotated[
        Method,
        Field(
            description="A string representing the [method](@docroot@/store/store-object/content-address.md) of content addressing that is chosen.\n\nValid method strings are:\n\n- [`flat`](@docroot@/store/store-object/content-address.md#method-flat) (provided the contents are a single file)\n- [`nar`](@docroot@/store/store-object/content-address.md#method-nix-archive)\n- [`text`](@docroot@/store/store-object/content-address.md#method-text)\n- [`git`](@docroot@/store/store-object/content-address.md#method-git)\n",
            title="Content-Addressing Method",
        ),
    ]
    hash: Annotated[
        str,
        Field(
            description="This would be the content-address itself.\n\nFor all current methods, this is just a content address of the file system object of the store object, [as described in the store chapter](@docroot@/store/file-system-object/content-address.md), and not of the store object as a whole.\nIn particular, the references of the store object are *not* taken into account with this hash (and currently-supported methods).\n",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="Content Address",
        ),
    ]


class Impure(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    version: Annotated[
        Literal[2],
        Field(
            description='Must be `2`.\nThis is a guard that allows us to continue evolving this format.\nHere is the rough version history:\n\n- Version 0: `.narinfo` line-oriented format\n\n- Version 1: Original JSON format, with ugly `"r:sha256"` inherited from `.narinfo` format.\n\n- Version 2: Use structured JSON type for `ca`\n',
            title="Format version (must be 2)",
        ),
    ] = 2
    path: Annotated[
        str | None,
        Field(
            description="[Store path](@docroot@/store/store-path.md) to the given store object.\n\nNote: This field may not be present in all contexts, such as when the path is used as the key and the the store object info the value in map.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Store Path",
        ),
    ] = None
    narHash: Annotated[
        str,
        Field(
            description="Hash of the [file system object](@docroot@/store/file-system-object.md) part of the store object when serialized as a [Nix Archive](@docroot@/store/file-system-object/content-address.md#serial-nix-archive).\n",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="NAR Hash",
        ),
    ]
    narSize: Annotated[
        int,
        Field(
            description="Size of the [file system object](@docroot@/store/file-system-object.md) part of the store object when serialized as a [Nix Archive](@docroot@/store/file-system-object/content-address.md#serial-nix-archive).\n",
            ge=0,
            title="NAR Size",
        ),
    ]
    references: Annotated[
        list[Reference],
        Field(
            description="An array of [store paths](@docroot@/store/store-path.md), possibly including this one.\n",
            title="References",
        ),
    ]
    ca: Annotated[
        Ca4 | None,
        Field(
            description="If the store object is [content-addressed](@docroot@/store/store-object/content-address.md),\nthis is the content address of this store object's file system object, used to compute its store path.\nOtherwise (i.e. if it is [input-addressed](@docroot@/glossary.md#gloss-input-addressed-store-object)), this is `null`.\n",
            title="Content Address",
        ),
    ]
    storeDir: Annotated[
        str,
        Field(
            description="The [store directory](@docroot@/store/store-path.md#store-directory) this store object belongs to (e.g. `/nix/store`).\n",
            title="Store Directory",
        ),
    ]
    deriver: Annotated[
        Deriver | None,
        Field(
            description='If known, the path to the [store derivation](@docroot@/glossary.md#gloss-store-derivation) from which this store object was produced.\nOtherwise `null`.\n\n> This is an "impure" field that may not be included in certain contexts.\n',
            title="Deriver",
        ),
    ]
    registrationTime: Annotated[
        int | None,
        Field(
            description='If known, when this derivation was added to the store (Unix timestamp).\nOtherwise `null`.\n\n> This is an "impure" field that may not be included in certain contexts.\n',
            title="Registration Time",
        ),
    ]
    ultimate: Annotated[
        bool,
        Field(
            description='Whether this store object is trusted because we built it ourselves, rather than substituted a build product from elsewhere.\n\n> This is an "impure" field that may not be included in certain contexts.\n',
            title="Ultimate",
        ),
    ]
    signatures: Annotated[
        list[str],
        Field(
            description='Signatures claiming that this store object is what it claims to be.\nNot relevant for [content-addressed](@docroot@/store/store-object/content-address.md) store objects,\nbut useful for [input-addressed](@docroot@/glossary.md#gloss-input-addressed-store-object) store objects.\n\n> This is an "impure" field that may not be included in certain contexts.\n',
            title="Signatures",
        ),
    ]
    closureSize: Annotated[
        int | None,
        Field(
            description="The total size of this store object and every other object in its [closure](@docroot@/glossary.md#gloss-closure).\n\n> This field is not stored at all, but computed by traversing the other fields across all the store objects in a closure.\n",
            ge=0,
            title="Closure Size",
        ),
    ] = None


class Ca5(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    method: Annotated[
        Method,
        Field(
            description="A string representing the [method](@docroot@/store/store-object/content-address.md) of content addressing that is chosen.\n\nValid method strings are:\n\n- [`flat`](@docroot@/store/store-object/content-address.md#method-flat) (provided the contents are a single file)\n- [`nar`](@docroot@/store/store-object/content-address.md#method-nix-archive)\n- [`text`](@docroot@/store/store-object/content-address.md#method-text)\n- [`git`](@docroot@/store/store-object/content-address.md#method-git)\n",
            title="Content-Addressing Method",
        ),
    ]
    hash: Annotated[
        str,
        Field(
            description="This would be the content-address itself.\n\nFor all current methods, this is just a content address of the file system object of the store object, [as described in the store chapter](@docroot@/store/file-system-object/content-address.md), and not of the store object as a whole.\nIn particular, the references of the store object are *not* taken into account with this hash (and currently-supported methods).\n",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="Content Address",
        ),
    ]


class NarInfo(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    version: Annotated[
        Literal[2],
        Field(
            description='Must be `2`.\nThis is a guard that allows us to continue evolving this format.\nHere is the rough version history:\n\n- Version 0: `.narinfo` line-oriented format\n\n- Version 1: Original JSON format, with ugly `"r:sha256"` inherited from `.narinfo` format.\n\n- Version 2: Use structured JSON type for `ca`\n',
            title="Format version (must be 2)",
        ),
    ] = 2
    path: Annotated[
        str | None,
        Field(
            description="[Store path](@docroot@/store/store-path.md) to the given store object.\n\nNote: This field may not be present in all contexts, such as when the path is used as the key and the the store object info the value in map.\n",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Store Path",
        ),
    ] = None
    narHash: Annotated[
        str,
        Field(
            description="Hash of the [file system object](@docroot@/store/file-system-object.md) part of the store object when serialized as a [Nix Archive](@docroot@/store/file-system-object/content-address.md#serial-nix-archive).\n",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="NAR Hash",
        ),
    ]
    narSize: Annotated[
        int,
        Field(
            description="Size of the [file system object](@docroot@/store/file-system-object.md) part of the store object when serialized as a [Nix Archive](@docroot@/store/file-system-object/content-address.md#serial-nix-archive).\n",
            ge=0,
            title="NAR Size",
        ),
    ]
    references: Annotated[
        list[Reference],
        Field(
            description="An array of [store paths](@docroot@/store/store-path.md), possibly including this one.\n",
            title="References",
        ),
    ]
    ca: Annotated[
        Ca5 | None,
        Field(
            description="If the store object is [content-addressed](@docroot@/store/store-object/content-address.md),\nthis is the content address of this store object's file system object, used to compute its store path.\nOtherwise (i.e. if it is [input-addressed](@docroot@/glossary.md#gloss-input-addressed-store-object)), this is `null`.\n",
            title="Content Address",
        ),
    ]
    storeDir: Annotated[
        str,
        Field(
            description="The [store directory](@docroot@/store/store-path.md#store-directory) this store object belongs to (e.g. `/nix/store`).\n",
            title="Store Directory",
        ),
    ]
    deriver: Annotated[
        Deriver | None,
        Field(
            description='If known, the path to the [store derivation](@docroot@/glossary.md#gloss-store-derivation) from which this store object was produced.\nOtherwise `null`.\n\n> This is an "impure" field that may not be included in certain contexts.\n',
            title="Deriver",
        ),
    ]
    registrationTime: Annotated[
        int | None,
        Field(
            description='If known, when this derivation was added to the store (Unix timestamp).\nOtherwise `null`.\n\n> This is an "impure" field that may not be included in certain contexts.\n',
            title="Registration Time",
        ),
    ]
    ultimate: Annotated[
        bool,
        Field(
            description='Whether this store object is trusted because we built it ourselves, rather than substituted a build product from elsewhere.\n\n> This is an "impure" field that may not be included in certain contexts.\n',
            title="Ultimate",
        ),
    ]
    signatures: Annotated[
        list[str],
        Field(
            description='Signatures claiming that this store object is what it claims to be.\nNot relevant for [content-addressed](@docroot@/store/store-object/content-address.md) store objects,\nbut useful for [input-addressed](@docroot@/glossary.md#gloss-input-addressed-store-object) store objects.\n\n> This is an "impure" field that may not be included in certain contexts.\n',
            title="Signatures",
        ),
    ]
    closureSize: Annotated[
        int | None,
        Field(
            description="The total size of this store object and every other object in its [closure](@docroot@/glossary.md#gloss-closure).\n\n> This field is not stored at all, but computed by traversing the other fields across all the store objects in a closure.\n",
            ge=0,
            title="Closure Size",
        ),
    ] = None
    url: Annotated[
        str,
        Field(
            description='Where to download a compressed archive of the file system objects of this store object.\n\n> This is an impure "`.narinfo`" field that may not be included in certain contexts.\n',
            title="URL",
        ),
    ]
    compression: Annotated[
        str,
        Field(
            description='The compression format that the archive is in.\n\n> This is an impure "`.narinfo`" field that may not be included in certain contexts.\n',
            title="Compression",
        ),
    ]
    downloadHash: Annotated[
        str,
        Field(
            description='A digest for the compressed archive itself, as opposed to the data contained within.\n\n> This is an impure "`.narinfo`" field that may not be included in certain contexts.\n',
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="Download Hash",
        ),
    ]
    downloadSize: Annotated[
        int,
        Field(
            description='The size of the compressed archive itself.\n\n> This is an impure "`.narinfo`" field that may not be included in certain contexts.\n',
            ge=0,
            title="Download Size",
        ),
    ]
    closureDownloadSize: Annotated[
        int | None,
        Field(
            description='The total size of the compressed archive itself for this object, and the compressed archive of every object in this object\'s [closure](@docroot@/glossary.md#gloss-closure).\n\n> This is an impure "`.narinfo`" field that may not be included in certain contexts.\n\n> This field is not stored at all, but computed by traversing the other fields across all the store objects in a closure.\n',
            ge=0,
            title="Closure Download Size",
        ),
    ] = None
