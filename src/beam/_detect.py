"""Working out what is in a project, and how to set it up and start it.

Each language has a function that looks at one folder and returns an App:
what has to be installed first (tools), the setup steps, and the command that
starts it, both for start.bat (Windows) and start.sh (macOS, Linux). _starter
turns a list of apps into the two scripts.

Setup lines run inside the app's folder. start.bat lines jump to ``failed``
when a step fails; start.sh lines call ``failed``. Where a start.bat label has
to be unique to one app, it contains ``@N@`` and _starter fills in a number.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import shlex
import sys
import sysconfig
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath

from ._stacks import UNITY

# --- what has to be installed ------------------------------------------------


@dataclass(frozen=True)
class Tool:
    """Something the other laptop needs before setup can start."""

    name: str  # what the person is told: "Rust"
    command: str  # on PATH once it is installed: "cargo"
    winget: str | None  # Windows can install it with winget; None: a link only
    url: str  # where to get it by hand
    check: str = ""  # a check of its own instead of the command: "python", ...
    arg: str = ""  # for that check: a Python or .NET version, "rust", ...
    only: str = ""  # "win" or "posix": needed on that system only


NODE = Tool("Node.js", "node", "OpenJS.NodeJS.LTS", "https://nodejs.org/")
PNPM = Tool("pnpm", "pnpm", "pnpm.pnpm", "https://pnpm.io/installation")
YARN = Tool("Yarn", "yarn", "Yarn.Yarn", "https://yarnpkg.com/getting-started/install")
BUN = Tool("Bun", "bun", "Oven-sh.Bun", "https://bun.sh/")
DENO = Tool("Deno", "deno", "DenoLand.Deno", "https://deno.com/")
RUST = Tool("Rust", "cargo", "Rustlang.Rustup", "https://rustup.rs/")
GO = Tool("Go", "go", "GoLang.Go", "https://go.dev/dl/")
MAVEN = Tool("Maven", "mvn", None, "https://maven.apache.org/download.cgi")
GRADLE = Tool("Gradle", "gradle", None, "https://gradle.org/install/")
PHP = Tool("PHP", "php", "PHP.PHP.8.4", "https://www.php.net/downloads")
COMPOSER = Tool("Composer", "composer", None, "https://getcomposer.org/download/")
RUBY = Tool(
    "Ruby", "ruby", "RubyInstallerTeam.RubyWithDevKit.3.4",
    "https://www.ruby-lang.org/en/downloads/",
)
ERLANG = Tool("Erlang", "erl", "Erlang.ErlangOTP", "https://www.erlang.org/downloads")
ELIXIR = Tool("Elixir", "elixir", "Elixir.Elixir", "https://elixir-lang.org/install.html")
FLUTTER = Tool("Flutter", "flutter", None, "https://docs.flutter.dev/get-started/install")
DART = Tool("Dart", "dart", "Google.DartSDK", "https://dart.dev/get-dart")
STACK = Tool("Haskell Stack", "stack", None, "https://www.haskell.org/ghcup/")
CABAL = Tool("Cabal", "cabal", None, "https://www.haskell.org/ghcup/")
SWIFT = Tool("Swift", "swift", "Swift.Toolchain", "https://www.swift.org/install/")
CMAKE = Tool("CMake", "cmake", "Kitware.CMake", "https://cmake.org/download/")
MSVC = Tool(
    "Microsoft C++ Build Tools", "", "Microsoft.VisualStudio.BuildTools",
    "https://visualstudio.microsoft.com/visual-cpp-build-tools/",
    check="msvc", only="win",
)
MSVC_FOR_RUST = replace(MSVC, arg="rust")  # only when Rust uses the MSVC linker
CXX = Tool(
    "A C/C++ compiler", "c++", None,
    "xcode-select --install on a Mac, build-essential on Ubuntu", only="posix",
)

PYTHON_MINORS = range(9, 15)  # Python.Python.3.9 ... 3.14 are all on winget
PYTHON_DEFAULT = 13
JAVA_VERSIONS = (8, 11, 17, 21, 25)  # Temurin JDKs on winget
DOTNET_VERSIONS = (6, 7, 8, 9, 10)


def python_tool(minor: int = PYTHON_DEFAULT) -> Tool:
    return Tool(
        "Python", "python", f"Python.Python.3.{minor}",
        "https://www.python.org/downloads/", check="python",
    )


def java_tool(version: int = 21) -> Tool:
    return Tool(
        f"Java JDK {version}", "javac", f"EclipseAdoptium.Temurin.{version}.JDK",
        "https://adoptium.net/",
    )


def dotnet_tool(major: int, runtime: str) -> Tool:
    return Tool(
        f".NET SDK {major}", "dotnet", f"Microsoft.DotNet.SDK.{major}",
        "https://dotnet.microsoft.com/download", check="dotnet",
        arg=f"{major} {runtime}",
    )


# --- one thing to set up and start -------------------------------------------

ANY = "*"  # in App.absorbs: everything below belongs to it (a game engine)


@dataclass
class App:
    """One thing in the project to set up and start."""

    folder: str  # relative to the project, "" for the project itself
    stack: str  # for the printout: "Python", "Node", "Rust", ...
    what: str = ""  # how it starts, for the printout: "npm run dev"
    tools: list = field(default_factory=list)
    setup_bat: list = field(default_factory=list)
    setup_sh: list = field(default_factory=list)
    run_bat: str | None = None  # the command that starts it; None: setup only
    run_sh: str | None = None
    waits: bool = True  # False when starting it returns at once (a browser tab)
    note: str = ""  # said at the end when nothing is started
    note_sh: str | None = None  # instead of ``note`` in start.sh
    frontend: bool = False  # started after the back ends
    files: dict = field(default_factory=dict)  # added to the zip, path in folder
    requires: list = field(default_factory=list)  # a pip list beam worked out
    absorbs: frozenset = frozenset()  # families whose sub-projects belong here
    family: str = ""


# --- the files going into the zip, by folder ---------------------------------


def _join(folder: str, name: str) -> str:
    return f"{folder}/{name}" if folder else name


def _win(path: str) -> str:
    """A path for start.bat: backslashes, and % doubled so it stays literal."""
    return path.replace("/", "\\").replace("%", "%%")


class Tree:
    """The files going into the zip, looked up by folder ("" is the top)."""

    def __init__(self, files):
        self.paths = {}  # "backend/app.py" -> absolute path
        self.children = {"": set()}  # folder -> names directly inside it
        for abs_path, rel, _ in files:
            key = rel.as_posix()
            self.paths[key] = abs_path
            parts = key.split("/")
            for depth in range(len(parts)):
                folder = "/".join(parts[:depth])
                self.children.setdefault(folder, set()).add(parts[depth])
        self._text = {}

    def is_file(self, folder: str, name: str) -> bool:
        return _join(folder, name) in self.paths

    def names(self, folder: str) -> set:
        return self.children.get(folder, set())

    def files(self, folder: str) -> list:
        return sorted(n for n in self.names(folder) if self.is_file(folder, n))

    def ending(self, folder: str, *suffixes) -> list:
        return [n for n in self.files(folder) if n.endswith(suffixes)]

    def under(self, folder: str) -> list:
        if not folder:
            return sorted(self.paths)
        return sorted(k for k in self.paths if k.startswith(folder + "/"))

    def read(self, folder: str, name: str = "") -> str:
        key = _join(folder, name) if name else folder
        if key not in self._text:
            path = self.paths.get(key)
            try:
                text = path.read_text("utf-8", errors="replace") if path else ""
            except OSError:
                text = ""
            self._text[key] = text
        return self._text[key]


def _relative(keys, folder: str) -> list:
    if not folder:
        return list(keys)
    cut = len(folder) + 1
    return [k[cut:] for k in keys if k.startswith(folder + "/")]


# --- reading config files ----------------------------------------------------


def _json(text: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    return data if isinstance(data, dict) else {}


def _strip_jsonc(text: str) -> str:
    """JSON with // and /* */ comments and trailing commas, made plain JSON."""
    out, i, n, in_string = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_string = False
        elif c == '"':
            in_string = True
            out.append(c)
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _toml(text: str) -> dict:
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        return _mini_toml(text)
    try:
        return tomllib.loads(text)
    except ValueError:
        return _mini_toml(text)


_TOML_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"|\'([^\']*)\'')


def _uncomment(line: str) -> str:
    quote = None
    for i, c in enumerate(line):
        if quote:
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "#":
            return line[:i]
    return line


def _toml_value(raw: str):
    raw = raw.strip()
    if raw[:1] in "\"'":
        m = _TOML_STRING.match(raw)
        return (m.group(1) if m.group(1) is not None else m.group(2)) if m else raw
    if raw.startswith("["):
        return [m.group(1) if m.group(1) is not None else m.group(2)
                for m in _TOML_STRING.finditer(raw)]
    if raw.startswith("{"):
        pairs = re.findall(r"([\w.-]+)\s*=\s*(\"[^\"]*\"|'[^']*'|[^,}]+)", raw)
        return {k: _toml_value(v) for k, v in pairs}
    return {"true": True, "false": False}.get(raw, raw)


def _mini_toml(text: str) -> dict:
    """Enough TOML for the keys beam reads, on Pythons without tomllib."""
    root: dict = {}
    table = root
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = _uncomment(lines[i]).strip()
        i += 1
        if not line:
            continue
        if line.startswith("["):
            many = line.startswith("[[")
            keys = [k.strip().strip("\"'") for k in line.strip("[] ").split(".")]
            parent = root
            for key in keys[:-1]:
                parent = parent.setdefault(key, {})
                if isinstance(parent, list):
                    parent = parent[-1]
            if many:
                table = {}
                parent.setdefault(keys[-1], []).append(table)
            else:
                table = parent.setdefault(keys[-1], {})
            continue
        key, eq, value = line.partition("=")
        if not eq:
            continue
        value = value.strip()
        while value.startswith("[") and value.count("[") > value.count("]") \
                and i < len(lines):
            value += " " + _uncomment(lines[i]).strip()
            i += 1
        table[key.strip().strip("\"'")] = _toml_value(value)
    return root


# --- Python ------------------------------------------------------------------

ENTRY_CANDIDATES = ("main.py", "app.py", "run.py", "start.py", "manage.py")
PY_MARKERS = frozenset({
    "requirements.txt", "pyproject.toml", "setup.py", "Pipfile", "uv.lock",
    "poetry.lock", "environment.yml", *ENTRY_CANDIDATES,
})

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


def _imports_of(source: str) -> set:
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


def _notebook_imports(text: str) -> set:
    cells = _json(text).get("cells")
    found = set()
    for cell in cells if isinstance(cells, list) else []:
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        if isinstance(source, list):
            source = "".join(str(s) for s in source)
        # %magic and !shell lines are not Python
        code = "\n".join(
            ln for ln in str(source).splitlines()
            if not ln.lstrip().startswith(("%", "!"))
        )
        found |= _imports_of(code)
    return found


def guess_requirements(tree: Tree, folder: str, own) -> list:
    """Pip packages the app's .py files and notebooks import."""
    rel = _relative(own, folder)
    local = set()
    imports = set()
    for path in rel:
        parts = path.split("/")
        local.update(parts[:-1])
        if path.endswith(".py"):
            local.add(parts[-1][:-3])
            imports |= _imports_of(tree.read(folder, path))
        elif path.endswith(".ipynb"):
            imports |= _notebook_imports(tree.read(folder, path))
    needed = {
        PIP_NAMES.get(name, name)
        for name in imports
        if name not in local and name not in UNGUESSABLE and not _is_stdlib(name)
    }
    return sorted(needed, key=str.lower)


def _pip_spec(name: str, value) -> str:
    """A Poetry or Pipfile dependency as a line pip understands."""
    if not isinstance(value, str):
        return name  # {version = ..., extras = ...}: the name is enough
    value = value.strip()
    if value in ("", "*") or (value[0] in "^~" and not value.startswith("~=")):
        return name  # ^1.2 and ~1.2 are Poetry's own syntax
    if value[0].isdigit():
        return f"{name}=={value}"
    return name + value


def declared_requirements(tree: Tree, folder: str):
    """Dependencies listed in pyproject.toml or a Pipfile, or None."""
    pyproject = _toml(tree.read(folder, "pyproject.toml"))
    project = _dict(pyproject.get("project"))
    if isinstance(project.get("dependencies"), list):
        return [str(d) for d in project["dependencies"]]
    poetry = _dict(pyproject.get("tool")).get("poetry")
    poetry = _dict(_dict(poetry).get("dependencies"))
    if poetry:
        return [_pip_spec(k, v) for k, v in poetry.items() if k.lower() != "python"]
    pipfile = _dict(_toml(tree.read(folder, "Pipfile")).get("packages"))
    if pipfile:
        return [_pip_spec(k, v) for k, v in pipfile.items()]
    return None


def _python_minor(tree: Tree, folder: str) -> int:
    """The Python 3 minor version to install if there is none: 13 unless told."""
    want = None
    pinned = tree.read(folder, ".python-version").strip()
    runtime = tree.read(folder, "runtime.txt").strip()
    m = re.search(r"3\.(\d+)", pinned or runtime)
    if m:
        want = int(m.group(1))
    else:
        spec = str(_dict(_toml(tree.read(folder, "pyproject.toml")).get("project"))
                   .get("requires-python", ""))
        spec += str(_dict(_toml(tree.read(folder, "Pipfile")).get("requires"))
                    .get("python_version", ""))
        low = re.search(r"(?:>=|==|~=|\^|^)\s*3\.(\d+)", spec)
        high = re.search(r"<(=?)\s*3\.(\d+)", spec)
        want = PYTHON_DEFAULT
        if low and int(low.group(1)) > want:
            want = int(low.group(1))
        if high:
            top = int(high.group(2)) - (0 if high.group(1) else 1)
            want = min(want, top)
    return min(max(want, PYTHON_MINORS[0]), PYTHON_MINORS[-1])


def _py_entry(tree: Tree, folder: str, rel) -> str | None:
    """The script to run: main.py, app.py, ..., else the one top-level script."""
    rel = set(rel)
    for prefix in ("", "src/"):
        for name in ENTRY_CANDIDATES:
            if prefix + name in rel:
                return prefix + name
    top = sorted(r for r in rel if "/" not in r and r.endswith(".py"))
    if len(top) == 1:
        return top[0]
    with_main = [r for r in top if "__main__" in tree.read(folder, r)]
    if len(with_main) == 1:
        return with_main[0]
    return None


def _python(tree: Tree, folder: str, own, entry: str | None = None,
            requirements=None) -> App:
    rel = _relative(own, folder)
    entry = entry or _py_entry(tree, folder, rel)
    source = tree.read(folder, entry) if entry else ""
    imports = _imports_of(source)
    notebooks = [r for r in rel if r.endswith(".ipynb")]

    kind, what = "script", entry
    if entry is None:
        kind = "notebook" if notebooks else None
        what = "jupyter notebook" if notebooks else ""
    elif "streamlit" in imports:
        kind, what = "streamlit", f"streamlit run {entry}"
    elif entry.endswith("manage.py"):
        kind, what = "django", f"{entry} runserver"
    elif "fastapi" in imports and "uvicorn.run(" not in source:
        kind = "fastapi"
    elif "flask" in imports and not re.search(r"\.run\(", source):
        kind, what = "flask", f"flask run ({entry})"

    has_file = "requirements.txt" in rel
    generated = None
    if requirements is not None:
        generated = list(requirements)
    elif not has_file:
        declared = declared_requirements(tree, folder)
        generated = declared if declared is not None else guess_requirements(
            tree, folder, own
        )
    ensure = []  # (module, package) that have to be there to start it
    if kind == "fastapi":
        ensure.append(("uvicorn", "uvicorn"))
    if kind == "notebook":
        ensure.append(("notebook", "notebook"))
    has_reqs = has_file or bool(generated)

    installer = None
    if requirements is None:
        pyproject = _toml(tree.read(folder, "pyproject.toml"))
        if tree.is_file(folder, "uv.lock"):
            installer = "uv"
        elif tree.is_file(folder, "poetry.lock") or "poetry" in _dict(
            pyproject.get("tool")
        ):
            installer = "poetry"
        elif tree.is_file(folder, "Pipfile"):
            installer = "pipenv"

    bat = [
        r'set "VPY=.venv\Scripts\python.exe"',
        r'if exist ".venv\.beam-ready" goto py_ready@N@',
        'if not exist "%VPY%" (',
        "    echo Creating a virtual environment ...",
        "    %PY% -m venv .venv",
        "    if errorlevel 1 goto failed",
        ")",
    ]
    sh = [
        'VPY=".venv/$VBIN/python"',
        "if [ ! -f .venv/.beam-ready ]; then",
        '    [ -x "$VPY" ] || [ -x "$VPY.exe" ] || make_venv',
        '    installed=""',
    ]
    if installer:
        command = {
            "uv": "uv sync",
            "poetry": "poetry install --no-root",
            "pipenv": "pipenv sync" if tree.is_file(folder, "Pipfile.lock")
            else "pipenv install",
        }[installer]
        env_bat = {
            "poetry": 'set "POETRY_VIRTUALENVS_IN_PROJECT=true"',
            "pipenv": 'set "PIPENV_VENV_IN_PROJECT=1"',
        }.get(installer)
        env_sh = {
            "poetry": "POETRY_VIRTUALENVS_IN_PROJECT=true ",
            "pipenv": "PIPENV_VENV_IN_PROJECT=1 ",
        }.get(installer, "")
        bat += [
            f"where {installer} >nul 2>nul || goto py_pip@N@",
            f"echo Installing requirements ({installer}) ...",
            *([env_bat] if env_bat else []),
            f"call {command}",
            "if not errorlevel 1 goto py_done@N@",
            f"echo {installer} could not install them - trying pip instead.",
        ]
        sh += [
            f"    if command -v {installer} >/dev/null 2>&1; then",
            f'        echo "Installing requirements ({installer}) ..."',
            f"        {env_sh}{command} && installed=1 ||",
            f'            echo "{installer} could not install them - trying pip '
            'instead."',
            "    fi",
        ]
    if installer:
        bat.append(":py_pip@N@")
    if has_reqs:
        bat += [
            "echo Installing requirements ...",
            '"%VPY%" -m pip install --disable-pip-version-check -r requirements.txt',
            "if errorlevel 1 goto failed",
        ]
        sh += [
            '    if [ -z "$installed" ]; then',
            '        echo "Installing requirements ..."',
            '        "$VPY" -m pip install --disable-pip-version-check -r '
            "requirements.txt || failed",
            "    fi",
        ]
    if installer:
        bat.append(":py_done@N@")
    for module, package in ensure:
        bat += [
            f'"%VPY%" -c "import {module}" >nul 2>nul || "%VPY%" -m pip install '
            f"--disable-pip-version-check {package}",
            "if errorlevel 1 goto failed",
        ]
        sh.append(
            f'    "$VPY" -c "import {module}" >/dev/null 2>&1 || "$VPY" -m pip '
            f"install --disable-pip-version-check {package} || failed"
        )
    bat += [r'echo ok> ".venv\.beam-ready"', ":py_ready@N@"]
    sh += ["    echo ok > .venv/.beam-ready", "fi"]

    run_bat = run_sh = None
    note = ""
    python_bat, python_sh = r".venv\Scripts\python.exe", '".venv/$VBIN/python"'
    if kind is None:
        note = ("Python is set up in .venv, but no main script was found, so "
                "nothing was started.")
    elif kind == "notebook":
        run_bat, run_sh = f"{python_bat} -m notebook", f"{python_sh} -m notebook"
    else:
        e_bat, e_sh = f'"{_win(entry)}"', shlex.quote(entry)
        if kind == "streamlit":
            run_bat = f"{python_bat} -m streamlit run {e_bat}"
            run_sh = f"{python_sh} -m streamlit run {e_sh}"
        elif kind == "django":
            bat += [
                "echo Preparing the database ...",
                f'"%VPY%" {e_bat} migrate --noinput',
                "if errorlevel 1 echo The database step failed - starting anyway.",
            ]
            sh += [
                'echo "Preparing the database ..."',
                f'"$VPY" {e_sh} migrate --noinput ||',
                '    echo "The database step failed - starting anyway."',
            ]
            run_bat = f"{python_bat} {e_bat} runserver"
            run_sh = f"{python_sh} {e_sh} runserver"
        elif kind == "flask":
            run_bat = f"{python_bat} -m flask --app {e_bat} run"
            run_sh = f"{python_sh} -m flask --app {e_sh} run"
        elif kind == "fastapi":
            module = entry[:-3].replace("/", ".")
            m = re.search(r"(?m)^(\w+)\s*(?::[^=]+)?=\s*FastAPI\(", source)
            target = f"{module}:{m.group(1) if m else 'app'}"
            run_bat = f"{python_bat} -m uvicorn {target}"
            run_sh = f"{python_sh} -m uvicorn {target}"
            what = f"uvicorn {target}"
        else:
            run_bat, run_sh = f"{python_bat} {e_bat}", f"{python_sh} {e_sh}"

    return App(
        folder, "Python", what=what,
        tools=[python_tool(_python_minor(tree, folder))],
        setup_bat=bat, setup_sh=sh, run_bat=run_bat, run_sh=run_sh, note=note,
        frontend=kind in ("streamlit", "notebook"),
        files={"requirements.txt": "\n".join(generated) + "\n"} if generated else {},
        requires=generated or [], absorbs=frozenset({"python"}), family="python",
    )


# --- JavaScript: Node, Bun, Deno ---------------------------------------------

FRONTEND_PACKAGES = frozenset({
    "react", "react-dom", "vue", "svelte", "@angular/core", "next", "nuxt",
    "vite", "solid-js", "preact", "astro", "@sveltejs/kit", "gatsby", "expo",
    "react-native", "@remix-run/react", "electron", "lit", "@builder.io/qwik",
})
LOCKFILES = (
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("bun.lock", "bun"),
    ("bun.lockb", "bun"),
    ("package-lock.json", "npm"),
)


def _package_manager(tree: Tree, folder: str, pkg: dict) -> str:
    declared = str(pkg.get("packageManager") or "").split("@")[0]
    if declared in ("npm", "pnpm", "yarn", "bun"):
        return declared
    for lockfile, manager in LOCKFILES:
        if tree.is_file(folder, lockfile):
            return manager
    return "npm"


def _node(tree: Tree, folder: str, secondary: bool = False) -> App:
    """A package.json. ``secondary``: it sits beside a back end (Laravel's,
    Django's front-end files): install and build it, the back end starts."""
    pkg = _json(tree.read(folder, "package.json"))
    scripts = _dict(pkg.get("scripts"))
    pm = _package_manager(tree, folder, pkg)
    deps = {**_dict(pkg.get("dependencies")), **_dict(pkg.get("devDependencies"))}
    tauri = "tauri" in scripts and tree.is_file(folder, "src-tauri/Cargo.toml")

    if pm == "bun":
        tools = [BUN]
    else:
        tools = [NODE, *{"pnpm": [PNPM], "yarn": [YARN]}.get(pm, [])]
    if tauri:
        tools += [RUST, MSVC_FOR_RUST]
    bat = [
        r'if exist "node_modules\" goto node_ready@N@',
        f"echo Installing packages ({pm}) ...",
        f"call {pm} install",
        "if errorlevel 1 goto failed",
        ":node_ready@N@",
    ]
    sh = [
        "if [ ! -d node_modules ]; then",
        f'    echo "Installing packages ({pm}) ..."',
        f"    {pm} install || failed",
        "fi",
    ]
    if secondary:
        if "build" in scripts:
            bat += [
                r'if exist "node_modules\.beam-built" goto node_built@N@',
                "echo Building the front-end files ...",
                f"call {pm} run build",
                "if errorlevel 1 goto failed",
                r'if not exist "node_modules\" mkdir node_modules',
                r'echo ok> "node_modules\.beam-built"',
                ":node_built@N@",
            ]
            sh += [
                "if [ ! -f node_modules/.beam-built ]; then",
                '    echo "Building the front-end files ..."',
                f"    {pm} run build || failed",
                "    mkdir -p node_modules && echo ok > node_modules/.beam-built",
                "fi",
            ]
        return App(folder, "Node", tools=tools, setup_bat=bat, setup_sh=sh,
                   family="node")

    run_bat = run_sh = None
    if tauri:
        run_bat = run_sh = f"{pm} run tauri dev"
    elif "dev" in scripts:
        run_bat = run_sh = f"{pm} run dev"
    elif "start" in scripts:
        run_bat = run_sh = f"{pm} run start" if pm == "bun" else f"{pm} start"
    else:
        main = pkg.get("main") if isinstance(pkg.get("main"), str) else None
        runner = "bun" if pm == "bun" else "node"
        for script in ([main] if main else []) + ["index.js", "server.js", "app.js",
                                                  "main.js"]:
            if tree.is_file(folder, script):
                run_bat = f'{runner} "{_win(script)}"'
                run_sh = f"{runner} {shlex.quote(script)}"
                break
    workspaces = bool(pkg.get("workspaces")) or tree.is_file(
        folder, "pnpm-workspace.yaml"
    )
    absorbs = {"node"} if workspaces else set()
    if tauri:
        absorbs.add("rust")
    what = run_sh.replace("'", "") if run_sh else ""
    return App(
        folder, "Node", what=what, tools=tools, setup_bat=bat, setup_sh=sh,
        run_bat=run_bat, run_sh=run_sh,
        note="" if run_bat else "Packages installed. There is no start or dev "
        "script, so nothing was started.",
        frontend=tauri or bool(FRONTEND_PACKAGES & set(deps)),
        absorbs=frozenset(absorbs), family="node",
    )


def _deno(tree: Tree, folder: str) -> App:
    config = _json(_strip_jsonc(
        tree.read(folder, "deno.json") or tree.read(folder, "deno.jsonc")
    ))
    tasks = _dict(config.get("tasks"))
    run = None
    for task in ("dev", "start"):
        if task in tasks:
            run = f"deno task {task}"
            break
    else:
        for script in ("main.ts", "main.tsx", "main.js", "mod.ts", "server.ts",
                       "index.ts", "app.ts"):
            if tree.is_file(folder, script):
                run = f"deno run -A {script}"
                break
    return App(
        folder, "Deno", what=run or "", tools=[DENO], run_bat=run, run_sh=run,
        note="" if run else "There is no dev or start task, so nothing was started.",
        absorbs=frozenset({"deno"}), family="deno",
    )


# --- compiled languages ------------------------------------------------------


def _build_step(label: str, command: str) -> tuple:
    """The same build command for start.bat and start.sh."""
    return (
        [f"echo Building ({label}) ...", f"call {command}",
         "if errorlevel 1 goto failed"],
        [f'echo "Building ({label}) ..."', f"{command} || failed"],
    )


def _rust(tree: Tree, folder: str, own) -> App:
    cargo = _toml(tree.read(folder, "Cargo.toml"))
    package = _dict(cargo.get("package"))
    run, note = None, ""
    if package:
        name = str(package.get("name", ""))
        bins = [name] if tree.is_file(folder, "src/main.rs") else []
        bins += [str(b["name"]) for b in cargo.get("bin") or []
                 if isinstance(b, dict) and b.get("name")]
        bins += [n[:-3] for n in tree.ending(_join(folder, "src/bin"), ".rs")]
        bins = list(dict.fromkeys(bins))
        if package.get("default-run") or len(bins) == 1:
            run = "cargo run"
        elif tree.is_file(folder, "src/main.rs") and name:
            run = f"cargo run --bin {name}"
        elif bins:
            note = f"Built. Start one with: cargo run --bin NAME ({', '.join(bins)})"
        else:
            note = "Built. This is a library, so there is nothing to start."
    else:  # a workspace with no package of its own
        members = []
        for key in own:
            sub = key[: -len("/Cargo.toml")]
            if key.endswith("/Cargo.toml") and tree.is_file(sub, "src/main.rs"):
                member = _dict(_toml(tree.read(key)).get("package")).get("name")
                if member:
                    members.append(str(member))
        if len(members) == 1:
            run = f"cargo run -p {members[0]}"
        elif members:
            note = f"Built. Start one with: cargo run -p NAME ({', '.join(members)})"
        else:
            note = "Built. Nothing in this workspace is a program to start."
    bat, sh = _build_step("cargo", "cargo build")
    return App(
        folder, "Rust", what=run or "", tools=[RUST, MSVC_FOR_RUST],
        setup_bat=bat, setup_sh=sh, run_bat=run, run_sh=run, note=note,
        absorbs=frozenset({"rust"}), family="rust",
    )


def _go(tree: Tree, folder: str, own) -> App:
    main_package = re.compile(r"(?m)^package\s+main\b")
    run, note = None, ""
    top = tree.ending(folder, ".go")
    if any(main_package.search(tree.read(folder, n)) for n in top):
        run = "go run ."
    else:
        mains = sorted({
            r.rsplit("/", 1)[0] for r in _relative(own, folder)
            if r.endswith("/main.go") and r.count("/") <= 2
            and main_package.search(tree.read(folder, r))
        })
        if len(mains) == 1:
            run = f"go run ./{mains[0]}"
        elif mains:
            note = f"Ready. Start one with: go run ./PATH ({', '.join(mains)})"
        else:
            note = "Ready. There is no main package, so nothing was started."
    return App(
        folder, "Go", what=run or "", tools=[GO],
        setup_bat=["echo Downloading Go modules ...", "call go mod download",
                   "if errorlevel 1 goto failed"],
        setup_sh=['echo "Downloading Go modules ..."', "go mod download || failed"],
        run_bat=run, run_sh=run, note=note, absorbs=frozenset({"go"}), family="go",
    )


_JAVA_VERSION_PATTERNS = (
    r"<java\.version>\s*(?:1\.)?(\d+)",
    r"<maven\.compiler\.(?:release|source|target)>\s*(?:1\.)?(\d+)",
    r"<release>\s*(\d+)\s*</release>",
    r"JavaLanguageVersion\.of\(\s*(\d+)",
    r"jvmToolchain\(\s*(\d+)",
    r"JavaVersion\.VERSION_(?:1_)?(\d+)",
    r"(?:source|target)Compatibility\s*=\s*['\"]?(?:1\.)?(\d+)",
    r"JvmTarget\.JVM_(?:1_)?(\d+)",
)


def _java_version(text: str, default: int = 21) -> int:
    """The JDK a build file asks for, as one of the LTS versions on winget."""
    found = [int(v) for p in _JAVA_VERSION_PATTERNS for v in re.findall(p, text)]
    want = max(found) if found else default
    return next((v for v in JAVA_VERSIONS if v >= want), JAVA_VERSIONS[-1])


def _java_home_bat() -> list:
    """Some wrappers want JAVA_HOME; point it at the JDK that is on PATH."""
    return [
        "if defined JAVA_HOME goto java_home@N@",
        "for /f \"delims=\" %%J in ('where javac 2^>nul') do "
        'if not defined JAVA_HOME set "JAVA_HOME=%%~dpJ.."',
        ":java_home@N@",
    ]


def _maven(tree: Tree, folder: str) -> App:
    pom = tree.read(folder, "pom.xml")
    mvn_bat = "mvnw.cmd" if tree.is_file(folder, "mvnw.cmd") else "mvn"
    mvn_sh = "sh ./mvnw" if tree.is_file(folder, "mvnw") else "mvn"
    tools = [java_tool(_java_version(pom))]
    if mvn_bat == "mvn" or mvn_sh == "mvn":
        only = "" if mvn_bat == mvn_sh else ("win" if mvn_bat == "mvn" else "posix")
        tools.append(replace(MAVEN, only=only))

    goal = None
    if "spring-boot-maven-plugin" in pom or "spring-boot-starter" in pom:
        goal = "spring-boot:run"
    elif "quarkus-maven-plugin" in pom:
        goal = "quarkus:dev"
    elif "javafx-maven-plugin" in pom:
        goal = "javafx:run"
    elif "exec-maven-plugin" in pom and "<mainClass>" in pom:
        goal = "compile exec:java"

    bat, sh = _java_home_bat(), []
    if goal:
        return App(
            folder, "Java (Maven)", what=f"{mvn_sh.replace('sh ./', '')} {goal}",
            tools=tools, setup_bat=bat, run_bat=f"{mvn_bat} {goal}",
            run_sh=f"{mvn_sh} {goal}", frontend=goal == "javafx:run",
            absorbs=frozenset({"maven"}), family="maven",
        )
    build_bat, build_sh = _build_step("Maven", f"{mvn_bat} -DskipTests package")
    build_sh[1] = f"{mvn_sh} -DskipTests package || failed"
    return App(
        folder, "Java (Maven)", tools=tools, setup_bat=bat + build_bat,
        setup_sh=sh + build_sh,
        note="Built with Maven: the result is in the target folder.",
        absorbs=frozenset({"maven"}), family="maven",
    )


GRADLE_FILES = ("build.gradle", "build.gradle.kts", "settings.gradle",
                "settings.gradle.kts")


def _gradle(tree: Tree, folder: str) -> App:
    subfolders = [folder] + [_join(folder, d) for d in sorted(tree.names(folder))]
    text = "\n".join(
        tree.read(sub, name) for sub in subfolders for name in GRADLE_FILES
        if tree.is_file(sub, name)
    )
    if re.search(r"com\.android\.application|android[.-]application", text):
        return App(
            folder, "Android",
            note="This is an Android app: open the folder in Android Studio to run it.",
            absorbs=frozenset({"gradle"}), family="gradle",
        )
    gradle_bat = "gradlew.bat" if tree.is_file(folder, "gradlew.bat") else "gradle"
    gradle_sh = "sh ./gradlew" if tree.is_file(folder, "gradlew") else "gradle"
    tools = [java_tool(_java_version(text))]
    if gradle_bat == "gradle" or gradle_sh == "gradle":
        only = "" if gradle_bat == gradle_sh else (
            "win" if gradle_bat == "gradle" else "posix")
        tools.append(replace(GRADLE, only=only))

    task = None
    if re.search(r"org\.springframework\.boot|spring[.-]boot", text):
        task = "bootRun"
    elif re.search(r"io\.quarkus|\bquarkus\b", text):
        task = "quarkusDev"
    elif re.search(
        r"""(?m)id\s*\(?\s*['"]application['"]|^\s*application\s*(\{|$)"""
        r"""|apply\s+plugin:\s*['"]application['"]|compose\.desktop""",
        text,
    ):
        task = "run"

    bat = _java_home_bat()
    if task:
        return App(
            folder, "Java (Gradle)", what=f"{gradle_sh.replace('sh ./', '')} {task}",
            tools=tools, setup_bat=bat, run_bat=f"{gradle_bat} {task}",
            run_sh=f"{gradle_sh} {task}", absorbs=frozenset({"gradle"}),
            family="gradle",
        )
    build_bat, build_sh = _build_step("Gradle", f"{gradle_bat} build -x test")
    build_sh[1] = f"{gradle_sh} build -x test || failed"
    return App(
        folder, "Java (Gradle)", tools=tools, setup_bat=bat + build_bat,
        setup_sh=build_sh,
        note="Built with Gradle: the result is in the build folder.",
        absorbs=frozenset({"gradle"}), family="gradle",
    )


_JAVA_MAIN = re.compile(r"static\s+void\s+main\s*\(")


def _java_mains(tree: Tree, folder: str) -> list:
    """.java files with a main(), at the top of the folder or under src/."""
    found = []
    for sub in (folder, _join(folder, "src")):
        for name in tree.ending(sub, ".java"):
            if _JAVA_MAIN.search(tree.read(sub, name)):
                found.append(_relative([_join(sub, name)], folder)[0])
    return found


def _plain_java(tree: Tree, folder: str) -> App:
    """.java files and no build tool: Java 22+ runs them straight from source."""
    mains = _java_mains(tree, folder)
    chosen = next((m for m in mains if m.rsplit("/", 1)[-1] == "Main.java"), None)
    if chosen is None and len(mains) == 1:
        chosen = mains[0]
    run_bat = f'java "{_win(chosen)}"' if chosen else None
    run_sh = f"java {shlex.quote(chosen)}" if chosen else None
    return App(
        folder, "Java", what=f"java {chosen}" if chosen else "",
        tools=[java_tool(25)], run_bat=run_bat, run_sh=run_sh,
        note="" if chosen else "Several classes have a main(); start the one you "
        "want with: java File.java",
        absorbs=frozenset({"java"}), family="java",
    )


PROJECT_SUFFIXES = (".csproj", ".fsproj", ".vbproj")


def _dotnet(tree: Tree, folder: str, own) -> App:
    projects = []
    for key in own:
        if not key.endswith(PROJECT_SUFFIXES):
            continue
        text = tree.read(key)
        sdk = re.search(r'<Project[^>]*\bSdk\s*=\s*"([^"]+)"', text)
        tfms = re.search(r"<TargetFrameworks?>\s*([^<]+)<", text)
        output = re.search(r"<OutputType>\s*(\w+)", text)
        projects.append({
            "path": _relative([key], folder)[0],
            "sdk": sdk.group(1) if sdk else "",
            "tfms": [t.strip() for t in tfms.group(1).split(";") if t.strip()]
            if tfms else [],
            "exe": bool(output and output.group(1).lower() in ("exe", "winexe")),
            "test": "Microsoft.NET.Test.Sdk" in text
            or bool(re.search(r"<IsTestProject>\s*true", text, re.I)),
            "maui": bool(re.search(r"<UseMaui>\s*true", text, re.I)),
            "desktop": bool(re.search(r"<Use(?:WPF|WindowsForms)>\s*true", text, re.I)),
        })
    modern = [p for p in projects if p["sdk"]]
    if projects and not modern:
        return App(
            folder, ".NET Framework",
            note="This is a .NET Framework project: open the .sln in Visual Studio "
            "to run it.", absorbs=frozenset({"dotnet"}), family="dotnet",
        )
    for p in modern:
        p["web"] = any(w in p["sdk"] for w in ("Web", "Blazor", "Worker"))
        p["desktop"] = p["desktop"] or any("-windows" in t for t in p["tfms"])
    runnable = [p for p in modern if (p["exe"] or p["web"]) and not p["test"]
                and not p["maui"]]
    runnable.sort(key=lambda p: (not p["web"], p["path"]))

    if not runnable:
        if any(p["maui"] for p in modern):
            return App(
                folder, ".NET MAUI",
                note="This is a .NET MAUI app: open it in Visual Studio to run it.",
                absorbs=frozenset({"dotnet"}), family="dotnet",
            )
        bat, sh = _build_step("dotnet", "dotnet build")
        tfm = next((t for p in modern for t in p["tfms"]), "net8.0")
        return App(
            folder, ".NET",
            tools=[dotnet_tool(_dotnet_major(tfm), "Microsoft.NETCore.App")],
            setup_bat=bat, setup_sh=sh,
            note="Built. Nothing in it is a program to start.",
            absorbs=frozenset({"dotnet"}), family="dotnet",
        )

    chosen = runnable[0]
    tfm = chosen["tfms"][0] if chosen["tfms"] else "net8.0"
    runtime = ("Microsoft.AspNetCore.App" if chosen["web"] else
               "Microsoft.WindowsDesktop.App" if chosen["desktop"] else
               "Microsoft.NETCore.App")
    pick = f" -f {tfm}" if len(chosen["tfms"]) > 1 else ""
    run_bat = f'dotnet run --project "{_win(chosen["path"])}"{pick}'
    run_sh = None if chosen["desktop"] else (
        f"dotnet run --project {shlex.quote(chosen['path'])}{pick}")
    return App(
        folder, ".NET", what=f"dotnet run ({chosen['path']})",
        tools=[dotnet_tool(_dotnet_major(tfm), runtime)],
        run_bat=run_bat, run_sh=run_sh,
        note_sh="This is a Windows desktop app, so it only runs on Windows."
        if chosen["desktop"] else None,
        frontend=chosen["desktop"] or "Blazor" in chosen["sdk"],
        absorbs=frozenset({"dotnet"}), family="dotnet",
    )


def _dotnet_major(tfm: str) -> int:
    m = re.match(r"net(\d+)\.", tfm)
    major = int(m.group(1)) if m else 8
    return major if major in DOTNET_VERSIONS else 8


def _haskell(tree: Tree, folder: str) -> App:
    stack = tree.is_file(folder, "stack.yaml")
    command = "stack" if stack else "cabal"
    text = "\n".join(tree.read(folder, n) for n in tree.ending(folder, ".cabal"))
    text += tree.read(folder, "package.yaml")
    program = bool(re.search(r"(?m)^executables?\b|^executable\s", text))
    bat, sh = _build_step(command, f"{command} build")
    run = f"{command} run" if program else None
    return App(
        folder, "Haskell", what=run or "", tools=[STACK if stack else CABAL],
        setup_bat=bat, setup_sh=sh, run_bat=run, run_sh=run,
        note="" if run else "Built. This is a library, so there is nothing to start.",
        absorbs=frozenset({"haskell"}), family="haskell",
    )


def _swift(tree: Tree, folder: str) -> App:
    text = tree.read(folder, "Package.swift")
    program = ".executableTarget(" in text or ".executable(" in text
    bat, sh = _build_step("swift", "swift build")
    run = "swift run" if program else None
    return App(
        folder, "Swift", what=run or "", tools=[SWIFT, MSVC], setup_bat=bat,
        setup_sh=sh, run_bat=run, run_sh=run,
        note="" if run else "Built. This is a library, so there is nothing to start.",
        absorbs=frozenset({"swift"}), family="swift",
    )


def _cmake(tree: Tree, folder: str) -> App:
    return App(
        folder, "C/C++ (CMake)", tools=[CMAKE, MSVC, CXX],
        setup_bat=["echo Building (CMake) ...", "cmake -S . -B build",
                   "if errorlevel 1 goto failed", "cmake --build build",
                   "if errorlevel 1 goto failed"],
        setup_sh=['echo "Building (CMake) ..."', "cmake -S . -B build || failed",
                  "cmake --build build || failed"],
        note="Built into the build folder: run the program from there.",
        absorbs=frozenset({"cmake"}), family="cmake",
    )


# --- PHP, Ruby, Elixir, Dart -------------------------------------------------

PHP_EXTENSIONS = "curl fileinfo mbstring openssl pdo_mysql pdo_sqlite sqlite3 zip"


def _php(tree: Tree, folder: str) -> App:
    composer = _json(tree.read(folder, "composer.json"))
    packages = {**_dict(composer.get("require")), **_dict(composer.get("require-dev"))}
    needs_composer = any(k != "php" and not k.startswith("ext-") for k in packages)
    laravel = tree.is_file(folder, "artisan")
    public = tree.is_file(folder, "public/index.php")

    # PHP from winget has no php.ini, so openssl and friends are off, and
    # Composer and Laravel need them. Switch them on for this window only.
    bat = [
        "php -r \"exit(extension_loaded('openssl') ? 0 : 1);\" >nul 2>nul "
        "&& goto php_ini@N@",
        "for /f \"delims=\" %%D in ('php -r \"echo dirname(realpath(PHP_BINARY));\"')"
        ' do set "PHPDIR=%%D"',
        r'if not exist "%TEMP%\beam-php" mkdir "%TEMP%\beam-php"',
        r'echo extension_dir="%PHPDIR%\ext"> "%TEMP%\beam-php\beam.ini"',
        f"for %%E in ({PHP_EXTENSIONS}) do "
        r'echo extension=%%E>> "%TEMP%\beam-php\beam.ini"',
        r'set "PHP_INI_SCAN_DIR=%TEMP%\beam-php"',
        ":php_ini@N@",
    ]
    sh = []
    tools = [PHP]
    if needs_composer:
        tools.append(COMPOSER)
        bat += [
            r'if exist "vendor\autoload.php" goto php_vendor@N@',
            "echo Installing packages (composer) ...",
            "call composer install",
            "if errorlevel 1 goto failed",
            ":php_vendor@N@",
        ]
        sh += [
            "if [ ! -f vendor/autoload.php ]; then",
            '    echo "Installing packages (composer) ..."',
            "    composer install || failed",
            "fi",
        ]
    if laravel:
        bat += [
            'if exist ".env" goto php_env@N@',
            'if not exist ".env.example" goto php_env@N@',
            'copy ".env.example" ".env" >nul',
            "php artisan key:generate",
            ":php_env@N@",
            "echo Preparing the database ...",
            "php artisan migrate --force --no-interaction",
            "if errorlevel 1 echo The database step failed - starting anyway.",
        ]
        sh += [
            "if [ ! -f .env ] && [ -f .env.example ]; then",
            "    cp .env.example .env && php artisan key:generate",
            "fi",
            'echo "Preparing the database ..."',
            "php artisan migrate --force --no-interaction ||",
            '    echo "The database step failed - starting anyway."',
        ]
        run = "php artisan serve"
    elif public:
        run = "php -S 127.0.0.1:8000 -t public"
    elif tree.is_file(folder, "index.php"):
        run = "php -S 127.0.0.1:8000"
    else:
        run = None
    return App(
        folder, "PHP (Laravel)" if laravel else "PHP", what=run or "", tools=tools,
        setup_bat=bat, setup_sh=sh, run_bat=run, run_sh=run,
        note="" if run else "Packages installed. There is no index.php, so nothing "
        "was started.", absorbs=frozenset({"php"}), family="php",
    )


def _ruby(tree: Tree, folder: str) -> App:
    rails = tree.is_file(folder, "config/application.rb") and tree.is_file(
        folder, "bin/rails")
    bat = [
        "call bundle check >nul 2>nul && goto rb_gems@N@",
        "echo Installing gems (bundler) ...",
        "call bundle install",
        "if not errorlevel 1 goto rb_gems@N@",
        "where ridk >nul 2>nul || goto failed",
        "echo Installing Ruby's build tools (ridk install), then trying again ...",
        "call ridk install 3",
        "call bundle install",
        "if errorlevel 1 goto failed",
        ":rb_gems@N@",
    ]
    sh = [
        "if ! bundle check >/dev/null 2>&1; then",
        '    echo "Installing gems (bundler) ..."',
        "    bundle install || failed",
        "fi",
    ]
    run_bat = run_sh = None
    if rails:
        bat += ["echo Preparing the database ...", r"ruby bin\rails db:prepare",
                "if errorlevel 1 echo The database step failed - starting anyway."]
        sh += ['echo "Preparing the database ..."', "ruby bin/rails db:prepare ||",
               '    echo "The database step failed - starting anyway."']
        run_bat, run_sh = r"ruby bin\rails server", "ruby bin/rails server"
    elif tree.is_file(folder, "config.ru"):
        run_bat = run_sh = "bundle exec rackup"
    else:
        script = next((s for s in ("app.rb", "main.rb", "server.rb")
                       if tree.is_file(folder, s)), None)
        if script:
            run_bat = run_sh = f"bundle exec ruby {script}"
    return App(
        folder, "Ruby (Rails)" if rails else "Ruby",
        what=(run_sh or "").replace("bundle exec ", ""),
        tools=[RUBY], setup_bat=bat, setup_sh=sh, run_bat=run_bat, run_sh=run_sh,
        note="" if run_bat else "Gems installed. Nothing was found to start.",
        absorbs=frozenset({"ruby"}), family="ruby",
    )


def _elixir(tree: Tree, folder: str) -> App:
    mix = tree.read(folder, "mix.exs")
    bat = [
        "call mix local.hex --force --if-missing >nul",
        "call mix local.rebar --force --if-missing >nul",
        r'if exist "deps\" goto ex_deps@N@',
        "echo Downloading dependencies (mix) ...",
        "call mix deps.get",
        "if errorlevel 1 goto failed",
        ":ex_deps@N@",
    ]
    sh = [
        "mix local.hex --force --if-missing >/dev/null",
        "mix local.rebar --force --if-missing >/dev/null",
        "if [ ! -d deps ]; then",
        '    echo "Downloading dependencies (mix) ..."',
        "    mix deps.get || failed",
        "fi",
    ]
    run = None
    if re.search(r"\{\s*:phoenix\s*,", mix):
        run = "mix phx.server"
    elif re.search(r"\bmod:\s*\{", mix):
        run = "mix run --no-halt"
    else:
        bat += ["call mix compile", "if errorlevel 1 goto failed"]
        sh += ["mix compile || failed"]
    return App(
        folder, "Elixir", what=run or "", tools=[ERLANG, ELIXIR], setup_bat=bat,
        setup_sh=sh, run_bat=run, run_sh=run,
        note="" if run else "Built. There is no application to start.",
        absorbs=frozenset({"elixir"}), family="elixir",
    )


def _dart(tree: Tree, folder: str) -> App:
    flutter = bool(re.search(r"sdk:\s*flutter", tree.read(folder, "pubspec.yaml")))
    command = "flutter" if flutter else "dart"
    if flutter:
        run = "flutter run"
    else:
        run = "dart run" if tree.ending(_join(folder, "bin"), ".dart") else None
    return App(
        folder, "Flutter" if flutter else "Dart", what=run or "",
        tools=[FLUTTER if flutter else DART],
        setup_bat=[f"echo Getting packages ({command} pub get) ...",
                   f"call {command} pub get", "if errorlevel 1 goto failed"],
        setup_sh=[f'echo "Getting packages ({command} pub get) ..."',
                  f"{command} pub get || failed"],
        run_bat=run, run_sh=run, frontend=flutter,
        note="" if run else "Packages ready. There is no bin/ program to start.",
        absorbs=frozenset({"dart"}), family="dart",
    )


# --- things that open in an editor, and plain websites -----------------------


def _engine(tree: Tree, folder: str, family: str) -> App:
    if family == "unity":
        m = re.search(r"m_EditorVersion:\s*(\S+)", tree.read(folder, UNITY))
        version = f" (Unity {m.group(1)})" if m else ""
        note = f"This is a Unity project: open this folder in Unity Hub{version}."
        stack = "Unity"
    elif family == "unreal":
        project = tree.ending(folder, ".uproject")[0]
        engine = _json(tree.read(folder, project)).get("EngineAssociation")
        version = f" {engine}" if isinstance(engine, str) and engine else ""
        note = f"This is an Unreal project: open {project} with Unreal Engine{version}."
        stack = "Unreal"
    else:
        note = "This is a Godot project: open project.godot in Godot."
        stack = "Godot"
    return App(folder, stack, note=note, absorbs=frozenset({ANY}), family=family)


def _static(tree: Tree, folder: str, entry: str = "index.html") -> App:
    q = shlex.quote(entry)
    return App(
        folder, "Website", what=f"opens {entry}",
        run_bat=f'start "" "{_win(entry)}"',
        run_sh=f"if command -v xdg-open >/dev/null 2>&1; then xdg-open {q}; "
        f"else open {q}; fi",
        waits=False, frontend=True, family="static",
    )


# --- which languages are in a folder -----------------------------------------


def families(tree: Tree, folder: str) -> list:
    """The kinds of project in ``folder``, the one to start first."""
    files = set(tree.files(folder))
    names = tree.names(folder)
    if "Assets" in names and tree.is_file(folder, UNITY):
        return ["unity"]
    if any(n.endswith(".uproject") for n in files):
        return ["unreal"]
    if "project.godot" in files:
        return ["godot"]
    found = []
    if files & PY_MARKERS:
        found.append("python")
    if "Gemfile" in files:
        found.append("ruby")
    if files & {"composer.json", "artisan", "index.php"} or tree.is_file(
        folder, "public/index.php"
    ):
        found.append("php")
    if "mix.exs" in files:
        found.append("elixir")
    if "pom.xml" in files:
        found.append("maven")
    elif files & set(GRADLE_FILES):
        found.append("gradle")
    if any(n.endswith(PROJECT_SUFFIXES + (".sln", ".slnx")) for n in files):
        found.append("dotnet")
    if "go.mod" in files:
        found.append("go")
    if "Cargo.toml" in files:
        found.append("rust")
    if files & {"stack.yaml", "cabal.project"} or any(
        n.endswith(".cabal") for n in files
    ):
        found.append("haskell")
    if "Package.swift" in files:
        found.append("swift")
    if "pubspec.yaml" in files:
        found.append("dart")
    if files & {"deno.json", "deno.jsonc"}:
        found.append("deno")
    if "package.json" in files:
        found.append("node")
    if "CMakeLists.txt" in files:
        found.append("cmake")
    if not found and (tree.ending(folder, ".java") or tree.ending(
            _join(folder, "src"), ".java")) and _java_mains(tree, folder):
        found.append("java")
    if "index.html" in files and not found:
        found.append("static")
    return found


def build(family: str, tree: Tree, folder: str, own, requirements=None) -> App:
    """The App for one kind of project in ``folder``."""
    if family in ("unity", "unreal", "godot"):
        return _engine(tree, folder, family)
    if family == "python":
        return _python(tree, folder, own, requirements=requirements)
    simple = {
        "node": _node, "deno": _deno, "maven": _maven, "gradle": _gradle,
        "java": _plain_java, "haskell": _haskell, "swift": _swift,
        "cmake": _cmake, "php": _php, "ruby": _ruby, "elixir": _elixir,
        "dart": _dart, "static": _static,
    }
    if family in simple:
        return simple[family](tree, folder)
    return {"rust": _rust, "go": _go, "dotnet": _dotnet}[family](tree, folder, own)


def app_in(tree: Tree, folder: str, own, requirements=None) -> App | None:
    """Everything in one folder as one App: the first thing that can be
    started is started, and a Python or Node part beside it is set up too
    (Django's front-end files, a Node project's Python helpers)."""
    kinds = families(tree, folder)
    if not kinds:
        return None
    apps = [build(k, tree, folder, own, requirements) for k in kinds]
    primary = next((a for a in apps if a.run_bat or a.run_sh), apps[0])
    for kind, app in zip(kinds, apps):
        if app is primary or kind not in ("python", "node"):
            continue
        extra = _node(tree, folder, secondary=True) if kind == "node" else app
        primary.tools = primary.tools + extra.tools
        primary.setup_bat = primary.setup_bat + extra.setup_bat
        primary.setup_sh = primary.setup_sh + extra.setup_sh
        primary.files = {**extra.files, **primary.files}
        primary.requires = primary.requires or extra.requires
    return primary


# --- the whole project -------------------------------------------------------


# Folders never searched for apps: their package.json or main.py is an
# example or a test fixture, not something to start.
NOT_APPS = frozenset({
    "doc", "docs", "example", "examples", "sample", "samples", "test", "tests",
    "__tests__", "fixtures", "e2e", "benchmark", "benchmarks",
})
MAX_DEPTH = 2  # backend/, apps/web/ - deep enough for monorepos, no deeper


def _search_folders(tree: Tree) -> list:
    out = []
    for folder in tree.children:
        parts = folder.split("/") if folder else []
        if len(parts) > MAX_DEPTH or any(
            p.startswith(".") or p.lower() in NOT_APPS for p in parts
        ):
            continue
        out.append(folder)
    return sorted(out, key=lambda f: (f.count("/") + bool(f), f))


def _owned(tree: Tree, folder: str, kinds) -> frozenset:
    """Kinds of project below ``folder`` that are part of it, not apps of
    their own: a Cargo workspace's members, Maven modules, everything in a
    Unity project. Node packages only belong to a workspace root."""
    if kinds[0] in ("unity", "unreal", "godot"):
        return frozenset({ANY})
    owned = set(kinds)
    if "node" in kinds:
        pkg = _json(tree.read(folder, "package.json"))
        if not (pkg.get("workspaces") or tree.is_file(folder, "pnpm-workspace.yaml")):
            owned.discard("node")
        if tree.is_file(folder, "src-tauri/Cargo.toml"):
            owned.add("rust")  # Tauri: the Rust half is started by `tauri dev`
    return frozenset(owned)


def _is_ancestor(parent: str, child: str) -> bool:
    return parent != child and (parent == "" or child.startswith(parent + "/"))


def find_apps(files, main: str | None = None, requirements=None) -> list:
    """What to set up and start, back ends first. ``files`` is walk() output.

    Apps are looked for in the project folder and two levels below it, so a
    backend/ and a frontend/ are both found and both started.
    """
    tree = Tree(files)
    if main is not None:
        return [_main_app(tree, main, requirements)]

    roots = []  # (folder, kinds, owned)
    for folder in _search_folders(tree):
        kinds = families(tree, folder)
        if not kinds:
            continue
        above = [r for r in roots if _is_ancestor(r[0], folder)]
        if any(ANY in owned or kinds[0] in owned for _, _, owned in above):
            continue
        if kinds == ["static"] and above:
            continue  # a template's index.html, not a website of its own
        if folder == "src" and kinds[0] == "python" and not roots:
            folder = ""  # src/main.py: the project itself, as it always was
        roots.append((folder, kinds, _owned(tree, folder, kinds)))

    if not roots and any(k.endswith((".py", ".ipynb")) for k in tree.paths):
        roots.append(("", ["python"], frozenset({"python"})))

    apps = []
    for folder, kinds, _ in roots:
        inner = [f for f, _, _ in roots if _is_ancestor(folder, f)]
        own = [k for k in tree.under(folder)
               if not any(k.startswith(f + "/") for f in inner)]
        wants_reqs = "python" in kinds and not any(a.family == "python" for a in apps)
        app = app_in(tree, folder, own, requirements if wants_reqs else None)
        if app is None:  # the loose .py fallback
            app = _python(tree, folder, own, requirements=requirements)
        apps.append(app)

    _started_by_parent(tree, apps)
    return sorted(apps, key=lambda a: (a.frontend, a.folder))


def _started_by_parent(tree: Tree, apps) -> None:
    """A root package.json whose dev script starts frontend/ as well (with
    concurrently, or cd frontend && ...) - then frontend/ is only installed,
    or it would be started twice."""
    for parent in apps:
        if parent.family != "node" or not parent.run_bat:
            continue
        scripts = json.dumps(_dict(_json(tree.read(parent.folder, "package.json"))
                                   .get("scripts")))
        for child in apps:
            name = child.folder.rsplit("/", 1)[-1]
            if (child.family == "node" and _is_ancestor(parent.folder, child.folder)
                    and re.search(rf"\b{re.escape(name)}\b", scripts)):
                child.run_bat = child.run_sh = None
                child.what, child.note = "", ""


def _main_app(tree: Tree, main: str, requirements=None) -> App:
    """The one app --main points at."""
    main = PurePosixPath(str(main).replace("\\", "/")).as_posix()
    if main not in tree.paths:
        raise FileNotFoundError(f"main={main!r} is not in the project")
    suffix = PurePosixPath(main).suffix.lower()
    parents = [str(p) for p in PurePosixPath(main).parents]
    parents = ["" if p == "." else p for p in parents]

    def nearest(marker_family: str) -> str:
        for folder in parents:
            if marker_family in families(tree, folder):
                return folder
        return ""

    if suffix == ".py":
        folder = nearest("python")
        return _python(tree, folder, tree.under(folder),
                       entry=_relative([main], folder)[0], requirements=requirements)
    if suffix in (".js", ".mjs", ".cjs"):
        folder = nearest("node")
        app = _node(tree, folder) if tree.is_file(folder, "package.json") else App(
            folder, "Node", tools=[NODE], family="node")
        script = _relative([main], folder)[0]
        app.run_bat = f'node "{_win(script)}"'
        app.run_sh = f"node {shlex.quote(script)}"
        app.what, app.note = f"node {script}", ""
        return app
    return _static(tree, "", entry=main)
