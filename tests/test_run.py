"""Tests: running start.bat / start.sh right after beam receive."""

from __future__ import annotations

import shutil
import signal
import subprocess

import pytest
from test_online import fake_host  # noqa: F401  (a fixture)
from test_skip_rules import build

import beam
from beam import cli, transfer
from beam.transfer import START_SCRIPT, _safe_unzip, run_start_script


@pytest.fixture
def sent(tmp_path, fake_host):  # noqa: F811
    project = build(tmp_path / "app", {"main.py": "print('hi')\n"})
    return beam.send(project, quiet=True)


@pytest.fixture
def ran(monkeypatch):
    """Record start scripts run instead of running them."""
    folders = []
    monkeypatch.setattr(transfer, "run_start_script", lambda f, **_: folders.append(f))
    return folders


def test_run_true_runs_it_after_unzipping(tmp_path, sent, ran):
    folder = beam.receive(sent, out=tmp_path / "in", extract=True, run=True,
                          quiet=True)
    assert ran == [folder] and (folder / START_SCRIPT).exists()


def test_run_can_be_a_question(tmp_path, sent, ran, capsys):
    asked = []
    beam.receive(sent, out=tmp_path / "in", extract=True,
                 run=lambda script: asked.append(script) or False)
    assert asked == [START_SCRIPT] and ran == []
    said = capsys.readouterr().out
    assert "double-click start.bat" in said or "bash start.sh" in said


def test_nothing_runs_by_default_or_without_unzipping(tmp_path, sent, ran):
    beam.receive(sent, out=tmp_path / "a", extract=True, quiet=True)
    beam.receive(sent, out=tmp_path / "b", extract=False, run=True, quiet=True)
    assert ran == []


# --- the command line --------------------------------------------------------


@pytest.fixture
def receive_kw(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "receive", lambda code, **kw: seen.update(kw))
    return seen


@pytest.mark.parametrize("interactive,argv,expected", [
    (True, [], "ask"),
    (True, ["--run"], True),
    (True, ["--no-run"], False),
    (True, ["-q"], False),
    (False, [], False),  # a script, or piped: nobody to answer
    (False, ["--run"], True),
])
def test_when_receive_asks(monkeypatch, receive_kw, interactive, argv, expected):
    monkeypatch.setattr(cli, "_interactive", lambda: interactive)
    assert cli.main(["receive", "abc", *argv]) == 0
    if expected == "ask":
        assert receive_kw["run"] is cli._ask_to_run
    else:
        assert receive_kw["run"] is expected


def test_run_and_no_run_together_are_refused(capsys):
    with pytest.raises(SystemExit):
        cli.main(["receive", "abc", "--run", "--no-run"])
    assert "not allowed with" in capsys.readouterr().err


def test_run_needs_the_zip_unzipped(receive_kw, capsys):
    assert cli.main(["receive", "abc", "--run", "--no-extract"]) == 1
    assert "--no-extract" in capsys.readouterr().err


@pytest.mark.parametrize("answer,runs", [
    ("", True), ("y", True), ("YES", True), ("n", False), ("no", False),
])
def test_the_question(monkeypatch, answer, runs):
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    assert cli._ask_to_run("start.bat") is runs


def test_no_answer_means_no(monkeypatch):
    def closed(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", closed)
    assert cli._ask_to_run("start.sh") is False


# --- really running it -------------------------------------------------------


def unpacked(tmp_path, files):
    zip_path = beam.pack(build(tmp_path / "proj", files), quiet=True)
    folder = tmp_path / "received"
    _safe_unzip(zip_path, folder)
    return folder


def test_a_python_project_really_starts(tmp_path, capfd):
    folder = unpacked(tmp_path, {"main.py": "print('hello from the other laptop')\n"})
    code = run_start_script(folder, stdin=subprocess.DEVNULL)
    out = capfd.readouterr().out
    assert code == 0, out
    assert "hello from the other laptop" in out
    assert (folder / ".venv").is_dir()

    # a second run skips the setup that is already done
    run_start_script(folder, stdin=subprocess.DEVNULL)
    assert "Installing" not in capfd.readouterr().out


@pytest.mark.skipif(shutil.which("npm") is None, reason="needs Node.js")
def test_a_node_project_really_starts(tmp_path, capfd):
    folder = unpacked(tmp_path, {
        "package.json": '{"scripts": {"start": "node index.js"}}',
        "index.js": "console.log('hello from node')\n",
    })
    code = run_start_script(folder, stdin=subprocess.DEVNULL)
    out = capfd.readouterr().out
    assert code == 0, out
    assert "hello from node" in out


def test_ctrl_c_is_left_to_the_project_and_given_back(tmp_path, monkeypatch):
    before = signal.getsignal(signal.SIGINT)
    during = []

    def fake_call(command, **kw):
        during.append(signal.getsignal(signal.SIGINT))
        return 0

    monkeypatch.setattr(subprocess, "call", fake_call)
    run_start_script(tmp_path)
    assert during[0] is not before  # beam does not stop on Ctrl+C meanwhile
    assert signal.getsignal(signal.SIGINT) is before
