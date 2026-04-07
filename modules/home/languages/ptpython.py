"""Configuration for ptpython REPL."""

import importlib
from typing import Protocol, cast


class _Style(Protocol):
    """Marker protocol for ptpython style objects."""


class _PythonInput(Protocol):
    show_signature: bool
    show_docstring: bool
    enable_auto_suggest: bool
    vi_mode: bool

    def install_ui_colorscheme(self, name: str, style: _Style) -> None: ...

    def use_ui_colorscheme(self, name: str) -> None: ...

    def install_code_colorscheme(self, name: str, style: _Style) -> None: ...

    def use_code_colorscheme(self, name: str) -> None: ...


STYLE_NAME: str = "catppuccin-frappe"


def _build_style() -> _Style:
    pygments_mod = importlib.import_module("catppuccin.extras.pygments")
    styles_mod = importlib.import_module("prompt_toolkit.styles")
    return cast("_Style", styles_mod.style_from_pygments_cls(pygments_mod.FrappeStyle))


def configure(repl: _PythonInput) -> None:
    """Configure ptpython REPL."""
    style = _build_style()
    repl.show_signature = True
    repl.show_docstring = True
    repl.enable_auto_suggest = True
    repl.vi_mode = True
    repl.install_ui_colorscheme(STYLE_NAME, style)
    repl.use_ui_colorscheme(STYLE_NAME)
    repl.install_code_colorscheme(STYLE_NAME, style)
    repl.use_code_colorscheme(STYLE_NAME)
