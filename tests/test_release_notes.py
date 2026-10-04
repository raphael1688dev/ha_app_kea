import importlib.util
import os
import subprocess
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load("release")
release_notes = load("release_notes")
# release_notes imports `release` by name; make both views agree on the exception class
ReleaseError = release_notes.release.ReleaseError

CONFIG = 'name: "X"\nversion: "1.2.3"\n'
CHANGELOG = ("# Changelog\n\n## 1.2.3\n- Fix a thing\n- Another thing\n\n## 1.2.2\n- older\n\n"
             "## 1.2.1\n\n## 1.2.0\n- x\n")


@pytest.fixture
def root(tmp_path):
    (tmp_path / "kea").mkdir()
    (tmp_path / "kea" / "config.yaml").write_text(CONFIG)
    (tmp_path / "kea" / "CHANGELOG.md").write_text(CHANGELOG)
    return str(tmp_path)


def test_notes_are_the_changelog_section_plus_a_link(root):
    out = release_notes.notes_for(root, "v1.2.3", "me/repo")
    assert out == ("- Fix a thing\n- Another thing\n\n"
                   "Full history: [CHANGELOG.md](https://github.com/me/repo/blob/v1.2.3/kea/CHANGELOG.md)\n")
    assert "older" not in out


@pytest.mark.parametrize("tag", ["2.1.4", "v2.1", "v2.1.4.1", "v2.1.4-rc1", "V2.1.4", "v2.1.4; rm -rf /",
                                 "refs/tags/v1.2.3", "", "latest"])
def test_rejects_anything_that_is_not_vxyz(root, tag):
    with pytest.raises(ReleaseError, match="not a release tag"):
        release_notes.notes_for(root, tag, "me/repo")


def test_rejects_a_tag_that_does_not_match_the_config_version(root):
    with pytest.raises(ReleaseError, match="does not match version 1.2.3"):
        release_notes.notes_for(root, "v1.2.4", "me/repo")


def test_rejects_a_missing_changelog_entry(root):
    with open(os.path.join(root, "kea", "config.yaml"), "w") as fh:
        fh.write('version: "1.3.0"\n')
    with pytest.raises(ReleaseError, match="no entry"):
        release_notes.notes_for(root, "v1.3.0", "me/repo")


def test_rejects_an_empty_changelog_entry(root):
    with open(os.path.join(root, "kea", "config.yaml"), "w") as fh:
        fh.write('version: "1.2.1"\n')
    with pytest.raises(ReleaseError, match="no entry"):
        release_notes.notes_for(root, "v1.2.1", "me/repo")


def test_section_for():
    assert release.section_for(CHANGELOG, (1, 2, 2)) == "- older"
    assert release.section_for(CHANGELOG, (9, 9, 9)) is None
    assert release.section_for(CHANGELOG, (1, 2, 0)) == "- x"           # last section, no trailing heading


def test_the_real_repository_state_is_releasable():
    """What the Release workflow will do for the tag of the current version must work."""
    config = open(os.path.join(ROOT, "kea", "config.yaml"), encoding="utf-8").read()
    version = release.fmt(release.parse_config_version(config))
    out = release_notes.notes_for(ROOT, f"v{version}", "me/repo")
    assert out.strip() and "Full history" in out


def test_cli_exit_codes():
    script = os.path.join(SCRIPTS, "release_notes.py")
    bad = subprocess.run([sys.executable, script, "--tag", "nope"], capture_output=True, text=True)
    assert bad.returncode == 1 and "error:" in bad.stderr and bad.stdout == ""
    config = open(os.path.join(ROOT, "kea", "config.yaml"), encoding="utf-8").read()
    version = release.fmt(release.parse_config_version(config))
    good = subprocess.run([sys.executable, script, "--tag", f"v{version}", "--repo", "me/repo"],
                          capture_output=True, text=True)
    assert good.returncode == 0 and "github.com/me/repo/blob" in good.stdout
