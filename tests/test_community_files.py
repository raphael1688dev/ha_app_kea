"""The files a stranger meets when the repository is public: security policy, issue forms, third-party notices."""
import os
import re

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def test_third_party_notices_list_every_runtime_package():
    """When a dependency is added to kea/requirements.txt its license has to be listed too."""
    pinned = {norm(line.split("==")[0]) for line in read("kea", "requirements.txt").splitlines()
              if "==" in line and not line.lstrip().startswith("#")}
    pinned.add("pydantic-core")                       # not pinned (pydantic pins it) but installed with pydantic
    listed = {norm(m.group(1)) for m in re.finditer(r"^\| ([A-Za-z0-9_.-]+)(?: \([^)]*\))? \| [^|]+ \|$",
                                                     read("THIRD_PARTY_NOTICES.md"), re.M)}
    assert pinned <= listed, f"missing from THIRD_PARTY_NOTICES.md: {sorted(pinned - listed)}"


def test_third_party_notices_state_the_important_facts():
    text = read("THIRD_PARTY_NOTICES.md")
    assert "MPL-2.0" in text and "Kea" in text
    assert "not affiliated with" in text
    assert "Open Home Foundation" in text


def test_security_policy_tells_people_how_to_report_privately():
    text = read("SECURITY.md")
    assert "Report a vulnerability" in text and "do not open a public issue" in text.lower()
    for upstream in ("isc-projects/kea", "home-assistant/core"):
        assert upstream in text, upstream


def test_issue_forms_are_usable():
    folder = os.path.join(ROOT, ".github", "ISSUE_TEMPLATE")
    config = yaml.safe_load(read(".github", "ISSUE_TEMPLATE", "config.yml"))
    assert config["blank_issues_enabled"] is False
    assert any("security/advisories/new" in link["url"] for link in config["contact_links"])

    bug = yaml.safe_load(read(".github", "ISSUE_TEMPLATE", "bug_report.yml"))
    ids = {item["id"] for item in bug["body"] if "id" in item}
    assert {"app_version", "ha_version", "what_happened", "logs"} <= ids          # what a maintainer needs first
    required = {item["id"] for item in bug["body"]
                if item.get("validations", {}).get("required")}
    assert {"app_version", "ha_version", "what_happened"} <= required
    assert yaml.safe_load(read(".github", "ISSUE_TEMPLATE", "feature_request.yml"))["name"]
    assert sorted(os.listdir(folder)) == ["bug_report.yml", "config.yml", "feature_request.yml"]


def test_license_is_mit_with_a_holder_and_year():
    text = read("LICENSE")
    assert text.startswith("MIT License\n")
    assert re.search(r"^Copyright \(c\) 20\d\d \S", text, re.M)
    assert "Permission is hereby granted, free of charge" in text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in text


def test_the_license_is_announced_where_people_look():
    readme = read("README.md")
    assert "[MIT License](LICENSE)" in readme and "THIRD_PARTY_NOTICES.md" in readme and "SECURITY.md" in readme
    assert "MIT licensed" in read("kea", "DOCS.md")
    assert 'org.opencontainers.image.licenses="MIT"' in read("kea", "Dockerfile")
    assert "released under the MIT License" in read("THIRD_PARTY_NOTICES.md")
