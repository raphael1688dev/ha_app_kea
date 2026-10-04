# Development

The Web UI backend is FastAPI (`kea/app/`). The frontend is a single `static/index.html` using plain
JavaScript and relative URLs so it works behind Ingress. Design decisions and open risks are in
[webui-design-notes.md](webui-design-notes.md).

```bash
pip install -r requirements-dev.txt
pytest tests                              # fake Kea; tests that need the real Kea are skipped
python scripts/check_supervisor_rules.py  # config.yaml vs. the live Home Assistant Supervisor rules
```

## How settings are applied

Web UI settings are stored in `/config/kea/settings.json` inside the container (the app's config folder on the
host). **Save & apply** validates the input, renders the Kea configuration, and checks it with `kea-dhcpX -t` (only
for the daemons that are switched on). Only then is it written, and running daemons get `SIGHUP` so Kea reloads
without interrupting service. A changed interface, a daemon that is not running, or one that started less than 2 s
ago is (re)started instead; switching DHCPv6 on or off only starts or stops `kea-dhcp6`. Because `kea -t` accepts
some configurations Kea then cannot run, the manager watches the daemons for about 1.5 s afterwards; if one stopped
or logged a failed reconfiguration, `settings.json`, the generated configs and the running daemons are restored and
the error is returned. A failure *before* the write leaves everything untouched.

## Continuous integration

`.github/workflows/ci.yml` runs on every push to `main`, on pull requests, and weekly (Monday):

| Job | What it checks |
|---|---|
| Unit tests | `pytest` on Python 3.13 and 3.14, including static checks of the app packaging |
| Supervisor rules | `kea/config.yaml` against the **current** Supervisor source: valid `privileged` capabilities, non-deprecated `map` types and `arch` |
| Image | Builds the image, then inside it: the whole test suite with the **real Kea** (`kea-dhcp4 -t` / `kea-dhcp6 -t` on generated configs, and a real start), and a smoke test of the running container (startup, SIGHUP reload, lease deletion, restart) |
| Image (aarch64) | Builds under QEMU and runs the real-Kea config tests. Not on pull requests, and not part of the `CI passed` check |

The image job also guards the Kea version: Alpine decides which Kea is installed, and `ci/kea-series` holds the
approved `major.minor` (a patch update, 3.0.x to 3.0.y, is not reported). If Alpine moves to a different series the job fails; review the change (Kea release notes,
generated configuration) and then update that file. If the **weekly** run fails, an issue is opened automatically,
because something upstream changed without a commit here.

To reproduce the smoke test locally you need Docker; see the `image` job for the exact commands.

## Dependency updates (Renovate)

`renovate.json5` makes [Renovate](https://docs.renovatebot.com/) open pull requests, weekly on Monday morning
(Asia/Taipei; security fixes at any time), for:

| What | Where | PR grouping |
|---|---|---|
| GitHub Actions (Renovate pins them by digest) | `.github/workflows/` | one "GitHub Actions" PR |
| Home Assistant base image (Alpine tag; Renovate adds the digest) | `kea/Dockerfile` | separate PR, labelled `base-image` |
| Python runtime packages | `kea/requirements.txt` | one PR, labelled `runtime` |
| Python test tooling | `requirements-dev.txt` | one PR |

Nothing is merged automatically. CI (including the real-Kea tests) must pass on every PR.

- **Kea itself is not tracked by Renovate.** It comes from Alpine, so it only changes with the base image. If a base
  image PR moves Kea to a different `major.minor`, the CI *Kea series guard* fails: read the Kea release notes, check
  the generated configuration, then update `ci/kea-series`.
- **Merging a runtime or base-image PR does not ship it.** Users only get an update when `version` in
  `kea/config.yaml` is bumped (and `kea/CHANGELOG.md` has an entry for it). See *Releasing* below.
- Renovate must be enabled for the repository once: install the [Renovate GitHub App](https://github.com/apps/renovate)
  and grant it access to this repository.

## Releasing

Home Assistant offers an update only when `version` in `kea/config.yaml` changes, and the GitHub Release is published
when a version tag is pushed. `scripts/release.py` does the bookkeeping:

```bash
scripts/release.py patch --from-git --tag           # bump, write the changelog entry, commit, create tag vX.Y.Z
git push origin main vX.Y.Z                         # publishes the version and triggers the Release workflow
```

Other forms:

```bash
scripts/release.py patch -m "Fix the lease table"   # hand-written note instead of / in addition to --from-git
scripts/release.py minor --from-git --dry-run       # preview only, nothing is written
scripts/release.py 3.0.0 -m "..." --commit          # explicit version, commit but no tag
```

Notes can come from any mix of `-m` (repeatable), `--from-git` (commit subjects under `kea/` since the last release,
e.g. Renovate's "Update dependency ..." commits) and a `## Unreleased` section at the top of the changelog, which the
tool turns into the new version's entry. A release with no notes is refused. The tool never pushes.

`.github/workflows/release.yml` runs when a tag `vX.Y.Z` is pushed and creates the GitHub Release with that version's
changelog section. It refuses unless the tag equals `version` in `kea/config.yaml`, the changelog has an entry for it,
the commit is on `main`, and CI passed for that commit (it waits for CI if it is still running). To redo the release
of an existing tag, run the workflow by hand from the Actions tab and give it the tag name.
