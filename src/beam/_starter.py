"""Working out how a project runs, and writing a start.bat that does it."""

from __future__ import annotations

import ast
import importlib.util
import sys
import sysconfig
from pathlib import Path, PurePosixPath

ENTRY_CANDIDATES = ("main.py", "app.py", "run.py", "start.py", "manage.py")

# import name -> pip package, for common packages where the two differ
PIP_NAMES = {
    "attr": "attrs",
    "bs4": "beautifulsoup4",
    "Crypto": "pycryptodome",
    "cv2": "opencv-python",
    "dateutil": "python-dateutil",
    "docx": "python-docx",
    "dotenv": "python-dotenv",
    "fitz": "PyMuPDF",
    "git": "GitPython",
    "jose": "python-jose",
    "jwt": "PyJWT",
    "magic": "python-magic",
    "multipart": "python-multipart",
    "MySQLdb": "mysqlclient",
    "OpenSSL": "pyOpenSSL",
    "PIL": "Pillow",
    "pkg_resources": "setuptools",
    "pptx": "python-pptx",
    "psycopg2": "psycopg2-binary",
    "pythoncom": "pywin32",
    "serial": "pyserial",
    "skimage": "scikit-image",
    "sklearn": "scikit-learn",
    "speech_recognition": "SpeechRecognition",
    "telegram": "python-telegram-bot",
    "usb": "pyusb",
    "win32api": "pywin32",
    "win32com": "pywin32",
    "win32con": "pywin32",
    "yaml": "PyYAML",
}

# Namespace roots that cannot be mapped to one package without guessing.
UNGUESSABLE = {"google", "azure", "__future__"}


def _is_stdlib(name: str) -> bool:
    if name in sys.builtin_module_names:
        return True
    names = getattr(sys, "stdlib_module_names", None)  # 3.10+
    if names is not None:
        return name in names
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return False
    if spec is None or not spec.origin:
        return False
    if spec.origin in ("built-in", "frozen"):
        return True
    stdlib = sysconfig.get_paths()["stdlib"].lower()
    origin = spec.origin.lower()
    return origin.startswith(stdlib) and "site-packages" not in origin


def _imports_of(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def guess_requirements(files) -> list[str]:
    """Pip packages the project's .py files import. ``files`` is walk() output."""
    local = set()
    imports = set()
    for abs_path, rel, _ in files:
        local.update(rel.parts[:-1])
        if rel.suffix == ".py":
            local.add(rel.stem)
            imports |= _imports_of(_read(abs_path))
    needed = {
        PIP_NAMES.get(name, name)
        for name in imports
        if name not in local and name not in UNGUESSABLE and not _is_stdlib(name)
    }
    return sorted(needed, key=str.lower)


def find_entry(files) -> str | None:
    """Pick the script to run: main.py/app.py/..., else the one __main__ script."""
    rels = {rel.as_posix(): (abs_path, rel) for abs_path, rel, _ in files}
    for name in ENTRY_CANDIDATES:
        if name in rels:
            return name
    top = [r for r in rels.values() if len(r[1].parts) == 1 and r[1].suffix == ".py"]
    if len(top) == 1:
        return top[0][1].as_posix()
    with_main = [r for r in top if "__main__" in _read(r[0])]
    if len(with_main) == 1:
        return with_main[0][1].as_posix()
    return None


def detect(files, main: str | None = None, requirements=None) -> dict:
    """Work out what kind of project this is and how start.bat should run it.

    Returns {"kind", "entry", "requirements", "has_requirements_file"}.
    """
    rels = {rel.as_posix() for _, rel, _ in files}
    has_py = any(r.endswith(".py") for r in rels)
    info = {
        "kind": None,
        "entry": None,
        "requirements": [],
        "has_requirements_file": "requirements.txt" in rels,
    }

    if main is not None:
        main = PurePosixPath(str(main).replace("\\", "/")).as_posix()
        if main not in rels:
            raise FileNotFoundError(f"main={main!r} is not in the project")

    if (main or "").endswith(".py") or (main is None and has_py):
        info["kind"] = "python"
        info["entry"] = main or find_entry(files)
        if requirements is not None:
            info["requirements"] = list(requirements)
        elif not info["has_requirements_file"]:
            info["requirements"] = guess_requirements(files)
        imports = set()
        if info["entry"]:
            for abs_path, rel, _ in files:
                if rel.as_posix() == info["entry"]:
                    imports = _imports_of(_read(abs_path))
        if "streamlit" in imports:
            info["kind"] = "streamlit"
        elif info["entry"] and info["entry"].endswith("manage.py"):
            info["kind"] = "django"
    elif "package.json" in rels:
        info["kind"] = "node"
    elif main or "index.html" in rels:
        info["kind"] = "static"
        info["entry"] = main or "index.html"
    return info


def _win(path: str) -> str:
    return path.replace("/", "\\")


PY_SETUP = r"""
set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY goto nopython
%PY% -c "import sys" >nul 2>nul
if errorlevel 1 goto nopython

if not exist ".venv\Scripts\python.exe" (
    echo Creating a virtual environment ...
    %PY% -m venv .venv
    if errorlevel 1 goto failed
)
set "VPY=.venv\Scripts\python.exe"

if exist "requirements.txt" if not exist ".venv\.beam-ready" (
    echo Installing requirements ...
    "%VPY%" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto failed
    echo ok> ".venv\.beam-ready"
)
"""

PY_TAIL = r"""
:nopython
echo.
echo Python was not found on this computer.
echo Install it from https://www.python.org/downloads/ and tick
echo "Add python.exe to PATH" during setup, then run start.bat again.
pause
exit /b 1

:failed
echo.
echo Setup failed - see the messages above.
echo Delete the .venv folder and run start.bat again to start fresh.
pause
exit /b 1
"""


def start_bat(name: str, info: dict) -> str:
    """The text of start.bat for a project described by detect()."""
    kind, entry = info["kind"], info["entry"]
    name = "".join(ch for ch in name if ch not in '&|<>^%"()') or "project"
    lines = [
        "@echo off",
        "setlocal",
        f"title {name}",
        'cd /d "%~dp0"',
        "",
        "rem Made by beam. Sets the project up on this computer and runs it.",
        "rem Safe to run again: setup is skipped once it has been done.",
    ]

    if kind in ("python", "streamlit", "django"):
        lines.append(PY_SETUP)
        if entry is None:
            lines += [
                "echo.",
                "echo Setup finished. No main script was found, so nothing was",
                "echo started. Run your script with:",
                "echo     .venv\\Scripts\\python.exe your_script.py",
            ]
        else:
            if kind == "streamlit":
                run = f'"%VPY%" -m streamlit run "{_win(entry)}"'
            elif kind == "django":
                run = f'"%VPY%" "{_win(entry)}" runserver'
            else:
                run = f'"%VPY%" "{_win(entry)}"'
            lines += [f"echo Starting {name} ...", "echo.", run, "echo.",
                      "echo The program has finished."]
        lines += ["pause", "exit /b 0", PY_TAIL]
    elif kind == "node":
        lines += [
            "",
            "where npm >nul 2>nul",
            "if errorlevel 1 (",
            "    echo Node.js was not found. Install it from https://nodejs.org/",
            "    pause",
            "    exit /b 1",
            ")",
            'if not exist "node_modules" (',
            "    echo Installing packages ...",
            "    call npm install",
            "    if errorlevel 1 (",
            "        echo npm install failed - see the messages above.",
            "        pause",
            "        exit /b 1",
            "    )",
            ")",
            f"echo Starting {name} ...",
            "call npm start",
            "pause",
        ]
    elif kind == "static":
        lines += ["", f'start "" "{_win(entry)}"']
    else:
        lines += ["", "echo Nothing to run for this project.", "pause"]

    text = "\n".join(lines).strip("\n") + "\n"
    return text.replace("\n", "\r\n")  # cmd.exe wants CRLF line endings
