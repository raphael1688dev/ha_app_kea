"""The repository is English only (see CLAUDE.md): no Chinese characters in tracked or new files."""
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
CJK = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")   # ranges as escapes: this file must stay ASCII


def tracked_files():
    if not shutil.which("git"):
        pytest.skip("git is not installed (e.g. inside the app image); the unit-test job covers this")
    # Tracked files plus new files that are not ignored yet, so a file is checked before its first commit.
    result = subprocess.run(["git", "-C", ROOT, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                            capture_output=True, text=True)
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
