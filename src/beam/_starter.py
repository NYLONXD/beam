"""Writing start.bat (Windows) and start.sh (macOS, Linux) for a project.

_detect works out what is in the project; this turns it into the two scripts.
Both do the same three things, in order:

1. check that each language the project needs is installed - start.bat
   offers to install a missing one with winget, start.sh says where to get it;
2. set everything up (packages, a venv, a first build);
3. start it - several programs (a back end and a front end) each get a window
   of their own in start.bat, and run side by side in start.sh.

Running either again skips the setup that is already done.
"""

from __future__ import annotations

import shlex

from ._detect import App, Tool

MAX_STARTED = 4  # more programs than this are set up, and the person picks one


def _label(app: App, name: str) -> str:
    return app.folder or name


def _clean_title(text: str) -> str:
    return "".join(ch for ch in text if ch not in '&|<>^%"()') or "project"


def _tools(apps, system: str) -> list:
    """Every tool the apps need on ``system`` ("win" or "posix"), once each."""
    seen, out = set(), []
    for app in apps:
        if system == "win" and not (app.setup_bat or app.run_bat):
            continue
        if system == "posix" and not (app.setup_sh or app.run_sh):
            continue
        for tool in app.tools:
            if tool.only and tool.only != system:
                continue
            arg = "" if tool.check == "python" else tool.arg
            key = (tool.check or tool.command, arg)
            if key not in seen:
                seen.add(key)
                out.append(tool)
    return out


# --- start.bat ---------------------------------------------------------------


def _bat_escape(text: str) -> str:
    text = text.replace("^", "^^").replace("%", "%%")
    for ch in "&|<>":
        text = text.replace(ch, "^" + ch)
    return text


def _bat_echo(text: str = "") -> str:
    return f"echo {_bat_escape(text)}" if text else "echo."


def _bat_dir(folder: str) -> str:
    """The app's folder from start.bat's own: %ROOT% ends with a backslash."""
    return "%ROOT%" + (folder.replace("/", "\\").replace("%", "%%") if folder else ".")


def _bat_need(tool: Tool) -> list:
    if tool.check == "msvc":
        return [f"call :need_msvc {tool.arg}".rstrip(), "if errorlevel 1 goto stop"]
    if tool.check == "python":
        call = f"call :need_python {tool.winget.replace('Python.Python.', '')}"
    elif tool.check == "dotnet":
        call = f"call :need_dotnet {tool.arg}"
    else:
        winget = tool.winget or "-"
        call = f'call :need {tool.command} "{tool.name}" {winget} {tool.url}'
    return [call, "if errorlevel 2 goto reopen", "if errorlevel 1 goto stop"]


BAT_NEED = r"""
:need
rem call :need COMMAND "NAME" WINGET-ID URL
rem   0: ready  1: not installed  2: installed, but this window cannot see it
where %1 >nul 2>nul && exit /b 0
call :offer "%~2" %3 %4
if errorlevel 1 exit /b 1
where %1 >nul 2>nul && exit /b 0
exit /b 2

:offer
rem call :offer "NAME" WINGET-ID URL  ->  0 once winget has installed it
echo.
echo %~1 is needed to run this project, and it is not installed.
if "%~2"=="-" goto offer_link
where winget >nul 2>nul || goto offer_link
choice /C YN /M "Install it now with winget"
if errorlevel 2 goto offer_link
winget install -e --id %~2 --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto offer_failed
call :refresh_path
exit /b 0
:offer_failed
echo winget did not install it.
:offer_link
echo Download it from %~3
echo and run start.bat again once it is installed.
exit /b 1

:refresh_path
rem winget set PATH for new windows; read it again for this one
for /f "usebackq delims=" %%P in (`%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%%P"
exit /b 0
"""  # noqa: E501

BAT_PYTHON = r"""
:need_python
rem call :need_python 3.13  ->  sets PY; 0 ready, 1 not installed, 2 not seen yet
call :find_python && exit /b 0
call :offer "Python" Python.Python.%~1 https://www.python.org/downloads/
if errorlevel 1 exit /b 1
call :find_python && exit /b 0
exit /b 2

:find_python
set "PY="
where py >nul 2>nul && py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if defined PY exit /b 0
where python >nul 2>nul && python -c "import sys" >nul 2>nul && set "PY=python"
if defined PY exit /b 0
exit /b 1
"""

BAT_DOTNET = r"""
:need_dotnet
rem call :need_dotnet MAJOR RUNTIME  ->  0 ready, 1 not installed, 2 not seen yet
call :has_dotnet %1 %2 && exit /b 0
call :offer ".NET SDK %~1" Microsoft.DotNet.SDK.%~1 https://dotnet.microsoft.com/download
if errorlevel 1 exit /b 1
call :has_dotnet %1 %2 && exit /b 0
exit /b 2

:has_dotnet
rem the SDK of that version, or any SDK plus the runtime the app runs on
where dotnet >nul 2>nul || exit /b 1
dotnet --list-sdks 2>nul | findstr /B /C:"%~1." >nul && exit /b 0
dotnet --list-sdks 2>nul | findstr /R "^[0-9]" >nul || exit /b 1
dotnet --list-runtimes 2>nul | findstr /C:"%~2 %~1." >nul && exit /b 0
exit /b 1
"""

BAT_MSVC = r"""
:need_msvc
rem call :need_msvc [rust]  ->  0 ready or not needed, 1 not installed
if not "%~1"=="rust" goto msvc_check
rem Rust only needs them for its default (MSVC) toolchain
rustc -vV 2>nul | findstr /C:"-msvc" >nul || exit /b 0
:msvc_check
call :has_msvc && exit /b 0
echo.
echo Microsoft's C++ Build Tools are needed to build this project, and they are
echo not installed. They are a big download (about 2-5 GB) and take a while.
where winget >nul 2>nul || goto msvc_link
choice /C YN /M "Install them now with winget"
if errorlevel 2 goto msvc_link
winget install -e --id Microsoft.VisualStudio.BuildTools --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended" --accept-package-agreements --accept-source-agreements
call :has_msvc && exit /b 0
:msvc_link
echo Download them from https://visualstudio.microsoft.com/visual-cpp-build-tools/
echo and choose "Desktop development with C++", then run start.bat again.
exit /b 1

:has_msvc
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" exit /b 1
"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath | findstr /R "." >nul && exit /b 0
exit /b 1
"""  # noqa: E501

BAT_END = r"""
:reopen
echo.
echo It is installed, but this window cannot see it yet.
echo Close this window and run start.bat again.
pause
exit /b 1

:stop
echo.
pause
exit /b 1

:failed
echo.
echo Setup failed - see the messages above.
echo Run start.bat again once it is fixed; the steps that worked are skipped.
echo If the messages mention a C/C++ compiler (cl.exe, link.exe, MSBuild or
echo node-gyp), install Microsoft's C++ Build Tools and try again:
echo     winget install Microsoft.VisualStudio.BuildTools
"""


def start_bat(name: str, apps) -> str:
    """The text of start.bat for the apps _detect found."""
    title = _clean_title(name)
    lines = [
        "@echo off",
        "setlocal EnableExtensions",
        f"title {title}",
        'cd /d "%~dp0"',
        'set "ROOT=%~dp0"',
        "",
        "rem Made by beam. Sets the project up on this computer and starts it.",
        "rem Safe to run again: setup is skipped once it has been done.",
    ]
    tools = _tools(apps, "win")
    if tools:
        lines += ["", "rem --- what it needs " + "-" * 56]
        for tool in tools:
            lines += _bat_need(tool)

    set_up = [app for app in apps if app.setup_bat]
    for n, app in enumerate(apps):
        if not app.setup_bat:
            continue
        label = _label(app, name)
        lines += [
            "",
            f"rem --- setting up {_bat_escape(label)} " + "-" * max(4, 58 - len(label)),
            "echo.",
            _bat_echo(f"Setting up {label} ..."),
            f'cd /d "{_bat_dir(app.folder)}"',
        ]
        lines += [line.replace("@N@", str(n)) for line in app.setup_bat]

    started = [app for app in apps if app.run_bat]
    waiting = [app for app in started if app.waits]
    notes = [app.note for app in apps if app.note and not app.run_bat]
    lines += ["", "rem --- starting " + "-" * 60]
    if len(waiting) > MAX_STARTED:
        lines += ["echo.", _bat_echo(
            f"This project has {len(waiting)} programs. Start the one you want:")]
        for app in waiting:
            lines.append(_bat_echo(
                f"    cd {app.folder.replace('/', chr(92)) or '.'} && {app.run_bat}"))
        started = [app for app in started if not app.waits]
        waiting = []
    for app in started:
        if not app.waits:
            lines += [f'cd /d "{_bat_dir(app.folder)}"', app.run_bat]
    for app in waiting[:-1]:
        label = _label(app, name)
        lines += [
            _bat_echo(f"Starting {label} in a window of its own ..."),
            f'start "{_clean_title(label)}" /d "{_bat_dir(app.folder)}" '
            f"cmd /k {app.run_bat}",
        ]
    if waiting:
        last = waiting[-1]
        lines += [
            f'cd /d "{_bat_dir(last.folder)}"',
            _bat_echo(f"Starting {_label(last, name)} ..."),
            "echo.",
            f"call {last.run_bat}",
            "echo.",
            "echo The program has finished.",
        ]
    if notes:
        lines.append("echo.")
        lines += [_bat_echo(note) for note in notes]
    if waiting or set_up or notes:
        lines.append("pause")
    lines.append("exit /b 0")

    if any(t.check != "msvc" for t in tools):
        lines.append(BAT_NEED)  # :need, and the :offer the other checks use
    if any(t.check == "python" for t in tools):
        lines.append(BAT_PYTHON)
    if any(t.check == "dotnet" for t in tools):
        lines.append(BAT_DOTNET)
    if any(t.check == "msvc" for t in tools):
        lines.append(BAT_MSVC)
    lines.append(BAT_END)
    if any("py_ready@N@" in line for app in set_up for line in app.setup_bat):
        lines += ["echo To set Python up from scratch, delete the .venv folder."]
    lines += ["pause", "exit /b 1"]

    text = "\n".join(lines).strip("\n") + "\n"
    return text.replace("\n", "\r\n")  # cmd.exe wants CRLF line endings


# --- start.sh ----------------------------------------------------------------

SH_HEAD = """#!/usr/bin/env bash
# Made by beam. Sets the project up on this computer and starts it.
# Safe to run again: setup is skipped once it has been done.
# Run it with:  bash start.sh

cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) VBIN=Scripts ;;
    *) VBIN=bin ;;
esac

need() {
    # need COMMAND NAME WHERE-TO-GET-IT
    command -v "$1" >/dev/null 2>&1 && return 0
    echo
    echo "$2 is needed to run this project, and it is not installed."
    echo "Install it first: $3"
    echo "Then run start.sh again."
    exit 1
}

failed() {
    echo
    echo "Setup failed - see the messages above."
    echo "Run start.sh again once it is fixed; the steps that worked are skipped."
    exit 1
}
"""

SH_PYTHON = """
find_python() {
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 &&
            "$candidate" -c 'import sys; sys.exit(sys.version_info[0] != 3)' \\
                >/dev/null 2>&1; then
            PY="$candidate"
            return 0
        fi
    done
    return 1
}

make_venv() {
    echo "Creating a virtual environment ..."
    "$PY" -m venv .venv && return 0
    echo "If it says venv or ensurepip is not available, install that first:"
    echo "    sudo apt install python3-venv"
    failed
}
"""

SH_DOTNET = """
need_dotnet() {
    # need_dotnet MAJOR RUNTIME: that SDK, or any SDK plus that runtime
    if command -v dotnet >/dev/null 2>&1; then
        dotnet --list-sdks 2>/dev/null | grep -q "^$1\\\\." && return 0
        dotnet --list-sdks 2>/dev/null | grep -q "^[0-9]" &&
            dotnet --list-runtimes 2>/dev/null | grep -q "^$2 $1\\\\." && return 0
    fi
    need "dotnet-sdk-$1" ".NET SDK $1" "https://dotnet.microsoft.com/download"
}
"""


def _sh_need(tool: Tool) -> str:
    if tool.check == "python":
        return 'find_python || need python3 "Python 3" "https://www.python.org/downloads/"'
    if tool.check == "dotnet":
        return f"need_dotnet {tool.arg}"
    q = shlex.quote
    return f"need {q(tool.command)} {q(tool.name)} {q(tool.url)}"


def _sh_cd(folder: str) -> str:
    return 'cd "$ROOT"' + (f"/{shlex.quote(folder)}" if folder else "")


def start_sh(name: str, apps) -> str:
    """The text of start.sh for the apps _detect found."""
    q = shlex.quote
    tools = _tools(apps, "posix")
    lines = [SH_HEAD.rstrip("\n")]
    if any(t.check == "python" for t in tools):
        lines.append(SH_PYTHON.rstrip("\n"))
    if any(t.check == "dotnet" for t in tools):
        lines.append(SH_DOTNET.rstrip("\n"))
    if tools:
        lines += ["", "# --- what it needs " + "-" * 58]
        lines += [_sh_need(tool) for tool in tools]

    for app in apps:
        if not app.setup_sh:
            continue
        label = _label(app, name)
        lines += [
            "",
            f"# --- setting up {label} " + "-" * max(4, 60 - len(label)),
            "echo",
            f"echo {q(f'Setting up {label} ...')}",
            f"{_sh_cd(app.folder)} || failed",
            *app.setup_sh,
        ]

    started = [app for app in apps if app.run_sh]
    waiting = [app for app in started if app.waits]
    notes = [app.note_sh if app.note_sh is not None else app.note
             for app in apps if not app.run_sh]
    notes = [n for n in notes if n]
    lines += ["", "# --- starting " + "-" * 63]
    if len(waiting) > MAX_STARTED:
        many = f"This project has {len(waiting)} programs. Start the one you want:"
        lines += ["echo", f"echo {q(many)}"]
        for app in waiting:
            lines.append(f"echo {q(f'    cd {app.folder or chr(46)} && {app.run_sh}')}")
        started = [app for app in started if not app.waits]
        waiting = []
    for app in started:
        if not app.waits:
            lines.append(f"({_sh_cd(app.folder)} && {app.run_sh})")
    if len(waiting) > 1:
        lines += [
            'PIDS=""',
            'stop_all() { [ -n "$PIDS" ] && kill $PIDS 2>/dev/null; }',
            "trap stop_all EXIT",
            "trap 'exit 130' INT TERM",
        ]
        for app in waiting[:-1]:
            lines += [
                f"echo {q(f'Starting {_label(app, name)} ...')}",
                f"({_sh_cd(app.folder)} && exec {app.run_sh}) &",
                'PIDS="$PIDS $!"',
            ]
    if waiting:
        last = waiting[-1]
        lines += [
            f"{_sh_cd(last.folder)} || exit 1",
            f"echo {q(f'Starting {_label(last, name)} ...')}",
            "echo",
            last.run_sh,
        ]
    if notes:
        lines.append("echo")
        lines += [f"echo {q(note)}" for note in notes]
    return "\n".join(lines).strip("\n") + "\n"
