"""Tests: working out what a project is, and the start scripts written for it."""

from __future__ import annotations

import re
import zipfile

import pytest
from test_skip_rules import build

import beam
from beam._detect import _mini_toml, _strip_jsonc


def scripts(tmp_path, files, **options):
    """Pack a project; return (start.bat, start.sh, all names in the zip)."""
    project = build(tmp_path / "proj", files)
    out = beam.pack(project, quiet=True, **options)
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        bat = zf.read("start.bat").decode() if "start.bat" in names else ""
        sh = zf.read("start.sh").decode() if "start.sh" in names else ""
    return bat, sh, names


# (files, in start.bat, in start.sh)
PROJECTS = {
    "django": (
        {"manage.py": "import django\n", "requirements.txt": "django\n"},
        [r'"%VPY%" "manage.py" migrate --noinput',
         r'.venv\Scripts\python.exe "manage.py" runserver'],
        ['".venv/$VBIN/python" manage.py runserver'],
    ),
    "flask without app.run": (
        {"app.py": "from flask import Flask\napp = Flask(__name__)\n"},
        [r'-m flask --app "app.py" run'],
        ["-m flask --app app.py run"],
    ),
    "fastapi": (
        {"main.py": "from fastapi import FastAPI\nserver = FastAPI()\n"},
        ["-m uvicorn main:server", '"%VPY%" -c "import uvicorn"'],
        ["-m uvicorn main:server"],
    ),
    "notebooks only": (
        {"analysis.ipynb": '{"cells": [{"cell_type": "code", "source": '
                           '["%matplotlib inline\\n", "import pandas as pd"]}]}'},
        [r".venv\Scripts\python.exe -m notebook", "import notebook"],
        ["-m notebook"],
    ),
    "uv project": (
        {"main.py": "print(1)\n", "uv.lock": "",
         "pyproject.toml": '[project]\nname = "x"\ndependencies = ["httpx>=0.27"]\n'},
        ["where uv >nul 2>nul || goto py_pip0", "call uv sync",
         "-r requirements.txt"],
        ["uv sync && installed=1"],
    ),
    "poetry project": (
        {"main.py": "print(1)\n", "poetry.lock": "",
         "pyproject.toml": '[tool.poetry.dependencies]\npython = "^3.10"\n'
                           'requests = "^2.31"\n'},
        ['set "POETRY_VIRTUALENVS_IN_PROJECT=true"', "call poetry install --no-root"],
        ["POETRY_VIRTUALENVS_IN_PROJECT=true poetry install --no-root"],
    ),
    "python version from .python-version": (
        {"main.py": "print(1)\n", ".python-version": "3.11.4\n"},
        ["call :need_python 3.11"], ["find_python ||"],
    ),
    "python version from requires-python": (
        {"main.py": "print(1)\n",
         "pyproject.toml": '[project]\nname = "x"\nrequires-python = ">=3.9,<3.12"\n'},
        ["call :need_python 3.11"], [],
    ),
    "node prefers dev over start": (
        {"package.json": '{"scripts": {"dev": "vite", "start": "vite preview"}, '
                         '"devDependencies": {"vite": "5"}}'},
        ['call :need node "Node.js" OpenJS.NodeJS.LTS', "call npm install",
         "call npm run dev"],
        ["need node Node.js", "npm run dev"],
    ),
    "pnpm": (
        {"package.json": '{"scripts": {"start": "node s.js"}}', "pnpm-lock.yaml": ""},
        ["call :need pnpm", "call pnpm install", "call pnpm start"],
        ["need pnpm pnpm", "pnpm start"],
    ),
    "bun": (
        {"package.json": '{"scripts": {"start": "bun s.ts"}}', "bun.lock": "{}"},
        ["call :need bun", "call bun install", "call bun run start"], [],
    ),
    "node with only a main file": (
        {"package.json": '{"main": "src/server.js"}', "src/server.js": ""},
        [r'call node "src\server.js"'], ["node src/server.js"],
    ),
    "tauri": (
        {"package.json": '{"scripts": {"tauri": "tauri", "dev": "vite"}}',
         "src-tauri/Cargo.toml": '[package]\nname = "app"\n',
         "src-tauri/src/main.rs": ""},
        ["call npm run tauri dev", "call :need cargo", "call :need_msvc rust"],
        ["need cargo Rust"],
    ),
    "deno with comments in deno.jsonc": (
        {"deno.jsonc": '{\n  // tasks\n  "tasks": {"dev": "deno run -A main.ts"},\n}',
         "main.ts": ""},
        ["call :need deno", "call deno task dev"], ["deno task dev"],
    ),
    "rust program": (
        {"Cargo.toml": '[package]\nname = "hi"\n', "src/main.rs": ""},
        ["call cargo build", "call cargo run", "call :need_msvc rust"],
        ["cargo build || failed", "cargo run"],
    ),
    "rust workspace with one program": (
        {"Cargo.toml": '[workspace]\nmembers = ["cli", "core"]\n',
         "cli/Cargo.toml": '[package]\nname = "my-cli"\n', "cli/src/main.rs": "",
         "core/Cargo.toml": '[package]\nname = "core"\n', "core/src/lib.rs": ""},
        ["call cargo run -p my-cli"], ["cargo run -p my-cli"],
    ),
    "go": (
        {"go.mod": "module x\n", "main.go": "package main\nfunc main() {}\n"},
        ["call go mod download", "call go run ."], ["go run ."],
    ),
    "go with cmd folder": (
        {"go.mod": "module x\n", "cmd/api/main.go": "package main\n",
         "internal/db.go": "package internal\n"},
        ["call go run ./cmd/api"], ["go run ./cmd/api"],
    ),
    "spring boot with the maven wrapper": (
        {"pom.xml": "<project><properties><java.version>17</java.version>"
                    "</properties><artifactId>spring-boot-starter-web"
                    "</artifactId></project>",
         "mvnw": "", "mvnw.cmd": ""},
        ["EclipseAdoptium.Temurin.17.JDK", "call mvnw.cmd spring-boot:run",
         "JAVA_HOME"],
        ["sh ./mvnw spring-boot:run"],
    ),
    "maven without a wrapper needs maven": (
        {"pom.xml": "<project></project>"},
        ['call :need mvn "Maven" - https://maven.apache.org',
         "call mvn -DskipTests package"],
        ["mvn -DskipTests package || failed"],
    ),
    "gradle application": (
        {"build.gradle.kts": "plugins {\n    application\n}\n", "gradlew": "",
         "gradlew.bat": ""},
        ["call gradlew.bat run", "Temurin.21.JDK"], ["sh ./gradlew run"],
    ),
    "plain java": (
        {"Main.java": "class Main { public static void main(String[] a) {} }"},
        ['call java "Main.java"', "Temurin.25.JDK"], ["java Main.java"],
    ),
    "dotnet web api": (
        {"App.sln": "", "Api/Api.csproj": '<Project Sdk="Microsoft.NET.Sdk.Web">'
         "<PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup>"
         "</Project>",
         "Api.Tests/Api.Tests.csproj": '<Project Sdk="Microsoft.NET.Sdk">'
         '<ItemGroup><PackageReference Include="Microsoft.NET.Test.Sdk"/>'
         "</ItemGroup></Project>"},
        ["call :need_dotnet 8 Microsoft.AspNetCore.App",
         r'call dotnet run --project "Api\Api.csproj"'],
        ["need_dotnet 8 Microsoft.AspNetCore.App",
         "dotnet run --project Api/Api.csproj"],
    ),
    "wpf only runs on windows": (
        {"Desk.csproj": '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
         "<OutputType>WinExe</OutputType><UseWPF>true</UseWPF>"
         "<TargetFramework>net9.0-windows</TargetFramework></PropertyGroup></Project>"},
        ["call :need_dotnet 9 Microsoft.WindowsDesktop.App", "dotnet run"],
        ["only runs on Windows"],
    ),
    "laravel": (
        {"artisan": "", "composer.json": '{"require": {"laravel/framework": "^11"}}'},
        ["call :need composer", "call composer install",
         "php artisan migrate --force", "call php artisan serve",
         "PHP_INI_SCAN_DIR"],
        ["composer install || failed", "php artisan serve"],
    ),
    "plain php": (
        {"index.php": "<?php echo 1;"},
        ["call php -S 127.0.0.1:8000"], ["php -S 127.0.0.1:8000"],
    ),
    "rails": (
        {"Gemfile": "", "config/application.rb": "", "bin/rails": ""},
        ["call bundle install", "ridk install", r"ruby bin\rails db:prepare",
         r"call ruby bin\rails server"],
        ["ruby bin/rails server"],
    ),
    "phoenix": (
        {"mix.exs": "defp deps do [{:phoenix, \"~> 1.7\"}] end"},
        ["Erlang.ErlangOTP", "Elixir.Elixir", "call mix deps.get",
         "call mix phx.server"],
        ["mix phx.server"],
    ),
    "flutter": (
        {"pubspec.yaml": "dependencies:\n  flutter:\n    sdk: flutter\n"},
        ['call :need flutter "Flutter" -', "call flutter pub get",
         "call flutter run"],
        ["flutter run"],
    ),
    "haskell stack": (
        {"stack.yaml": "", "app.cabal": "executable app\n  main-is: Main.hs\n"},
        ["call stack build", "call stack run"], ["stack run"],
    ),
    "swift": (
        {"Package.swift": "targets: [.executableTarget(name: \"x\")]"},
        ["call swift build", "call swift run", "call :need_msvc"], ["swift run"],
    ),
    "cmake": (
        {"CMakeLists.txt": "project(x)\n", "main.cpp": ""},
        ["cmake -S . -B build", "call :need_msvc", "echo Built into the build folder"],
        ["need c++ 'A C/C++ compiler'", "cmake --build build || failed"],
    ),
    "unity": (
        {"ProjectSettings/ProjectVersion.txt": "m_EditorVersion: 2022.3.5f1\n",
         "Assets/Player.cs": "", "Assembly-CSharp.csproj": ""},
        ["open this folder in Unity Hub (Unity 2022.3.5f1)"],
        ["open this folder in Unity Hub"],
    ),
    "android": (
        {"build.gradle": "",
         "app/build.gradle": "plugins { id 'com.android.application' }"},
        ["open the folder in Android Studio"], ["Android Studio"],
    ),
    "a website": (
        {"index.html": "<h1>hi</h1>"},
        ['start "" "index.html"'], ["xdg-open index.html"],
    ),
    "django with front-end files beside it": (
        {"manage.py": "", "requirements.txt": "django\n",
         "package.json": '{"scripts": {"build": "tailwind build", "dev": "x"}}'},
        ["call npm install", "call npm run build", r"manage.py" + '" runserver'],
        ["npm run build || failed", "manage.py runserver"],
    ),
    "a node app with a requirements.txt for its scripts": (
        {"package.json": '{"scripts": {"dev": "vite"}}', "requirements.txt": "x\n"},
        ["call npm run dev", "-r requirements.txt", "call :need_python"],
        ["npm run dev"],
    ),
}


@pytest.mark.parametrize("files,bat_has,sh_has", PROJECTS.values(), ids=PROJECTS.keys())
def test_start_scripts(tmp_path, files, bat_has, sh_has):
    bat, sh, _ = scripts(tmp_path, files)
    for text in bat_has:
        assert text in bat, f"start.bat is missing: {text}"
    for text in sh_has:
        assert text in sh, f"start.sh is missing: {text}"
    check_bat(bat)
    assert "@N@" not in sh and "\r" not in sh


def check_bat(bat: str) -> None:
    """Every goto and call :label has somewhere to go; nothing is left unfilled."""
    assert "@N@" not in bat
    assert "\n" not in bat.replace("\r\n", "")
    labels = {m.lower() for m in re.findall(r"(?m)^:(\w+)", bat)}
    for target in re.findall(r"\bgoto (\w+)", bat) + re.findall(r"\bcall :(\w+)", bat):
        assert target.lower() in labels, f"no :{target} label"
    assert len(labels) == len(re.findall(r"(?m)^:(\w+)", bat)), "a label is repeated"


def test_generated_requirements_come_from_pyproject_not_imports(tmp_path):
    _, _, names = scripts(tmp_path, {
        "main.py": "import numpy\n",
        "pyproject.toml": '[project]\nname = "x"\ndependencies = [\n'
                          '  "httpx>=0.27",  # a comment\n  "rich",\n]\n',
    })
    project = tmp_path / "proj"
    with zipfile.ZipFile(project.parent / "proj.zip") as zf:
        assert zf.read("requirements.txt").decode() == "httpx>=0.27\nrich\n"


def test_poetry_versions_become_pip_versions(tmp_path):
    scripts(tmp_path, {
        "main.py": "",
        "pyproject.toml": '[tool.poetry.dependencies]\npython = "^3.10"\n'
                          'requests = "^2.31"\nrich = "13.7.0"\nclick = ">=8"\n'
                          'httpx = {version = "^0.27", extras = ["http2"]}\n',
    })
    with zipfile.ZipFile(tmp_path / "proj.zip") as zf:
        reqs = zf.read("requirements.txt").decode().split()
    assert reqs == ["requests", "rich==13.7.0", "click>=8", "httpx"]


def test_notebook_imports_are_guessed(tmp_path):
    scripts(tmp_path, {"a.ipynb": '{"cells": [{"cell_type": "code", "source": '
                                  '"import cv2\\n!pip install x"}]}'})
    with zipfile.ZipFile(tmp_path / "proj.zip") as zf:
        assert zf.read("requirements.txt").decode() == "opencv-python\n"


def test_a_library_is_set_up_but_not_started(tmp_path):
    bat, sh, _ = scripts(tmp_path, {"Cargo.toml": '[package]\nname = "lib"\n',
                                    "src/lib.rs": ""})
    assert "call cargo build" in bat and "cargo run" not in bat
    assert "This is a library" in bat and "This is a library" in sh


def test_no_start_script_when_nothing_is_recognised(tmp_path, capsys):
    project = build(tmp_path / "proj", {"notes.txt": "hello"})
    out = beam.pack(project)
    assert "start.bat" not in zipfile.ZipFile(out).namelist()
    assert "nothing beam knows how to set up" in capsys.readouterr().out


def test_main_can_point_at_a_node_script(tmp_path):
    bat, sh, _ = scripts(tmp_path, {
        "package.json": '{"scripts": {"dev": "vite"}}', "server/index.js": "",
    }, main="server/index.js")
    assert r'call node "server\index.js"' in bat and "call npm install" in bat
    assert "node server/index.js" in sh


def test_the_plan_is_printed(tmp_path, capsys):
    project = build(tmp_path / "proj", {"Cargo.toml": '[package]\nname = "x"\n',
                                        "src/main.rs": ""})
    beam.pack(project)
    assert "  start    : Rust -> cargo run" in capsys.readouterr().out


def test_mini_toml_reads_what_beam_needs():
    data = _mini_toml(
        '# top\n[project]\nname = "x"  # the name\nrequires-python = ">=3.10"\n'
        'dependencies = [\n  "a>=1",\n  \'b\',  # why\n]\n'
        "[tool.poetry.dependencies]\n"
        'python = "^3.10"\nhttpx = {version = "^0.27", extras = ["http2"]}\n'
        '[[bin]]\nname = "one"\n[[bin]]\nname = "two"\n'
    )
    assert data["project"]["name"] == "x"
    assert data["project"]["dependencies"] == ["a>=1", "b"]
    assert data["tool"]["poetry"]["dependencies"]["httpx"]["version"] == "^0.27"
    assert [b["name"] for b in data["bin"]] == ["one", "two"]


def test_jsonc_comments_and_trailing_commas():
    text = ('{\n  // a\n  "url": "https://x.dev/*not a comment*/",\n'
            '  /* b */ "n": [1,],\n}')
    assert _strip_jsonc(text) == (
        '{\n  \n  "url": "https://x.dev/*not a comment*/",\n   "n": [1]\n}'
    )
