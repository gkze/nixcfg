"""Configuration for ptpython REPL."""

from catppuccin.extras.pygments import FrappeStyle
from prompt_toolkit.styles import Style, style_from_pygments_cls
from ptpython.python_input import PythonInput

STYLE_NAME: str = "catppuccin-frappe"
PT_STYLE: Style = style_from_pygments_cls(FrappeStyle)


def configure(repl: PythonInput) -> None:
    """Configure ptpython REPL."""
    repl.show_signature = True
    repl.show_docstring = True
    repl.enable_auto_suggest = True
    repl.vi_mode = True
    repl.install_ui_colorscheme(STYLE_NAME, PT_STYLE)
    repl.use_ui_colorscheme(STYLE_NAME)
    repl.install_code_colorscheme(STYLE_NAME, PT_STYLE)
    repl.use_code_colorscheme(STYLE_NAME)
