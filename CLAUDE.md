# CLAUDE.md

Home Assistant add-on that runs the ISC Kea DHCP server (DHCPv4 + optional DHCPv6) with a FastAPI Web UI shown
through Home Assistant Ingress. Users install it from this repository; Home Assistant reads `main`.

## Layout

| Path | What |
|---|---|
| `kea/` | The add-on itself: `config.yaml`, `Dockerfile`, `run.sh`, `requirements.txt` (runtime pins), `CHANGELOG.md`, `DOCS.md`, `translations/` |
| `kea/app/` | Backend (`settings.py` validate + render Kea configs, `manager.py` daemons/reload/logs, `leases.py`, `server.py`) and `static/index.html` (the whole frontend) |
| `tests/` | pytest. Fake Kea scripts for most tests; `test_real_kea.py` needs the real Kea |
| `ci/`, `scripts/` | CI smoke test and approved Kea series; release tool, Supervisor-rule checker |
| `.github/workflows/` | `ci.yml` (tests, image, real Kea, smoke test), `release.yml` (tag -> GitHub Release) |
| `docs/` | `development.md` (CI, Renovate, releasing), `webui-design-notes.md` (decisions and open risks) |

## Commands

```bash
pip install -r requirements-dev.txt
pytest tests                                     # real-Kea tests are skipped without Kea
python scripts/check_supervisor_rules.py         # kea/config.yaml vs. the live Supervisor source
python scripts/release.py patch --from-git --dry-run
```

The developer machine often has neither Docker nor Kea. Anything about the image or the real Kea can only be
checked in GitHub CI (`gh run watch`, `gh run view --log-failed`).

## Conventions

- **Everything in the repo is English** (UI, messages, docs, comments, commits, release notes). `tests/test_english_only.py`
  fails on Chinese characters in tracked files.
- **The user guide exists twice**: in `README.md` and `kea/DOCS.md`, between `<!-- guide:start -->` and
  `<!-- guide:end -->`. Edit both identically. `tests/test_docs.py` ties the guide's wording to the Web UI labels
  (`UI_TERMS`) and to the validation limits, so renaming a label means updating the guide and that list.
- **Releases**: `version` in `kea/config.yaml` is bumped only by `scripts/release.py`, never by hand. Note user-visible
  changes under `## Unreleased` in `kea/CHANGELOG.md`. A release is `scripts/release.py patch --from-git`, commit, then
  `git push --atomic origin main vX.Y.Z`; `release.yml` publishes the GitHub Release once CI is green. Merging code
  does not ship it, only a version bump does.
- **Dependencies**: `kea/requirements.txt` and `requirements-dev.txt` are exact `==` pins (tests enforce it).
  `pydantic-core` is deliberately not pinned because pydantic pins it. Renovate (`renovate.json5`) proposes updates.
- **Settings file** `/config/kea/settings.json` is versioned (`settings.migrate()`); keep old formats readable.
- Kea's version comes from Alpine, not from us. `ci/kea-series` holds the approved `major.minor`; change it only after
  reviewing a Kea upgrade.

## Hard-won facts (each one broke something before)

- `config.yaml`: `privileged` accepts only the capabilities in the Supervisor's `Capabilities` enum.
  `NET_BIND_SERVICE` is **not** one of them and makes the Supervisor reject the whole add-on. Use `app_config:rw`, not
  `config` or `addon_config` (deprecated). The Supervisor does not provide `BUILD_FROM` any more: write a plain `FROM`.
- Kea 3.x reads lease files only from its data directory unless `KEA_DHCP_DATA_DIR` is set (`KEA_LEASE_DIR` does not
  exist). `kea -t` does not open the lease database, so only really starting Kea (the CI smoke test) catches this.
- The Home Assistant base image starts with s6: container environment variables reach `run.sh` only through the
  `#!/usr/bin/with-contenv` shebang.
- The Web UI accepts only requests from the Supervisor (`172.30.32.2`); tests override it with `KEA_UI_ALLOWED`.
- The sidebar entry is off by default (`ingress_panel` is a user option), so the guide says "Open Web UI".
- Web UI text from the network (lease hostnames) is untrusted: build DOM with `textContent`, never `innerHTML`.

## Not verified yet

The add-on has never been installed in a real Home Assistant: the Supervisor loading it, the Ingress panel, the
`app_config` mount and real DHCP clients are only covered by static checks and CI. Deleting a lease appends a
`valid_lifetime=0` row that Kea accepts, but that Kea treats the lease as deleted is not proven.
See `docs/webui-design-notes.md` section 6.
