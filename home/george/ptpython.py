"""Configuration for ptpython REPL."""

from catppuccin.extras.pygments import FrappeStyle
from prompt_toolkit.styles import style_from_pygments_cls
from ptpython.python_input import PythonInput

STYLE_NAME: str = "catppuccin-frappe"


def configure(repl: PythonInput) -> None:
    """Configure ptpython REPL."""
    repl.show_signature = True
    repl.show_docstring = True
    repl.vi_mode = True
    repl.install_ui_colorscheme(STYLE_NAME, style_from_pygments_cls(FrappeStyle))
    repl.use_code_colorscheme(STYLE_NAME)
    repl.use_ui_colorscheme(STYLE_NAME)
