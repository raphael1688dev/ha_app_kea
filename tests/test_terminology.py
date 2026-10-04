"""Home Assistant renamed add-ons to apps (user interface since 2026.2): user-facing text says "app".

Technical identifiers keep the old word on purpose and are not touched: the host path /addon_configs/...,
the legacy `addon_config` map type and our internal log source key `addon`. None of them contains "add-on".
spec.md and the old CHANGELOG entries are history and CLAUDE.md explains the rule, so they are exempt.
"""
import glob
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
OLD_TERM = re.compile(r"add[- ]ons?\b", re.IGNORECASE)

PATTERNS = [
    "README.md", "repository.yaml", "renovate.json5", "requirements-dev.txt",
    "kea/DOCS.md", "kea/config.yaml", "kea/Dockerfile", "kea/run.sh", "kea/requirements.txt",
    "kea/app/*.py", "kea/app/static/*.html", "kea/translations/*.yaml",
    "docs/*.md", "scripts/*.py", "ci/*", ".github/workflows/*.yml", "tests/*.py",
]


def files():
    this = os.path.abspath(__file__)
    for pattern in PATTERNS:
        for path in glob.glob(os.path.join(ROOT, pattern)):
            if os.path.isfile(path) and os.path.abspath(path) != this:
                yield path


def test_user_facing_text_says_app_not_add_on():
    offenders = []
    for path in files():
        with open(path, encoding="utf-8") as fh:
            for number, line in enumerate(fh, start=1):
                if OLD_TERM.search(line):
                    offenders.append(f"{os.path.relpath(path, ROOT)}:{number}")
    assert not offenders, 'say "app", not "add-on": ' + ", ".join(offenders)


def test_the_scan_covers_the_files_users_read():
    names = {os.path.relpath(p, ROOT) for p in files()}
    for expected in ("README.md", "kea/DOCS.md", "kea/app/static/index.html", "repository.yaml",
                     "kea/translations/en.yaml"):
        assert expected in names, expected


def test_the_host_folder_keeps_the_name_home_assistant_still_uses():
    for name in ("README.md", "kea/DOCS.md"):
        with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
            assert "/addon_configs/" in fh.read(), f"{name}: the folder is still /addon_configs/..."


def test_install_steps_use_the_current_menu_names():
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
        text = fh.read()
    for needle in ("Settings → Apps", "Install app", "Repositories"):
        assert needle in text, needle
