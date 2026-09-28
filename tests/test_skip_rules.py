"""Tests: what stays behind because the other laptop rebuilds it."""

from __future__ import annotations

import zipfile

import pytest

import beam
from beam._stacks import CACHE_SIGNATURE


def build(root, files):
    """Make a project from {"path/in/project": text}."""
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text)
    return root


def packed(tmp_path, files, **options):
    project = build(tmp_path / "proj", files)
    out = beam.pack(project, start_script=False, quiet=True, **options)
    with zipfile.ZipFile(out) as zf:
        return set(zf.namelist())


def gone(names, folder):
    return not any(n == folder or n.startswith(folder.rstrip("/") + "/") for n in names)


# (files in the project, left out, kept)
CASES = {
    "rust target": (
        {"Cargo.toml": "", "src/main.rs": "", "target/debug/app.exe": "x"},
        ["target"], ["src/main.rs", "Cargo.toml"],
    ),
    "a target folder with no Cargo.toml is somebody's code": (
        {"main.py": "", "target/notes.txt": "x"},
        [], ["target/notes.txt"],
    ),
    "maven": (
        {"pom.xml": "", "target/app.jar": "x", "src/Main.java": ""},
        ["target"], ["src/Main.java"],
    ),
    "gradle": (
        {"build.gradle": "", "build/libs/a.jar": "x", ".gradle/cache": "x",
         "local.properties": "sdk.dir=C:/sdk", "src/Main.kt": ""},
        ["build", ".gradle", "local.properties"], ["src/Main.kt", "build.gradle"],
    ),
    "a python build folder stays: it may be what you meant to send": (
        {"pyproject.toml": "", "build/tool.exe": "x"},
        [], ["build/tool.exe"],
    ),
    "dotnet": (
        {"App.csproj": "", "bin/Debug/app.dll": "x", "obj/x.json": "x",
         "Program.cs": ""},
        ["bin", "obj"], ["Program.cs"],
    ),
    "a bin folder with no project file is scripts": (
        {"main.py": "", "bin/run.sh": "x"},
        [], ["bin/run.sh"],
    ),
    "composer": (
        {"composer.json": "{}", "vendor/autoload.php": "x", "index.php": ""},
        ["vendor"], ["index.php"],
    ),
    "a vendor folder with no composer.json stays (Go vendors on purpose)": (
        {"go.mod": "", "vendor/modules.txt": "x"},
        [], ["vendor/modules.txt"],
    ),
    "bundler leaves vendor/bundle, not the rest of vendor": (
        {"Gemfile": "", "vendor/bundle/ruby/gem.rb": "x",
         "vendor/assets/app.js": "x"},
        ["vendor/bundle"], ["vendor/assets/app.js"],
    ),
    "elixir": (
        {"mix.exs": "", "deps/plug/mix.exs": "x", "_build/dev/x": "x",
         "lib/app.ex": ""},
        ["deps", "_build"], ["lib/app.ex"],
    ),
    "flutter": (
        {"pubspec.yaml": "", "build/app.apk": "x", ".dart_tool/x": "x",
         ".flutter-plugins": "x", "lib/main.dart": ""},
        ["build", ".dart_tool", ".flutter-plugins"], ["lib/main.dart"],
    ),
    "cocoapods": ({"Podfile": "", "Pods/x/y": "x"}, ["Pods"], ["Podfile"]),
    "swift": ({"Package.swift": "", ".build/x": "x"}, [".build"], ["Package.swift"]),
    "unreal": (
        {"Game.uproject": "", "Binaries/x.exe": "x", "Intermediate/x": "x",
         "Saved/x": "x", "DerivedDataCache/x": "x", "Content/map.umap": "x"},
        ["Binaries", "Intermediate", "Saved", "DerivedDataCache"],
        ["Content/map.umap"],
    ),
    "godot": (
        {"project.godot": "", ".godot/x": "x", "main.tscn": ""},
        [".godot"], ["main.tscn"],
    ),
    "unity": (
        {"ProjectSettings/ProjectVersion.txt": "m_EditorVersion: 2022.3.1f1",
         "Assets/Player.cs": "", "Library/big.asset": "x", "Temp/x": "x",
         "Logs/x": "x", "UserSettings/x": "x"},
        ["Library", "Temp", "Logs", "UserSettings"], ["Assets/Player.cs"],
    ),
    "a Library folder outside Unity stays": (
        {"main.py": "", "Library/books.csv": "x"},
        [], ["Library/books.csv"],
    ),
    "a venv is found by its pyvenv.cfg, whatever it is called": (
        {"main.py": "", "myenv/pyvenv.cfg": "home = x",
         "myenv/Lib/site-packages/big.py": "x"},
        ["myenv"], ["main.py"],
    ),
    "an ordinary env folder is not a venv": (
        {"main.py": "", "env/settings.yaml": "x"},
        [], ["env/settings.yaml"],
    ),
    "conda environment": (
        {"main.py": "", "condaenv/conda-meta/history": "x",
         "condaenv/python.exe": "x"},
        ["condaenv"], ["main.py"],
    ),
    "a cmake build folder, whatever it is called": (
        {"CMakeLists.txt": "", "out/CMakeCache.txt": "x", "out/app.exe": "x",
         "main.cpp": ""},
        ["out"], ["main.cpp"],
    ),
    "a tagged cache": (
        {"main.py": "", "cache/CACHEDIR.TAG": CACHE_SIGNATURE + b"\n",
         "cache/x": "x"},
        ["cache"], ["main.py"],
    ),
    "an untagged CACHEDIR.TAG file is not a cache": (
        {"main.py": "", "notes/CACHEDIR.TAG": "just a note"},
        [], ["notes/CACHEDIR.TAG"],
    ),
    "node, anywhere in the tree": (
        {"frontend/package.json": "{}", "frontend/node_modules/react/x.js": "x",
         "frontend/.next/cache": "x", "apps/web/.svelte-kit/x": "x",
         "frontend/src/App.jsx": ""},
        ["frontend/node_modules", "frontend/.next", "apps/web/.svelte-kit"],
        ["frontend/src/App.jsx"],
    ),
    "a marker only rules out what is next to it": (
        {"backend/Cargo.toml": "", "backend/target/x": "x", "target/keep.txt": "x"},
        ["backend/target"], ["target/keep.txt"],
    ),
}


@pytest.mark.parametrize("files,out,kept", CASES.values(), ids=CASES.keys())
def test_rebuilt_folders_stay_behind(tmp_path, files, out, kept):
    names = packed(tmp_path, files)
    for folder in out:
        assert gone(names, folder), f"{folder} should have been left out"
    for name in kept:
        assert name in names, f"{name} should have been kept"


def test_env_files_travel_now(tmp_path, capsys):
    project = build(tmp_path / "proj", {
        "main.py": "", ".env": "KEY=1", ".env.local": "KEY=2",
        ".env.example": "KEY=", "backend/.env": "KEY=3",
    })
    out = beam.pack(project, start_script=False)
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert {".env", ".env.local", ".env.example", "backend/.env"} <= names

    said = capsys.readouterr().out
    line = next(ln for ln in said.splitlines() if "included" in ln)
    assert ".env" in line and "backend/.env" in line and ".env.local" in line
    assert ".env.example" not in line  # no secrets in it, so nothing to warn about


def test_env_can_still_be_left_out(tmp_path):
    names = packed(tmp_path, {"main.py": "", ".env": "KEY=1"}, exclude=[".env"])
    assert ".env" not in names and "main.py" in names


def test_with_deps_keeps_packages_but_not_venvs_or_builds(tmp_path):
    names = packed(tmp_path, {
        "package.json": "{}", "node_modules/left-pad/index.js": "x",
        "api/composer.json": "{}", "api/vendor/autoload.php": "x",
        ".venv/pyvenv.cfg": "home = x", "tool/Cargo.toml": "",
        "tool/target/x": "x",
    }, with_deps=True)
    assert "node_modules/left-pad/index.js" in names
    assert "api/vendor/autoload.php" in names
    assert gone(names, ".venv") and gone(names, "tool/target")


def test_no_default_excludes_means_no_rules(tmp_path):
    names = packed(tmp_path, {
        "Cargo.toml": "", "target/x": "x", "node_modules/y": "y",
    }, use_default_excludes=False)
    assert {"target/x", "node_modules/y"} <= names


def test_the_saving_is_shown(tmp_path, capsys):
    project = build(tmp_path / "proj", {
        "main.py": "print(1)\n",
        "node_modules/big.bin": b"x" * 300_000,
        "tool/Cargo.toml": "", "tool/target/debug/x": b"y" * 2_000,
    })
    beam.pack(project, start_script=False)
    said = capsys.readouterr().out
    lines = [ln for ln in said.splitlines() if "node_modules" in ln or "target" in ln]
    assert lines[0].startswith("  left out :") and "node_modules" in lines[0]
    assert "293.0 KB" in lines[0]  # biggest first
    assert "tool/target" in lines[1]
    assert "the other laptop has to install these again" in said


def test_nothing_is_measured_when_quiet(tmp_path, capsys, monkeypatch):
    from beam import packer

    monkeypatch.setattr(packer, "measure", lambda *a, **k: pytest.fail("measured"))
    build(tmp_path / "proj", {"main.py": "", "node_modules/x": "x"})
    beam.pack(tmp_path / "proj", quiet=True)
    assert capsys.readouterr().out == ""
