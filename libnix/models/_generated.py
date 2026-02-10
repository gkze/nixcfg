"""Auto-generated Pydantic models from Nix JSON schemas.

DO NOT EDIT MANUALLY. Regenerate with:
    python -m libnix.schemas._codegen
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

# === hash-v1 ===


class Hash(RootModel[str]):
    root: Annotated[
        str,
        Field(
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
    method: Annotated[Method, Field(title="Content-Addressing Method")]
    hash: Annotated[
        str,
        Field(
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
    contents: str
    executable: bool | None = False


class FileSystemObject3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["symlink"] = "symlink"
    target: str


class Regular(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["regular"] = "regular"
    contents: str
    executable: bool | None = False


class Symlink(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["symlink"] = "symlink"
    target: str


class FileSystemObject2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["directory"] = "directory"
    entries: dict[str, FileSystemObject]


class Directory(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["directory"] = "directory"
    entries: dict[str, FileSystemObject]


class FileSystemObject(
    RootModel[FileSystemObject1 | FileSystemObject2 | FileSystemObject3],
):
    root: Annotated[
        FileSystemObject1 | FileSystemObject2 | FileSystemObject3,
        Field(title="File System Object"),
    ]


FileSystemObject2.model_rebuild()
Directory.model_rebuild()
# === build-trace-entry-v2 ===


class BuildTraceEntry(BaseModel):
    id: Annotated[
        str,
        Field(
            pattern="^sha256:[0-9a-f]{64}![a-zA-Z_][a-zA-Z0-9_-]*$",
            title="Derivation Output ID",
        ),
    ]
    out_path: Annotated[
        dict[str, dict[str, Any]],
        Field(
            alias="outPath",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Output Store Path",
        ),
    ]
    signatures: Annotated[list[str], Field(title="Build Signatures")]


class Key(BaseModel):
    id: Annotated[
        str,
        Field(
            pattern="^sha256:[0-9a-f]{64}![a-zA-Z_][a-zA-Z0-9_-]*$",
            title="Derivation Output ID",
        ),
    ]


class Value(BaseModel):
    out_path: Annotated[
        dict[str, dict[str, Any]],
        Field(
            alias="outPath",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Output Store Path",
        ),
    ]
    signatures: Annotated[list[str], Field(title="Build Signatures")]


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
            pattern="^sha256:[0-9a-f]{64}![a-zA-Z_][a-zA-Z0-9_-]*$",
            title="Derivation Output ID",
        ),
    ]
    out_path: Annotated[
        dict[str, dict[str, Any]],
        Field(
            alias="outPath",
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Output Store Path",
        ),
    ]
    signatures: Annotated[list[str], Field(title="Build Signatures")]


class BuildResult1(BaseModel):
    times_built: Annotated[
        int | None,
        Field(alias="timesBuilt", ge=0, title="Times built"),
    ] = None
    start_time: Annotated[
        int | None,
        Field(alias="startTime", ge=0, title="Start time"),
    ] = None
    stop_time: Annotated[
        int | None,
        Field(alias="stopTime", ge=0, title="Stop time"),
    ] = None
    cpu_user: Annotated[
        int | None,
        Field(alias="cpuUser", ge=0, title="User CPU time"),
    ] = None
    cpu_system: Annotated[
        int | None,
        Field(alias="cpuSystem", ge=0, title="System CPU time"),
    ] = None
    success: Annotated[Literal[True], Field(title="Success indicator")] = True
    status: Annotated[Status, Field(title="Success status")]
    built_outputs: Annotated[
        dict[str, BuiltOutputs],
        Field(alias="builtOutputs", title="Built outputs"),
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
    times_built: Annotated[
        int | None,
        Field(alias="timesBuilt", ge=0, title="Times built"),
    ] = None
    start_time: Annotated[
        int | None,
        Field(alias="startTime", ge=0, title="Start time"),
    ] = None
    stop_time: Annotated[
        int | None,
        Field(alias="stopTime", ge=0, title="Stop time"),
    ] = None
    cpu_user: Annotated[
        int | None,
        Field(alias="cpuUser", ge=0, title="User CPU time"),
    ] = None
    cpu_system: Annotated[
        int | None,
        Field(alias="cpuSystem", ge=0, title="System CPU time"),
    ] = None
    success: Annotated[Literal[False], Field(title="Success indicator")] = False
    status: Annotated[Status1, Field(title="Failure status")]
    error_msg: Annotated[str, Field(alias="errorMsg", title="Error message")]
    is_non_deterministic: Annotated[
        bool | None,
        Field(alias="isNonDeterministic", title="Non-deterministic flag"),
    ] = None


class BuildResult(RootModel[BuildResult1 | BuildResult2]):
    root: Annotated[BuildResult1 | BuildResult2, Field(title="Build Result")]


class Status2(StrEnum):
    BUILT = "Built"
    SUBSTITUTED = "Substituted"
    ALREADY_VALID = "AlreadyValid"
    RESOLVES_TO_ALREADY_VALID = "ResolvesToAlreadyValid"


class Success(BaseModel):
    success: Annotated[Literal[True], Field(title="Success indicator")] = True
    status: Annotated[Status2, Field(title="Success status")]
    built_outputs: Annotated[
        dict[str, BuiltOutputs],
        Field(alias="builtOutputs", title="Built outputs"),
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
    success: Annotated[Literal[False], Field(title="Success indicator")] = False
    status: Annotated[Status3, Field(title="Failure status")]
    error_msg: Annotated[str, Field(alias="errorMsg", title="Error message")]
    is_non_deterministic: Annotated[
        bool | None,
        Field(alias="isNonDeterministic", title="Non-deterministic flag"),
    ] = None


# === deriving-path-v1 ===


class DerivingPath1(RootModel[str]):
    root: Annotated[
        str,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class DerivingPath2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drv_path: Annotated[DerivingPath, Field(alias="drvPath")]
    output: str


class DerivingPath(RootModel[DerivingPath1 | DerivingPath2]):
    root: Annotated[DerivingPath1 | DerivingPath2, Field(title="Deriving Path")]


DerivingPath2.model_rebuild()
# === derivation-v4 ===


class Outputs(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    path: Annotated[
        str,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Output path",
        ),
    ]


class Outputs1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    method: Any
    hash: Annotated[Any, Field(title="Expected hash value")]


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
    method: Annotated[Method, Field(title="Content-Addressing Method")]
    hash_algo: Annotated[HashAlgo, Field(alias="hashAlgo", title="Hash algorithm")]


class Outputs3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    impure: Literal[True] = True
    method: Annotated[Method, Field(title="Content-Addressing Method")]
    hash_algo: Annotated[HashAlgo, Field(alias="hashAlgo", title="Hash algorithm")]


class Src(RootModel[str]):
    root: Annotated[
        str,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Store Path",
        ),
    ]


class Drvs(BaseModel):
    outputs: Annotated[list[str] | None, Field(title="Output Names")] = None
    dynamic_outputs: Annotated[dict[str, Any] | None, Field(alias="dynamicOutputs")] = (
        None
    )


class Inputs(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    srcs: Annotated[list[Src], Field(title="Input source paths")]
    drvs: Annotated[dict[str, list[str] | Drvs], Field(title="Input derivations")]


class Derivation(BaseModel):
    name: Annotated[str, Field(title="Derivation name")]
    version: Annotated[Literal[4], Field(title="Format version (must be 4)")] = 4
    outputs: Annotated[
        dict[str, Outputs | Outputs1 | Outputs2 | dict[str, Any] | Outputs3],
        Field(title="Output specifications"),
    ]
    inputs: Annotated[Inputs, Field(title="Derivation inputs")]
    system: Annotated[str, Field(title="Build system type")]
    builder: Annotated[str, Field(title="Build program path")]
    args: Annotated[list[str], Field(title="Builder arguments")]
    env: Annotated[dict[str, str], Field(title="Environment variables")]
    structured_attrs: Annotated[
        dict[str, Any] | None,
        Field(alias="structuredAttrs", title="Structured attributes"),
    ] = None


class Output(RootModel[Any]):
    root: Any


class OutputName(RootModel[str]):
    root: Annotated[str, Field(title="Output name")]


class OutputNames(RootModel[list[str]]):
    root: Annotated[list[str], Field(title="Output Names")]


class DynamicOutputs1(BaseModel):
    outputs: Annotated[list[str] | None, Field(title="Output Names")] = None
    dynamic_outputs: Annotated[dict[str, Any] | None, Field(alias="dynamicOutputs")] = (
        None
    )


class DynamicOutputs(BaseModel):
    outputs: Annotated[list[str] | None, Field(title="Output Names")] = None
    dynamic_outputs: Annotated[
        DynamicOutputs1 | None,
        Field(alias="dynamicOutputs", title="Dynamic Outputs"),
    ] = None


# === derivation-options-v1 ===


class AllowedReferences(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drv_path: Annotated[
        Literal["self"],
        Field(alias="drvPath", title="This derivation"),
    ] = "self"
    output: Annotated[str, Field(title="Output Name")]


class AllowedReferences1(RootModel[str]):
    root: Annotated[
        str,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class AllowedRequisites(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drv_path: Annotated[
        Literal["self"],
        Field(alias="drvPath", title="This derivation"),
    ] = "self"
    output: Annotated[str, Field(title="Output Name")]


class AllowedRequisites1(RootModel[str]):
    root: Annotated[
        str,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class DisallowedReferences(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drv_path: Annotated[
        Literal["self"],
        Field(alias="drvPath", title="This derivation"),
    ] = "self"
    output: Annotated[str, Field(title="Output Name")]


class DisallowedReferences1(RootModel[str]):
    root: Annotated[
        str,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class DisallowedRequisites(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drv_path: Annotated[
        Literal["self"],
        Field(alias="drvPath", title="This derivation"),
    ] = "self"
    output: Annotated[str, Field(title="Output Name")]


class DisallowedRequisites1(RootModel[str]):
    root: Annotated[
        str,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class AllowedReferences3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drv_path: Annotated[
        Literal["self"],
        Field(alias="drvPath", title="This derivation"),
    ] = "self"
    output: Annotated[str, Field(title="Output Name")]


class AllowedReferences4(RootModel[str]):
    root: Annotated[
        str,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class AllowedRequisites3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drv_path: Annotated[
        Literal["self"],
        Field(alias="drvPath", title="This derivation"),
    ] = "self"
    output: Annotated[str, Field(title="Output Name")]


class AllowedRequisites4(RootModel[str]):
    root: Annotated[
        str,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class DisallowedReferences3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drv_path: Annotated[
        Literal["self"],
        Field(alias="drvPath", title="This derivation"),
    ] = "self"
    output: Annotated[str, Field(title="Output Name")]


class DisallowedReferences4(RootModel[str]):
    root: Annotated[
        str,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class DisallowedRequisites3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drv_path: Annotated[
        Literal["self"],
        Field(alias="drvPath", title="This derivation"),
    ] = "self"
    output: Annotated[str, Field(title="Output Name")]


class DisallowedRequisites4(RootModel[str]):
    root: Annotated[
        str,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class ExportReferencesGraph(RootModel[str]):
    root: Annotated[
        str,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class AllowedReferences6(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drv_path: Annotated[
        Literal["self"],
        Field(alias="drvPath", title="This derivation"),
    ] = "self"
    output: Annotated[str, Field(title="Output Name")]


class AllowedReferences7(RootModel[str]):
    root: Annotated[
        str,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class AllowedRequisites6(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drv_path: Annotated[
        Literal["self"],
        Field(alias="drvPath", title="This derivation"),
    ] = "self"
    output: Annotated[str, Field(title="Output Name")]


class AllowedRequisites7(RootModel[str]):
    root: Annotated[
        str,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class DisallowedReferences6(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drv_path: Annotated[
        Literal["self"],
        Field(alias="drvPath", title="This derivation"),
    ] = "self"
    output: Annotated[str, Field(title="Output Name")]


class DisallowedReferences7(RootModel[str]):
    root: Annotated[
        str,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class DisallowedRequisites6(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drv_path: Annotated[
        Literal["self"],
        Field(alias="drvPath", title="This derivation"),
    ] = "self"
    output: Annotated[str, Field(title="Output Name")]


class DisallowedRequisites7(RootModel[str]):
    root: Annotated[
        str,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class DrvRef1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drv_path: Annotated[
        Literal["self"],
        Field(alias="drvPath", title="This derivation"),
    ] = "self"
    output: Annotated[str, Field(title="Output Name")]


class DrvRef2(RootModel[str]):
    root: Annotated[
        str,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Constant",
        ),
    ]


class AllowedReferences2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drv_path: Annotated[DerivationOptions, Field(alias="drvPath")]
    output: str


class AllowedRequisites2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drv_path: Annotated[DerivationOptions, Field(alias="drvPath")]
    output: str


class DisallowedReferences2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drv_path: Annotated[DerivationOptions, Field(alias="drvPath")]
    output: str


class DisallowedRequisites2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drv_path: Annotated[DerivationOptions, Field(alias="drvPath")]
    output: str


class ForAllOutputs(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    ignore_self_refs: Annotated[
        bool,
        Field(alias="ignoreSelfRefs", title="Ignore Self References"),
    ]
    max_size: Annotated[int | None, Field(alias="maxSize", ge=0, title="Maximum Size")]
    max_closure_size: Annotated[
        int | None,
        Field(alias="maxClosureSize", ge=0, title="Maximum Closure Size"),
    ]
    allowed_references: Annotated[
        list[AllowedReferences | AllowedReferences1 | AllowedReferences2] | None,
        Field(alias="allowedReferences", title="Allowed References"),
    ]
    allowed_requisites: Annotated[
        list[AllowedRequisites | AllowedRequisites1 | AllowedRequisites2] | None,
        Field(alias="allowedRequisites", title="Allowed Requisites"),
    ]
    disallowed_references: Annotated[
        list[DisallowedReferences | DisallowedReferences1 | DisallowedReferences2],
        Field(alias="disallowedReferences", title="Disallowed References"),
    ]
    disallowed_requisites: Annotated[
        list[DisallowedRequisites | DisallowedRequisites1 | DisallowedRequisites2],
        Field(alias="disallowedRequisites", title="Disallowed Requisites"),
    ]


class OutputChecks(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    for_all_outputs: Annotated[
        ForAllOutputs,
        Field(alias="forAllOutputs", title="Output Check Specification"),
    ]


class PerOutput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    ignore_self_refs: Annotated[
        bool,
        Field(alias="ignoreSelfRefs", title="Ignore Self References"),
    ]
    max_size: Annotated[int | None, Field(alias="maxSize", ge=0, title="Maximum Size")]
    max_closure_size: Annotated[
        int | None,
        Field(alias="maxClosureSize", ge=0, title="Maximum Closure Size"),
    ]
    allowed_references: Annotated[
        list[AllowedReferences3 | AllowedReferences4 | AllowedReferences2] | None,
        Field(alias="allowedReferences", title="Allowed References"),
    ]
    allowed_requisites: Annotated[
        list[AllowedRequisites3 | AllowedRequisites4 | AllowedRequisites2] | None,
        Field(alias="allowedRequisites", title="Allowed Requisites"),
    ]
    disallowed_references: Annotated[
        list[DisallowedReferences3 | DisallowedReferences4 | DisallowedReferences2],
        Field(alias="disallowedReferences", title="Disallowed References"),
    ]
    disallowed_requisites: Annotated[
        list[DisallowedRequisites3 | DisallowedRequisites4 | DisallowedRequisites2],
        Field(alias="disallowedRequisites", title="Disallowed Requisites"),
    ]


class OutputChecks1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    per_output: Annotated[dict[str, PerOutput], Field(alias="perOutput")]


class ExportReferencesGraph1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drv_path: Annotated[DerivationOptions, Field(alias="drvPath")]
    output: str


class DerivationOptions(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    output_checks: Annotated[
        OutputChecks | OutputChecks1,
        Field(alias="outputChecks", title="Output Check"),
    ]
    unsafe_discard_references: Annotated[
        dict[str, list[str]],
        Field(alias="unsafeDiscardReferences", title="Unsafe Discard References"),
    ]
    pass_as_file: Annotated[list[str], Field(alias="passAsFile", title="Pass As File")]
    export_references_graph: Annotated[
        dict[str, list[ExportReferencesGraph | ExportReferencesGraph1]],
        Field(alias="exportReferencesGraph", title="Export References Graph"),
    ]
    additional_sandbox_profile: Annotated[
        str,
        Field(alias="additionalSandboxProfile", title="Additional Sandbox Profile"),
    ]
    no_chroot: Annotated[bool, Field(alias="noChroot", title="No Chroot")]
    impure_host_deps: Annotated[
        list[str],
        Field(alias="impureHostDeps", title="Impure Host Dependencies"),
    ]
    impure_env_vars: Annotated[
        list[str],
        Field(alias="impureEnvVars", title="Impure Environment Variables"),
    ]
    allow_local_networking: Annotated[
        bool,
        Field(alias="allowLocalNetworking", title="Allow Local Networking"),
    ]
    required_system_features: Annotated[
        list[str],
        Field(alias="requiredSystemFeatures", title="Required System Features"),
    ]
    prefer_local_build: Annotated[
        bool,
        Field(alias="preferLocalBuild", title="Prefer Local Build"),
    ]
    allow_substitutes: Annotated[
        bool,
        Field(alias="allowSubstitutes", title="Allow Substitutes"),
    ]


class OutputCheckSpec(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    ignore_self_refs: Annotated[
        bool,
        Field(alias="ignoreSelfRefs", title="Ignore Self References"),
    ]
    max_size: Annotated[int | None, Field(alias="maxSize", ge=0, title="Maximum Size")]
    max_closure_size: Annotated[
        int | None,
        Field(alias="maxClosureSize", ge=0, title="Maximum Closure Size"),
    ]
    allowed_references: Annotated[
        list[AllowedReferences6 | AllowedReferences7 | AllowedReferences2] | None,
        Field(alias="allowedReferences", title="Allowed References"),
    ]
    allowed_requisites: Annotated[
        list[AllowedRequisites6 | AllowedRequisites7 | AllowedRequisites2] | None,
        Field(alias="allowedRequisites", title="Allowed Requisites"),
    ]
    disallowed_references: Annotated[
        list[DisallowedReferences6 | DisallowedReferences7 | DisallowedReferences2],
        Field(alias="disallowedReferences", title="Disallowed References"),
    ]
    disallowed_requisites: Annotated[
        list[DisallowedRequisites6 | DisallowedRequisites7 | DisallowedRequisites2],
        Field(alias="disallowedRequisites", title="Disallowed Requisites"),
    ]


class DrvRef3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    drv_path: Annotated[DerivationOptions, Field(alias="drvPath")]
    output: str


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
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Store Path",
        ),
    ]


class Ca(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    method: Annotated[Method, Field(title="Content-Addressing Method")]
    hash: Annotated[
        str,
        Field(
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
    version: Annotated[Literal[2], Field(title="Format version (must be 2)")] = 2
    path: Annotated[
        str | None,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Store Path",
        ),
    ] = None
    nar_hash: Annotated[
        str,
        Field(
            alias="narHash",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="NAR Hash",
        ),
    ]
    nar_size: Annotated[int, Field(alias="narSize", ge=0, title="NAR Size")]
    references: Annotated[list[Reference], Field(title="References")]
    ca: Annotated[Ca | None, Field(title="Content Address")]
    store_dir: Annotated[str, Field(alias="storeDir", title="Store Directory")]


class Ca1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    method: Annotated[Method, Field(title="Content-Addressing Method")]
    hash: Annotated[
        str,
        Field(
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
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Store Path",
        ),
    ]


class StoreObjectInfoV22(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    version: Annotated[Literal[2], Field(title="Format version (must be 2)")] = 2
    path: Annotated[
        str | None,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Store Path",
        ),
    ] = None
    nar_hash: Annotated[
        str,
        Field(
            alias="narHash",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="NAR Hash",
        ),
    ]
    nar_size: Annotated[int, Field(alias="narSize", ge=0, title="NAR Size")]
    references: Annotated[list[Reference], Field(title="References")]
    ca: Annotated[Ca1 | None, Field(title="Content Address")]
    store_dir: Annotated[str, Field(alias="storeDir", title="Store Directory")]
    deriver: Annotated[Deriver | None, Field(title="Deriver")]
    registration_time: Annotated[
        int | None,
        Field(alias="registrationTime", title="Registration Time"),
    ]
    ultimate: Annotated[bool, Field(title="Ultimate")]
    signatures: Annotated[list[str], Field(title="Signatures")]
    closure_size: Annotated[
        int | None,
        Field(alias="closureSize", ge=0, title="Closure Size"),
    ] = None


class Ca2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    method: Annotated[Method, Field(title="Content-Addressing Method")]
    hash: Annotated[
        str,
        Field(
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
    version: Annotated[Literal[2], Field(title="Format version (must be 2)")] = 2
    path: Annotated[
        str | None,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Store Path",
        ),
    ] = None
    nar_hash: Annotated[
        str,
        Field(
            alias="narHash",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="NAR Hash",
        ),
    ]
    nar_size: Annotated[int, Field(alias="narSize", ge=0, title="NAR Size")]
    references: Annotated[list[Reference], Field(title="References")]
    ca: Annotated[Ca2 | None, Field(title="Content Address")]
    store_dir: Annotated[str, Field(alias="storeDir", title="Store Directory")]
    deriver: Annotated[Deriver | None, Field(title="Deriver")]
    registration_time: Annotated[
        int | None,
        Field(alias="registrationTime", title="Registration Time"),
    ]
    ultimate: Annotated[bool, Field(title="Ultimate")]
    signatures: Annotated[list[str], Field(title="Signatures")]
    closure_size: Annotated[
        int | None,
        Field(alias="closureSize", ge=0, title="Closure Size"),
    ] = None
    url: Annotated[str, Field(title="URL")]
    compression: Annotated[str, Field(title="Compression")]
    download_hash: Annotated[
        str,
        Field(
            alias="downloadHash",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="Download Hash",
        ),
    ]
    download_size: Annotated[
        int,
        Field(alias="downloadSize", ge=0, title="Download Size"),
    ]
    closure_download_size: Annotated[
        int | None,
        Field(alias="closureDownloadSize", ge=0, title="Closure Download Size"),
    ] = None


class StoreObjectInfoV2(
    RootModel[StoreObjectInfoV21 | StoreObjectInfoV22 | StoreObjectInfoV23],
):
    root: Annotated[
        StoreObjectInfoV21 | StoreObjectInfoV22 | StoreObjectInfoV23,
        Field(title="Store Object Info v2"),
    ]


class Ca3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    method: Annotated[Method, Field(title="Content-Addressing Method")]
    hash: Annotated[
        str,
        Field(
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
    version: Annotated[Literal[2], Field(title="Format version (must be 2)")] = 2
    path: Annotated[
        str | None,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Store Path",
        ),
    ] = None
    nar_hash: Annotated[
        str,
        Field(
            alias="narHash",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="NAR Hash",
        ),
    ]
    nar_size: Annotated[int, Field(alias="narSize", ge=0, title="NAR Size")]
    references: Annotated[list[Reference], Field(title="References")]
    ca: Annotated[Ca3 | None, Field(title="Content Address")]
    store_dir: Annotated[str, Field(alias="storeDir", title="Store Directory")]


class Ca4(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    method: Annotated[Method, Field(title="Content-Addressing Method")]
    hash: Annotated[
        str,
        Field(
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
    version: Annotated[Literal[2], Field(title="Format version (must be 2)")] = 2
    path: Annotated[
        str | None,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Store Path",
        ),
    ] = None
    nar_hash: Annotated[
        str,
        Field(
            alias="narHash",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="NAR Hash",
        ),
    ]
    nar_size: Annotated[int, Field(alias="narSize", ge=0, title="NAR Size")]
    references: Annotated[list[Reference], Field(title="References")]
    ca: Annotated[Ca4 | None, Field(title="Content Address")]
    store_dir: Annotated[str, Field(alias="storeDir", title="Store Directory")]
    deriver: Annotated[Deriver | None, Field(title="Deriver")]
    registration_time: Annotated[
        int | None,
        Field(alias="registrationTime", title="Registration Time"),
    ]
    ultimate: Annotated[bool, Field(title="Ultimate")]
    signatures: Annotated[list[str], Field(title="Signatures")]
    closure_size: Annotated[
        int | None,
        Field(alias="closureSize", ge=0, title="Closure Size"),
    ] = None


class Ca5(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    method: Annotated[Method, Field(title="Content-Addressing Method")]
    hash: Annotated[
        str,
        Field(
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
    version: Annotated[Literal[2], Field(title="Format version (must be 2)")] = 2
    path: Annotated[
        str | None,
        Field(
            min_length=34,
            pattern="^[0123456789abcdfghijklmnpqrsvwxyz]{32}-.+$",
            title="Store Path",
        ),
    ] = None
    nar_hash: Annotated[
        str,
        Field(
            alias="narHash",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="NAR Hash",
        ),
    ]
    nar_size: Annotated[int, Field(alias="narSize", ge=0, title="NAR Size")]
    references: Annotated[list[Reference], Field(title="References")]
    ca: Annotated[Ca5 | None, Field(title="Content Address")]
    store_dir: Annotated[str, Field(alias="storeDir", title="Store Directory")]
    deriver: Annotated[Deriver | None, Field(title="Deriver")]
    registration_time: Annotated[
        int | None,
        Field(alias="registrationTime", title="Registration Time"),
    ]
    ultimate: Annotated[bool, Field(title="Ultimate")]
    signatures: Annotated[list[str], Field(title="Signatures")]
    closure_size: Annotated[
        int | None,
        Field(alias="closureSize", ge=0, title="Closure Size"),
    ] = None
    url: Annotated[str, Field(title="URL")]
    compression: Annotated[str, Field(title="Compression")]
    download_hash: Annotated[
        str,
        Field(
            alias="downloadHash",
            examples=[
                "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                "sha512-IEqPxt2oLwoM7XvrjgikFlfBbvRosiioJ5vjMacDwzWW/RXBOxsH+aodO+pXeJygMa2Fx6cd1wNU7GMSOMo0RQ==",
            ],
            pattern="^(blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$",
            title="Download Hash",
        ),
    ]
    download_size: Annotated[
        int,
        Field(alias="downloadSize", ge=0, title="Download Size"),
    ]
    closure_download_size: Annotated[
        int | None,
        Field(alias="closureDownloadSize", ge=0, title="Closure Download Size"),
    ] = None
