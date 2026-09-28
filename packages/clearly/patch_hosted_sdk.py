"""Strip Liquid Glass APIs that Xcode 16.4 / MacOSX15.5 cannot type-check.

Clearly 3.3.0 gates `GlassEffectContainer` / `.glassEffect` with
`@available(macOS 26.0, *)`. That is a runtime check. Hosted macos-15 still
type-checks those symbols against the 15.5 SDK and fails the build. Keep the
macOS 15 fallback layout so Darwin validate can compile the current pin.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

_TOOLBAR_RELATIVE = Path("Clearly") / "BottomToolbar.swift"
_LIQUID_GLASS_TOKENS = ("GlassEffectContainer", ".glassEffect(")


@dataclass(frozen=True, slots=True)
class _SourcePatch:
    old: str
    new: str


_PATCHES = (
    _SourcePatch(
        """    var body: some View {
        if #available(macOS 26.0, *) {
            glassBody
        } else {
            legacyBody
        }
    }
""",
        """    var body: some View {
        legacyBody
    }
""",
    ),
    _SourcePatch(
        """    @available(macOS 26.0, *)
    private var glassBody: some View {
        GlassEffectContainer(spacing: 0) {
            HStack(spacing: 0) {
                ModePill(viewMode: $viewMode)

                Spacer(minLength: 12)
                    .contentShape(Rectangle())
                    .allowsHitTesting(false)

                countText
"""
        '                    .accessibilityLabel("\\(statusBarState.counts.totalWords) '
        'words, \\(statusBarState.counts.totalChars) characters")\n'
        """                    .accessibilityAddTraits(.isStaticText)

                Spacer(minLength: 12)
                    .contentShape(Rectangle())
                    .allowsHitTesting(false)

                HStack(spacing: 8) {
                    glassCopyMenu
                    glassOutlineToggle
                }
            }
            .frame(height: Self.pillHeight)
        }
    }

""",
        "",
    ),
    _SourcePatch(
        """    @available(macOS 26.0, *)
    private var glassCopyMenu: some View {
        Menu {
            copyMenuContent
        } label: {
            Image(systemName: "doc.on.doc")
                .font(.system(size: 13, weight: .medium))
                .frame(width: 32, height: 32)
                .contentShape(Circle())
        }
        .menuStyle(.button)
        .buttonStyle(.plain)
        .menuIndicator(.hidden)
        .glassEffect(.regular.interactive(), in: .circle)
        .help("Copy document")
        .accessibilityLabel("Copy document")
    }

    @available(macOS 26.0, *)
    private var glassOutlineToggle: some View {
        Button {
            outlineState.isVisible.toggle()
        } label: {
            Image(systemName: "list.bullet.indent")
                .font(.system(size: 13, weight: .medium))
                .frame(width: 32, height: 32)
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .glassEffect(
            outlineState.isVisible
                ? .regular.tint(Color.accentColor.opacity(0.35)).interactive()
                : .regular.interactive(),
            in: .circle
        )
        .help("Toggle outline")
        .accessibilityLabel("Toggle outline")
        .accessibilityAddTraits(outlineState.isVisible ? .isSelected : [])
    }

""",
        "",
    ),
    _SourcePatch(
        """    var body: some View {
        if #available(macOS 26.0, *) {
            HStack(spacing: 2) {
                segment(.edit, title: "Edit", systemImage: "pencil")
                segment(.preview, title: "Preview", systemImage: "eye")
            }
            .padding(2)
            .glassEffect(.regular, in: .capsule)
        } else {
            HStack(spacing: 2) {
                segment(.edit, title: "Edit", systemImage: "pencil")
                segment(.preview, title: "Preview", systemImage: "eye")
            }
            .padding(2)
            .background(
                Capsule(style: .continuous)
                    .fill(Color(NSColor.unemphasizedSelectedContentBackgroundColor))
            )
        }
    }
""",
        """    var body: some View {
        HStack(spacing: 2) {
            segment(.edit, title: "Edit", systemImage: "pencil")
            segment(.preview, title: "Preview", systemImage: "eye")
        }
        .padding(2)
        .background(
            Capsule(style: .continuous)
                .fill(Color(NSColor.unemphasizedSelectedContentBackgroundColor))
        )
    }
""",
    ),
)


def _has_liquid_glass(text: str) -> bool:
    return any(token in text for token in _LIQUID_GLASS_TOKENS)


def _apply_patches(source: str, patches: Iterable[_SourcePatch]) -> str:
    patched = source
    for patch in patches:
        count = patched.count(patch.old)
        if count != 1:
            msg = f"expected one Clearly hosted-SDK toolbar match, found {count}"
            raise RuntimeError(msg)
        patched = patched.replace(patch.old, patch.new, 1)
    return patched


def _liquid_glass_offenders(
    files: Iterable[tuple[Path, str]],
) -> tuple[str, ...]:
    return tuple(str(path) for path, text in files if _has_liquid_glass(text))


def patch_tree(source_root: Path) -> None:
    """Keep Clearly compiling on hosted macos-15 Xcode 16.4."""
    toolbar = source_root / _TOOLBAR_RELATIVE
    if not toolbar.is_file():
        msg = "Clearly source tree is missing Clearly/BottomToolbar.swift"
        raise RuntimeError(msg)

    swift_files = tuple(sorted(source_root.rglob("*.swift")))
    current_sources = tuple(
        (path.relative_to(source_root), path.read_text(encoding="utf-8"))
        for path in swift_files
    )
    if not any(_has_liquid_glass(text) for _, text in current_sources):
        return

    original = toolbar.read_text(encoding="utf-8")
    patched = _apply_patches(original, _PATCHES)
    planned = tuple(
        (_TOOLBAR_RELATIVE, patched) if path == _TOOLBAR_RELATIVE else (path, text)
        for path, text in current_sources
    )
    offenders = _liquid_glass_offenders(planned)
    if offenders:
        listed = ", ".join(offenders)
        msg = (
            "Clearly still references Liquid Glass APIs that Xcode 16.4 "
            f"cannot compile: {listed}"
        )
        raise RuntimeError(msg)
    toolbar.write_text(patched, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Patch an unpacked Clearly source tree from the package build."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    args = parser.parse_args(argv)
    patch_tree(args.source_root)
    return 0


if __name__ == "__main__":  # pragma: no cover -- packaged CLI guard
    raise SystemExit(main())
