#!/usr/bin/env python3
"""Check a release tag and print the GitHub Release notes for it (standard library only).

Used by .github/workflows/release.yml. It refuses (exit 1) unless
  - the tag looks like vX.Y.Z,
  - X.Y.Z equals `version` in kea/config.yaml, and
  - kea/CHANGELOG.md has a non-empty `## X.Y.Z` section.
The notes are that section, plus a link to the full changelog at the tagged commit.

  scripts/release_notes.py --tag v2.1.5 > notes.md
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release  # noqa: E402  (sibling script, no package)

TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")


def notes_for(root, tag, repo):
    match = TAG.fullmatch(tag)
    if not match:
        raise release.ReleaseError(f"{tag!r} is not a release tag of the form vX.Y.Z")
    version = tuple(int(part) for part in match.groups())

    with open(os.path.join(root, "kea", "config.yaml"), encoding="utf-8") as fh:
        current = release.parse_config_version(fh.read())
    if current != version:
        raise release.ReleaseError(
            f"tag {tag} does not match version {release.fmt(current)} in kea/config.yaml "
            "(was the version bumped before tagging?)")

    with open(os.path.join(root, "kea", "CHANGELOG.md"), encoding="utf-8") as fh:
        section = release.section_for(fh.read(), version)
    if not section:
        raise release.ReleaseError(f"kea/CHANGELOG.md has no entry (or an empty one) for {release.fmt(version)}")

    return (f"{section}\n\n"
            f"Full history: [CHANGELOG.md](https://github.com/{repo}/blob/{tag}/kea/CHANGELOG.md)\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", required=True, help="the release tag, e.g. v2.1.5")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "raphael1688dev/ha_app_kea"))
    args = parser.parse_args()
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    try:
        sys.stdout.write(notes_for(root, args.tag, args.repo))
    except release.ReleaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
