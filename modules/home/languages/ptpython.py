"""Configuration for ptpython REPL."""

# ruff: noqa: INP001

import importlib
from typing import Protocol


class _PythonInput(Protocol):
    show_signature: bool
    show_docstring: bool
    enable_auto_suggest: bool
    vi_mode: bool

    def install_ui_colorscheme(self, name: str, style: object) -> None: ...

    def use_ui_colorscheme(self, name: str) -> None: ...

    def install_code_colorscheme(self, name: str, style: object) -> None: ...

    def use_code_colorscheme(self, name: str) -> None: ...


STYLE_NAME: str = "catppuccin-frappe"


def _build_style() -> object:
    pygments_mod = importlib.import_module("catppuccin.extras.pygments")
    styles_mod = importlib.import_module("prompt_toolkit.styles")
    return styles_mod.style_from_pygments_cls(pygments_mod.FrappeStyle)


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
