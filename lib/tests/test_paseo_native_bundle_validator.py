"""Behavioral CLI contracts for Paseo's native-bundle validator."""

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent

import pytest

from lib.update.paths import REPO_ROOT

_VALIDATOR = REPO_ROOT / "packages/paseo/validate-native-bundle.sh"
_SYSTEM_DEPENDENCY = "/usr/lib/libSystem.B.dylib"


@dataclass
class _ValidatorFixture:
    root: Path
    app: Path = field(init=False)
    asar_root: Path = field(init=False)
    expected_manifest: Path = field(init=False)
    metadata_path: Path = field(init=False)
    tools: dict[str, Path] = field(init=False)
    metadata: dict[str, dict[str, object]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.app = self.root / "Paseo.app"
        self.asar_root = self.root / "asar"
        self.expected_manifest = self.root / "expected-manifest"
        self.metadata_path = self.root / "macho.json"
        self.asar_root.mkdir()
        self._add_macho("Contents/MacOS/Paseo", executable=True)
        self._add_macho(
            "Contents/Resources/app.asar.unpacked/node_modules/"
            "node-pty/build/Release/pty.node"
        )
        self._add_macho(
            "Contents/Resources/app.asar.unpacked/node_modules/"
            "sherpa-onnx-darwin-arm64/sherpa-onnx.node"
        )
        self.tools = self._write_macho_tools()

    def _add_macho(
        self,
        relative_path: str,
        *,
        dependencies: tuple[str, ...] = (_SYSTEM_DEPENDENCY,),
        rpaths: tuple[str, ...] = (),
        executable: bool = False,
    ) -> Path:
        path = self.app / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture Mach-O\n")
        path.chmod(0o755)
        description = "Mach-O 64-bit arm64"
        description += (
            " executable" if executable else " dynamically linked shared library"
        )
        self.metadata[str(path)] = {
            "dependencies": list(dependencies),
            "description": description,
            "id": None if executable else f"@rpath/{path.name}",
            "rpaths": list(rpaths),
        }
        return path

    def _add_asar_macho(
        self,
        relative_path: str,
        *,
        dependencies: tuple[str, ...] = (_SYSTEM_DEPENDENCY,),
        rpaths: tuple[str, ...] = (),
    ) -> Path:
        unpacked = self._add_macho(
            f"Contents/Resources/app.asar.unpacked/{relative_path}",
            dependencies=dependencies,
            rpaths=rpaths,
        )
        archived = self.asar_root / relative_path
        archived.parent.mkdir(parents=True, exist_ok=True)
        archived.write_bytes(unpacked.read_bytes())
        archived.chmod(0o755)
        self.metadata[str(archived)] = dict(self.metadata[str(unpacked)])
        return archived

    def add_loader_chain(self, *, include_grandchild: bool = True) -> None:
        main = self.app / "Contents/MacOS/Paseo"
        self.metadata[str(main)]["dependencies"] = [
            _SYSTEM_DEPENDENCY,
            "@rpath/libloader.dylib",
        ]
        self.metadata[str(main)]["rpaths"] = ["@executable_path/../Frameworks"]
        self._add_macho(
            "Contents/Frameworks/libloader.dylib",
            dependencies=("@rpath/libchild.dylib",),
            rpaths=("@loader_path/Nested",),
        )
        self._add_macho(
            "Contents/Frameworks/Nested/libchild.dylib",
            dependencies=("@rpath/libgrandchild.dylib",),
        )
        if include_grandchild:
            self._add_macho("Contents/Frameworks/Nested/libgrandchild.dylib")

    def run(
        self,
        *,
        extra_manifest_paths: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        self.metadata_path.write_text(
            json.dumps(self.metadata, sort_keys=True),
            encoding="utf-8",
        )
        native_paths: list[str] = []
        for raw_path in self.metadata:
            path = Path(raw_path)
            try:
                relative_path = path.relative_to(self.app)
            except ValueError:
                relative_path = Path("app.asar") / path.relative_to(self.asar_root)
            native_paths.append(str(relative_path))
        native_paths.extend(extra_manifest_paths)
        native_paths.sort()
        self.expected_manifest.write_text(
            "".join(f"1\t{path}\n" for path in native_paths),
            encoding="utf-8",
        )
        scratch = self.root / "scratch"
        scratch.mkdir()
        return subprocess.run(  # noqa: S603 -- Executes the repo-owned validator.
            [
                "/bin/bash",
                str(_VALIDATOR),
                str(self.app),
                str(self.asar_root),
                str(self.expected_manifest),
                "Paseo",
            ],
            env=os.environ
            | {
                "PASEO_FILE_TOOL": str(self.tools["file"]),
                "PASEO_LIPO_TOOL": str(self.tools["lipo"]),
                "PASEO_MACHO_FIXTURE": str(self.metadata_path),
                "PASEO_OTOOL_TOOL": str(self.tools["otool"]),
                "PASEO_PYTHON": sys.executable,
                "TMPDIR": str(scratch),
            },
            check=False,
            capture_output=True,
            text=True,
        )

    def _write_macho_tools(self) -> dict[str, Path]:
        tool_dir = self.root / "tools"
        tool_dir.mkdir()
        program = dedent(
            f"""\
            #!{sys.executable}
            import json
            import os
            from pathlib import Path
            import sys

            metadata = json.loads(Path(os.environ["PASEO_MACHO_FIXTURE"]).read_text())
            tool = Path(sys.argv[0]).name
            candidate = sys.argv[-1]
            record = metadata.get(str(Path(candidate).resolve(strict=True)))
            if tool == "file":
                print(record["description"] if record else "ASCII text")
            elif tool == "lipo":
                print("arm64")
            elif tool == "otool" and sys.argv[1] == "-D":
                print(f"{{candidate}}:")
                if record and record["id"]:
                    print(record["id"])
            elif tool == "otool" and sys.argv[1] == "-L":
                print(f"{{candidate}}:")
                for dependency in record["dependencies"]:
                    print(
                        f"    {{dependency}} (compatibility version 0.0.0, "
                        "current version 0.0.0)"
                    )
            elif tool == "otool" and sys.argv[1] == "-l":
                for index, rpath in enumerate(record["rpaths"]):
                    print(f"Load command {{index}}")
                    print("          cmd LC_RPATH")
                    print("      cmdsize 48")
                    print(f"         path {{rpath}} (offset 12)")
            else:
                raise SystemExit(f"unsupported fixture invocation: {{sys.argv!r}}")
            """
        )
        tools: dict[str, Path] = {}
        for name in ("file", "lipo", "otool"):
            path = tool_dir / name
            path.write_text(program, encoding="utf-8")
            path.chmod(0o755)
            tools[name] = path
        return tools


def test_validator_resolves_inherited_executable_and_loader_rpaths(
    tmp_path: Path,
) -> None:
    """A child image inherits the resolved run-path stack of its loader chain."""
    fixture = _ValidatorFixture(tmp_path)
    fixture.add_loader_chain()

    completed = fixture.run()

    assert completed.returncode == 0, completed.stderr


def test_validator_emits_an_exact_marker_delimited_manifest_on_mismatch(
    tmp_path: Path,
) -> None:
    """A failed provisional build must expose rows that can be copied verbatim."""
    fixture = _ValidatorFixture(tmp_path)

    completed = fixture.run(
        extra_manifest_paths=("Contents/Frameworks/not-in-the-bundle.dylib",),
    )

    assert completed.returncode != 0
    begin = "PASEO_NATIVE_MANIFEST_V1_BEGIN"
    end = "PASEO_NATIVE_MANIFEST_V1_END"
    assert completed.stderr.count(begin) == 1
    assert completed.stderr.count(end) == 1
    payload = completed.stderr.split(f"{begin}\n", maxsplit=1)[1].split(
        f"{end}\n", maxsplit=1
    )[0]
    assert payload == (
        "1\tContents/MacOS/Paseo\n"
        "1\tContents/Resources/app.asar.unpacked/node_modules/"
        "node-pty/build/Release/pty.node\n"
        "1\tContents/Resources/app.asar.unpacked/node_modules/"
        "sherpa-onnx-darwin-arm64/sherpa-onnx.node\n"
    )


def test_validator_rejects_an_unresolved_inherited_rpath(tmp_path: Path) -> None:
    """A loader context may not turn a missing @rpath target into acceptance."""
    fixture = _ValidatorFixture(tmp_path)
    fixture.add_loader_chain(include_grandchild=False)

    completed = fixture.run()

    assert completed.returncode != 0
    assert (
        "unresolved @rpath linkage in "
        "Contents/Frameworks/Nested/libchild.dylib: @rpath/libgrandchild.dylib"
        in completed.stderr
    )


def test_validator_rejects_a_bundle_symlink_escape(tmp_path: Path) -> None:
    """A symlink cannot make an external file part of the trusted bundle closure."""
    fixture = _ValidatorFixture(tmp_path)
    outside = tmp_path / "outside.dylib"
    outside.write_bytes(b"outside\n")
    escape = fixture.app / "Contents/Frameworks/libescape.dylib"
    escape.parent.mkdir(parents=True)
    escape.symlink_to(outside)

    completed = fixture.run()

    assert completed.returncode != 0
    assert (
        "bundle symlink escapes or has no target: "
        "Contents/Frameworks/libescape.dylib" in completed.stderr
    )


def test_validator_rejects_an_asar_symlink_escape(tmp_path: Path) -> None:
    """An archived alias cannot escape the extracted ASAR trust boundary."""
    fixture = _ValidatorFixture(tmp_path)
    outside = tmp_path / "outside.dylib"
    outside.write_bytes(b"outside\n")
    escape = fixture.asar_root / "node_modules/example/libescape.dylib"
    escape.parent.mkdir(parents=True)
    escape.symlink_to(outside)

    completed = fixture.run()

    assert completed.returncode != 0
    assert (
        "app.asar symlink escapes or has no target: "
        "node_modules/example/libescape.dylib" in completed.stderr
    )


def test_validator_inventories_an_internal_asar_native_symlink(tmp_path: Path) -> None:
    """An archived native alias is a distinct audited logical runtime path."""
    fixture = _ValidatorFixture(tmp_path)
    target = fixture._add_asar_macho("node_modules/example/libreal.dylib")
    alias_relative = "node_modules/example/libalias.dylib"
    alias = fixture.asar_root / alias_relative
    alias.symlink_to(target.name)

    completed = fixture.run(
        extra_manifest_paths=(f"app.asar/{alias_relative}",),
    )

    assert completed.returncode == 0, completed.stderr


def test_validator_rejects_the_opaque_claude_sdk_platform_runtime(
    tmp_path: Path,
) -> None:
    """The SDK's optional Darwin monolith must never enter the app artifact."""
    fixture = _ValidatorFixture(tmp_path)
    monolith = fixture.asar_root / (
        "node_modules/@anthropic-ai/claude-agent-sdk/"
        "node_modules/@anthropic-ai/claude-agent-sdk-darwin-arm64/claude"
    )
    monolith.parent.mkdir(parents=True)
    monolith.write_bytes(b"opaque Claude runtime\n")

    completed = fixture.run()

    assert completed.returncode != 0
    assert "opaque Anthropic SDK platform runtime survived pruning" in completed.stderr


@pytest.mark.parametrize("tool", ["ripgrep", "tree-sitter-bash"])
def test_validator_rejects_stale_anthropic_sdk_vendor_trees(
    tmp_path: Path,
    tool: str,
) -> None:
    """Legacy vendor-tree assumptions may not return under a new SDK lock."""
    fixture = _ValidatorFixture(tmp_path)
    payload = fixture.asar_root / (
        f"node_modules/@anthropic-ai/claude-agent-sdk/vendor/{tool}/arm64-darwin/{tool}"
    )
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"stale vendor payload\n")

    completed = fixture.run()

    assert completed.returncode != 0
    assert "stale Anthropic SDK vendor tree survived pruning" in completed.stderr
