import importlib.util
import os
import shutil
import subprocess

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
spec = importlib.util.spec_from_file_location("release", os.path.join(ROOT, "scripts", "release.py"))
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

CONFIG = 'name: "X"\nversion: "1.2.3"\nslug: "x"\n'
CHANGELOG = "# Changelog\n\n## 1.2.3\n- old note\n\n## 1.2.2\n- older\n"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A throw-away git repo with the two files, in the same layout as the real one."""
    if not shutil.which("git"):
        pytest.skip("git is not installed (e.g. inside the app image); the unit-test job covers this")
    for key, value in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"),
                       ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
        monkeypatch.setenv(key, value)
    (tmp_path / "kea").mkdir()
    (tmp_path / "kea" / "config.yaml").write_text(CONFIG)
    (tmp_path / "kea" / "CHANGELOG.md").write_text(CHANGELOG)
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "Initial release 1.2.3"], check=True)
    return tmp_path


def commit(repo, path, text, subject):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", subject], check=True)


def read(repo, name):
    return (repo / "kea" / name).read_text()


# ------------------------------------------------------------------ versions
@pytest.mark.parametrize("spec_, expected", [
    ("patch", (1, 2, 4)), ("minor", (1, 3, 0)), ("major", (2, 0, 0)), ("1.2.9", (1, 2, 9)), ("3.0.0", (3, 0, 0)),
])
def test_next_version(spec_, expected):
    assert release.next_version((1, 2, 3), spec_) == expected


@pytest.mark.parametrize("bad", ["1.2.3", "1.2.2", "0.9.9", "v1.3.0", "1.3", "latest", ""])
def test_next_version_rejects(bad):
    with pytest.raises(release.ReleaseError):
        release.next_version((1, 2, 3), bad)


def test_set_config_version_keeps_formatting():
    assert release.set_config_version(CONFIG, (1, 2, 4)) == 'name: "X"\nversion: "1.2.4"\nslug: "x"\n'
    assert release.set_config_version("version: 1.2.3\n", (2, 0, 0)) == "version: 2.0.0\n"
    assert release.set_config_version("version: '1.2.3'\n", (1, 3, 0)) == "version: '1.3.0'\n"
    with pytest.raises(release.ReleaseError):
        release.parse_config_version("name: x\n")


# ----------------------------------------------------------------- changelog
def test_render_changelog_inserts_below_the_header():
    out = release.render_changelog(CHANGELOG, (1, 2, 4), ["- new thing"])
    assert out.startswith("# Changelog\n\n## 1.2.4\n- new thing\n\n## 1.2.3\n")
    assert out.endswith("- older\n")


def test_render_changelog_consumes_the_unreleased_section():
    text = "# Changelog\n\n## Unreleased\n- pending a\n- pending b\n\n## 1.2.3\n- old note\n"
    assert release.unreleased_notes(text) == ["- pending a", "- pending b"]
    out = release.render_changelog(text, (1, 2, 4), ["- pending a", "- pending b"])
    assert "Unreleased" not in out
    assert out.startswith("# Changelog\n\n## 1.2.4\n- pending a\n- pending b\n\n## 1.2.3\n")


def test_render_changelog_refuses_an_existing_version():
    with pytest.raises(release.ReleaseError):
        release.render_changelog(CHANGELOG, (1, 2, 3), ["- x"])


def test_render_changelog_without_header():
    out = release.render_changelog("## 1.2.3\n- old\n", (1, 2, 4), ["- new"])
    assert out.startswith("# Changelog\n\n## 1.2.4\n- new\n\n## 1.2.3\n")


# ------------------------------------------------------------------- the CLI
def test_release_with_messages(repo):
    assert release.run(["patch", "-m", "Fix a thing", "-m", "- Another"], str(repo)) == 0
    assert 'version: "1.2.4"' in read(repo, "config.yaml")
    log = read(repo, "CHANGELOG.md")
    assert log.startswith("# Changelog\n\n## 1.2.4\n- Fix a thing\n- Another\n\n## 1.2.3")


def test_dry_run_writes_nothing(repo, capsys):
    assert release.run(["minor", "-m", "x", "--dry-run"], str(repo)) == 0
    assert read(repo, "config.yaml") == CONFIG and read(repo, "CHANGELOG.md") == CHANGELOG
    assert "1.2.3 -> 1.3.0" in capsys.readouterr().out


def test_refuses_a_release_with_no_notes(repo):
    with pytest.raises(release.ReleaseError, match="nothing to put"):
        release.run(["patch"], str(repo))
    assert read(repo, "config.yaml") == CONFIG


def test_refuses_a_version_that_is_not_newer(repo):
    with pytest.raises(release.ReleaseError):
        release.run(["1.2.3", "-m", "x"], str(repo))


def test_unreleased_and_messages_are_combined(repo):
    (repo / "kea" / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n- from the file\n\n## 1.2.3\n- old note\n")
    release.run(["patch", "-m", "from the flag"], str(repo))
    assert read(repo, "CHANGELOG.md").startswith("# Changelog\n\n## 1.2.4\n- from the file\n- from the flag\n")


def test_from_git_collects_only_changes_under_kea_since_the_last_release(repo):
    commit(repo, "kea/app/a.py", "1", "Update dependency fastapi to v0.143.0")
    commit(repo, "docs/note.md", "x", "Docs only change")                   # not under kea/
    commit(repo, "kea/app/b.py", "2", "Fix the lease table")
    release.run(["patch", "--from-git"], str(repo))
    assert read(repo, "CHANGELOG.md").startswith(
        "# Changelog\n\n## 1.2.4\n- Update dependency fastapi to v0.143.0\n- Fix the lease table\n\n## 1.2.3")


def test_from_git_starts_after_the_previous_release_and_skips_release_commits(repo):
    commit(repo, "kea/app/a.py", "1", "Old change, already released")
    release.run(["patch", "-m", "first", "--commit"], str(repo))
    commit(repo, "kea/app/b.py", "2", "New change")
    release.run(["patch", "--from-git"], str(repo))
    log = read(repo, "CHANGELOG.md")
    assert log.startswith("# Changelog\n\n## 1.2.5\n- New change\n\n## 1.2.4\n- first\n")
    assert "Release 1.2.4" not in log and "Old change" not in log


def test_commit_flag_creates_a_release_commit(repo):
    release.run(["minor", "-m", "x", "--commit"], str(repo))
    subject = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%s"],
                             capture_output=True, text=True).stdout.strip()
    assert subject == "Release 1.3.0"
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                            capture_output=True, text=True).stdout
    assert status.strip() == ""


# ------------------------------------------------ on the repository's own files
def test_result_on_the_real_files_passes_the_packaging_rules(tmp_path):
    """Run the tool on copies of the real config/changelog and check the output the way CI would."""
    import re
    import yaml
    (tmp_path / "kea").mkdir()
    for name in ("config.yaml", "CHANGELOG.md"):
        shutil.copy(os.path.join(ROOT, "kea", name), tmp_path / "kea" / name)
    release.run(["patch", "-m", "Example note"], str(tmp_path))
    config = yaml.safe_load((tmp_path / "kea" / "config.yaml").read_text())
    log = (tmp_path / "kea" / "CHANGELOG.md").read_text()
    assert re.fullmatch(r"\d+\.\d+\.\d+", str(config["version"]))
    # what test_docs_and_changelog_present requires: a section for the new version, holding our note
    # (plus whatever the real changelog had under "## Unreleased", which the tool moves into it)
    section = log.split(f"## {config['version']}\n", 1)[1].split("\n## ", 1)[0]
    assert "- Example note" in section
    assert "Unreleased" not in log
    original = yaml.safe_load(open(os.path.join(ROOT, "kea", "config.yaml"), encoding="utf-8"))
    assert {k: v for k, v in config.items() if k != "version"} == {k: v for k, v in original.items() if k != "version"}


def test_from_git_with_nothing_new_says_so(repo):
    with pytest.raises(release.ReleaseError, match="no commits under kea/"):
        release.run(["patch", "--from-git"], str(repo))
    assert read(repo, "config.yaml") == CONFIG


def test_tag_flag_commits_and_creates_an_annotated_tag(repo):
    release.run(["minor", "-m", "Shiny", "--tag"], str(repo))
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True).stdout.strip()
    assert git("log", "-1", "--format=%s") == "Release 1.3.0"
    assert git("cat-file", "-t", "v1.3.0") == "tag"                      # annotated, not lightweight
    assert git("rev-parse", "v1.3.0^{commit}") == git("rev-parse", "HEAD")
    assert "Shiny" in git("tag", "-l", "-n5", "v1.3.0")
    assert git("status", "--porcelain") == ""


def test_tag_flag_refuses_an_existing_tag_before_changing_anything(repo):
    subprocess.run(["git", "-C", str(repo), "tag", "v1.2.4"], check=True)
    with pytest.raises(release.ReleaseError, match="already exists"):
        release.run(["patch", "-m", "x", "--tag"], str(repo))
    assert read(repo, "config.yaml") == CONFIG and read(repo, "CHANGELOG.md") == CHANGELOG


def test_tag_flag_with_dry_run_changes_nothing(repo):
    release.run(["patch", "-m", "x", "--tag", "--dry-run"], str(repo))
    assert read(repo, "config.yaml") == CONFIG
    tags = subprocess.run(["git", "-C", str(repo), "tag"], capture_output=True, text=True).stdout.strip()
    assert tags == ""
