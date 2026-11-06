# CLAUDE.md

Home Assistant app that runs the ISC Kea DHCP server (DHCPv4 + optional DHCPv6) with a FastAPI Web UI shown
through Home Assistant Ingress. Users install it from this repository; Home Assistant reads `main`.

**The GitHub repository is private** (owner's choice, 2026-10-04). Home Assistant clones repositories anonymously,
so it cannot add a private one: until it is made public, nobody can install from the README's steps.
Do not assume it is public.

## Layout

| Path | What |
|---|---|
| `kea/` | The app itself: `config.yaml`, `Dockerfile`, `run.sh`, `requirements.txt` (runtime pins), `CHANGELOG.md`, `DOCS.md`, `translations/` |
| `kea/app/` | Backend (`settings.py` validate + render Kea configs, `manager.py` daemons/reload/logs/NTP refresh, `leases.py`, `server.py`, `ntpcheck.py` SNTP probe + name refresh, `migrate.py` old DHCP server app import) and `static/index.html` (the whole frontend) |
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
- **Say "app", not "add-on"**: Home Assistant renamed add-ons to apps (user interface since 2026.2). Technical
  identifiers keep the old word on purpose: the host path `/addon_configs/...`, the legacy `addon_config` map type,
  and our internal log source key `addon`. `tests/test_terminology.py` enforces this in user-facing files;
  `spec.md` and old CHANGELOG entries are history and keep the old word.
- **The user guide exists twice**: in `README.md` and `kea/DOCS.md`, between `<!-- guide:start -->` and
  `<!-- guide:end -->`. Edit both identically. `tests/test_docs.py` ties the guide's wording to the Web UI labels
  (`UI_TERMS`) and to the validation limits, so renaming a label means updating the guide and that list.
- **Releases**: `version` in `kea/config.yaml` is bumped only by `scripts/release.py`, never by hand. Note user-visible
  changes under `## Unreleased` in `kea/CHANGELOG.md`. A release is `scripts/release.py patch --from-git --tag` (bumps, commits, tags), then
  `git push --atomic origin main vX.Y.Z`; `release.yml` publishes the GitHub Release once CI is green. Merging code
  does not ship it, only a version bump does.
- **License**: MIT (`LICENSE`, copyright Raphael Chen). Software the app installs is listed in `THIRD_PARTY_NOTICES.md`; a new
  runtime dependency has to be added there too (a test checks it). Security reports go through `SECURITY.md`.
- **Dependencies**: `kea/requirements.txt` and `requirements-dev.txt` are exact `==` pins (tests enforce it).
  `pydantic-core` is deliberately not pinned because pydantic pins it. Renovate (`renovate.json5`) proposes updates.
- **Settings file** `/config/kea/settings.json` is versioned (`settings.migrate()`); keep old formats readable.
- Kea's version comes from Alpine, not from us. `ci/kea-series` holds the approved `major.minor`; change it only after
  reviewing a Kea upgrade.

## Hard-won facts (each one broke something before)

- `config.yaml`: `privileged` accepts only the capabilities in the Supervisor's `Capabilities` enum.
  `NET_BIND_SERVICE` is **not** one of them and makes the Supervisor reject the whole app. Use `app_config:rw`, not
  `config` or `addon_config` (deprecated). The Supervisor does not provide `BUILD_FROM` any more: write a plain `FROM`.
- `homeassistant: "2026.7.0"` in `config.yaml` is the minimum **Core** version. It stands in for "Supervisor 2026.07"
  (needed for the `app_config` mount, silently skipped by older Supervisors), because the Supervisor has no
  minimum-Supervisor key. The Core to Supervisor correspondence is assumed, not verified. Do not drop it while `app_config` is used.
- Kea 3.x reads lease files only from its data directory unless `KEA_DHCP_DATA_DIR` is set (`KEA_LEASE_DIR` does not
  exist). `kea -t` does not open the lease database, so only really starting Kea (`tests/test_real_kea.py`, the CI smoke test) catches this.
- The Home Assistant base image starts with s6: container environment variables reach `run.sh` only through the
  `#!/usr/bin/with-contenv` shebang.
- Kea rotates its lease file about every hour (LFC, `lfc-interval` 3600): the old state moves to `<file>.1`/`.2`/`.completed`
  and the main file only holds what changed since. Always read leases with `leases.lease_file_chain()` (Kea's own load
  order), never just the main file; a smoke test shorter than an hour cannot notice the difference.
- `kea -t` does not open sockets or databases, so it can accept a config that Kea then cannot run (a lease directory outside
  its data dir passed `-t` and only failed at start), and a rejected SIGHUP reload only shows in Kea's log. (A non-existent
  interface *is* already rejected by `-t`: seen in CI with Kea 3.0.3.) So `manager.apply` watches the daemons for a moment and rolls back (settings.json, generated
  configs, running daemons) if one stopped or logged a failure. A daemon younger than 2 s is restarted, not signalled
  (the default action of SIGHUP is to terminate it).
- uvicorn must run with `proxy_headers=False` (`server.make_config`): by default it takes the client address from
  `X-Forwarded-For` for connections from 127.0.0.1, which would let any process on this host-network machine pass as the Supervisor.
- The Web UI accepts only requests from the Supervisor (`172.30.32.2`); tests override it with `KEA_UI_ALLOWED`.
- The sidebar entry is off by default (`ingress_panel` is a user option), so the guide says "Open Web UI".
- Kea cannot resolve host names when it answers a client (DHCPv4 option 42 is addresses only), unlike ISC dhcpd, which
  resolved per request with a one-hour cache. So NTP names stay in `settings.json`, `manager` resolves and SNTP-probes
  them (`ntpcheck.refresh`, saved in `ntp-servers.json`, hourly in `ntp_loop`, 5 minutes after a failure) and renders
  only the good addresses; Kea is reloaded only when that list changes. `startup()` must never wait for DNS.
  pool.ntp.org answers 4 random A records with a 130 s TTL, hence the "keep addresses that still work" rule.
- Kea needs `ip-reservations-unique: false` for several reservations on one IP, `authoritative` is off by default
  (dhcpd's was on), and DHCPv6 NTP is option 56 with suboptions in the `v6-ntp-server-suboptions` space. These are
  covered by `tests/test_real_kea.py` and the smoke test, which only CI can run.
- The app talks to Kea through the daemons' **unix control sockets** (`kea_control.py`, `manager.control_api`): Kea 3.0 only accepts a
  control socket in `KEA_CONTROL_SOCKET_DIR` (mode 0750, we use `/run/kea`) and hook libraries only from its hooks directory, so the
  config names them by file name only (`kea4-ctrl.sock`, `libdhcp_lease_cmds.so`). It is on only when that hook file exists, so
  unit tests (fake Kea) and development machines use the file/SIGHUP fallbacks. Real behaviour (lease4-add/get-all/del, config-reload,
  result 3 = nothing matched) is checked by `tests/test_real_kea.py` and the smoke test in CI only.
- Web UI text from the network (lease hostnames) is untrusted: build DOM with `textContent`, never `innerHTML`.

## Not verified yet

The app has never been installed in a real Home Assistant: the Supervisor loading it, the Ingress panel, the
`app_config` mount and real DHCP clients are only covered by static checks and CI. Deleting a lease appends a
`valid_lifetime=0` row that Kea accepts, but that Kea treats the lease as deleted is not proven.
See `docs/webui-design-notes.md` section 6.
