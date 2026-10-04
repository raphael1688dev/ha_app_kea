"""The user guide (README.md and kea/DOCS.md) must stay in step with the product."""
import html
import os
import re

import pytest
import yaml

import settings as cfg

ROOT = os.path.join(os.path.dirname(__file__), "..")
START, END = "<!-- guide:start -->", "<!-- guide:end -->"

# Wording the guide relies on. Each must exist in the Web UI (so a renamed label fails here) and in the guide.
UI_TERMS = [
    "IPv4", "IPv6", "Leases", "Logs", "Settings",                               # tabs
    "Save & apply", "Discard changes", "Restart Kea", "Reserve", "Delete",       # buttons
    "Export settings", "Import settings",
    "Subnet (CIDR)", "Default gateway", "DNS servers (comma separated)",         # subnet fields
    "Lease time (seconds)", "Address pools", "Reservations", "Reserved IP", "Hostname",
    "Advanced options", "Domain name", "Search domains (comma separated)",
    "NTP servers (comma separated)", "Custom DHCP options", "Prefix delegation pools",
    "Prefix length", "Delegated length",
    "Network interface", "Enable DHCPv6 server",                                  # settings tab
    "Request failed", "Kea problem", "Persistent storage is not mounted",        # banners
]


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def guide(text):
    assert text.count(START) == 1 and text.count(END) == 1, "guide markers missing or duplicated"
    return text.split(START, 1)[1].split(END, 1)[0].strip()


@pytest.fixture(scope="module")
def readme_guide():
    return guide(read("README.md"))


@pytest.fixture(scope="module")
def ui_text():
    return html.unescape(read("kea", "app", "static", "index.html"))


def test_readme_and_in_app_docs_have_the_same_guide(readme_guide):
    assert guide(read("kea", "DOCS.md")) == readme_guide, \
        "README.md and kea/DOCS.md must contain the same guide between the guide markers"


def test_guide_explains_every_add_on_option(readme_guide):
    schema = yaml.safe_load(read("kea", "config.yaml"))["schema"]
    for option in schema:
        assert f"`{option}`" in readme_guide, f"option {option} is not explained"


@pytest.mark.parametrize("term", UI_TERMS)
def test_ui_wording_matches_the_guide(term, readme_guide, ui_text):
    assert term in ui_text, f"{term!r} is no longer in the Web UI; update the guide and UI_TERMS"
    assert term in readme_guide, f"{term!r} is not mentioned in the guide"


def test_guide_numbers_match_the_validation_rules(readme_guide):
    assert f"{cfg.LEASE_MIN} to {cfg.LEASE_MAX:,}" in readme_guide
    assert "86,400" in readme_guide                                            # the default lease time
    assert "86400" in str(cfg.default_settings()["dhcp4"]["subnets"][0]["lease_time"])


def test_guide_covers_the_essentials(readme_guide):
    for heading in ("## Before you start", "## Quick start", "## Settings reference",
                    "## Troubleshooting", "## Your data", "## IPv6"):
        assert heading in readme_guide, heading
    numbered = re.findall(r"^\d+\. ", readme_guide.split("## Quick start", 1)[1].split("##", 1)[0], re.M)
    assert len(numbered) >= 5, "the quick start should be a numbered list"


def test_readme_links_and_badge_point_at_real_files():
    text = read("README.md")
    for target in re.findall(r"\]\((?!https?:|#)([^)#]+)", text):
        assert os.path.exists(os.path.join(ROOT, target)), f"README links to a missing file: {target}"
    assert os.path.isfile(os.path.join(ROOT, ".github", "workflows", "ci.yml"))
    assert "workflows/ci.yml/badge.svg" in text


def test_development_doc_describes_the_release_flow():
    text = read("docs", "development.md")
    for needle in ("scripts/release.py", "--tag", "git push origin main", "release.yml", "Renovate",
                   "Continuous integration"):
        assert needle in text, needle
    assert os.path.isfile(os.path.join(ROOT, ".github", "workflows", "release.yml"))
