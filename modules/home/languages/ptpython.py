"""Configuration for ptpython REPL."""

import importlib
import os
from pathlib import Path
from typing import Literal, Protocol, cast


class _Style(Protocol):
    """Marker protocol for ptpython style objects."""


class _Application(Protocol):
    refresh_interval: float | None


class _PythonInput(Protocol):
    show_signature: bool
    show_docstring: bool
    enable_auto_suggest: bool
    vi_mode: bool
    app: _Application

    def install_ui_colorscheme(self, name: str, style: _Style) -> None: ...

    def use_ui_colorscheme(self, name: str) -> None: ...

    def install_code_colorscheme(self, name: str, style: _Style) -> None: ...

    def use_code_colorscheme(self, name: str) -> None: ...


STYLE_NAME: str = "catppuccin-system"


def _appearance() -> Literal["light", "dark"]:
    """Read the mode published by the macOS appearance listener."""
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    path = Path(
        os.environ.get("NIXCFG_APPEARANCE_FILE", state_home / "nixcfg/appearance/mode")
    )
    try:
        mode = path.read_text().strip()
    except FileNotFoundError:
        # Non-Darwin hosts retain the previous Frappé default.
        return "dark"
    if mode in {"light", "dark"}:
        return mode
    message = f"Invalid appearance mode in {path}"
    raise ValueError(message)


def _build_style() -> _Style:
    pygments_mod = importlib.import_module("catppuccin.extras.pygments")
    styles_mod = importlib.import_module("prompt_toolkit.styles")
    styles = {
        "light": styles_mod.style_from_pygments_cls(pygments_mod.LatteStyle),
        "dark": styles_mod.style_from_pygments_cls(pygments_mod.FrappeStyle),
    }
    return cast("_Style", styles_mod.DynamicStyle(lambda: styles[_appearance()]))


def configure(repl: _PythonInput) -> None:
    """Configure ptpython REPL."""
    style = _build_style()
    repl.show_signature = True
    repl.show_docstring = True
    repl.enable_auto_suggest = True
    repl.vi_mode = True
    repl.app.refresh_interval = 1.0
    repl.install_ui_colorscheme(STYLE_NAME, style)
    repl.use_ui_colorscheme(STYLE_NAME)
    repl.install_code_colorscheme(STYLE_NAME, style)
    repl.use_code_colorscheme(STYLE_NAME)
