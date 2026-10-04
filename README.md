# ISC Kea DHCP Add-on for Home Assistant

ISC Kea dual-stack DHCP server (DHCPv4 + optional DHCPv6) with a Home Assistant Ingress Web UI.

## Installation

1. In Home Assistant, go to Settings → Add-ons → Add-on Store → ⋮ menu (top right) → Repositories.
2. Add `https://github.com/raphael1688dev/ha_app_kea`.
3. Install **ISC Kea DHCP Server**, set the network interface, and start it.
4. Open **Kea DHCP** in the sidebar and configure your subnets in the Web UI.

## Features (Web UI)

| Tab | What you can do |
|---|---|
| **IPv4 / IPv6** | Multiple subnets; multiple address pools per subnet; gateway, DNS, lease time; static reservations (MAC for IPv4, DUID for IPv6). Advanced options: domain name, search domains, NTP servers, custom DHCP options by name, DHCPv6 prefix delegation pools |
| **Leases** | Active IPv4 / IPv6 leases with filtering and sorting; **Reserve** turns a lease into a static reservation; **Delete** removes a lease |
| **Logs** | Live Kea and add-on log, filter by daemon or text |
| **Settings** | Network interface, DHCPv6 on/off, settings export and import |

The server status and a **Restart Kea** button are always visible at the top.

## How settings are applied

Web UI settings are stored in `/config/kea/settings.json`. When you click **Save & apply**:

1. The input is validated.
2. The generated Kea configuration is checked with `kea-dhcpX -t`.
3. Only if both pass is the configuration written. Running daemons are told to reload it (`SIGHUP`), so
   leases and service are not interrupted. Changing the network interface, enabling DHCPv6, or a daemon
   that is not running causes a (re)start instead.

If any step fails, the running configuration is left untouched and the errors are shown.

**Import** loads a settings file into the editor (older single-pool files are upgraded automatically) and
changes nothing until you click **Save & apply**.

## Add-on Configuration tab

`interface` and `enable_dhcp6` in the add-on Configuration tab are only the **initial defaults** used until
the first save in the Web UI. After that, the Web UI's Settings tab is authoritative.

> A sample `192.168.1.0/24` subnet is created on first start. **Change it to your own network** before
> letting devices obtain addresses from this server.

## Notes

- The add-on uses `host_network`. Make sure no other DHCP server (for example your router) is handing out
  addresses on the same network.
- Lease files and `settings.json` are kept in `/config/kea/` in Home Assistant.
- **Deleting a lease** briefly stops the Kea daemon while the lease file is edited. The client keeps its
  address until the lease runs out and then asks again.
- Kea output is also written to the add-on log in Home Assistant.
- The Web UI only accepts connections from the Home Assistant Supervisor (Ingress). It is not exposed
  anywhere else.

## Development

The Web UI backend is FastAPI (`kea/app/`). The frontend is a single `static/index.html` using plain
JavaScript and relative URLs so it works behind Ingress.

```bash
pip install -r requirements-dev.txt
pytest tests                              # fake Kea; tests that need the real Kea are skipped
python scripts/check_supervisor_rules.py  # config.yaml vs. the live Home Assistant Supervisor rules
```

### Dependency updates (Renovate)

`renovate.json5` makes [Renovate](https://docs.renovatebot.com/) open pull requests, weekly on Monday morning
(Asia/Taipei; security fixes at any time), for:

| What | Where | PR grouping |
|---|---|---|
| GitHub Actions (pinned by digest) | `.github/workflows/` | one "GitHub Actions" PR |
| Home Assistant base image (Alpine tag + digest) | `kea/Dockerfile` | separate PR, labelled `base-image` |
| Python runtime packages | `kea/requirements.txt` | one PR, labelled `runtime` |
| Python test tooling | `requirements-dev.txt` | one PR |

Nothing is merged automatically. CI (including the real-Kea tests) must pass on every PR.

- **Kea itself is not tracked by Renovate.** It comes from Alpine, so it only changes with the base image. If a base
  image PR moves Kea to a different `major.minor`, the CI *Kea series guard* fails: read the Kea release notes, check
  the generated configuration, then update `ci/kea-series`.
- **Merging a runtime or base-image PR does not ship it.** Users only get an update when `version` in
  `kea/config.yaml` is bumped (and `kea/CHANGELOG.md` has an entry for it).
- Renovate must be enabled for the repository once: install the [Renovate GitHub App](https://github.com/apps/renovate)
  and grant it access to this repository.

### Continuous integration

`.github/workflows/ci.yml` runs on every push to `main`, on pull requests, and weekly (Monday):

| Job | What it checks |
|---|---|
| Unit tests | `pytest` on Python 3.13 and 3.14, including static checks of the add-on packaging |
| Supervisor rules | `kea/config.yaml` against the **current** Supervisor source: valid `privileged` capabilities, non-deprecated `map` types and `arch` |
| Image | Builds the image, then inside it: the whole test suite with the **real Kea** (`kea-dhcp4 -t` / `kea-dhcp6 -t` on generated configs), and a smoke test of the running container (startup, SIGHUP reload, lease deletion, restart) |
| Image (aarch64) | Builds under QEMU and runs the real-Kea config tests (not on pull requests) |

The image job also guards the Kea version: Alpine decides which Kea is installed, and `ci/kea-series`
holds the approved `major.minor`. If Alpine moves to a different series the job fails; review the change
(Kea release notes, generated config) and then update that file. If the **weekly** run fails, an issue is
opened automatically, because something upstream changed without a commit here.

To reproduce the smoke test locally you need Docker; see the `image` job for the exact commands.
