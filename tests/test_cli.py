"""Tests for the ``beam`` command: argument handling and a real transfer."""

from __future__ import annotations

import subprocess
import sys
import threading
import zipfile

import pytest
from test_roundtrip import make_project

import beam
from beam import cli, transfer


def run(*argv) -> int:
    return cli.main(list(argv))


# --- parsing -----------------------------------------------------------------


def test_send_defaults_to_the_upload(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "send", lambda path, **kw: seen.update(kw, path=path))

    assert run("send", ".") == 0
    assert seen["lan"] is False  # goes over the internet, any distance
    assert seen["start_script"] is True
    assert seen["exclude"] == []


def test_cloud_flag_only_spells_the_default_out(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "send", lambda path, **kw: seen.update(kw))

    assert run("send", ".", "--cloud") == 0
    assert seen["lan"] is False


def test_lan_flag_keeps_it_on_the_local_network(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "send", lambda path, **kw: seen.update(kw))

    assert run("send", ".", "--lan") == 0
    assert seen["lan"] is True


def test_lan_and_cloud_together_are_refused(capsys):
    with pytest.raises(SystemExit):
        run("send", ".", "--lan", "--cloud")
    assert "not allowed with" in capsys.readouterr().err


def test_excludes_accept_repeats_and_commas(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "send", lambda path, **kw: seen.update(kw))

    run("send", ".", "-x", ".mp4,.log", "--exclude", "data")
    assert seen["exclude"] == [".mp4", ".log", "data"]


def test_receive_unzips_unless_told_not_to(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "receive", lambda code, **kw: seen.update(kw, code=code))

    run("receive", "ab3f9c")
    assert seen["code"] == "ab3f9c" and seen["extract"] is True

    run("receive", "ab3f9c", "--no-extract")
    assert seen["extract"] is False


@pytest.mark.parametrize("alias", ["receive", "recv", "get"])
def test_receive_aliases(monkeypatch, alias):
    seen = {}
    monkeypatch.setattr(cli, "receive", lambda code, **kw: seen.update(code=code))
    assert run(alias, "zz9") == 0
    assert seen["code"] == "zz9"


def test_no_command_prints_help(capsys):
    assert run() == 2
    out = capsys.readouterr().out
    assert "beam send <folder>" in out and "beam receive <code>" in out


def test_beam_errors_become_exit_1(monkeypatch, capsys):
    def boom(code, **kw):
        raise beam.BeamError("no sender with code 'nope' found")

    monkeypatch.setattr(cli, "receive", boom)
    assert run("receive", "nope") == 1
    assert "no sender" in capsys.readouterr().err


# --- a real transfer ---------------------------------------------------------


def test_cli_roundtrip_over_the_local_network(tmp_path, monkeypatch):
    project = make_project(tmp_path / "app")
    info, ready = {}, threading.Event()

    def on_ready(details):
        info.update(details)
        ready.set()

    thread = threading.Thread(
        target=beam.send,
        args=(project,),
        kwargs=dict(
            lan=True, port=0, code="clitest", quiet=True, on_ready=on_ready,
            discoverable=True, discovery_port=18767,
        ),
        daemon=True,
    )
    thread.start()
    assert ready.wait(10), "sender never started listening"

    inbox = tmp_path / "inbox"
    monkeypatch.setattr(
        cli, "receive",
        lambda code, **kw: beam.receive(code, discovery_port=18767, **kw),
    )
    assert run("receive", "clitest", "-o", str(inbox), "--wait", "8", "-q") == 0
    thread.join(timeout=10)

    assert (inbox / "app.zip").is_file()          # the zip stays in the folder
    assert (inbox / "app" / "main.py").is_file()  # and it is unzipped for them
    assert (inbox / "app" / "start.bat").is_file()
    assert "data/clip.mp4" in set(zipfile.ZipFile(inbox / "app.zip").namelist())


def test_sender_prints_the_cli_command_not_python(monkeypatch, capsys):
    monkeypatch.setattr(transfer, "HINT", "python")
    assert transfer._receive_hint("ab3f9c") == 'beam.receive("ab3f9c")'

    monkeypatch.setattr(transfer, "HINT", "cli")
    assert transfer._receive_hint("ab3f9c") == "beam receive ab3f9c"
    assert transfer._receive_hint("ab3f9c", "10.0.0.2:8765") == (
        "beam receive ab3f9c --host 10.0.0.2:8765"
    )


def test_installed_as_a_command():
    """python -m beam works, so the console script has something to point at."""
    out = subprocess.run(
        [sys.executable, "-m", "beam", "--version"],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0
    assert out.stdout.strip() == f"beam {beam.__version__}"
