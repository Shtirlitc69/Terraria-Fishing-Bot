"""Make PySide6's split wheel directories available to the Windows loader."""

import os
import sys


if sys.platform == "win32" and getattr(sys, "frozen", False):
    for name in ("PySide6", "shiboken6"):
        directory = os.path.join(sys._MEIPASS, name)
        if os.path.isdir(directory):
            os.add_dll_directory(directory)
            os.environ["PATH"] = directory + os.pathsep + os.environ["PATH"]
