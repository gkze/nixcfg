{
  cctools,
  cmake,
  fetchFromGitHub,
  gitMinimal,
  lib,
  llamaCppSrcHash,
  meshLlmSrcHash,
  ninja,
  python3,
  stdenv,
  stdenvNoCC,
}:
assert stdenvNoCC.hostPlatform.system == "aarch64-darwin";
let
  meshLlm = import ./mesh-llm.nix {
    inherit
      fetchFromGitHub
      lib
      python3
      stdenvNoCC
      ;
    srcHash = meshLlmSrcHash;
  };
  llamaCpp = import ./llama-cpp.nix {
    inherit
      cctools
      cmake
      fetchFromGitHub
      gitMinimal
      lib
      ninja
      stdenv
      ;
    meshSrcHash = meshLlmSrcHash;
    srcHash = llamaCppSrcHash;
  };
  expectedMeshContract = {
    kind = "mesh-llm";
    version = "0.75.1";
    commit = "3295c902d4c4f859aaadf9240042ffdaf06dd07e";
    sdkFeatures = [
      "client"
      "serving"
    ];
    hostRuntimeFeatures = [ "dynamic-native-runtime" ];
  };
  expectedLlamaContract = {
    kind = "llama.cpp";
    commit = "8190848bb36c7df4251db4352bd81bc07d0a4385";
    target = "aarch64-apple-darwin";
    backend = "metal";
    linkMode = "dynamic";
    buildType = "Release";
    ggmlNative = false;
    cmakeOptions = {
      BUILD_SHARED_LIBS = true;
      GGML_METAL = true;
      LLAMA_BUILD_APP = false;
      LLAMA_BUILD_EXAMPLES = false;
      LLAMA_BUILD_SERVER = false;
      LLAMA_BUILD_TESTS = false;
      LLAMA_CURL = false;
      LLAMA_OPENSSL = false;
    };
  };
  implementedBundleContract = {
    kind = "mesh-native-runtime-bundle";
    meshVersion = "0.75.1";
    skippyAbi = "0.1.35";
    target = "aarch64-apple-darwin";
    platform = {
      os = "macos";
      arch = "aarch64";
    };
    backend = "metal";
    sourceInputs = [
      "meshLlm"
      "llamaCpp"
    ];
    manifestHasFileDigests = true;
    releaseArchiveAllowed = false;
  };
  runtimeId = "meshllm-native-runtime-darwin-aarch64-metal";
  bundleScript = ''
    import hashlib
    import json
    import os
    import re
    import shutil
    import subprocess
    import sys
    from pathlib import Path, PurePosixPath

    MESH_VERSION = "0.75.1"
    MESH_COMMIT = "3295c902d4c4f859aaadf9240042ffdaf06dd07e"
    LLAMA_COMMIT = "8190848bb36c7df4251db4352bd81bc07d0a4385"
    SKIPPY_ABI = "0.1.35"
    RUNTIME_ID = "meshllm-native-runtime-darwin-aarch64-metal"
    PACKAGING_PATHS = (
        "scripts/build-llama.sh",
        "scripts/package-native-runtime.sh",
        "scripts/prepare-llama.sh",
        "third_party/llama.cpp/upstream.txt",
    )
    SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
    ABI_SYMBOL = "_skippy_abi_version"


    def fail(message):
        raise SystemExit(message)


    def sha256(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()


    def load_json(path, label):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            fail(f"invalid {label}: {error}")


    def safe_relative_path(value, label):
        if not isinstance(value, str) or not value:
            fail(f"{label} must be a nonempty relative path")
        path = PurePosixPath(value)
        if path.is_absolute() or path.as_posix() != value or ".." in path.parts:
            fail(f"{label} is not a normalized relative path: {value}")
        return path


    def require_regular_file(path, label):
        if path.is_symlink() or not path.is_file():
            fail(f"{label} is not a regular file: {path}")


    def validate_digest(value, label):
        if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
            fail(f"{label} is not a lowercase SHA-256 digest")


    def validate_mesh_provenance(source_root, provenance_path):
        require_regular_file(provenance_path, "Mesh provenance")
        provenance = load_json(provenance_path, "Mesh provenance JSON")
        if not isinstance(provenance, dict) or provenance.get("schemaVersion") != 1:
            fail("unexpected Mesh provenance schema")
        if provenance.get("meshLlm") != {
            "version": MESH_VERSION,
            "commit": MESH_COMMIT,
        }:
            fail("Mesh provenance identity does not match the pinned source")

        llama = provenance.get("llamaCpp")
        if not isinstance(llama, dict) or llama.get("upstreamPin") != LLAMA_COMMIT:
            fail("Mesh provenance llama.cpp pin does not match the runtime input")
        upstream_path = source_root / "third_party/llama.cpp/upstream.txt"
        require_regular_file(upstream_path, "Mesh llama.cpp upstream pin")
        if upstream_path.read_text(encoding="utf-8") != f"{LLAMA_COMMIT}\n":
            fail("Mesh source llama.cpp upstream pin is not one exact line")

        patch_directory = source_root / "third_party/llama.cpp/patches"
        if patch_directory.is_symlink() or not patch_directory.is_dir():
            fail("Mesh source has no regular llama.cpp patch directory")
        patch_paths = sorted(patch_directory.iterdir(), key=lambda path: path.name)
        if not patch_paths:
            fail("Mesh source contains no llama.cpp patches")
        if any(
            path.is_symlink() or not path.is_file() or path.suffix != ".patch"
            for path in patch_paths
        ):
            fail("Mesh source patch inventory contains an unsupported entry")

        patch_records = llama.get("patches")
        expected_patch_names = [path.name for path in patch_paths]
        if not isinstance(patch_records, list) or [
            record.get("name") if isinstance(record, dict) else None
            for record in patch_records
        ] != expected_patch_names:
            fail("Mesh provenance patch inventory does not match the source")
        for path, record in zip(patch_paths, patch_records, strict=True):
            if not isinstance(record, dict) or set(record) != {"name", "sha256"}:
                fail("Mesh provenance patch record has an unexpected schema")
            validate_digest(record["sha256"], "Mesh patch digest")
            if sha256(path) != record["sha256"]:
                fail(f"Mesh patch digest does not match the source: {path.name}")

        packaging_records = provenance.get("packagingInputs")
        if not isinstance(packaging_records, list) or [
            record.get("path") if isinstance(record, dict) else None
            for record in packaging_records
        ] != list(PACKAGING_PATHS):
            fail("Mesh provenance packaging input inventory is not exact")
        for relative, record in zip(PACKAGING_PATHS, packaging_records, strict=True):
            if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
                fail("Mesh provenance packaging input record has an unexpected schema")
            validate_digest(record["sha256"], "Mesh packaging input digest")
            source_path = source_root / relative
            require_regular_file(source_path, "Mesh packaging input")
            if sha256(source_path) != record["sha256"]:
                fail(f"Mesh packaging input digest does not match the source: {relative}")


    def is_dylib_name(name):
        return name.endswith(".dylib") or ".dylib." in name


    def is_within(relative, parent):
        return relative == parent or relative.startswith(f"{parent}/")


    def validate_library_symlink(path, library_root):
        target = os.readlink(path)
        if not target or PurePosixPath(target).name != target:
            fail(f"unsafe library symlink: {path.name} -> {target}")
        try:
            resolved = (path.parent / target).resolve(strict=True)
            resolved.relative_to(library_root.resolve(strict=True))
        except (OSError, ValueError):
            fail(f"unsafe library symlink: {path.name} -> {target}")
        if not resolved.is_file() or not is_dylib_name(resolved.name):
            fail(f"unsafe library symlink: {path.name} -> {target}")


    def validate_llama_inventory(llama_root, lib_subdir, resource_values):
        if lib_subdir != "lib":
            fail(f"unexpected llama.cpp library subdirectory: {lib_subdir}")
        if not isinstance(resource_values, list):
            fail("llama.cpp resourceSubpaths must be a list")
        resource_paths = [
            safe_relative_path(value, "llama.cpp resource subpath")
            for value in resource_values
        ]
        normalized_resources = [path.as_posix() for path in resource_paths]
        if normalized_resources != sorted(set(normalized_resources)):
            fail("llama.cpp resource subpaths must be sorted and unique")
        if any(is_within(path, lib_subdir) for path in normalized_resources):
            fail("llama.cpp resources must be outside the library closure")

        library_root = llama_root / lib_subdir
        if library_root.is_symlink() or not library_root.is_dir():
            fail("llama.cpp output has no regular library directory")
        allowed_resource_dirs = {
            parent.as_posix()
            for resource in resource_paths
            for parent in resource.parents
            if parent.as_posix() != "."
        }
        for path in sorted(llama_root.rglob("*"), key=lambda item: item.as_posix()):
            relative = path.relative_to(llama_root).as_posix()
            in_library = is_within(relative, lib_subdir)
            if path.is_symlink():
                if not in_library or not is_dylib_name(path.name):
                    fail(f"undeclared llama.cpp output: {relative}")
                validate_library_symlink(path, library_root)
            elif path.is_dir():
                if not in_library and relative not in allowed_resource_dirs:
                    fail(f"undeclared llama.cpp output directory: {relative}")
            elif path.is_file():
                if in_library:
                    if not is_dylib_name(path.name):
                        fail(f"non-dylib file in llama.cpp library closure: {relative}")
                elif relative not in normalized_resources:
                    fail(f"undeclared llama.cpp output: {relative}")
            else:
                fail(f"unsupported llama.cpp output entry: {relative}")

        for relative in normalized_resources:
            require_regular_file(llama_root / relative, "declared llama.cpp resource")
        physical_libraries = sorted(
            (
                path
                for path in library_root.rglob("*")
                if path.is_file() and not path.is_symlink()
            ),
            key=lambda path: path.relative_to(llama_root).as_posix(),
        )
        if not physical_libraries:
            fail("llama.cpp output contains no physical dynamic libraries")
        basenames = [path.name for path in physical_libraries]
        if len(basenames) != len(set(basenames)):
            fail("llama.cpp output contains duplicate physical dylib basenames")
        return library_root, resource_paths, physical_libraries


    def copy_runtime(llama_root, output_root, library_root, resource_paths):
        if output_root.exists() or output_root.is_symlink():
            fail(f"runtime output already exists: {output_root}")
        output_root.mkdir(parents=True)
        output_library_root = output_root / "lib"
        shutil.copytree(
            library_root,
            output_library_root,
            symlinks=True,
            copy_function=shutil.copyfile,
        )
        for relative in resource_paths:
            source = llama_root / relative.as_posix()
            destination = output_root / relative.as_posix()
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if sha256(source) != sha256(destination):  # pragma: no cover -- post-copy filesystem corruption defense
                fail(f"resource hash changed while copying: {relative.as_posix()}")
            destination.chmod(0o444)

        for source in sorted(library_root.rglob("*"), key=lambda path: path.as_posix()):
            relative = source.relative_to(library_root)
            destination = output_library_root / relative
            if source.is_symlink():
                if not destination.is_symlink() or os.readlink(destination) != os.readlink(source):  # pragma: no cover -- post-copy filesystem corruption defense
                    fail(f"library symlink was not preserved: {relative.as_posix()}")
            elif source.is_file():
                if sha256(source) != sha256(destination):  # pragma: no cover -- post-copy filesystem corruption defense
                    fail(f"library hash changed while copying: {relative.as_posix()}")
                destination.chmod(0o555)
        return output_library_root


    def manifest_files(output_root):
        files = {}
        for path in sorted(output_root.rglob("*"), key=lambda item: item.as_posix()):
            relative = path.relative_to(output_root).as_posix()
            if relative == "manifest.json" or not path.is_file():
                continue
            files[relative] = sha256(path)
        if not files:
            fail("runtime files manifest is empty")
        return files


    def validate_manifest_files(files, libraries):
        missing = [library for library in libraries if library not in files]
        if missing:
            fail(f"runtime files manifest is missing libraries: {', '.join(missing)}")


    def run_tool(command, label):
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic"
            fail(f"{label} failed: {detail}")
        return result.stdout, result.stderr


    def parse_install_id(otool, library):
        stdout, _stderr = run_tool([otool, "-D", str(library)], "otool -D")
        values = [line.strip() for line in stdout.splitlines()[1:] if line.strip()]
        if len(values) != 1:
            fail(f"expected one install ID for {library.name}")
        expected = f"@rpath/{library.name}"
        if values[0] != expected:
            fail(f"unexpected install ID for {library.name}: {values[0]}")
        return values[0]


    def parse_dependencies(otool, library, install_id):
        stdout, _stderr = run_tool([otool, "-L", str(library)], "otool -L")
        dependencies = []
        for line in stdout.splitlines()[1:]:
            stripped = line.strip()
            if not stripped:
                continue
            dependency = stripped.split(" ", 1)[0]
            if dependency != install_id:
                dependencies.append(dependency)
        return dependencies


    def resolve_dependency(dependency, library, library_root, physical_by_path):
        if dependency.startswith("/usr/lib/") or dependency.startswith("/System/Library/"):
            return None
        if dependency.startswith("@loader_path/"):
            relative = safe_relative_path(
                dependency.removeprefix("@loader_path/"),
                "loader-relative dependency",
            )
            candidate = library.parent / relative.as_posix()
        elif dependency.startswith("@rpath/"):
            relative = safe_relative_path(
                dependency.removeprefix("@rpath/"),
                "rpath dependency",
            )
            candidate = library_root / relative.as_posix()
        else:
            fail(f"unsupported dependency for {library.name}: {dependency}")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(library_root.resolve(strict=True))
        except (OSError, ValueError):
            fail(f"unresolved local dependency for {library.name}: {dependency}")
        canonical = physical_by_path.get(resolved)
        if canonical is None:  # pragma: no cover -- complete physical inventory makes this unreachable
            fail(f"unresolved local dependency for {library.name}: {dependency}")
        return canonical


    def inspect_libraries(library_root, lipo, otool, nm, codesign):
        physical_libraries = sorted(
            (
                path
                for path in library_root.rglob("*")
                if path.is_file() and not path.is_symlink()
            ),
            key=lambda path: path.relative_to(library_root.parent).as_posix(),
        )
        canonical = {
            path: path.relative_to(library_root.parent).as_posix()
            for path in physical_libraries
        }
        physical_by_path = {path.resolve(strict=True): relative for path, relative in canonical.items()}
        dependency_graph = {relative: set() for relative in canonical.values()}
        abi_libraries = []

        for library in physical_libraries:
            architectures, _stderr = run_tool(
                [lipo, "-archs", str(library)],
                "lipo architecture inspection",
            )
            if architectures.strip() != "arm64":
                fail(f"{library.name} must be arm64-only, found: {architectures.strip()}")
            run_tool(
                [codesign, "--verify", "--strict", str(library)],
                "code signature verification",
            )
            _stdout, signature = run_tool(
                [codesign, "-dv", "--verbose=4", str(library)],
                "code signature inspection",
            )
            if "Signature=adhoc" not in signature.splitlines():
                fail(f"{library.name} must use an ad-hoc signature")

            install_id = parse_install_id(otool, library)
            relative = canonical[library]
            for dependency in parse_dependencies(otool, library, install_id):
                resolved = resolve_dependency(
                    dependency,
                    library,
                    library_root,
                    physical_by_path,
                )
                if resolved is not None:
                    dependency_graph[relative].add(resolved)

            symbols, _stderr = run_tool(
                [nm, "-gjU", str(library)],
                "external symbol inspection",
            )
            if ABI_SYMBOL in symbols.splitlines():
                abi_libraries.append(relative)

        if len(abi_libraries) != 1:
            fail(
                "exactly one physical dylib must export "
                f"{ABI_SYMBOL}; found {len(abi_libraries)}"
            )
        primary = abi_libraries[0]
        state = {}
        ordered = []

        def visit(library):
            if state.get(library) == "active":
                fail(f"llama.cpp runtime dependency cycle includes {library}")
            if state.get(library) == "done":
                return
            state[library] = "active"
            for dependency in sorted(dependency_graph[library]):
                visit(dependency)
            state[library] = "done"
            ordered.append(library)

        for library in sorted(dependency_graph):
            visit(library)
        ordered = [library for library in ordered if library != primary]
        ordered.append(primary)
        return ordered

    def main(arguments):
        if len(arguments) != 10:
            fail("bundle script requires exactly ten arguments")
        mesh_source = Path(arguments[0])
        mesh_provenance = Path(arguments[1])
        llama_root = Path(arguments[2])
        output_root = Path(arguments[3])
        lib_subdir = arguments[4]
        try:
            resource_values = json.loads(arguments[5])
        except json.JSONDecodeError as error:
            fail(f"invalid llama.cpp resourceSubpaths JSON: {error}")
        lipo, otool, nm, codesign = arguments[6:]

        validate_mesh_provenance(mesh_source, mesh_provenance)
        library_root, resource_paths, _physical_libraries = validate_llama_inventory(
            llama_root,
            lib_subdir,
            resource_values,
        )
        output_library_root = copy_runtime(
            llama_root,
            output_root,
            library_root,
            resource_paths,
        )
        libraries = inspect_libraries(output_library_root, lipo, otool, nm, codesign)
        files = manifest_files(output_root)
        validate_manifest_files(files, libraries)
        # The Rust loader contract ends here. Mesh's separate release verifier
        # also expects build.primary_library and build.library_sha256; adding
        # that verifier-only metadata remains a P2 follow-up, not a load gate.
        manifest = {
            "runtime": {
                "id": RUNTIME_ID,
                "mesh_version": MESH_VERSION,
                "skippy_abi": SKIPPY_ABI,
                "platform": {
                    "os": "macos",
                    "arch": "aarch64",
                    "target": "aarch64-apple-darwin",
                },
                "backend": {"kind": "metal"},
                "rank": 0,
                "libraries": libraries,
                "files": files,
            }
        }
        (output_root / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


    if __name__ == "__main__":
        main(sys.argv[1:])
  '';
in
assert (meshLlm.passthru.buzzNativeContract or null) == expectedMeshContract;
assert (meshLlm.sourceSubdir or null) == "share/mesh-llm/source";
assert (meshLlm.provenanceSubpath or null) == "share/mesh-llm/provenance.json";
assert (llamaCpp.passthru.buzzNativeContract or null) == expectedLlamaContract;
assert (llamaCpp.libSubdir or null) == "lib";
assert builtins.isList (llamaCpp.resourceSubpaths or null);
stdenvNoCC.mkDerivation {
  pname = "buzz-mesh-native-runtime";
  version = "0.75.1";
  strictDeps = true;
  dontUnpack = true;
  dontConfigure = true;
  dontBuild = true;
  dontFixup = true;
  nativeBuildInputs = [
    cctools
    python3
  ];
  installPhase = ''
    runHook preInstall
    ${python3}/bin/python3 -c ${lib.escapeShellArg bundleScript} ${lib.escapeShellArg "${meshLlm}/${meshLlm.sourceSubdir}"} ${lib.escapeShellArg "${meshLlm}/${meshLlm.provenanceSubpath}"} ${lib.escapeShellArg "${llamaCpp}"} "$out" lib ${lib.escapeShellArg (builtins.toJSON llamaCpp.resourceSubpaths)} ${cctools}/bin/lipo ${cctools}/bin/otool ${cctools}/bin/nm /usr/bin/codesign
    runHook postInstall
  '';
  passthru = {
    buzzNativeContract = implementedBundleContract;
    manifestSubpath = "manifest.json";
    inherit runtimeId;
  };
  meta = {
    description = "Repo-owned Mesh 0.75.1 Metal native runtime for Buzz";
    license = lib.licenses.asl20;
    platforms = [ "aarch64-darwin" ];
  };
}
