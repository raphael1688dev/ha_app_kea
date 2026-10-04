#!/usr/bin/env python3
"""Cut a release of the app: bump `version` in kea/config.yaml and write the CHANGELOG entry.

Home Assistant only offers an update when `version` changes, so merging a dependency PR (Renovate)
or a fix does NOT ship it until this has been run. Standard library only.

  scripts/release.py patch -m "Fix X"                bump 2.1.3 -> 2.1.4 with a hand-written note
  scripts/release.py minor --from-git                bump 2.1.3 -> 2.2.0, notes from the commits since the last release
  scripts/release.py 3.0.0 --dry-run                 show what would change, write nothing
  scripts/release.py patch --from-git --commit       also create the "Release x.y.z" commit (never pushes)
  scripts/release.py patch --from-git --tag          commit and create the annotated tag vX.Y.Z (never pushes)

Pushing the tag (`git push origin main vX.Y.Z`) makes the "Release" GitHub Actions workflow publish the GitHub Release.

Notes come from any mix of: the "## Unreleased" section at the top of kea/CHANGELOG.md, -m/--message
(repeatable) and --from-git. A release with no notes at all is refused.
"""
import argparse
import os
import re
import subprocess
import sys

VERSION_LINE = re.compile(r'^(version:[ \t]*)(["\']?)(\d+)\.(\d+)\.(\d+)\2[ \t]*$', re.M)
UNRELEASED = re.compile(r"^## Unreleased[ \t]*\n(.*?)(?=^## |\Z)", re.M | re.S)
HEADER = "# Changelog\n"


class ReleaseError(Exception):
    pass


# ----------------------------------------------------------------- versions
def parse_config_version(config_text):
    match = VERSION_LINE.search(config_text)
    if not match:
        raise ReleaseError('could not find a `version: "x.y.z"` line in kea/config.yaml')
    return tuple(int(match.group(i)) for i in (3, 4, 5))


def next_version(current, spec):
    """`spec` is patch / minor / major or an explicit x.y.z that must be greater than *current*."""
    major, minor, patch = current
    if spec == "patch":
        return (major, minor, patch + 1)
    if spec == "minor":
        return (major, minor + 1, 0)
    if spec == "major":
        return (major + 1, 0, 0)
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", spec)
    if not match:
        raise ReleaseError(f"{spec!r} is not patch, minor, major or a version like 2.3.0")
    explicit = tuple(int(g) for g in match.groups())
    if explicit <= current:
        raise ReleaseError(f"{spec} is not newer than the current version {fmt(current)}")
    return explicit


def fmt(version):
    return ".".join(str(part) for part in version)


def set_config_version(config_text, version):
    """Replace only the version number; quoting and the rest of the file stay as they are."""
    return VERSION_LINE.sub(lambda m: f"{m.group(1)}{m.group(2)}{fmt(version)}{m.group(2)}", config_text, count=1)


# ---------------------------------------------------------------- changelog
def unreleased_notes(changelog_text):
    match = UNRELEASED.search(changelog_text)
    return [line.rstrip() for line in match.group(1).splitlines() if line.strip()] if match else []


def as_bullet(text):
    text = text.strip()
    return text if text.startswith(("- ", "* ")) else f"- {text}"


def render_changelog(changelog_text, version, notes):
    """Return the changelog with a `## <version>` section on top; `## Unreleased` is consumed."""
    heading = f"## {fmt(version)}"
    if re.search(rf"^{re.escape(heading)}\s*$", changelog_text, re.M):
        raise ReleaseError(f"kea/CHANGELOG.md already has an entry for {fmt(version)}")
    body = "\n".join(notes) + "\n\n"
    stripped = UNRELEASED.sub("", changelog_text, count=1)
    if HEADER in stripped:
        head, _, rest = stripped.partition(HEADER)
        return f"{head}{HEADER}\n{heading}\n{body}{rest.lstrip(chr(10))}"
    return f"{HEADER}\n{heading}\n{body}{stripped.lstrip(chr(10))}"


def section_for(changelog_text, version):
    """Body of the `## <version>` section (stripped), or None if there is no such section."""
    match = re.search(rf"^## {re.escape(fmt(version))}[ \t]*\n(.*?)(?=^## |\Z)", changelog_text, re.M | re.S)
    return match.group(1).strip() if match else None


# ---------------------------------------------------------------------- git
def git(root, *args):
    result = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def notes_from_git(root):
    """Subjects of commits that touched kea/ since the last commit that changed `version:`."""
    last = git(root, "log", "-G", r"^version:", "--format=%H", "-n", "1", "--", "kea/config.yaml").strip()
    rng = [f"{last}..HEAD"] if last else []
    subjects = git(root, "log", *rng, "--no-merges", "--format=%s", "--", "kea/").splitlines()
    notes = []
    for subject in subjects:
        subject = subject.strip()
        if subject and not subject.startswith("Release ") and as_bullet(subject) not in notes:
            notes.append(as_bullet(subject))
    return list(reversed(notes))          # oldest first, like a changelog reads


# --------------------------------------------------------------------- main
def run(argv, root):
    parser = argparse.ArgumentParser(description="Bump the app version and write the CHANGELOG entry.")
    parser.add_argument("bump", help="patch, minor, major, or an explicit version such as 2.3.0")
    parser.add_argument("-m", "--message", action="append", default=[], metavar="TEXT",
                        help="changelog line (repeatable)")
    parser.add_argument("--from-git", action="store_true",
                        help="add the commit subjects since the last release (changes under kea/)")
    parser.add_argument("--dry-run", action="store_true", help="show the result, write nothing")
    parser.add_argument("--commit", action="store_true",
                        help='git commit the two files as "Release x.y.z" (never pushes)')
    parser.add_argument("--tag", action="store_true",
                        help="implies --commit, then creates the annotated tag vX.Y.Z (never pushes)")
    args = parser.parse_args(argv)

    config_path = os.path.join(root, "kea", "config.yaml")
    changelog_path = os.path.join(root, "kea", "CHANGELOG.md")
    with open(config_path, encoding="utf-8") as fh:
        config_text = fh.read()
    with open(changelog_path, encoding="utf-8") as fh:
        changelog_text = fh.read()

    current = parse_config_version(config_text)
    new = next_version(current, args.bump)

    notes = unreleased_notes(changelog_text)
    notes += [as_bullet(m) for m in args.message]
    from_git = notes_from_git(root) if args.from_git else []
    notes += [n for n in from_git if n not in notes]
    if not notes:
        if args.from_git:
            raise ReleaseError("--from-git found no commits under kea/ since the last release; "
                               "there is nothing to ship (or add -m \"...\" to release anyway)")
        raise ReleaseError("nothing to put in the changelog: add -m \"...\", use --from-git, "
                           "or write notes under \"## Unreleased\" in kea/CHANGELOG.md")

    tag_name = f"v{fmt(new)}"
    if args.tag and subprocess.run(["git", "-C", root, "rev-parse", "-q", "--verify", f"refs/tags/{tag_name}"],
                                   capture_output=True).returncode == 0:
        raise ReleaseError(f"the tag {tag_name} already exists")

    new_config = set_config_version(config_text, new)
    new_changelog = render_changelog(changelog_text, new, notes)

    print(f"version  {fmt(current)} -> {fmt(new)}")
    print("changelog entry:")
    for line in notes:
        print(f"  {line}")
    if args.dry_run:
        print("(dry run: nothing written)")
        return 0

    for path, text in ((config_path, new_config), (changelog_path, new_changelog)):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    print("updated kea/config.yaml and kea/CHANGELOG.md")

    if args.commit or args.tag:
        git(root, "add", "kea/config.yaml", "kea/CHANGELOG.md")
        git(root, "commit", "-m", f"Release {fmt(new)}")
        print(f'committed "Release {fmt(new)}" (not pushed)')
        if args.tag:
            git(root, "tag", "-a", tag_name, "-m", f"Release {fmt(new)}\n\n" + "\n".join(notes))
            print(f"created tag {tag_name}; publish with: git push origin main {tag_name}")
    else:
        print("next: review the changelog wording, run the tests, then commit and push "
              "(Home Assistant offers the update once main has the new version).")
    return 0


def main():
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    try:
        return run(sys.argv[1:], root)
    except ReleaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
