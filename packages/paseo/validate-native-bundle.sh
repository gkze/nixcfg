#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "usage: $0 APP ASAR_ROOT EXPECTED_MANIFEST EXECUTABLE_NAME" >&2
  exit 64
fi

app=$1
asar_root=$2
expected_manifest=$3
executable_name=$4
python=${PASEO_PYTHON:?PASEO_PYTHON must name the package Python interpreter}
file_tool=${PASEO_FILE_TOOL:-/usr/bin/file}
lipo_tool=${PASEO_LIPO_TOOL:-/usr/bin/lipo}
otool_tool=${PASEO_OTOOL_TOOL:-/usr/bin/otool}
scratch=${TMPDIR:?TMPDIR must be set}
bundle_inventory=$scratch/paseo-bundle-inventory
asar_inventory=$scratch/paseo-asar-inventory
bundle_native=$scratch/paseo-bundle-native
bundle_candidates=$scratch/paseo-bundle-native-candidates
asar_native=$scratch/paseo-asar-native
asar_candidates=$scratch/paseo-asar-native-candidates
logical_native=$scratch/paseo-logical-native
actual_manifest=$scratch/paseo-native-manifest

for output in \
  "$bundle_inventory" \
  "$asar_inventory" \
  "$bundle_native" \
  "$bundle_candidates" \
  "$asar_native" \
  "$asar_candidates" \
  "$logical_native"; do
  : >"$output"
done

fail() {
  echo "$*" >&2
  exit 1
}

while IFS= read -r -d '' candidate; do
  relative_path=${candidate#"$app"/}
  case "$relative_path" in
  *$'\n'* | *$'\t'*) fail "unsupported control character in bundle path" ;;
  esac
  printf '%s\n' "$relative_path" >>"$bundle_inventory"
done < <(find "$app" -mindepth 1 -print0)
LC_ALL=C sort -u -o "$bundle_inventory" "$bundle_inventory"

while IFS= read -r -d '' candidate; do
  relative_path=${candidate#"$asar_root"/}
  case "$relative_path" in
  *$'\n'* | *$'\t'*) fail "unsupported control character in app.asar path" ;;
  esac
  printf '%s\n' "$relative_path" >>"$asar_inventory"
done < <(find "$asar_root" -mindepth 1 -print0)
LC_ALL=C sort -u -o "$asar_inventory" "$asar_inventory"

scan_native_tree() {
  local root=$1
  local prefix=$2
  local physical_inventory=$3
  local candidate_inventory=$4
  local candidate description relative_path architectures

  while IFS= read -r -d '' candidate; do
    description=$("$file_tool" -b "$candidate")
    relative_path=${candidate#"$root"/}
    case "$relative_path" in
    *$'\n'* | *$'\t'*) fail "unsupported control character in native path" ;;
    esac
    case "$description" in
    *Mach-O*)
      architectures=$("$lipo_tool" -archs "$candidate")
      [[ $architectures == arm64 ]] ||
        fail "Paseo runtime is not arm64-only: $prefix$relative_path ($architectures)"
      printf '%s\n' "$relative_path" >>"$physical_inventory"
      printf '%s\n' "$candidate" >>"$candidate_inventory"
      printf '%s%s\n' "$prefix" "$relative_path" >>"$logical_native"
      ;;
    *)
      case "$relative_path" in
      *.node | *.dylib | *.dylib.*)
        fail "Paseo native-looking file is not Mach-O: $prefix$relative_path"
        ;;
      esac
      ;;
    esac
  done < <(find "$root" -type f -print0)
  LC_ALL=C sort -u -o "$physical_inventory" "$physical_inventory"
}

scan_native_tree "$app" "" "$bundle_native" "$bundle_candidates"
scan_native_tree "$asar_root" "app.asar/" "$asar_native" "$asar_candidates"

if find "$app" "$asar_root" \
  \( -path '*/node-pty/prebuilds' -o -path '*/node-pty/prebuilds/*' \) \
  -print -quit | grep -q .; then
  fail "node-pty prebuilds survived the mandatory source rebuild cleanup"
fi

if find "$app" "$asar_root" \
  \( -path '*/node_modules/@anthropic-ai/claude-agent-sdk-darwin-arm64' \
  -o -path '*/node_modules/@anthropic-ai/claude-agent-sdk-darwin-arm64/*' \) \
  -print -quit | grep -q .; then
  fail "opaque Anthropic SDK platform runtime survived pruning"
fi

if find "$app" "$asar_root" \
  \( -path '*/node_modules/@anthropic-ai/claude-agent-sdk/vendor' \
  -o -path '*/node_modules/@anthropic-ai/claude-agent-sdk/vendor/*' \) \
  -print -quit | grep -q .; then
  fail "stale Anthropic SDK vendor tree survived pruning"
fi

resolve_within_root() {
  "$python" - "$1" "$2" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve(strict=True)
target = Path(sys.argv[2]).resolve(strict=True)
try:
    target.relative_to(root)
except ValueError:
    raise SystemExit(1) from None
print(target)
PY
}

while IFS= read -r -d '' link; do
  resolved=$(resolve_within_root "$app" "$link") ||
    fail "bundle symlink escapes or has no target: ${link#"$app"/}"
  target_relative=${resolved#"$app"/}
  grep -Fqx "$target_relative" "$bundle_inventory" ||
    fail "bundle symlink target is absent from inventory: ${link#"$app"/}"

  description=$("$file_tool" -b "$resolved")
  link_relative=${link#"$app"/}
  case "$description" in
  *Mach-O*)
    grep -Fqx "$target_relative" "$bundle_native" ||
      fail "native symlink target was not inventoried: $link_relative"
    printf '%s\n' "$link_relative" >>"$logical_native"
    ;;
  *)
    case "$link_relative" in
    *.node | *.dylib | *.dylib.*)
      fail "native-looking bundle symlink does not target Mach-O: $link_relative"
      ;;
    esac
    ;;
  esac
done < <(find "$app" -type l -print0)

while IFS= read -r -d '' link; do
  resolved=$(resolve_within_root "$asar_root" "$link") ||
    fail "app.asar symlink escapes or has no target: ${link#"$asar_root"/}"
  target_relative=${resolved#"$asar_root"/}
  grep -Fqx "$target_relative" "$asar_inventory" ||
    fail "app.asar symlink target is absent from inventory: ${link#"$asar_root"/}"

  description=$("$file_tool" -b "$resolved")
  link_relative=${link#"$asar_root"/}
  case "$description" in
  *Mach-O*)
    grep -Fqx "$target_relative" "$asar_native" ||
      fail "native app.asar symlink target was not inventoried: $link_relative"
    printf 'app.asar/%s\n' "$link_relative" >>"$logical_native"
    ;;
  *)
    case "$link_relative" in
    *.node | *.dylib | *.dylib.*)
      fail "native-looking app.asar symlink does not target Mach-O: $link_relative"
      ;;
    esac
    ;;
  esac
done < <(find "$asar_root" -type l -print0)

while IFS= read -r relative_path; do
  unpacked_path="Contents/Resources/app.asar.unpacked/$relative_path"
  grep -Fqx "$unpacked_path" "$bundle_native" ||
    fail "native app.asar entry lacks an inventoried unpacked runtime: $relative_path"
  cmp -s "$asar_root/$relative_path" "$app/$unpacked_path" ||
    fail "native app.asar entry differs from its validated unpacked runtime: $relative_path"
done <"$asar_native"

"$python" - \
  "$app" \
  "$bundle_native" \
  "$bundle_candidates" \
  "$file_tool" \
  "$otool_tool" \
  "$executable_name" \
  "$scratch" <<'PY'
from __future__ import annotations

from functools import cache
from pathlib import Path
import subprocess
import sys
import tempfile

app = Path(sys.argv[1]).resolve(strict=True)
native_inventory = Path(sys.argv[2])
candidate_inventory = Path(sys.argv[3])
file_tool = sys.argv[4]
otool_tool = sys.argv[5]
executable_name = sys.argv[6]
scratch = Path(sys.argv[7]).resolve(strict=True)

native_paths = {
    (app / relative_path).resolve(strict=True)
    for relative_path in native_inventory.read_text().splitlines()
}
candidates = tuple(
    Path(candidate).resolve(strict=True)
    for candidate in candidate_inventory.read_text().splitlines()
)
system_target = object()
validated: set[Path] = set()
validated_contexts: set[tuple[Path, Path, tuple[Path, ...]]] = set()
otool_alias_root = Path(
    tempfile.mkdtemp(prefix="paseo-otool-aliases-", dir=scratch)
)
otool_aliases: dict[Path, Path] = {}


def tool_output(
    *arguments: str,
    allow_failure: bool = False,
    failure_target: Path | None = None,
) -> str:
    completed = subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        if allow_failure:
            return ""
        detail = completed.stderr.strip() or f"exit {completed.returncode}"
        target = (
            f" for {failure_target.relative_to(app)}"
            if failure_target is not None
            else ""
        )
        raise SystemExit(f"Mach-O inspection failed{target}: {detail}")
    return completed.stdout


def otool_alias(candidate: Path) -> Path:
    alias = otool_aliases.get(candidate)
    if alias is None:
        alias = otool_alias_root / f"image-{len(otool_aliases):06d}"
        alias.symlink_to(candidate)
        otool_aliases[candidate] = alias
    return alias


def otool_output(
    operation: str,
    candidate: Path,
    *,
    allow_failure: bool = False,
) -> str:
    return tool_output(
        otool_tool,
        operation,
        str(otool_alias(candidate)),
        allow_failure=allow_failure,
        failure_target=candidate,
    )


@cache
def description(candidate: Path) -> str:
    return tool_output(file_tool, "-b", str(candidate)).strip()


@cache
def dylib_id(candidate: Path) -> str | None:
    lines = otool_output(
        "-D",
        candidate,
        allow_failure=True,
    ).splitlines()
    return lines[1].strip() if len(lines) > 1 else None


@cache
def dependencies(candidate: Path) -> tuple[str, ...]:
    lines = otool_output("-L", candidate).splitlines()[1:]
    return tuple(
        line.strip().split(" (compatibility version", 1)[0] for line in lines
    )


@cache
def raw_rpaths(candidate: Path) -> tuple[str, ...]:
    lines = otool_output("-l", candidate).splitlines()
    rpaths: list[str] = []
    for index, line in enumerate(lines):
        if line.strip() != "cmd LC_RPATH":
            continue
        for detail in lines[index + 1 : index + 4]:
            stripped = detail.strip()
            if stripped.startswith("path "):
                rpaths.append(stripped.removeprefix("path ").rsplit(" (offset", 1)[0])
                break
        else:
            raise SystemExit(f"malformed LC_RPATH command in {candidate.relative_to(app)}")
    return tuple(rpaths)


def is_system_path(path: str | Path) -> bool:
    rendered = str(path)
    return rendered.startswith(("/System/Library/", "/usr/lib/"))


def executable_directory(candidate: Path) -> Path:
    relative_parts = candidate.relative_to(app).parts
    for index, part in enumerate(relative_parts):
        if part.endswith(".app"):
            return app.joinpath(*relative_parts[: index + 1], "Contents", "MacOS")
    return app / "Contents/MacOS"


def associated_executable(candidate: Path) -> Path:
    directory = executable_directory(candidate)
    if directory == app / "Contents/MacOS":
        executable = directory / executable_name
        if executable.resolve(strict=True) not in native_paths:
            raise SystemExit("main executable is absent from the native inventory")
        return executable.resolve(strict=True)
    executables = tuple(
        item
        for item in candidates
        if item.parent == directory and "executable" in description(item)
    )
    if len(executables) != 1:
        raise SystemExit(
            "helper loader context does not contain exactly one native executable: "
            f"{directory.relative_to(app)}"
        )
    return executables[0]


def resolved_rpaths(
    owner: Path,
    owner_executable_directory: Path,
) -> tuple[Path, ...]:
    resolved: list[Path] = []
    for rpath in raw_rpaths(owner):
        if rpath == "@loader_path":
            base = owner.parent
        elif rpath.startswith("@loader_path/"):
            base = owner.parent / rpath.removeprefix("@loader_path/")
        elif rpath == "@executable_path":
            base = owner_executable_directory
        elif rpath.startswith("@executable_path/"):
            base = owner_executable_directory / rpath.removeprefix(
                "@executable_path/"
            )
        elif is_system_path(rpath):
            base = Path(rpath)
        elif rpath.startswith("/"):
            raise SystemExit(
                f"unknown absolute LC_RPATH in {owner.relative_to(app)}: {rpath}"
            )
        else:
            raise SystemExit(
                f"unknown or relative LC_RPATH in {owner.relative_to(app)}: {rpath}"
            )
        if not is_system_path(base):
            base = base.resolve(strict=False)
            try:
                base.relative_to(app)
            except ValueError:
                raise SystemExit(
                    f"LC_RPATH escapes the bundle in {owner.relative_to(app)}: {rpath}"
                ) from None
        if base not in resolved:
            resolved.append(base)
    return tuple(resolved)


def resolve_runtime_target(target: Path) -> object | Path | None:
    if is_system_path(target):
        return system_target
    try:
        resolved = target.resolve(strict=True)
        resolved.relative_to(app)
    except (FileNotFoundError, RuntimeError, ValueError):
        return None
    return resolved if resolved in native_paths else None


def validate_candidate(
    candidate: Path,
    candidate_executable_directory: Path,
    inherited_rpaths: tuple[Path, ...],
    ancestry: tuple[Path, ...] = (),
) -> None:
    own_rpaths = resolved_rpaths(candidate, candidate_executable_directory)
    effective_rpaths = tuple(dict.fromkeys((*own_rpaths, *inherited_rpaths)))
    context = (candidate, candidate_executable_directory, effective_rpaths)
    if context in validated_contexts or candidate in ancestry:
        return
    validated_contexts.add(context)

    for dependency in dependencies(candidate):
        if not dependency or dependency == dylib_id(candidate):
            continue
        resolved_dependency: object | Path | None
        if is_system_path(dependency):
            continue
        if dependency.startswith("@loader_path/"):
            resolved_dependency = resolve_runtime_target(
                candidate.parent / dependency.removeprefix("@loader_path/")
            )
            if resolved_dependency is None:
                raise SystemExit(
                    "unresolved @loader_path linkage in "
                    f"{candidate.relative_to(app)}: {dependency}"
                )
        elif dependency.startswith("@executable_path/"):
            resolved_dependency = resolve_runtime_target(
                candidate_executable_directory
                / dependency.removeprefix("@executable_path/")
            )
            if resolved_dependency is None:
                raise SystemExit(
                    "unresolved @executable_path linkage in "
                    f"{candidate.relative_to(app)}: {dependency}"
                )
        elif dependency.startswith("@rpath/"):
            suffix = dependency.removeprefix("@rpath/")
            resolved_dependency = None
            for rpath in effective_rpaths:
                resolved_dependency = resolve_runtime_target(rpath / suffix)
                if resolved_dependency is not None:
                    break
            if resolved_dependency is None:
                raise SystemExit(
                    "unresolved @rpath linkage in "
                    f"{candidate.relative_to(app)}: {dependency}"
                )
        elif dependency.startswith("/"):
            raise SystemExit(
                f"unknown absolute linkage in {candidate.relative_to(app)}: {dependency}"
            )
        else:
            raise SystemExit(
                "unknown or relative linkage in "
                f"{candidate.relative_to(app)}: {dependency}"
            )

        if isinstance(resolved_dependency, Path):
            validate_candidate(
                resolved_dependency,
                candidate_executable_directory,
                effective_rpaths,
                (*ancestry, candidate),
            )
    validated.add(candidate)


for candidate in candidates:
    if "executable" in description(candidate):
        validate_candidate(candidate, candidate.parent, ())

for candidate in candidates:
    if candidate.suffix != ".node":
        continue
    loader = associated_executable(candidate)
    validate_candidate(
        candidate,
        loader.parent,
        resolved_rpaths(loader, loader.parent),
    )

for candidate in candidates:
    if candidate in validated:
        continue
    loader = associated_executable(candidate)
    validate_candidate(
        candidate,
        loader.parent,
        resolved_rpaths(loader, loader.parent),
    )
PY

for required_native in \
  "Contents/MacOS/$executable_name" \
  "Contents/Resources/app.asar.unpacked/node_modules/node-pty/build/Release/pty.node" \
  "Contents/Resources/app.asar.unpacked/node_modules/sherpa-onnx-darwin-arm64/sherpa-onnx.node"; do
  grep -Fqx "$required_native" "$bundle_native" ||
    fail "required Paseo native runtime was not inventoried: $required_native"
done

LC_ALL=C sort "$logical_native" |
  uniq -c |
  sed -E 's/^[[:space:]]*([0-9]+)[[:space:]]/\1\	/' >"$actual_manifest"
if ! cmp -s "$expected_manifest" "$actual_manifest"; then
  echo "Paseo native relative-path/count manifest mismatch" >&2
  echo "PASEO_NATIVE_MANIFEST_V1_BEGIN" >&2
  cat "$actual_manifest" >&2
  echo "PASEO_NATIVE_MANIFEST_V1_END" >&2
  diff -u "$expected_manifest" "$actual_manifest" >&2 || true
  exit 1
fi
