"""Tests: a front end and a back end in one project are both set up and started."""

from __future__ import annotations

import zipfile

from test_detect import check_bat, scripts
from test_skip_rules import build

import beam

BACKEND_FRONTEND = {
    "backend/app.py": "import flask\nflask.Flask(__name__).run()\n",
    "backend/requirements.txt": "flask\n",
    "frontend/package.json": '{"scripts": {"dev": "vite"}, '
                             '"dependencies": {"react": "18"}}',
    "frontend/src/App.jsx": "",
    "README.md": "",
}


def test_both_are_set_up_and_started_back_end_first(tmp_path):
    bat, sh, _ = scripts(tmp_path, BACKEND_FRONTEND)
    check_bat(bat)
    assert bat.index("Setting up backend") < bat.index("Setting up frontend")
    assert 'cd /d "%ROOT%backend"' in bat and 'cd /d "%ROOT%frontend"' in bat
    # the back end gets a window of its own; the front end runs in this one
    assert ('start "backend" /d "%ROOT%backend" cmd /k '
            r'.venv\Scripts\python.exe "app.py"') in bat
    assert bat.index('start "backend"') < bat.index("call npm run dev")

    assert '(cd "$ROOT"/backend && exec ".venv/$VBIN/python" app.py) &' in sh
    assert "trap stop_all EXIT" in sh
    assert sh.rstrip().endswith("npm run dev")


def test_the_plan_names_each_part(tmp_path, capsys):
    beam.pack(build(tmp_path / "proj", BACKEND_FRONTEND))
    out = capsys.readouterr().out
    assert "  start    : backend   Python -> app.py" in out
    assert "             frontend  Node -> npm run dev" in out


def test_requirements_are_worked_out_per_part(tmp_path):
    scripts(tmp_path, {
        "api/main.py": "import requests\n",
        "worker/main.py": "import redis\n",
        "api/requirements.txt": "requests\n",
        "worker/pyproject.toml": "",
    })
    with zipfile.ZipFile(tmp_path / "proj.zip") as zf:
        assert zf.read("worker/requirements.txt").decode().split() == ["redis"]
        assert zf.read("api/requirements.txt").decode().split() == ["requests"]
        assert "requirements.txt" not in zf.namelist()


def test_a_workspace_is_one_app(tmp_path):
    bat, _, _ = scripts(tmp_path, {
        "package.json": '{"workspaces": ["apps/*", "packages/*"], '
                        '"scripts": {"dev": "turbo dev"}}',
        "apps/web/package.json": '{"scripts": {"dev": "next dev"}}',
        "apps/docs/package.json": '{"scripts": {"dev": "next dev"}}',
        "packages/ui/package.json": "{}",
    })
    assert bat.count("call npm install") == 1 and "call npm run dev" in bat
    assert "start " not in bat.split("rem --- starting")[1].replace('start "', "")


def test_a_tooling_package_json_is_installed_not_started(tmp_path):
    bat, _, _ = scripts(tmp_path, {
        "package.json": '{"devDependencies": {"husky": "9"}}',
        "server/package.json": '{"scripts": {"start": "node index.js"}}',
        "web/package.json": '{"scripts": {"dev": "vite"}, '
                            '"dependencies": {"vue": "3"}}',
    })
    starting = bat.split("rem --- starting")[1]
    assert bat.count("call npm install") == 3
    assert 'start "server"' in starting and "call npm run dev" in starting


def test_a_root_script_that_starts_the_parts_is_trusted(tmp_path):
    bat, _, _ = scripts(tmp_path, {
        "package.json": '{"scripts": {"dev": "concurrently \\"npm:dev --prefix '
                        'client\\" \\"npm:start --prefix server\\""}}',
        "client/package.json": '{"scripts": {"dev": "vite"}}',
        "server/package.json": '{"scripts": {"start": "node index.js"}}',
    })
    starting = bat.split("rem --- starting")[1]
    assert bat.count("call npm install") == 3
    assert 'start "' not in starting and starting.count("call npm run dev") == 1


def test_examples_docs_and_deep_folders_are_not_apps(tmp_path):
    bat, _, _ = scripts(tmp_path, {
        "main.py": "print(1)\n",
        "docs/package.json": '{"scripts": {"dev": "docusaurus start"}}',
        "examples/demo/package.json": '{"scripts": {"dev": "x"}}',
        "a/b/c/package.json": '{"scripts": {"dev": "x"}}',
    })
    assert "npm" not in bat


def test_a_game_engine_owns_everything_inside(tmp_path):
    bat, _, _ = scripts(tmp_path, {
        "ProjectSettings/ProjectVersion.txt": "m_EditorVersion: 6000.0.1f1\n",
        "Assets/x.cs": "",
        "Packages/com.me.tool/package.json": '{"scripts": {"dev": "x"}}',
    })
    assert "npm" not in bat and "Unity Hub" in bat


def test_templates_are_not_websites(tmp_path):
    bat, _, _ = scripts(tmp_path, {
        "app.py": "from flask import Flask\n", "templates/index.html": "",
    })
    assert 'start ""' not in bat


def test_too_many_programs_are_listed_not_started(tmp_path):
    files = {f"svc{i}/package.json": '{"scripts": {"start": "node ."}}'
             for i in range(5)}
    bat, sh, _ = scripts(tmp_path, files)
    starting = bat.split("rem --- starting")[1]
    assert "This project has 5 programs" in starting
    assert "call npm" not in starting and 'start "' not in starting
    assert r"echo     cd svc0 ^&^& npm start" in starting
    assert "This project has 5 programs" in sh and "&\nPIDS" not in sh


def test_main_starts_just_that_one(tmp_path):
    bat, _, _ = scripts(tmp_path, BACKEND_FRONTEND, main="backend/app.py")
    assert "npm" not in bat
    assert r'call .venv\Scripts\python.exe "app.py"' in bat
    assert 'cd /d "%ROOT%backend"' in bat
