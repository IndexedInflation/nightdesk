"""Installation checks use disposable folders and never a user's live account."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tomllib
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]


def test_project_dependencies_are_pinned_in_install_requirements():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    requirements = set((ROOT / "requirements.txt").read_text().splitlines())
    assert set(metadata["project"]["dependencies"]) <= requirements
    assert metadata["project"]["scripts"]["nightdesk"] == "nightdesk.__main__:main"


def test_launcher_without_install_explains_next_step(tmp_path):
    folder = tmp_path / "Nightdesk with spaces"
    folder.mkdir()
    shutil.copyfile(ROOT / "Launch.command", folder / "Launch.command")
    result = subprocess.run(["bash", str(folder / "Launch.command")], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 1
    assert "Install.command" in result.stdout


def test_installer_handles_a_python_path_and_project_path_with_spaces(tmp_path):
    folder = tmp_path / "Nightdesk with spaces"
    (folder / "scripts").mkdir(parents=True)
    for name in ("install.sh", "doctor.py"):
        shutil.copyfile(ROOT / "scripts" / name, folder / "scripts" / name)
    (folder / "requirements.txt").write_text("# fake install, no network\n")
    python_dir = tmp_path / "Python tools"
    python_dir.mkdir()
    interpreter = python_dir / "python with spaces"
    recorder = tmp_path / "calls.jsonl"
    # The fake interpreter records argv and simulates venv creation; pip never runs.
    interpreter.write_text("#!" + sys.executable + "\n" + '''
import json, os, pathlib, shutil, sys
with open(os.environ["INSTALL_TEST_RECORD"], "a") as out:
    out.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["-m", "venv"]:
    target = pathlib.Path(sys.argv[3]) / "bin" / "python"
    target.parent.mkdir(parents=True)
    shutil.copyfile(__file__, target)
    target.chmod(0o755)
elif sys.argv[1:2] == ["-c"] and "platform.python_version" in sys.argv[2]:
    print("3.11.0")
''')
    interpreter.chmod(0o755)
    env = dict(os.environ, NIGHTDESK_PYTHON=str(interpreter), INSTALL_TEST_RECORD=str(recorder))
    result = subprocess.run(["bash", str(folder / "scripts" / "install.sh"), "--no-native"], env=env,
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in recorder.read_text().splitlines()]
    assert ["-m", "venv", str(folder / ".venv")] in calls
    assert any(call[-2:] == ["-e", str(folder)] for call in calls)
    assert any(call[-2:] == ["-r", str(folder / "requirements.txt")] for call in calls)


def test_runtime_and_model_artifacts_are_git_ignored(tmp_path):
    if not shutil.which("git"):
        return
    shutil.copyfile(ROOT / ".gitignore", tmp_path / ".gitignore")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    candidates = ["data/models/production.json", "logs/engine.log", ".venv/bin/python",
                  "video/models/voice.onnx", "Nightdesk.app/Contents/Info.plist", "node_modules/package/index.js"]
    result = subprocess.run(["git", "check-ignore", "--no-index", *candidates], cwd=tmp_path,
                            capture_output=True, text=True)
    assert set(result.stdout.splitlines()) == set(candidates)


def test_launch_rejects_an_unrelated_application_on_its_port(tmp_path):
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        env = dict(os.environ, NIGHTDESK_HOME=str(tmp_path / "account"), NIGHTDESK_PORT=str(port),
                   PYTHONPATH=str(ROOT / "engine"))
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "launch.py"), "--no-open"],
                                env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 1
    assert "Another application is using port" in result.stderr
