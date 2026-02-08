import traceback


def format_exception(exc: Exception) -> str:
    return f"{exc}\n{traceback.format_exc()}"
