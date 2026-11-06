# Web UI (Home Assistant Ingress) design notes

> Last updated: 2026-10-04. Status: **implemented; CI-verified against the real Kea; not yet tried in a real Home Assistant.**

## 1. Requirement
- A Web UI is needed for configuration, reachable **only through Home Assistant Ingress** (no extra port exposed to users).

## 2. How settings are split

| Level | Where | What |
|---|---|---|
| Deployment | Home Assistant app Configuration tab (`options`/`schema`) | `interface`, `enable_dhcp6`, **initial defaults only** (since v2.1.0 they are managed in the Web UI's Settings tab and stored in the `global` section of `settings.json`) |
| Operation | Web UI (Ingress) | Multiple IPv4/IPv6 subnets, pools, gateway, DNS, lease time, static reservations, lease list, service status |

- Web UI settings are stored in `/config/kea/settings.json` and survive restarts.
- Apply flow: validate, render the configuration, test it with `kea-dhcpX -t` (enabled daemons only), and only if that
  passes write it and reload or restart the Kea processes. A failure before the write leaves everything untouched; one
  after it (a daemon that stops, or a rejected reload) is detected by watching the daemons for ~1.5 s and rolled back.
- This replaces the original `spec.md` design where every field lives on the Home Assistant Configuration page
  (`spec.md` is kept as a historical document).

## 3. Use Kea's own Web UI (ISC Stork)?
**Decision (confirmed): no, build a lightweight UI.**

- Kea itself has no built-in Web UI; the official one is **Stork**.
- Stork needs `stork-server` + `stork-agent` + PostgreSQL, and the Alpine 3.24 repositories have no Stork package.
- Under a sub-path (Ingress) Stork needs a reverse proxy that rewrites URLs, and it has its own login system.
- Editing subnets and reservations needs the `host_cmds` and `subnet_cmds` hook libraries loaded in Kea.
- Fallback: if Stork's monitoring is ever wanted, run the official Stork containers separately instead of bundling
  them into this app.
- Note: the Stork information comes from documentation summaries; it was never deployed to verify.

## 4. Technical decisions
- Backend: **FastAPI + uvicorn** (the owner's choice; it replaced an earlier `http.server` plan). Installed in a
  separate venv, `/opt/venv`, with every version pinned in `kea/requirements.txt` (`pydantic-core` is left to pydantic, which pins it exactly). Routes are plain `def` (they run in
  a thread pool because the manager blocks); `/docs` and `/openapi.json` are disabled.
- Frontend: a single `index.html` in plain JavaScript, using **relative URLs** only so it works behind Ingress.
- Kea control: no `kea-ctrl-agent` (deprecated in Kea 3.0, removed in 3.2). The app uses each daemon's own **unix control
  socket** (`/run/kea/kea4-ctrl.sock`, `kea6-ctrl.sock`; Kea 3.0 only accepts sockets in `KEA_CONTROL_SOCKET_DIR`, mode 0750,
  and hook libraries only from its hooks directory) and the `libdhcp_lease_cmds.so` hook (Alpine package `kea-hook-lease-cmds`):
  `lease4-get-all` / `lease6-get-all` (leases), `lease4-del` / `lease6-del` (delete, no restart), `config-reload` (apply;
  Kea answers whether it accepted the file). `kea_control.py` is the client. It is used only when the hook file exists
  (`manager.control_api`); on any failure the app falls back to the lease CSV, editing the lease file and SIGHUP. The channel
  is not exposed to the network (a unix socket); the HTTP control channel is deliberately not used.
- Security: only connections from the Supervisor (`172.30.32.2`) are accepted, everything else gets 403; write
  endpoints require `Content-Type: application/json`.
- Kea 3.0 configuration uses `output-options` (`output_options` is deprecated).
- With `host_network: true` the Ingress source IP is assumed to be `172.30.32.2`; **not yet verified in a real Home
  Assistant** (see section 6).
- Kea 3.x only reads and writes lease, pid and lock files in fixed directories unless told otherwise. The lease
  directory variable is `KEA_DHCP_DATA_DIR` (`KEA_LEASE_DIR` does not exist).

## 5. Status

| Item | Status |
|---|---|
| Multiple subnets and pools, static reservations, lease list, service status | ✅ Done; unit tests, real-Kea CI, checked by hand in a browser during development |
| Advanced options: domain name, search domains, NTP, custom options | ✅ Done (the meaning of custom options is checked by Kea's `-t`) |
| DHCPv6 prefix delegation pools | ✅ Done |
| Leases: filter, sort, reserve, delete | ✅ Done |
| Log viewer in the Web UI (captures daemon stdout in a 3000-line ring buffer, shows the last 500 lines on opening and keeps 1500 in the page; still echoed to the Home Assistant log) | ✅ Done |
| Settings export / import (import only loads the editor, it does not apply; v1 files are migrated automatically) | ✅ Done |
| Applying settings reloads with SIGHUP (restart only when the interface changes, a daemon is not running, or DHCPv6 is toggled) | ✅ Done; DHCPv4 reload **verified against the real Kea 3.0.x on amd64 in CI** (DHCPv6 reload and aarch64 are not exercised) |
| Interface and DHCPv6 switch editable in the Web UI | ✅ Done (the Home Assistant options are initial defaults) |
| Image build (amd64 and aarch64), real-Kea configuration tests, container smoke test | ✅ Done in CI (GitHub Actions) |
| Installation in a real Home Assistant (Supervisor, Ingress, `app_config` mount, real DHCP clients) | ⬜ Not verified |

### Settings file schema
- v1: one `pool_start`/`pool_end` per subnet, no `global` section.
- v2: a `pools` list, `domain_name`/`domain_search`/`ntp_servers`/`custom_options`, `pd_pools` for IPv6, and `global`
  (`interface`, `enable_dhcp6`). v1 files are migrated on read.

## 6. Unverified risks
- **Ingress source IP** under `host_network`: whether it really is `172.30.32.2` is still an assumption. It can be
  changed with the `KEA_UI_ALLOWED` environment variable; when a request is blocked the page shows
  `forbidden (<ip>)`.
- **Deleting a lease**: normally `lease4-del` / `lease6-del` through the control channel; the smoke test proves with the real
  Kea 3.0.3 that the lease is gone afterwards and that Kea was not restarted (IPv6 deletion is only tested against a fake
  socket). The *fallback* (used when the channel does not answer) appends a row with `valid_lifetime=0` while the daemon is
  stopped (Kea's memfile convention: the last row per address wins, and lifetime 0 means deleted). CI proves that Kea accepts
  that row and starts normally afterwards, but not that Kea then treats the lease as deleted; that needs a full DHCP client
  exercise. The file state when Kea is stopped in the middle of lease file cleanup (LFC) has not been tested either.
- **Real Home Assistant**: the Supervisor loading the app, the Ingress panel, the `app_config` mount and backups
  are only covered by static rule checks, not by an installation.
- The sample subnet `192.168.1.0/24` is active from the first start.

Resolved by CI: the image builds on Alpine 3.24 (amd64 and aarch64) with the package names `kea-dhcp4` and
`kea-dhcp6`; the generated configuration (several pools, `pd-pools`, `ntp-servers`, `domain-search`, custom options)
is accepted by the real Kea 3.0.3; SIGHUP reload works; the lease path problem described in section 4 was found and
fixed by the smoke test (`kea -t` does not open the lease database, so only a real start exposes it).

## 7. Dependency tracking (Renovate)
- Configuration in `renovate.json5`. It tracks GitHub Actions, the Dockerfile base image, `kea/requirements.txt`
  (runtime, pinned including transitive dependencies except `pydantic-core`) and `requirements-dev.txt`.
- Kea is not tracked: its version is decided by the Alpine package repository and only changes with the base image.
  The CI *Kea series guard* and the weekly scheduled run watch it.
- Merging a dependency PR does not change `version` in `config.yaml`, so users only get the update after a release.
  `scripts/release.py` bumps the version and writes the CHANGELOG entry; the Renovate PR text points to it.
