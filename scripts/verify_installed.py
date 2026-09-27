"""Execute the installed console script outside source with network/credential hooks blocked."""

import json
import os
import runpy
import socket
import subprocess
import sys
from pathlib import Path

assert not os.environ.get("GH_TOKEN") and not os.environ.get("GITHUB_TOKEN")


def blocked(*args, **kwargs):
    raise AssertionError("Offline wheel command attempted network or a child process.")


socket.socket.connect = blocked
socket.create_connection = blocked
subprocess.Popen = blocked
import httpx  # noqa: E402 — install network guards before importing package code.

httpx.Client.send = blocked
import oss_pr_tracker  # noqa: E402

assert Path(oss_pr_tracker.__file__).is_relative_to(Path(sys.prefix))
assert not Path(oss_pr_tracker.__file__).is_relative_to(Path.cwd())
console = str(Path(sys.prefix) / "bin/oss-pr-tracker")
for args in (
    ["--help"],
    ["demo", "--output-dir", "demo"],
    ["render", "--input", "demo/status.json", "--output-dir", "rendered"],
):
    sys.argv = [console] + args
    try:
        runpy.run_path(console, run_name="__main__")
    except SystemExit as e:
        assert e.code == 0, e.code
assert json.loads(Path("demo/status.json").read_text()) == json.loads(
    Path("rendered/status.json").read_text()
)
print(
    "Installed wheel: help, demo, render passed outside source; "
    "sockets, HTTP and subprocesses blocked."
)
print("Python:", sys.version.split()[0])
