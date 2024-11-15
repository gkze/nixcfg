from catppuccin.extras.pygments import FrappeStyle
from prompt_toolkit.styles import style_from_pygments_cls
from ptpython.python_input import PythonInput

STYLE: str = "catppuccin-frappe"


def configure(repl: PythonInput) -> None:
    repl.show_signature = True
    repl.show_docstring = True
    repl.vi_mode = True
    repl.install_ui_colorscheme(STYLE, style_from_pygments_cls(FrappeStyle))
    repl.use_ui_colorscheme(STYLE)
