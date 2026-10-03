#!/usr/bin/env python3
"""Check kea/config.yaml against the *live* Home Assistant Supervisor source.

Catches the class of bug where the Supervisor would reject or warn about the
app (invalid `privileged` capability, deprecated `map` type, deprecated `arch`),
and notices when those rules change upstream.

Exit codes: 0 = fine (or the Supervisor source could not be fetched: warning only),
            1 = violation found.
Usage: scripts/check_supervisor_rules.py [--ref main] [--strict]
       --strict makes a failed download an error as well.
"""
import argparse
import os
import re
import sys
import urllib.request

import yaml

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RAW = "https://raw.githubusercontent.com/home-assistant/supervisor/{ref}/{path}"


def fetch(ref, path):
    url = RAW.format(ref=ref, path=path)
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8")


def class_block(source, header):
    start = source.index(header)
    rest = source[start + len(header):]
    match = re.search(r"\n(?=\S)", rest)          # next top-level statement
    return rest[:match.start()] if match else rest


def enum_members(block):
    return re.findall(r'^\s+[A-Z0-9_]+\s*=\s*"([^"]+)"', block, re.M)


def legacy_members(block):
    """Members listed after a comment that mentions legacy/deprecated."""
    out, legacy = [], False
    for line in block.splitlines():
        if line.strip().startswith("#"):
            legacy = bool(re.search(r"legacy|deprecated", line, re.I))
            continue
        m = re.match(r'^\s+[A-Z0-9_]+\s*=\s*"([^"]+)"', line)
        if m and legacy:
            out.append(m.group(1))
    return out


def arch_lists(source):
    names = dict(re.findall(r'^(ARCH_[A-Z0-9]+)\s*=\s*"([^"]+)"', source, re.M))

    def lst(var):
        m = re.search(rf"^{var}\s*=\s*\[([^\]]*)\]", source, re.M)
        return {names[n.strip()] for n in m.group(1).split(",") if n.strip() in names} if m else set()

    return lst("ARCH_ALL"), lst("ARCH_DEPRECATED")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", default="main")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    try:
        docker_const = fetch(args.ref, "supervisor/docker/const.py")
        apps_const = fetch(args.ref, "supervisor/apps/const.py")
        root_const = fetch(args.ref, "supervisor/const.py")
    except Exception as exc:                      # network, 404, ...
        print(f"WARNING: could not download the Supervisor source ({exc}); rules not checked")
        return 1 if args.strict else 0

    capabilities = set(enum_members(class_block(docker_const, "class Capabilities(StrEnum):")))
    map_block = class_block(apps_const, "class MappingType(StrEnum):")
    map_types = set(enum_members(map_block))
    deprecated_maps = set(legacy_members(map_block)) | {"config"}   # `config` is warned about in validate.py
    arch_ok, arch_deprecated = arch_lists(root_const)

    if not capabilities or not map_types or not arch_ok:
        print("WARNING: could not parse the Supervisor source (layout changed?); rules not checked")
        return 1 if args.strict else 0

    with open(os.path.join(ROOT, "kea", "config.yaml"), encoding="utf-8") as fh:
        config = yaml.safe_load(fh)

    problems = []
    for cap in config.get("privileged", []):
        if cap not in capabilities:
            problems.append(f"privileged: {cap} is not a valid capability (the Supervisor would reject the app)")
    for entry in config.get("map", []):
        kind = entry.split(":")[0]
        if kind not in map_types:
            problems.append(f"map: {kind} is not a known map type")
        elif kind in deprecated_maps:
            problems.append(f"map: {kind} is deprecated")
    for arch in config.get("arch", []):
        if arch in arch_deprecated:
            problems.append(f"arch: {arch} is deprecated")
        elif arch not in arch_ok:
            problems.append(f"arch: {arch} is not supported")

    print(f"Supervisor ({args.ref}): {len(capabilities)} capabilities, "
          f"{len(map_types)} map types ({len(deprecated_maps)} deprecated), arch {sorted(arch_ok)}")
    for p in problems:
        print(f"ERROR: {p}")
    if not problems:
        print("OK: kea/config.yaml matches the current Supervisor rules")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
