from catppuccin.extras.pygments import FrappeStyle
from prompt_toolkit.styles import Style, style_from_pygments_cls
from ptpython.python_input import PythonInput

STYLE_NAME: str = "catppuccin-frappe"
STYLE_CLS: Style = style_from_pygments_cls(FrappeStyle)


def configure(repl: PythonInput) -> None:
    repl.show_signature = True
    repl.show_docstring = True
    repl.vi_mode = True
    repl.install_ui_colorscheme(STYLE_NAME, STYLE_CLS)
    repl.use_code_colorscheme(STYLE_NAME)
    repl.use_ui_colorscheme(STYLE_NAME)
