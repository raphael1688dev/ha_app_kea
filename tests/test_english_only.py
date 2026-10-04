"""The repository is English only (see CLAUDE.md): no Chinese characters in tracked files."""
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
CJK = re.compile("[　-〿一-鿿＀-￯]")


def tracked_files():
    if not shutil.which("git"):
        pytest.skip("git is not installed (e.g. inside the add-on image); the unit-test job covers this")
    result = subprocess.run(["git", "-C", ROOT, "ls-files", "-z"], capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip("not a git checkout")
    return [f for f in result.stdout.split("\0") if f]


def test_no_chinese_in_tracked_files():
    offenders = []
    for name in tracked_files():
        path = os.path.join(ROOT, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                for number, line in enumerate(fh, start=1):
                    if CJK.search(line):
                        offenders.append(f"{name}:{number}")
                        break
        except UnicodeDecodeError:
            continue                                  # binary file
    assert not offenders, "Chinese text found (the repo is English only): " + ", ".join(offenders)
